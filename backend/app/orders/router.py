"""Order lists are CATALOGS, not orders: curated sets of currently-active
products that people order FROM (the phase-3 city-center order form draws
its choices from these).

  * Admin curates lists (no quantities) and grants them to zones.
  * A zone's coordinator decides which of those lists each of their centers
    can order from.

No approvals, no Odoo writes — approving actual center ORDERS is the
coordinator's phase-3 job.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles
from ..db import get_db
from ..models import (
    Center,
    OrderList,
    OrderListCenter,
    OrderListLine,
    OrderListZone,
    Product,
    Role,
    StockLevel,
    User,
    Zone,
)

router = APIRouter(prefix="/order-lists", tags=["order-lists"])

COORDINATOR_ROLES = (Role.ZONE_COORDINATOR, Role.DEPT_LIAISON)


# ------------------------------------------------------------------ schemas
class LineOut(BaseModel):
    id: int
    product_id: int
    sku: str
    name: str
    category: str
    is_active: bool  # stale items surface so admins can prune them
    retail_price: float
    bwhse_qty: float


class ZoneGrantOut(BaseModel):
    zone_id: int
    zone_name: str


class CenterGrantOut(BaseModel):
    center_id: int
    center_name: str
    zone_id: int | None


class OrderListOut(BaseModel):
    id: int
    name: str
    notes: str
    is_archived: bool
    created_by: str
    created_at: datetime
    updated_at: datetime
    cloned_from_id: int | None
    lines: list[LineOut]
    zones: list[ZoneGrantOut]
    centers: list[CenterGrantOut]
    stale_line_count: int  # inactive products still on the list


class OrderListSummaryOut(BaseModel):
    id: int
    name: str
    is_archived: bool
    line_count: int
    stale_line_count: int
    zone_names: list[str]
    center_count: int
    updated_at: datetime


# ------------------------------------------------------------------ helpers
def _load(db: Session, order_list_id: int) -> OrderList:
    ol = db.scalar(
        select(OrderList)
        .options(
            selectinload(OrderList.lines).selectinload(OrderListLine.product),
            selectinload(OrderList.zone_grants),
            selectinload(OrderList.center_grants),
        )
        .where(OrderList.id == order_list_id)
        .execution_options(populate_existing=True)
    )
    if ol is None:
        raise HTTPException(404, "Order list not found.")
    return ol


def _granted_zone_ids(ol: OrderList) -> set[int]:
    return {g.zone_id for g in ol.zone_grants}


def _require_view(ol: OrderList, authed: AuthedUser) -> None:
    if authed.has_role(Role.ADMIN):
        return
    if not (_granted_zone_ids(ol) & authed.scoped_zone_ids):
        raise HTTPException(403, "This list isn't granted to your zone.")


def _out(db: Session, ol: OrderList) -> OrderListOut:
    pids = {line.product_id for line in ol.lines}
    stock: dict[int, float] = {}
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
    zone_names = {
        z.id: z.name
        for z in db.scalars(
            select(Zone).where(Zone.id.in_(_granted_zone_ids(ol) or {-1}))
        )
    }
    center_ids = {g.center_id for g in ol.center_grants}
    centers = (
        {
            c.id: c
            for c in db.scalars(select(Center).where(Center.id.in_(center_ids)))
        }
        if center_ids
        else {}
    )
    creator = db.get(User, ol.created_by_id) if ol.created_by_id else None
    lines = [
        LineOut(
            id=line.id,
            product_id=line.product_id,
            sku=line.product.global_sku,
            name=line.product.name,
            category=line.product.category,
            is_active=line.product.is_active,
            retail_price=float(line.product.retail_price or 0),
            bwhse_qty=stock.get(line.product_id, 0.0),
        )
        for line in ol.lines
    ]
    return OrderListOut(
        id=ol.id,
        name=ol.name,
        notes=ol.notes,
        is_archived=ol.is_archived,
        created_by=(creator.display_name or creator.email or "") if creator else "",
        created_at=ol.created_at,
        updated_at=ol.updated_at,
        cloned_from_id=ol.cloned_from_id,
        lines=lines,
        zones=[
            ZoneGrantOut(zone_id=zid, zone_name=zone_names.get(zid, f"zone {zid}"))
            for zid in sorted(_granted_zone_ids(ol))
        ],
        centers=[
            CenterGrantOut(
                center_id=cid,
                center_name=centers[cid].name if cid in centers else f"center {cid}",
                zone_id=centers[cid].zone_id if cid in centers else None,
            )
            for cid in sorted(center_ids)
        ],
        stale_line_count=sum(1 for line in lines if not line.is_active),
    )


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=list[OrderListSummaryOut])
def list_order_lists(
    include_archived: bool = False,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> list[OrderListSummaryOut]:
    """Admin: every list. Coordinator: lists granted to their zone(s)."""
    if not (authed.has_role(Role.ADMIN) or authed.scoped_zone_ids):
        raise HTTPException(403, "You don't have access to order lists.")
    q = (
        select(OrderList)
        .options(
            selectinload(OrderList.lines).selectinload(OrderListLine.product),
            selectinload(OrderList.zone_grants),
            selectinload(OrderList.center_grants),
        )
        .order_by(OrderList.updated_at.desc())
        .execution_options(populate_existing=True)
    )
    if not include_archived:
        q = q.where(OrderList.is_archived.is_(False))
    ols = db.scalars(q).all()
    if not authed.has_role(Role.ADMIN):
        mine = authed.scoped_zone_ids
        ols = [ol for ol in ols if _granted_zone_ids(ol) & mine]

    zone_names = {z.id: z.name for z in db.scalars(select(Zone))}
    return [
        OrderListSummaryOut(
            id=ol.id,
            name=ol.name,
            is_archived=ol.is_archived,
            line_count=len(ol.lines),
            stale_line_count=sum(1 for line in ol.lines if not line.product.is_active),
            zone_names=[
                zone_names.get(zid, f"zone {zid}") for zid in sorted(_granted_zone_ids(ol))
            ],
            center_count=len(ol.center_grants),
            updated_at=ol.updated_at,
        )
        for ol in ols
    ]


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
    _require_view(ol, authed)
    return _out(db, ol)


class PatchIn(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=160)
    notes: str | None = None
    is_archived: bool | None = None


@router.patch("/{order_list_id}", response_model=OrderListOut)
def patch_order_list(
    order_list_id: int,
    body: PatchIn,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    ol = _load(db, order_list_id)
    if body.name is not None:
        ol.name = body.name.strip()
    if body.notes is not None:
        ol.notes = body.notes.strip()
    if body.is_archived is not None:
        ol.is_archived = body.is_archived
    db.commit()
    return _out(db, _load(db, ol.id))


class LinesIn(BaseModel):
    product_ids: list[int]


@router.put("/{order_list_id}/lines", response_model=OrderListOut)
def put_lines(
    order_list_id: int,
    body: LinesIn,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    """Replace the list's products (order preserved — it's a menu, so no
    quantities). Only Odoo-tracked, active products belong on one."""
    ol = _load(db, order_list_id)
    seen: set[int] = set()
    validated: list[Product] = []
    for pid in body.product_ids:
        if pid in seen:
            raise HTTPException(422, "The same product appears twice.")
        seen.add(pid)
        product = db.get(Product, pid)
        if product is None:
            raise HTTPException(422, f"Product {pid} not found.")
        if not product.is_stock_tracked or not product.odoo_product_id:
            raise HTTPException(
                422, f"'{product.name}' isn't tracked in Odoo — it can't be ordered."
            )
        if not product.is_active:
            raise HTTPException(
                422, f"'{product.name}' is inactive — lists only carry live products."
            )
        validated.append(product)
    for old in list(ol.lines):
        db.delete(old)
    db.flush()
    for position, product in enumerate(validated):
        db.add(OrderListLine(order_list_id=ol.id, product_id=product.id, position=position))
    db.commit()
    return _out(db, _load(db, ol.id))


@router.delete("/{order_list_id}", status_code=204)
def delete_order_list(
    order_list_id: int,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> None:
    ol = _load(db, order_list_id)
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
        created_by_id=authed.id,
        cloned_from_id=src.id,
    )
    db.add(clone)
    db.flush()
    for line in src.lines:
        db.add(
            OrderListLine(
                order_list_id=clone.id, product_id=line.product_id, position=line.position
            )
        )
    db.commit()
    return _out(db, _load(db, clone.id))


class ZonesIn(BaseModel):
    zone_ids: list[int]


@router.put("/{order_list_id}/zones", response_model=OrderListOut)
def set_zone_grants(
    order_list_id: int,
    body: ZonesIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> OrderListOut:
    """Admin: which zones' coordinators may use this list. Revoking a zone
    also revokes its centers' grants — coordinators only re-grant what they
    still hold."""
    ol = _load(db, order_list_id)
    wanted = set(body.zone_ids)
    known = {z.id for z in db.scalars(select(Zone).where(Zone.id.in_(wanted or {-1})))}
    missing = wanted - known
    if missing:
        raise HTTPException(422, f"Unknown zone ids: {sorted(missing)}.")
    for grant in list(ol.zone_grants):
        if grant.zone_id not in wanted:
            db.delete(grant)
    existing = _granted_zone_ids(ol)
    for zone_id in wanted - existing:
        db.add(OrderListZone(order_list_id=ol.id, zone_id=zone_id, granted_by_id=authed.id))
    db.flush()
    # cascade: center grants outside the granted zones die with the zone grant
    if ol.center_grants:
        center_zone = {
            c.id: c.zone_id
            for c in db.scalars(
                select(Center).where(Center.id.in_({g.center_id for g in ol.center_grants}))
            )
        }
        for cgrant in list(ol.center_grants):
            if center_zone.get(cgrant.center_id) not in wanted:
                db.delete(cgrant)
    db.commit()
    return _out(db, _load(db, ol.id))


class CentersIn(BaseModel):
    center_ids: list[int]


@router.put("/{order_list_id}/centers", response_model=OrderListOut)
def set_center_grants(
    order_list_id: int,
    body: CentersIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*COORDINATOR_ROLES)),
) -> OrderListOut:
    """Coordinator: which of MY centers order from this list. Admin may set
    any center (within the list's granted zones). Coordinators only touch
    their own zones' centers; other zones' grants are left alone."""
    ol = _load(db, order_list_id)
    _require_view(ol, authed)
    granted_zones = _granted_zone_ids(ol)
    editable_zones = (
        granted_zones
        if authed.has_role(Role.ADMIN)
        else granted_zones & authed.scoped_zone_ids
    )
    if not editable_zones:
        raise HTTPException(403, "This list isn't granted to your zone.")

    wanted = set(body.center_ids)
    centers = {
        c.id: c for c in db.scalars(select(Center).where(Center.id.in_(wanted or {-1})))
    }
    for cid in wanted:
        center = centers.get(cid)
        if center is None:
            raise HTTPException(422, f"Center {cid} not found.")
        if center.zone_id not in editable_zones:
            raise HTTPException(
                422,
                f"'{center.name}' isn't in a zone this list is granted to "
                "(or isn't your zone).",
            )
    for grant in list(ol.center_grants):
        grant_zone = db.get(Center, grant.center_id)
        zone_id = grant_zone.zone_id if grant_zone else None
        if zone_id in editable_zones and grant.center_id not in wanted:
            db.delete(grant)
    existing = {g.center_id for g in ol.center_grants}
    for cid in wanted - existing:
        db.add(OrderListCenter(order_list_id=ol.id, center_id=cid, granted_by_id=authed.id))
    db.commit()
    return _out(db, _load(db, ol.id))
