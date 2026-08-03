"""City-center & department ordering API.

Orderers get a checkout: context (my centers) → catalog (the menu, with
honest availability) → preview (rules-only reasonability while composing) →
place. Coordinators get a POS-style board: pending orders to approve/adjust/
reject; approval renders the draft Odoo transfer and the orderer is notified
over WhatsApp (email fallback). The list/detail GETs are also the SHIPPED
listener — they poll the approval picking politely, like the phase-2 board.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles, visible_center_ids
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    Center,
    CenterOrder,
    CenterOrderEvent,
    CenterOrderEventKind,
    CenterOrderLine,
    CenterOrderStatus,
    NotificationKind,
    Product,
    Role,
    User,
    Zone,
    utcnow,
)
from ..notify import service as notify
from . import service
from .catalog import (
    availability_for,
    build_catalog,
    incoming_by_product,
    orderable_product_ids,
    source_location_key,
    stock_by_product,
)
from .flow import InvalidTransition, NotAllowedError, check_transition
from .reasonability import assess_order

S = CenterOrderStatus
PARTICIPANTS = (
    Role.CENTER_ORDERER,
    Role.DEPT_ORDERER,
    Role.ZONE_COORDINATOR,
    Role.DEPT_LIAISON,
)

router = APIRouter(
    prefix="/center-orders",
    tags=["center-orders"],
    dependencies=[Depends(require_roles(*PARTICIPANTS))],
)


# ------------------------------------------------------------------ schemas
class CenterRefOut(BaseModel):
    id: int
    name: str
    zone_name: str
    zone_kind: str  # field | departments — drives "center" vs "department" copy


class ContextCenterOut(BaseModel):
    id: int
    name: str
    zone_name: str
    zone_kind: str
    item_count: int  # 0 = no catalog granted yet — the form says so honestly


class AvailabilityOut(BaseModel):
    status: str  # in | low | out | untracked
    qty: float | None
    low_count_caveat: bool
    incoming_qty: float
    incoming_expected: str | None
    incoming_label: str


class CatalogItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str = ""
    name: str
    category: str
    retail_price: float
    case_size: int
    untracked: bool
    from_lists: list[str]
    availability: AvailabilityOut


class CatalogOut(BaseModel):
    center: CenterRefOut
    source_key: str  # bwhse | floor
    items: list[CatalogItemOut]


class BadgeOut(BaseModel):
    code: str
    level: str
    text: str


class LineIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)


class PlaceOrderIn(BaseModel):
    center_id: int
    notes: str = ""
    duplicate_of_id: int | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class PreviewIn(BaseModel):
    center_id: int
    lines: list[LineIn] = Field(min_length=1, max_length=200)


class LineOut(BaseModel):
    id: int
    product_id: int
    sku: str
    barcode: str = ""
    name: str
    category: str
    qty_requested: float
    qty_approved: float | None
    qty_final: float
    unit_price: float
    untracked: bool
    badges: list[BadgeOut]
    availability: AvailabilityOut | None  # live stock at the source (tracked items)


class EventOut(BaseModel):
    id: int
    kind: str
    status: str
    note: str
    actor: str
    created_at: datetime


class ActionsOut(BaseModel):
    can_approve: bool = False
    can_reject: bool = False
    can_adjust: bool = False
    can_cancel: bool = False


class OdooRefOut(BaseModel):
    status: str  # none | created | simulated | failed
    reference: str
    error: str
    picking_id: int | None
    picking_name: str
    url: str


class TotalsOut(BaseModel):
    items: int
    units: float
    value: float


class OrderOut(BaseModel):
    id: int
    display_name: str
    status: str
    notes: str
    center: CenterRefOut
    created_by: str
    created_at: datetime
    updated_at: datetime
    decided_by: str
    decided_at: datetime | None
    decision_note: str
    duplicate_of_id: int | None
    source_location_key: str
    reasonability: dict
    reasonability_level: str
    placement: OdooRefOut
    totals: TotalsOut
    lines: list[LineOut]
    events: list[EventOut]
    actions: ActionsOut


class OrderSummaryOut(BaseModel):
    id: int
    display_name: str
    status: str
    center_id: int
    center_name: str
    zone_name: str
    created_by: str
    created_at: datetime
    decided_at: datetime | None
    line_count: int
    total_units: float
    total_value: float
    reasonability_level: str
    picking_status: str
    odoo_picking_name: str


class PreviewOut(BaseModel):
    level: str
    summary: str
    source: str
    order_badges: list[BadgeOut]
    lines: dict[str, list[BadgeOut]]  # product_id (str) -> badges


# ------------------------------------------------------------------ helpers
def _center_ref(db: Session, center: Center) -> CenterRefOut:
    zone = db.get(Zone, center.zone_id) if center.zone_id else None
    return CenterRefOut(
        id=center.id,
        name=center.name,
        zone_name=zone.name if zone else "",
        zone_kind=zone.kind if zone else "field",
    )


def _get_center(db: Session, center_id: int) -> Center:
    center = db.get(Center, center_id)
    if center is None or not center.is_active:
        raise HTTPException(404, "Center not found (or inactive).")
    return center


def _may_order_for(db: Session, authed: AuthedUser, center: Center) -> bool:
    if authed.has_role(Role.ADMIN):
        return True
    if center.id in authed.scoped_center_ids:
        return True
    return center.zone_id is not None and center.zone_id in authed.scoped_zone_ids


def _is_coordinator_of(authed: AuthedUser, center: Center) -> bool:
    if authed.has_role(Role.ADMIN):
        return True
    return (
        authed.has_role(Role.ZONE_COORDINATOR, Role.DEPT_LIAISON)
        and center.zone_id is not None
        and center.zone_id in authed.scoped_zone_ids
    )


def _get_order(db: Session, order_id: int) -> CenterOrder:
    order = db.scalar(
        select(CenterOrder)
        .options(
            selectinload(CenterOrder.lines).selectinload(CenterOrderLine.product),
            selectinload(CenterOrder.events),
        )
        .where(CenterOrder.id == order_id)
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise HTTPException(404, "Order not found.")
    return order


def _require_view(db: Session, authed: AuthedUser, order: CenterOrder) -> Center:
    center = db.get(Center, order.center_id)
    if center is None:
        raise HTTPException(404, "Order's center no longer exists.")
    allowed = visible_center_ids(db, authed)
    if allowed is not None and center.id not in allowed:
        raise HTTPException(403, "This order belongs to another center.")
    return center


def _user_names(db: Session, ids: set[int | None]) -> dict[int, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(select(User).where(User.id.in_(real)))
    }


def _check(current: str, to: str, authed: AuthedUser) -> None:
    try:
        check_transition(current, to, authed.role_names)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    except NotAllowedError as e:
        raise HTTPException(403, str(e)) from e


def _event(
    db: Session,
    order: CenterOrder,
    kind: CenterOrderEventKind,
    actor: AuthedUser | None,
    status: str = "",
    note: str = "",
) -> None:
    db.add(
        CenterOrderEvent(
            order_id=order.id,
            kind=kind.value,
            status=status,
            note=note,
            actor_user_id=actor.id if actor else None,
        )
    )


def _actions(authed: AuthedUser, order: CenterOrder, center: Center) -> ActionsOut:
    pending = order.status == S.PENDING.value
    coordinator = _is_coordinator_of(authed, center)
    orderer_here = center.id in authed.scoped_center_ids
    return ActionsOut(
        can_approve=pending and coordinator,
        can_reject=pending and coordinator,
        can_adjust=pending and coordinator,
        can_cancel=pending and (coordinator or orderer_here),
    )


def _badges_from(stored: dict, product_id: int) -> list[BadgeOut]:
    raw = (stored or {}).get("lines", {}).get(str(product_id), [])
    return [BadgeOut(**b) for b in raw if isinstance(b, dict)]


def _order_out(
    db: Session, settings: Settings, order: CenterOrder, authed: AuthedUser
) -> OrderOut:
    center = db.get(Center, order.center_id)
    pids = {line.product_id for line in order.lines}
    stock = stock_by_product(db, pids, order.source_location_key or "bwhse")
    incoming = incoming_by_product(db, pids)
    today = utcnow().date()
    names = _user_names(
        db,
        {order.created_by_id, order.decided_by_id}
        | {e.actor_user_id for e in order.events},
    )
    lines: list[LineOut] = []
    for line in order.lines:
        p = line.product
        tracked = bool(p.is_stock_tracked and p.odoo_product_id)
        availability = None
        if tracked:
            availability = AvailabilityOut(
                **availability_for(
                    product=p,
                    on_hand=stock.get(p.id),
                    incoming=incoming.get(p.id, []),
                    low_threshold=settings.catalog_low_stock_threshold,
                    today=today,
                ).as_dict()
            )
        lines.append(
            LineOut(
                id=line.id,
                product_id=line.product_id,
                sku=p.global_sku,
                barcode=p.barcode or "",
                name=p.name,
                category=p.category,
                qty_requested=line.qty_requested,
                qty_approved=line.qty_approved,
                qty_final=line.qty_final,
                unit_price=float(line.unit_price or 0),
                untracked=not tracked,
                badges=_badges_from(order.reasonability, line.product_id),
                availability=availability,
            )
        )
    events = [
        EventOut(
            id=e.id,
            kind=e.kind,
            status=e.status,
            note=e.note,
            actor=names.get(e.actor_user_id or 0, "system"),
            created_at=e.created_at,
        )
        for e in order.events
    ]
    units = sum(line.qty_final for line in order.lines)
    value = sum(line.qty_final * float(line.unit_price or 0) for line in order.lines)
    return OrderOut(
        id=order.id,
        display_name=order.display_name,
        status=order.status,
        notes=order.notes,
        center=_center_ref(db, center) if center else CenterRefOut(
            id=order.center_id, name=f"center {order.center_id}", zone_name="", zone_kind="field"
        ),
        created_by=names.get(order.created_by_id or 0, "unknown"),
        created_at=order.created_at,
        updated_at=order.updated_at,
        decided_by=names.get(order.decided_by_id or 0, ""),
        decided_at=order.decided_at,
        decision_note=order.decision_note,
        duplicate_of_id=order.duplicate_of_id,
        source_location_key=order.source_location_key,
        reasonability=order.reasonability or {},
        reasonability_level=order.reasonability_level,
        placement=OdooRefOut(
            status=order.picking_status,
            reference=order.picking_reference,
            error=order.picking_error,
            picking_id=order.odoo_picking_id,
            picking_name=order.odoo_picking_name,
            url=order.odoo_picking_url,
        ),
        totals=TotalsOut(items=len(order.lines), units=units, value=round(value, 2)),
        lines=lines,
        events=events,
        actions=_actions(authed, order, center) if center else ActionsOut(),
    )


def _validate_order_lines(
    db: Session, center: Center, lines: list[LineIn]
) -> list[tuple[Product, float]]:
    """Placement guard: every product must be on the center's granted menu."""
    allowed = orderable_product_ids(db, center)
    if not allowed:
        raise HTTPException(
            422,
            "This center has no catalog granted yet — ask your coordinator to "
            "share an order list with it.",
        )
    seen: set[int] = set()
    out: list[tuple[Product, float]] = []
    for line in lines:
        if line.product_id in seen:
            raise HTTPException(422, "The same product appears twice — merge the quantities.")
        seen.add(line.product_id)
        if line.product_id not in allowed:
            raise HTTPException(
                422, f"Product {line.product_id} isn't on this center's catalog."
            )
        product = db.get(Product, line.product_id)
        if product is None or not product.is_active:
            raise HTTPException(422, f"Product {line.product_id} not found or inactive.")
        out.append((product, float(line.qty)))
    return out


