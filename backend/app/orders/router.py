"""Order lists: admin curates, the zone coordinator approves, the approval
renders ONE draft internal transfer in Odoo (BWHSE → the center's location)
through the OdooWriter. The write outcome — created / simulated / failed —
is stored on the list and shown in the UI without varnish.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    Center,
    OrderList,
    OrderListLine,
    OrderListStatus,
    Product,
    Role,
    StockLevel,
    User,
    Zone,
    utcnow,
)
from .service import approve_order_list

router = APIRouter(prefix="/order-lists", tags=["order-lists"])

EDITABLE_STATES = {OrderListStatus.DRAFT.value, OrderListStatus.RETURNED.value}


# ------------------------------------------------------------------ schemas
class LineIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)


class LineOut(BaseModel):
    id: int
    product_id: int
    sku: str
    name: str
    category: str
    qty: float
    bwhse_qty: float


class OrderListOut(BaseModel):
    id: int
    name: str
    notes: str
    status: str
    zone_id: int | None
    zone_name: str
    center_id: int | None
    center_name: str
    center_mapped: bool  # center has an Odoo location -> live write possible
    created_by: str
    created_at: datetime
    updated_at: datetime
    assigned_at: datetime | None
    approved_by: str
    approved_at: datetime | None
    returned_note: str
    cloned_from_id: int | None
    write_status: str
    write_reference: str
    write_dry_run_reason: str
    write_error: str
    odoo_picking_id: int | None
    odoo_picking_name: str
    odoo_url: str
    lines: list[LineOut]
    total_qty: float


class OrderListSummaryOut(BaseModel):
    id: int
    name: str
    status: str
    zone_name: str
    center_name: str
    center_mapped: bool
    line_count: int
    total_qty: float
    write_status: str
    updated_at: datetime


# ------------------------------------------------------------------ helpers
def _load(db: Session, order_list_id: int) -> OrderList:
    ol = db.scalar(
        select(OrderList)
        .options(selectinload(OrderList.lines).selectinload(OrderListLine.product))
        .where(OrderList.id == order_list_id)
        .execution_options(populate_existing=True)
    )
    if ol is None:
        raise HTTPException(404, "Order list not found.")
    return ol


def _coordinator_zones(authed: AuthedUser) -> set[int]:
    return authed.scoped_zone_ids


def _may_view(ol: OrderList, authed: AuthedUser) -> bool:
    if authed.has_role(Role.ADMIN):
        return True
    if ol.status == OrderListStatus.DRAFT.value:
        return False  # drafts are the admin's desk
    return bool(ol.zone_id and ol.zone_id in _coordinator_zones(authed))


def _require_coordinator_scope(ol: OrderList, authed: AuthedUser) -> None:
    if authed.has_role(Role.ADMIN):
        return
    if not (ol.zone_id and ol.zone_id in _coordinator_zones(authed)):
        raise HTTPException(403, "This list belongs to another zone.")


def _names(db: Session, ols: list[OrderList]) -> dict:
    zone_ids = {ol.zone_id for ol in ols if ol.zone_id}
    center_ids = {ol.center_id for ol in ols if ol.center_id}
    user_ids = {ol.created_by_id for ol in ols} | {ol.approved_by_id for ol in ols}
    zones = (
        {z.id: z.name for z in db.scalars(select(Zone).where(Zone.id.in_(zone_ids)))}
        if zone_ids
        else {}
    )
    centers = (
        {c.id: c for c in db.scalars(select(Center).where(Center.id.in_(center_ids)))}
        if center_ids
        else {}
    )
    users = (
        {
            u.id: (u.display_name or u.email or f"user {u.id}")
            for u in db.scalars(select(User).where(User.id.in_({i for i in user_ids if i})))
        }
        if any(user_ids)
        else {}
    )
    return {"zones": zones, "centers": centers, "users": users}


def _out(db: Session, ol: OrderList) -> OrderListOut:
    names = _names(db, [ol])
    center = names["centers"].get(ol.center_id)
    stock = {}
    pids = {line.product_id for line in ol.lines}
    if pids:
        stock = {
            pid: float(qty)
            for pid, key, qty in db.execute(
                select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
                    StockLevel.product_id.in_(pids)
                )
            )
            if key == "bwhse"
        }
    lines = [
        LineOut(
            id=line.id,
            product_id=line.product_id,
            sku=line.product.global_sku,
            name=line.product.name,
            category=line.product.category,
            qty=line.qty,
            bwhse_qty=stock.get(line.product_id, 0.0),
        )
        for line in ol.lines
    ]
    return OrderListOut(
        id=ol.id,
        name=ol.name,
        notes=ol.notes,
        status=ol.status,
        zone_id=ol.zone_id,
        zone_name=names["zones"].get(ol.zone_id, ""),
        center_id=ol.center_id,
        center_name=center.name if center else "",
        center_mapped=bool(center and center.odoo_location_id),
        created_by=names["users"].get(ol.created_by_id, ""),
        created_at=ol.created_at,
        updated_at=ol.updated_at,
        assigned_at=ol.assigned_at,
        approved_by=names["users"].get(ol.approved_by_id, ""),
        approved_at=ol.approved_at,
        returned_note=ol.returned_note,
        cloned_from_id=ol.cloned_from_id,
        write_status=ol.write_status,
        write_reference=ol.write_reference,
        write_dry_run_reason=ol.write_dry_run_reason,
        write_error=ol.write_error,
        odoo_picking_id=ol.odoo_picking_id,
        odoo_picking_name=ol.odoo_picking_name,
        odoo_url=ol.odoo_url,
        lines=lines,
        total_qty=sum(line.qty for line in ol.lines),
    )


def _set_lines(db: Session, ol: OrderList, lines: list[LineIn]) -> None:
    seen: set[int] = set()
    validated: list[tuple[Product, float]] = []
    for line in lines:
        if line.product_id in seen:
            raise HTTPException(422, "The same product appears twice — merge the quantities.")
        seen.add(line.product_id)
        product = db.get(Product, line.product_id)
        if product is None or not product.is_active:
            raise HTTPException(422, f"Product {line.product_id} not found or inactive.")
        if not product.is_stock_tracked or not product.odoo_product_id:
            raise HTTPException(
                422,
                f"'{product.name}' isn't tracked in Odoo — it can't go on a draft transfer.",
            )
        validated.append((product, float(line.qty)))
    for old in list(ol.lines):
        db.delete(old)
    db.flush()
    for position, (product, qty) in enumerate(validated):
        db.add(
            OrderListLine(
                order_list_id=ol.id, product_id=product.id, qty=qty, position=position
            )
        )


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=list[OrderListSummaryOut])
def list_order_lists(
    status: str = "",
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> list[OrderListSummaryOut]:
    if not (authed.has_role(Role.ADMIN) or _coordinator_zones(authed)):
        raise HTTPException(403, "You don't have access to order lists.")
    q = (
        select(OrderList)
        .options(selectinload(OrderList.lines))
        .order_by(OrderList.updated_at.desc())
        .execution_options(populate_existing=True)
    )
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        q = q.where(OrderList.status.in_(wanted))
    if not authed.has_role(Role.ADMIN):
        q = q.where(
            OrderList.zone_id.in_(_coordinator_zones(authed)),
            OrderList.status != OrderListStatus.DRAFT.value,
        )
    ols = db.scalars(q).all()
    names = _names(db, list(ols))
    out = []
    for ol in ols:
        center = names["centers"].get(ol.center_id)
        out.append(
            OrderListSummaryOut(
                id=ol.id,
                name=ol.name,
                status=ol.status,
                zone_name=names["zones"].get(ol.zone_id, ""),
                center_name=center.name if center else "",
                center_mapped=bool(center and center.odoo_location_id),
                line_count=len(ol.lines),
                total_qty=sum(line.qty for line in ol.lines),
                write_status=ol.write_status,
                updated_at=ol.updated_at,
            )
        )
    return out


class CreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    notes: str = ""


@router.post("", response_model=OrderListOut, status_code=201)
def create_order_list(
    body: CreateIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    ol = OrderList(name=body.name.strip(), notes=body.notes.strip(), created_by_id=authed.id)
    db.add(ol)
    db.commit()
    return _out(db, _load(db, ol.id))


@router.get("/{order_list_id}", response_model=OrderListOut)
def get_order_list(
    order_list_id: int,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderListOut:
    ol = _load(db, order_list_id)
    if not _may_view(ol, authed):
        raise HTTPException(403, "You don't have access to this list.")
    return _out(db, ol)


class PatchIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=160)
    notes: str | None = None


@router.patch("/{order_list_id}", response_model=OrderListOut)
def patch_order_list(
    order_list_id: int,
    body: PatchIn,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    ol = _load(db, order_list_id)
    if ol.status not in EDITABLE_STATES:
        raise HTTPException(409, f"A {ol.status} list can't be edited.")
    if body.name is not None:
        ol.name = body.name.strip()
    if body.notes is not None:
        ol.notes = body.notes.strip()
    db.commit()
    return _out(db, _load(db, ol.id))


class LinesIn(BaseModel):
    lines: list[LineIn]


@router.put("/{order_list_id}/lines", response_model=OrderListOut)
def put_lines(
    order_list_id: int,
    body: LinesIn,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    ol = _load(db, order_list_id)
    if ol.status not in EDITABLE_STATES:
        raise HTTPException(409, f"A {ol.status} list can't be edited.")
    _set_lines(db, ol, body.lines)
    db.commit()
    return _out(db, _load(db, ol.id))


@router.delete("/{order_list_id}", status_code=204)
def delete_order_list(
    order_list_id: int,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> None:
    ol = _load(db, order_list_id)
    if ol.status not in EDITABLE_STATES:
        raise HTTPException(
            409, f"A {ol.status} list can't be deleted — it's part of the paper trail."
        )
    db.delete(ol)
    db.commit()


@router.post("/{order_list_id}/clone", response_model=OrderListOut, status_code=201)
def clone_order_list(
    order_list_id: int,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    src = _load(db, order_list_id)
    clone = OrderList(
        name=f"Copy of {src.name}"[:160],
        notes=src.notes,
        zone_id=src.zone_id,
        center_id=src.center_id,
        created_by_id=authed.id,
        cloned_from_id=src.id,
    )
    db.add(clone)
    db.flush()
    for line in src.lines:
        db.add(
            OrderListLine(
                order_list_id=clone.id,
                product_id=line.product_id,
                qty=line.qty,
                position=line.position,
            )
        )
    db.commit()
    return _out(db, _load(db, clone.id))


class AssignIn(BaseModel):
    zone_id: int
    center_id: int


@router.post("/{order_list_id}/assign", response_model=OrderListOut)
def assign_order_list(
    order_list_id: int,
    body: AssignIn,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    """Hand the list to a zone's coordinator for approval, aimed at one of
    that zone's centers."""
    ol = _load(db, order_list_id)
    if ol.status not in EDITABLE_STATES:
        raise HTTPException(409, f"A {ol.status} list can't be (re)assigned.")
    if not ol.lines:
        raise HTTPException(422, "Add lines before assigning — an empty list isn't approvable.")
    zone = db.get(Zone, body.zone_id)
    if zone is None:
        raise HTTPException(422, "Zone not found.")
    center = db.get(Center, body.center_id)
    if center is None or center.zone_id != zone.id:
        raise HTTPException(422, "Pick a destination center that belongs to the chosen zone.")
    ol.zone_id = zone.id
    ol.center_id = center.id
    ol.status = OrderListStatus.PENDING_APPROVAL.value
    ol.assigned_at = utcnow()
    ol.returned_note = ""
    db.commit()
    return _out(db, _load(db, ol.id))


