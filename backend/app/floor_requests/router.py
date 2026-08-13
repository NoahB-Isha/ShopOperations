"""Floor Team item requests — "we need more of this" from the people on the
floor who can't raise transfers themselves.

Deliberately NOT a transfer request: it's an ask that lands on the Inventory
Flow Manager's Suggested items page, ABOVE the app's own computed
suggestions, where a human decides what actually gets pulled. Nothing here
touches Odoo.

Every ask is its own row, with the name of the person who raised it. Two
people asking for the same product make two entries — who noticed, and how
much they each thought was needed, is the information; collapsing them would
throw it away.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..db import get_db
from ..models import (
    FloorRequest,
    FloorRequestStatus,
    Product,
    Role,
    StockLevel,
    User,
    utcnow,
)

# Anyone who works the floor may ask; the Inventory Flow Manager (and admin)
# is who the asks are FOR.
ASKERS = (Role.FLOOR_ROTATING, Role.SHOPPE_FLOOR)
REVIEWERS = (Role.SHOPPE_FLOOR,)

router = APIRouter(prefix="/floor-requests", tags=["floor-requests"])


class LineIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)


class RequestIn(BaseModel):
    note: str = ""
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class FloorRequestOut(BaseModel):
    id: int
    product_id: int
    sku: str
    barcode: str
    name: str
    category: str
    qty: float
    note: str
    status: str
    requested_by: str
    created_at: datetime
    resolved_by: str = ""
    resolved_at: datetime | None = None
    floor_qty: float = 0
    bwhse_qty: float = 0


def _names(db: Session, ids: set[int | None]) -> dict[int, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(select(User).where(User.id.in_(real)))
    }


def _stock(db: Session, product_ids: set[int]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    if product_ids:
        for pid, key, qty in db.execute(
            select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
                StockLevel.product_id.in_(product_ids)
            )
        ):
            out.setdefault(pid, {})[key] = float(qty)
    return out


def _out(db: Session, rows: list[FloorRequest]) -> list[FloorRequestOut]:
    names = _names(db, {r.requested_by_id for r in rows} | {r.resolved_by_id for r in rows})
    stock = _stock(db, {r.product_id for r in rows})
    items = []
    for r in rows:
        p = r.product
        s = stock.get(r.product_id, {})
        items.append(
            FloorRequestOut(
                id=r.id,
                product_id=r.product_id,
                sku=p.global_sku,
                barcode=p.barcode or "",
                name=p.name,
                category=p.category,
                qty=r.qty,
                note=r.note,
                status=r.status,
                requested_by=names.get(r.requested_by_id or 0, "someone"),
                created_at=r.created_at,
                resolved_by=names.get(r.resolved_by_id or 0, ""),
                resolved_at=r.resolved_at,
                floor_qty=s.get("floor", 0.0),
                bwhse_qty=s.get("bwhse", 0.0),
            )
        )
    return items


def _load(db: Session, statuses: tuple[str, ...], mine: int | None = None) -> list[FloorRequest]:
    q = (
        select(FloorRequest)
        .options(selectinload(FloorRequest.product))
        .where(FloorRequest.status.in_(statuses))
        .order_by(FloorRequest.created_at.desc())
        .execution_options(populate_existing=True)
    )
    if mine is not None:
        q = q.where(FloorRequest.requested_by_id == mine)
    return list(db.scalars(q).all())


@router.post("", response_model=list[FloorRequestOut], status_code=201)
def raise_requests(
    body: RequestIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*ASKERS)),
) -> list[FloorRequestOut]:
    seen: set[int] = set()
    touched: list[FloorRequest] = []
    for line in body.lines:
        if line.product_id in seen:
            raise HTTPException(422, "The same product appears twice — merge the quantities.")
        seen.add(line.product_id)
        product = db.get(Product, line.product_id)
        if product is None or not product.is_active:
            raise HTTPException(422, f"Product {line.product_id} not found or inactive.")

        # one row per ask, always: the manager should see that two different
        # people flagged this, not a single quantity that lost its authors
        row = FloorRequest(
            product_id=product.id,
            qty=float(line.qty),
            note=body.note.strip(),
            requested_by_id=authed.id,
        )
        db.add(row)
        touched.append(row)
    db.commit()
    for row in touched:
        db.refresh(row)
    return _out(db, touched)


@router.get("", response_model=list[FloorRequestOut])
def list_requests(
    status: str = FloorRequestStatus.OPEN.value,
    mine: bool = False,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*ASKERS)),
) -> list[FloorRequestOut]:
    """Open asks by default. `mine=true` is the Floor Team's own board — what
    they asked for and whether it's been picked up yet."""
    wanted = tuple(s.strip() for s in status.split(",") if s.strip()) or (
        FloorRequestStatus.OPEN.value,
    )
    return _out(db, _load(db, wanted, mine=authed.id if mine else None))


@router.post("/{request_id}/{action}", response_model=FloorRequestOut)
def resolve_request(
    request_id: int,
    action: str,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> FloorRequestOut:
    """picked-up = it's going on a transfer; dismissed = looked at, not needed.
    Either way the floor sees what happened to their ask."""
    outcomes = {
        "picked-up": FloorRequestStatus.PICKED_UP.value,
        "dismiss": FloorRequestStatus.DISMISSED.value,
        "reopen": FloorRequestStatus.OPEN.value,
    }
    if action not in outcomes:
        raise HTTPException(404, "No such action.")
    row = db.get(FloorRequest, request_id)
    if row is None:
        raise HTTPException(404, "Request not found.")
    row.status = outcomes[action]
    reopened = row.status == FloorRequestStatus.OPEN.value
    row.resolved_by_id = None if reopened else authed.id
    row.resolved_at = None if reopened else utcnow()
    db.commit()
    db.refresh(row)
    return _out(db, [row])[0]