# ---------------------------------------------------------------- endpoints
@router.get("/context", response_model=list[ContextCenterOut])
def order_context(
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> list[ContextCenterOut]:
    """The centers this user may place orders for (usually exactly one)."""
    allowed = visible_center_ids(db, authed)
    q = select(Center).where(Center.is_active.is_(True)).order_by(Center.name)
    if allowed is not None:
        q = q.where(Center.id.in_(allowed or {-1}))
    centers = db.scalars(q).all()
    out = []
    for c in centers:
        zone = db.get(Zone, c.zone_id) if c.zone_id else None
        out.append(
            ContextCenterOut(
                id=c.id,
                name=c.name,
                zone_name=zone.name if zone else "",
                zone_kind=zone.kind if zone else "field",
                item_count=len(orderable_product_ids(db, c)),
            )
        )
    return out


@router.get("/catalog", response_model=CatalogOut)
def order_catalog(
    center_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> CatalogOut:
    center = _get_center(db, center_id)
    if not _may_order_for(db, authed, center):
        raise HTTPException(403, "You can't order for this center.")
    source_key, items = build_catalog(db, settings, center, utcnow().date())
    return CatalogOut(
        center=_center_ref(db, center),
        source_key=source_key,
        items=[
            CatalogItemOut(
                product_id=it.product.id,
                sku=it.product.global_sku,
                barcode=it.product.barcode or "",
                name=it.product.name,
                category=it.product.category,
                retail_price=float(it.product.retail_price or 0),
                case_size=it.product.case_size or 1,
                untracked=it.availability.status == "untracked",
                from_lists=it.from_lists,
                availability=AvailabilityOut(**it.availability.as_dict()),
            )
            for it in items
        ],
    )


@router.post("/preview", response_model=PreviewOut)
def reasonability_preview(
    body: PreviewIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> PreviewOut:
    """Rules-only assessment while the order is being composed (debounced by
    the UI). The full rules+LLM pass runs once, at placement."""
    center = _get_center(db, body.center_id)
    if not _may_order_for(db, authed, center):
        raise HTTPException(403, "You can't order for this center.")
    validated = _validate_order_lines(db, center, body.lines)
    zone = db.get(Zone, center.zone_id) if center.zone_id else None
    source_key = source_location_key(zone.kind if zone else None)
    a = assess_order(db, settings, center, validated, source_key, use_llm=False)
    d = a.as_dict()
    return PreviewOut(
        level=d["level"],
        summary=d["summary"],
        source=d["source"],
        order_badges=[BadgeOut(**b) for b in d["order_badges"]],
        lines={pid: [BadgeOut(**b) for b in badges] for pid, badges in d["lines"].items()},
    )


@router.post("", response_model=OrderOut, status_code=201)
def place_order(
    body: PlaceOrderIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    """Place the order: PENDING, reasonability computed and stored, the zone's
    coordinator(s) pinged over WhatsApp. Nothing touches Odoo until approval."""
    center = _get_center(db, body.center_id)
    if not _may_order_for(db, authed, center):
        raise HTTPException(403, "You can't order for this center.")
    validated = _validate_order_lines(db, center, body.lines)
    zone = db.get(Zone, center.zone_id) if center.zone_id else None
    source_key = source_location_key(zone.kind if zone else None)

    duplicate_of = None
    if body.duplicate_of_id is not None:
        duplicate_of = db.get(CenterOrder, body.duplicate_of_id)
        if duplicate_of is None or duplicate_of.center_id != center.id:
            duplicate_of = None  # a stale prefill shouldn't block the order

    order = CenterOrder(
        center_id=center.id,
        notes=body.notes.strip(),
        created_by_id=authed.id,
        duplicate_of_id=duplicate_of.id if duplicate_of else None,
        source_location_key=source_key,
    )
    db.add(order)
    db.flush()
    for product, qty in validated:
        db.add(
            CenterOrderLine(
                order_id=order.id,
                product_id=product.id,
                qty_requested=qty,
                unit_price=float(product.retail_price or 0),
            )
        )
    db.flush()
    db.refresh(order)

    assessment = assess_order(db, settings, center, validated, source_key, use_llm=True)
    order.reasonability = assessment.as_dict()
    order.reasonability_level = assessment.level

    _event(
        db, order, CenterOrderEventKind.STATUS, authed,
        status=S.PENDING.value,
        note=f"{len(validated)} item(s) requested"
        + (" (duplicate of an earlier order)" if order.duplicate_of_id else ""),
    )
    if assessment.level in ("info", "warn"):
        _event(
            db, order, CenterOrderEventKind.REASONABILITY, None,
            note=assessment.summary,
        )
    pinged = notify.enqueue_order_notifications(
        db, settings, order, NotificationKind.ORDER_PLACED
    )
    db.commit()
    notify.deliver_now(db, settings, pinged)
    return _order_out(db, settings, _get_order(db, order.id), authed)


@router.get("", response_model=list[OrderSummaryOut])
def list_orders(
    status: str = "",
    center_id: int | None = None,
    mine: bool = False,
    limit: int = 200,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> list[OrderSummaryOut]:
    allowed = visible_center_ids(db, authed)
    q = (
        select(CenterOrder)
        .options(selectinload(CenterOrder.lines))
        .order_by(CenterOrder.id.desc())
        .limit(max(1, min(limit, 500)))
        .execution_options(populate_existing=True)
    )
    if allowed is not None:
        q = q.where(CenterOrder.center_id.in_(allowed or {-1}))
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        q = q.where(CenterOrder.status.in_(wanted))
    if center_id is not None:
        q = q.where(CenterOrder.center_id == center_id)
    if mine:
        q = q.where(CenterOrder.created_by_id == authed.id)
    orders = db.scalars(q).all()

    # the board is the shipped-listener: check a few approved orders per read
    polled = 0
    for order in orders:
        if order.status == S.APPROVED.value and polled < 3:
            polled += 1
            if service.poll_shipped(db, settings, order):
                db.refresh(order)

    centers = {
        c.id: c
        for c in db.scalars(
            select(Center).where(Center.id.in_({o.center_id for o in orders} or {-1}))
        )
    }
    zones = {z.id: z.name for z in db.scalars(select(Zone))}
    names = _user_names(db, {o.created_by_id for o in orders})
    out = []
    for o in orders:
        center = centers.get(o.center_id)
        units = sum(line.qty_final for line in o.lines)
        value = sum(line.qty_final * float(line.unit_price or 0) for line in o.lines)
        out.append(
            OrderSummaryOut(
                id=o.id,
                display_name=o.display_name,
                status=o.status,
                center_id=o.center_id,
                center_name=center.name if center else f"center {o.center_id}",
                zone_name=(zones.get(center.zone_id) or "") if center and center.zone_id else "",
                created_by=names.get(o.created_by_id or 0, "unknown"),
                created_at=o.created_at,
                decided_at=o.decided_at,
                line_count=len(o.lines),
                total_units=units,
                total_value=round(value, 2),
                reasonability_level=o.reasonability_level,
                picking_status=o.picking_status,
                odoo_picking_name=o.odoo_picking_name,
            )
        )
    return out


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    order = _get_order(db, order_id)
    _require_view(db, authed, order)
    if service.poll_shipped(db, settings, order):
        order = _get_order(db, order_id)
    return _order_out(db, settings, order, authed)


class AdjustLineIn(BaseModel):
    product_id: int
    qty: float = Field(ge=0, le=100000)  # 0 = leave the line off the transfer


class ApproveIn(BaseModel):
    note: str = ""
    lines: list[AdjustLineIn] | None = None  # adjust-and-approve in one motion


def _apply_adjustments(
    db: Session, order: CenterOrder, adjustments: list[AdjustLineIn], authed: AuthedUser
) -> int:
    by_product = {line.product_id: line for line in order.lines}
    unknown = [a.product_id for a in adjustments if a.product_id not in by_product]
    if unknown:
        raise HTTPException(
            422,
            f"Product(s) {sorted(unknown)} aren't on this order — adjustments "
            "change quantities, not the item list.",
        )
    changed = 0
    for adj in adjustments:
        line = by_product[adj.product_id]
        if float(adj.qty) != line.qty_final:
            line.qty_approved = float(adj.qty)
            changed += 1
    if changed:
        _event(
            db, order, CenterOrderEventKind.LINES_EDITED, authed,
            note=f"{changed} quantity(ies) adjusted by the coordinator",
        )
    return changed


def _recompute_reasonability(
    db: Session, settings: Settings, order: CenterOrder, center: Center
) -> None:
    """Rules-only refresh after adjustments (the LLM ran once, at placement)."""
    pairs = [(line.product, line.qty_final) for line in order.lines if line.qty_final > 0]
    if not pairs:
        return
    a = assess_order(
        db, settings, center, pairs, order.source_location_key or "bwhse", use_llm=False
    )
    order.reasonability = a.as_dict()
    order.reasonability_level = a.level


@router.put("/{order_id}/lines", response_model=OrderOut)
def adjust_lines(
    order_id: int,
    body: list[AdjustLineIn],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    """Coordinator quantity adjustments while the order is still pending."""
    order = _get_order(db, order_id)
    center = _require_view(db, authed, order)
    if not _is_coordinator_of(authed, center):
        raise HTTPException(403, "Only the zone's coordinator can adjust an order.")
    if order.status != S.PENDING.value:
        raise HTTPException(409, "Quantities can only change while the order is pending.")
    if _apply_adjustments(db, order, body, authed):
        _recompute_reasonability(db, settings, order, center)
    db.commit()
    return _order_out(db, settings, _get_order(db, order.id), authed)


@router.post("/{order_id}/approve", response_model=OrderOut)
def approve_order(
    order_id: int,
    body: ApproveIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    """Approve (optionally adjusting quantities in the same motion): renders
    the draft Odoo transfer and notifies the orderer."""
    order = _get_order(db, order_id)
    center = _require_view(db, authed, order)
    if not _is_coordinator_of(authed, center):
        raise HTTPException(403, "Only the zone's coordinator can approve an order.")
    _check(order.status, S.APPROVED.value, authed)
    if body.lines:
        if _apply_adjustments(db, order, body.lines, authed):
            _recompute_reasonability(db, settings, order, center)
    if all(line.qty_final <= 0 for line in order.lines):
        raise HTTPException(422, "Every line is zero — reject the order instead.")

    order.status = S.APPROVED.value
    order.decided_by_id = authed.id
    order.decided_at = utcnow()
    order.decision_note = body.note.strip()
    _event(
        db, order, CenterOrderEventKind.STATUS, authed,
        status=order.status, note=body.note.strip() or "approved",
    )
    service.render_approval_draft(db, settings, order, authed.id)
    pinged = notify.enqueue_order_notifications(
        db, settings, order, NotificationKind.ORDER_APPROVED, note=body.note.strip()
    )
    db.commit()
    notify.deliver_now(db, settings, pinged)
    return _order_out(db, settings, _get_order(db, order.id), authed)


class NoteIn(BaseModel):
    note: str = ""


@router.post("/{order_id}/reject", response_model=OrderOut)
def reject_order(
    order_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    order = _get_order(db, order_id)
    center = _require_view(db, authed, order)
    if not _is_coordinator_of(authed, center):
        raise HTTPException(403, "Only the zone's coordinator can reject an order.")
    _check(order.status, S.REJECTED.value, authed)
    note = body.note.strip()
    if not note:
        raise HTTPException(422, "Give the orderer a reason — the note goes to them.")
    order.status = S.REJECTED.value
    order.decided_by_id = authed.id
    order.decided_at = utcnow()
    order.decision_note = note
    _event(db, order, CenterOrderEventKind.STATUS, authed, status=order.status, note=note)
    pinged = notify.enqueue_order_notifications(
        db, settings, order, NotificationKind.ORDER_REJECTED, note=note
    )
    db.commit()
    notify.deliver_now(db, settings, pinged)
    return _order_out(db, settings, _get_order(db, order.id), authed)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    """Withdraw a pending order — the orderer's own centers only (or the
    coordinator/admin)."""
    order = _get_order(db, order_id)
    center = _require_view(db, authed, order)
    if not (_is_coordinator_of(authed, center) or center.id in authed.scoped_center_ids):
        raise HTTPException(403, "You can only withdraw your own center's orders.")
    _check(order.status, S.CANCELLED.value, authed)
    order.status = S.CANCELLED.value
    _event(
        db, order, CenterOrderEventKind.STATUS, authed,
        status=order.status, note=body.note.strip() or "withdrawn",
    )
    db.commit()
    return _order_out(db, settings, _get_order(db, order.id), authed)


@router.post("/{order_id}/note", response_model=OrderOut)
def add_note(
    order_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> OrderOut:
    if not body.note.strip():
        raise HTTPException(422, "The note is empty.")
    order = _get_order(db, order_id)
    _require_view(db, authed, order)
    _event(db, order, CenterOrderEventKind.NOTE, authed, note=body.note.strip())
    db.commit()
    return _order_out(db, settings, _get_order(db, order.id), authed)