class ReturnIn(BaseModel):
    note: str = Field(min_length=3, max_length=2000)


@router.post("/{order_list_id}/return", response_model=OrderListOut)
def return_order_list(
    order_list_id: int,
    body: ReturnIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ZONE_COORDINATOR, Role.DEPT_LIAISON)),
) -> OrderListOut:
    """Coordinator sends the list back to the office with a note."""
    ol = _load(db, order_list_id)
    if ol.status != OrderListStatus.PENDING_APPROVAL.value:
        raise HTTPException(409, f"A {ol.status} list can't be returned.")
    _require_coordinator_scope(ol, authed)
    ol.status = OrderListStatus.RETURNED.value
    ol.returned_note = body.note.strip()
    db.commit()
    return _out(db, _load(db, ol.id))


class ApproveIn(BaseModel):
    dry_run: bool = False


@router.post("/{order_list_id}/approve", response_model=OrderListOut)
def approve(
    order_list_id: int,
    body: ApproveIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.ZONE_COORDINATOR, Role.DEPT_LIAISON)),
) -> OrderListOut:
    """Coordinator approval → the draft transfer write (or an honest
    simulated/failed outcome). Retries reuse the same reference, so approving
    twice can never create two Odoo drafts."""
    ol = _load(db, order_list_id)
    if ol.status != OrderListStatus.PENDING_APPROVAL.value:
        raise HTTPException(409, f"Only a pending list can be approved (this one is {ol.status}).")
    _require_coordinator_scope(ol, authed)
    approve_order_list(db, settings, authed, ol, dry_run=body.dry_run)
    return _out(db, _load(db, ol.id))
