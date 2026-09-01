"""BWHSE→Floor transfer requests and the delivery form.

Odoo-native flow (reworked 2026-08-17 — the warehouse lives in Odoo):

  1. floor places a request     → BWHSE→Staging2 draft rendered in Odoo
  2. warehouse touches it there → "seen by warehouse" (any write counts)
  3. they pull it, splitting it however they like, into III/Staging2
  4. they make ONE staging2 → floor-staging transfer in Odoo
  5. they fill the DELIVERY FORM here: which transfer, which requests are in
     it, and why any quantity differs by more than a few units
  6. the pallet validates → every request on it closes as done against it,
     and ONE count transfer is prepared for the whole pallet

The UI polls these endpoints (POS-board style); reads are cheap and the
Odoo checks are throttled per request and per delivery.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    OdooWriteOutcome,
    PalletRequestLink,
    PalletTransfer,
    Product,
    Role,
    StagingInboundMove,
    StockLevel,
    TransferEvent,
    TransferEventKind,
    TransferRequest,
    TransferRequestLine,
    TransferRequestStatus,
    User,
    not_blacklisted,
)
from . import delivery as delivery_service
from . import pallet as pallet_service
from . import service
from .flow import ACTIVE_STATUSES, InvalidTransition, NotAllowedError, check_transition

# floor_rotating participates in the flow (viewing, counting, closing) but
# cannot create requests or edit lines — those endpoints stay SHOPPE_FLOOR.
PARTICIPANTS = (Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE)
S = TransferRequestStatus

router = APIRouter(
    prefix="/transfer-requests",
    tags=["transfers"],
    dependencies=[Depends(require_roles(*PARTICIPANTS))],
)

# ------------------------------------------------------------------ schemas
class LineIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)


class CreateRequestIn(BaseModel):
    notes: str = ""
    # Bounded like the identical shape in center_orders/router.py: every line
    # becomes a move on ONE Odoo picking, and each is validated with its own
    # product lookup before anything is written.
    lines: list[LineIn] = Field(min_length=1, max_length=500)


class LineOut(BaseModel):
    id: int
    product_id: int
    sku: str
    barcode: str = ""
    name: str
    category: str
    qty_requested: float
    qty_sent: float | None
    qty_counted: float | None
    delta: float | None  # counted - sent, once done
    floor_qty: float
    bwhse_qty: float
    # the warehouse's own words, from the delivery form, when this line
    # didn't come in full — "a clear note that it wasn't included"
    reasons: list[str] = []
    reason_labels: list[str] = []
    reason_note: str = ""


class DeliveryRefOut(BaseModel):
    """The received transfer a request rode to the floor."""

    id: int
    status: str  # open | validated | counting | counted | cancelled
    picking_name: str
    picking_url: str
    declared_at: datetime | None
    validated_at: datetime | None
    request_count: int  # how many requests shared it


class EventOut(BaseModel):
    id: int
    kind: str
    status: str
    note: str
    actor: str
    created_at: datetime


class ActionsOut(BaseModel):
    can_edit_lines: bool = False
    can_ack: bool = False
    can_mark_sent: bool = False
    can_prepare_count: bool = False
    can_mark_done: bool = False
    can_cancel: bool = False


class OdooRefOut(BaseModel):
    """One picking's honest linkage: outcome + name + links."""

    status: str  # none | created | simulated | failed
    reference: str
    error: str
    picking_id: int | None
    picking_name: str
    url: str
    barcode_url: str = ""


class RequestSummaryOut(BaseModel):
    id: int
    display_name: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    line_count: int
    total_requested: float
    picking_status: str
    count_status: str
    delivery: DeliveryRefOut | None = None


class RequestOut(BaseModel):
    id: int
    display_name: str
    status: str
    notes: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    placement: OdooRefOut
    count: OdooRefOut
    lines: list[LineOut]
    events: list[EventOut]
    actions: ActionsOut
    delivery: DeliveryRefOut | None = None


# ------------------------------------------------------------------ helpers
def _get_request(db: Session, request_id: int) -> TransferRequest:
    # populate_existing: the session keeps objects across commits
    # (expire_on_commit=False), so re-reads must overwrite stale collections
    req = db.scalar(
        select(TransferRequest)
        .options(
            selectinload(TransferRequest.lines).selectinload(TransferRequestLine.product),
            selectinload(TransferRequest.events),
        )
        .where(TransferRequest.id == request_id)
        .execution_options(populate_existing=True)
    )
    if req is None:
        raise HTTPException(404, "Transfer request not found.")
    return req


def _stock_map(db: Session, product_ids: set[int]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    if product_ids:
        for pid, key, qty in db.execute(
            select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
                StockLevel.product_id.in_(product_ids)
            )
        ):
            out.setdefault(pid, {})[key] = float(qty)
    return out


def _user_names(db: Session, ids: set[int | None]) -> dict[int, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(select(User).where(User.id.in_(real)))
    }


def _deliveries_for(db: Session, request_ids: set[int]) -> dict[int, PalletTransfer]:
    """request id -> the delivery it rides, in ONE query. A request rides at
    most one (declare() refuses to double-book), so the newest link wins if
    history ever leaves two behind."""
    if not request_ids:
        return {}
    rows = db.execute(
        select(PalletRequestLink.request_id, PalletTransfer)
        .join(PalletTransfer, PalletTransfer.id == PalletRequestLink.pallet_id)
        .where(PalletRequestLink.request_id.in_(request_ids))
        .order_by(PalletRequestLink.id)
    ).all()
    return dict(rows)  # type: ignore[arg-type]


def _delivery_ref(db: Session, pallet: PalletTransfer | None) -> DeliveryRefOut | None:
    if pallet is None:
        return None
    count = db.scalar(
        select(func.count())
        .select_from(PalletRequestLink)
        .where(PalletRequestLink.pallet_id == pallet.id)
    )
    return DeliveryRefOut(
        id=pallet.id,
        status=pallet.status,
        picking_name=pallet.display_name,
        picking_url=pallet.odoo_picking_url,
        declared_at=pallet.declared_at,
        validated_at=pallet.validated_at,
        request_count=int(count or 0),
    )


def _reasons_by_product(db: Session, pallet: PalletTransfer | None) -> dict[int, tuple]:
    if pallet is None:
        return {}
    return {
        d.product_id: (list(d.reasons or []), d.note) for d in pallet.discrepancies
    }


def _may(current: str, to: str, authed: AuthedUser) -> bool:
    try:
        check_transition(current, to, authed.role_names)
        return True
    except (InvalidTransition, NotAllowedError):
        return False


def _actions(
    req: TransferRequest, authed: AuthedUser, on_delivery: bool = False
) -> ActionsOut:
    is_floor = authed.has_role(Role.SHOPPE_FLOOR, Role.ADMIN)
    count_live = req.count_status == OdooWriteOutcome.CREATED.value
    return ActionsOut(
        can_edit_lines=(
            req.status == S.REQUESTED.value
            and req.picking_status != OdooWriteOutcome.CREATED.value
            and is_floor
        ),
        can_ack=_may(req.status, S.WORKING.value, authed),
        can_mark_sent=_may(req.status, S.SENT.value, authed),
        can_prepare_count=(
            (
                req.status == S.SENT.value
                or (req.status == S.COUNTING.value and req.count_status == "failed")
            )
            # on a delivery, the PALLET's count is the count — offering a
            # per-request one here would move the same units twice
            and not on_delivery
            and authed.has_role(Role.WAREHOUSE, Role.SHOPPE_FLOOR, Role.ADMIN)
        ),
        can_mark_done=(
            req.status in (S.SENT.value, S.COUNTING.value)
            and not count_live
            and not on_delivery  # its delivery closes it when the pallet lands
            and is_floor
        ),
        can_cancel=_may(req.status, S.CANCELLED.value, authed),
    )


def _request_out(db: Session, settings: Settings, req: TransferRequest, authed: AuthedUser) -> RequestOut:
    pids = {line.product_id for line in req.lines}
    stock = _stock_map(db, pids)
    names = _user_names(db, {req.created_by_id} | {e.actor_user_id for e in req.events})
    pallet = _deliveries_for(db, {req.id}).get(req.id)
    reasons = _reasons_by_product(db, pallet)
    lines = []
    for line in req.lines:
        p = line.product
        s = stock.get(line.product_id, {})
        delta = None
        if line.qty_counted is not None:
            delta = round(float(line.qty_counted) - float(line.qty_sent or 0), 3)
        codes, note = reasons.get(line.product_id, ([], ""))
        lines.append(
            LineOut(
                id=line.id,
                product_id=line.product_id,
                sku=p.global_sku,
                barcode=p.barcode or "",
                name=p.name,
                category=p.category,
                qty_requested=line.qty_requested,
                qty_sent=line.qty_sent,
                qty_counted=line.qty_counted,
                delta=delta,
                floor_qty=s.get("floor", 0.0),
                bwhse_qty=s.get("bwhse", 0.0),
                reasons=codes,
                reason_labels=[
                    delivery_service.REASON_LABELS[c]
                    for c in codes
                    if c in delivery_service.REASON_LABELS
                ],
                reason_note=note,
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
        for e in req.events
    ]
    return RequestOut(
        id=req.id,
        display_name=req.display_name,
        status=req.status,
        notes=req.notes,
        created_by=names.get(req.created_by_id or 0, "unknown"),
        created_at=req.created_at,
        updated_at=req.updated_at,
        placement=OdooRefOut(
            status=req.picking_status,
            reference=req.picking_reference,
            error=req.picking_error,
            picking_id=req.odoo_picking_id,
            picking_name=req.odoo_picking_name,
            url=req.odoo_picking_url,
        ),
        count=OdooRefOut(
            status=req.count_status,
            reference=req.count_reference,
            error=req.count_error,
            picking_id=req.count_picking_id,
            picking_name=req.count_picking_name,
            url=req.count_picking_url,
            barcode_url=req.count_barcode_url,
        ),
        lines=lines,
        events=events,
        actions=_actions(req, authed, on_delivery=pallet is not None),
        delivery=_delivery_ref(db, pallet),
    )


def _validate_lines(db: Session, lines: list[LineIn]) -> list[tuple[Product, float]]:
    if not lines:
        raise HTTPException(422, "A request needs at least one line.")
    seen: set[int] = set()
    out: list[tuple[Product, float]] = []
    for line in lines:
        if line.product_id in seen:
            raise HTTPException(422, "The same product appears twice — merge the quantities.")
        seen.add(line.product_id)
        product = db.get(Product, line.product_id)
        if product is None or not product.is_active:
            raise HTTPException(422, f"Product {line.product_id} not found or inactive.")
        if not product.is_stock_tracked or not product.odoo_product_id:
            raise HTTPException(
                422, f"'{product.name}' isn't tracked in Odoo — it can't go on a transfer."
            )
        out.append((product, float(line.qty)))
    return out


def _event(
    db: Session,
    req: TransferRequest,
    kind: TransferEventKind,
    actor: AuthedUser | None,
    status: str = "",
    note: str = "",
) -> None:
    db.add(
        TransferEvent(
            request_id=req.id,
            kind=kind.value,
            status=status,
            note=note,
            actor_user_id=actor.id if actor else None,
        )
    )


def _check(current: str, to: str, authed: AuthedUser) -> None:
    try:
        check_transition(current, to, authed.role_names)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    except NotAllowedError as e:
        raise HTTPException(403, str(e)) from e


# ---------------------------------------------------------------- endpoints
@router.post("", response_model=RequestOut, status_code=201)
def create_request(
    body: CreateRequestIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> RequestOut:
    """Place the request AND render its Odoo draft in one go — the draft is
    the order; the app page is its live tracker."""
    validated = _validate_lines(db, body.lines)
    req = TransferRequest(notes=body.notes.strip(), created_by_id=authed.id)
    db.add(req)
    db.flush()
    for product, qty in validated:
        db.add(
            TransferRequestLine(request_id=req.id, product_id=product.id, qty_requested=qty)
        )
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=S.REQUESTED.value, note=f"{len(validated)} item(s) requested",
    )
    db.flush()
    db.refresh(req)
    service.render_placement_draft(db, settings, req, authed.id)
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


@router.get("", response_model=list[RequestSummaryOut])
def list_requests(
    status: str = "",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> list[RequestSummaryOut]:
    q = (
        select(TransferRequest)
        .options(selectinload(TransferRequest.lines))
        .order_by(TransferRequest.id.desc())
        .execution_options(populate_existing=True)
    )
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        q = q.where(TransferRequest.status.in_(wanted))
    requests = db.scalars(q).all()

    # the board is the listener — a few polls per refresh, both directions:
    # counting requests wait on the count validation; requested/working ones
    # follow whatever the warehouse does to the picking in Odoo; open pallets
    # flip waiting SENT requests to counting when they land
    polled = 0
    for req in requests:
        if polled >= 8:
            break
        if req.status in (S.COUNTING.value, S.SENT.value):
            polled += 1
            # One call, both closers — they share a throttle stamp, so calling
            # them separately let the first one starve the second (see
            # service.poll_close_out).
            service.poll_close_out(db, settings, req)
        elif req.status in (S.REQUESTED.value, S.WORKING.value):
            polled += 1
            service.poll_outbound_status(db, settings, req)
    # deliveries: land the declared ones, discover undeclared ones, and watch
    # for the pallet count being scanned onto the floor
    pallet_service.poll_pallets(db, settings)
    pallet_service.poll_manual_pallets(db, settings)
    delivery_service.poll_delivery_counts(db, settings)

    names = _user_names(db, {r.created_by_id for r in requests})
    deliveries = _deliveries_for(db, {r.id for r in requests})

    return [
        RequestSummaryOut(
            id=r.id,
            display_name=r.display_name,
            status=r.status,
            created_by=names.get(r.created_by_id or 0, "unknown"),
            created_at=r.created_at,
            updated_at=r.updated_at,
            line_count=len(r.lines),
            total_requested=sum(line.qty_requested for line in r.lines),
            picking_status=r.picking_status,
            count_status=r.count_status,
            delivery=_delivery_ref(db, deliveries.get(r.id)),
        )
        for r in requests
    ]


class ComingSoonRequestRef(BaseModel):
    id: int
    display_name: str
    status: str
    qty: float


class ComingSoonPickingRef(BaseModel):
    """A transfer someone made DIRECTLY in Odoo (drafts included)."""

    picking_name: str
    state: str
    qty: float
    expected_date: str | None


class ComingSoonItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str  # the identifier the team actually uses; sku is the fallback
    name: str
    category: str
    qty_on_the_way: float  # sent qty where known, requested otherwise
    floor_qty: float
    bwhse_qty: float
    requests: list[ComingSoonRequestRef]
    odoo_pickings: list[ComingSoonPickingRef] = []


# NOTE: declared before /{request_id} — the int route would otherwise
# swallow "coming-soon" and 422.
@router.get("/coming-soon", response_model=list[ComingSoonItemOut])
def coming_soon(
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> list[ComingSoonItemOut]:
    """Everything on its way to the floor, aggregated per product — items on
    ACTIVE app requests plus staging-bound transfers made directly in Odoo
    (the transfers sync discovers those, drafts included) — so nobody
    requests something twice."""
    requests = db.scalars(
        select(TransferRequest)
        .options(selectinload(TransferRequest.lines).selectinload(TransferRequestLine.product))
        .where(TransferRequest.status.in_(ACTIVE_STATUSES))
        .order_by(TransferRequest.id)
        .execution_options(populate_existing=True)
    ).all()

    by_product: dict[int, dict] = {}

    def entry_for(product: Product) -> dict:
        return by_product.setdefault(
            product.id,
            {"product": product, "qty": 0.0, "requests": [], "odoo_pickings": []},
        )

    for req in requests:
        for line in req.lines:
            qty = float(line.qty_sent) if line.qty_sent is not None else float(line.qty_requested)
            if qty <= 0:
                continue
            entry = entry_for(line.product)
            entry["qty"] += qty
            entry["requests"].append(
                ComingSoonRequestRef(
                    id=req.id, display_name=req.display_name, status=req.status, qty=qty
                )
            )

    # Odoo-native staging-bound pickings (discovered by the transfers sync)
    native_rows = db.execute(
        select(StagingInboundMove, Product)
        .join(Product, Product.id == StagingInboundMove.product_id)
        .where(Product.is_active.is_(True), not_blacklisted())
        .order_by(StagingInboundMove.picking_name)
    ).all()
    for move, product in native_rows:
        entry = entry_for(product)
        entry["qty"] += move.qty
        entry["odoo_pickings"].append(
            ComingSoonPickingRef(
                picking_name=move.picking_name,
                state=move.picking_state,
                qty=move.qty,
                expected_date=(
                    move.expected_date.isoformat() if move.expected_date else None
                ),
            )
        )

    stock = _stock_map(db, set(by_product.keys()))
    items = [
        ComingSoonItemOut(
            product_id=pid,
            sku=e["product"].global_sku,
            barcode=e["product"].barcode,
            name=e["product"].name,
            category=e["product"].category,
            qty_on_the_way=round(e["qty"], 3),
            floor_qty=stock.get(pid, {}).get("floor", 0.0),
            bwhse_qty=stock.get(pid, {}).get("bwhse", 0.0),
            requests=e["requests"],
            odoo_pickings=e["odoo_pickings"],
        )
        for pid, e in by_product.items()
    ]
    items.sort(key=lambda i: (i.category or "~", i.name))
    return items


class Staging2ItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str
    name: str
    qty: float


class PalletOut(BaseModel):
    id: int
    status: str  # open | validated | cancelled
    picking_status: str  # none | created | simulated | failed
    picking_name: str
    picking_url: str
    picking_error: str
    line_count: int
    total_units: float
    created_at: datetime
    validated_at: datetime | None


class Staging2Out(BaseModel):
    items: list[Staging2ItemOut]
    total_units: float
    source: str  # live | snapshot | unmapped
    note: str
    pallets: list[PalletOut]  # recent, newest first


def _pallet_out(p) -> PalletOut:
    lines = p.lines or []
    return PalletOut(
        id=p.id,
        status=p.status,
        picking_status=p.picking_status,
        picking_name=p.odoo_picking_name,
        picking_url=p.odoo_picking_url,
        picking_error=p.picking_error,
        line_count=len(lines),
        total_units=round(sum(float(ln.get("qty") or 0) for ln in lines), 3),
        created_at=p.created_at,
        validated_at=p.validated_at,
    )


def _staging2_out(db: Session, settings: Settings) -> Staging2Out:
    snapshot = pallet_service.staging2_snapshot(db, settings)
    from ..models import PalletTransfer  # local: keep the top import block stable

    pallets = db.scalars(
        select(PalletTransfer).order_by(PalletTransfer.id.desc()).limit(10)
    ).all()
    return Staging2Out(
        items=[
            Staging2ItemOut(
                product_id=i.product_id, sku=i.sku, barcode=i.barcode, name=i.name, qty=i.qty
            )
            for i in snapshot.items
        ],
        total_units=round(snapshot.total_units, 3),
        source=snapshot.source,
        note=snapshot.note,
        pallets=[_pallet_out(p) for p in pallets],
    )


# NOTE: declared before /{request_id} — route order matters.
@router.get("/staging2", response_model=Staging2Out)
def staging2_view(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> Staging2Out:
    """What's sitting in III/Staging2 (the warehouse's consolidation point)
    right now, plus recent pallets. Live read when Odoo is reachable; this
    GET is also the pallet-validation listener."""
    pallet_service.poll_pallets(db, settings)
    # ...and pallets the warehouse built themselves, straight in Odoo
    pallet_service.poll_manual_pallets(db, settings)
    return _staging2_out(db, settings)


@router.post("/staging2/send-all", response_model=Staging2Out)
def staging2_send_all(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.WAREHOUSE)),
) -> Staging2Out:
    """The big button: ONE draft pallet transfer moving everything in
    staging2 to III-FLORR-STAGING. Draft only — validate it in Odoo (the
    app prepares the floor's count transfers when it lands)."""
    try:
        pallet_service.create_pallet(db, settings, authed.id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _staging2_out(db, settings)


# ------------------------------------------------------------ delivery form
# "The warehouse must fill out a form that details the transfer being sent"
# (Noah, 2026-08-17). Three questions; the app suggests, the human decides.
# All declared BEFORE /{request_id} — route order matters.
class DeliveryRequestOut(BaseModel):
    id: int
    display_name: str
    status: str
    created_by: str
    line_count: int


class DeliveryDiscrepancyOut(BaseModel):
    product_id: int
    sku: str
    barcode: str
    name: str
    qty_requested: float
    qty_sent: float
    delta: float
    reasons: list[str]
    reason_labels: list[str]
    note: str


class DeliveryItemOut(BaseModel):
    product_id: int
    sku: str
    name: str
    qty: float


class DeliveryOut(BaseModel):
    id: int
    status: str
    picking_status: str
    # the Odoo picking itself — the form needs it to re-open an undeclared one
    odoo_picking_id: int | None
    picking_name: str
    picking_url: str
    picking_error: str
    item_count: int
    total_units: float
    created_at: datetime
    validated_at: datetime | None
    declared_at: datetime | None
    declared_by: str
    note: str
    needs_details: bool
    requests: list[DeliveryRequestOut]
    discrepancies: list[DeliveryDiscrepancyOut]
    items: list[DeliveryItemOut]
    count: OdooRefOut


class CandidateOut(BaseModel):
    odoo_picking_id: int
    name: str
    state: str
    date: str
    item_count: int
    total_units: float
    already_declared: bool
    declared_pallet_id: int | None
    from_staging2: bool


class CandidatesOut(BaseModel):
    candidates: list[CandidateOut]
    note: str


class SuggestionOut(BaseModel):
    request_id: int
    display_name: str
    status: str
    created_by: str
    created_at: datetime
    line_count: int
    matched_items: int
    total_requested: float
    reason: str
    suggested: bool
    auto_select: bool


class ReviewRowOut(BaseModel):
    product_id: int
    sku: str
    barcode: str
    name: str
    qty_requested: float
    qty_sent: float
    delta: float
    requested_by: list[str]
    reasons: list[str]
    note: str


class ExtraRowOut(BaseModel):
    product_id: int
    sku: str
    barcode: str
    name: str
    qty_sent: float


class PreviewOut(BaseModel):
    picking: CandidateOut | None
    suggestions: list[SuggestionOut]
    review: list[ReviewRowOut]
    extras: list[ExtraRowOut]
    threshold: float
    reason_options: list[dict[str, str]]
    note: str


class PreviewIn(BaseModel):
    odoo_picking_id: int = Field(gt=0)
    request_ids: list[int] = Field(default_factory=list, max_length=200)


class ReasonIn(BaseModel):
    product_id: int
    reasons: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=1000)


class DeclareIn(BaseModel):
    odoo_picking_id: int = Field(gt=0)
    request_ids: list[int] = Field(min_length=1, max_length=200)
    reasons: list[ReasonIn] = Field(default_factory=list, max_length=500)
    note: str = Field(default="", max_length=2000)


REASON_OPTIONS = [
    {"value": value, "label": label} for value, label in delivery_service.REASON_LABELS.items()
]


def _delivery_out(db: Session, pallet: PalletTransfer) -> DeliveryOut:
    names = _user_names(db, {pallet.declared_by_id, pallet.created_by_id})
    lines = pallet.lines or []
    return DeliveryOut(
        id=pallet.id,
        status=pallet.status,
        picking_status=pallet.picking_status,
        odoo_picking_id=pallet.odoo_picking_id,
        picking_name=pallet.display_name,
        picking_url=pallet.odoo_picking_url,
        picking_error=pallet.picking_error,
        item_count=len(lines),
        total_units=round(sum(float(ln.get("qty") or 0) for ln in lines), 3),
        created_at=pallet.created_at,
        validated_at=pallet.validated_at,
        declared_at=pallet.declared_at,
        declared_by=names.get(pallet.declared_by_id or 0, ""),
        note=pallet.note,
        # a validated pallet nobody has explained: it moved real stock and the
        # app can't close a single request until someone says whose it was
        needs_details=not pallet.is_declared and pallet.status != "cancelled",
        requests=[
            DeliveryRequestOut(
                id=link.request.id,
                display_name=link.request.display_name,
                status=link.request.status,
                created_by=_user_names(db, {link.request.created_by_id}).get(
                    link.request.created_by_id or 0, "unknown"
                ),
                line_count=len(link.request.lines),
            )
            for link in pallet.request_links
        ],
        discrepancies=[
            DeliveryDiscrepancyOut(
                product_id=d.product_id,
                sku=d.product.global_sku,
                barcode=d.product.barcode or "",
                name=d.product.name,
                qty_requested=d.qty_requested,
                qty_sent=d.qty_sent,
                delta=round(d.qty_sent - d.qty_requested, 3),
                reasons=list(d.reasons or []),
                reason_labels=[
                    delivery_service.REASON_LABELS[c]
                    for c in (d.reasons or [])
                    if c in delivery_service.REASON_LABELS
                ],
                note=d.note,
            )
            for d in pallet.discrepancies
        ],
        items=[
            DeliveryItemOut(
                product_id=int(ln.get("product_id") or 0),
                sku=str(ln.get("sku") or ""),
                name=str(ln.get("name") or ""),
                qty=float(ln.get("qty") or 0),
            )
            for ln in lines
        ],
        count=OdooRefOut(
            status=pallet.count_status,
            reference=pallet.count_reference,
            error=pallet.count_error,
            picking_id=pallet.count_picking_id,
            picking_name=pallet.count_picking_name,
            url=pallet.count_picking_url,
            barcode_url=pallet.count_barcode_url,
        ),
    )


def _delivery_query():
    # populate_existing for the same reason _get_request needs it: the session
    # keeps objects across commits, so a re-read must overwrite collections
    # that changed underneath (links and discrepancies are rewritten on every
    # form submission)
    return (
        select(PalletTransfer)
        .options(
            selectinload(PalletTransfer.request_links)
            .selectinload(PalletRequestLink.request)
            .selectinload(TransferRequest.lines),
            selectinload(PalletTransfer.discrepancies),
        )
        .execution_options(populate_existing=True)
    )


def _load_deliveries(db: Session, limit: int = 15) -> list[PalletTransfer]:
    return list(
        db.scalars(_delivery_query().order_by(PalletTransfer.id.desc()).limit(limit)).all()
    )


def _load_delivery(db: Session, pallet_id: int) -> PalletTransfer:
    pallet = db.scalar(_delivery_query().where(PalletTransfer.id == pallet_id))
    if pallet is None:
        raise HTTPException(404, "Delivery not found.")
    return pallet


@router.get("/deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> list[DeliveryOut]:
    """Recent pallets to the floor, newest first — and, like every board GET
    here, the listener for their validation and their count."""
    pallet_service.poll_pallets(db, settings)
    pallet_service.poll_manual_pallets(db, settings)
    delivery_service.poll_delivery_counts(db, settings)
    return [_delivery_out(db, p) for p in _load_deliveries(db)]


@router.get("/deliveries/candidates", response_model=CandidatesOut)
def delivery_candidates(
    search: str = "",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(Role.WAREHOUSE)),
) -> CandidatesOut:
    """Question 1: "please select the transfer you're sending". Recent
    staging2 → floor-staging transfers; `search` is the "Don't see it?" path
    and matches a picking name anywhere in Odoo."""
    candidates, note = delivery_service.candidate_pickings(db, settings, search[:80])
    return CandidatesOut(
        candidates=[CandidateOut(**vars(c)) for c in candidates], note=note
    )


@router.post("/deliveries/preview", response_model=PreviewOut)
def delivery_preview(
    body: PreviewIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(Role.WAREHOUSE)),
) -> PreviewOut:
    """Questions 2 and 3 for one selected transfer: which requests the app
    thinks are in it, and which items need a reason given that selection.
    Recomputed as the selection changes — the threshold lives here so every
    client asks the same questions."""
    try:
        preview = delivery_service.preview(
            db,
            settings,
            odoo_picking_id=body.odoo_picking_id,
            request_ids=body.request_ids,
        )
    except delivery_service.DeliveryError as e:
        raise HTTPException(422, str(e)) from e
    return PreviewOut(
        picking=CandidateOut(**vars(preview.picking)) if preview.picking else None,
        suggestions=[SuggestionOut(**vars(s)) for s in preview.suggestions],
        review=[ReviewRowOut(**vars(r)) for r in preview.review],
        extras=[ExtraRowOut(**vars(r)) for r in preview.extras],
        threshold=preview.threshold,
        reason_options=REASON_OPTIONS,
        note=preview.note,
    )


@router.post("/deliveries", response_model=DeliveryOut, status_code=201)
def declare_delivery(
    body: DeclareIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.WAREHOUSE)),
) -> DeliveryOut:
    """Submit the form. Links the requests to the pallet, freezes what's on
    it, writes the sent quantities back onto the request lines, and records
    the reasons. If the pallet is already validated in Odoo, the requests
    close as done in the same call."""
    try:
        pallet = delivery_service.declare(
            db,
            settings,
            actor_user_id=authed.id,
            odoo_picking_id=body.odoo_picking_id,
            request_ids=list(dict.fromkeys(body.request_ids)),
            reasons=[
                delivery_service.ReasonIn(
                    product_id=r.product_id, reasons=r.reasons, note=r.note
                )
                for r in body.reasons
            ],
            note=body.note,
        )
    except delivery_service.DeliveryError as e:
        db.rollback()
        raise HTTPException(422, str(e)) from e
    return _delivery_out(db, _load_delivery(db, pallet.id))


@router.post("/deliveries/{pallet_id}/prepare-count", response_model=DeliveryOut)
def retry_delivery_count(
    pallet_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.WAREHOUSE, Role.SHOPPE_FLOOR)),
) -> DeliveryOut:
    pallet = _load_delivery(db, pallet_id)
    if pallet.count_status == OdooWriteOutcome.CREATED.value:
        raise HTTPException(
            409, f"{pallet.count_picking_name} already exists — scan it in Odoo."
        )
    if pallet.status not in ("validated", "counting"):
        raise HTTPException(
            409, "The count applies once the pallet has been validated in Odoo."
        )
    delivery_service.prepare_delivery_count(db, settings, pallet, authed.id)
    db.commit()
    return _delivery_out(db, _load_delivery(db, pallet.id))


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    req = _get_request(db, request_id)
    # every listener that can move THIS request: Odoo-side warehouse actions,
    # the direct-path close-out, and the delivery it may be riding
    moved = service.poll_outbound_status(db, settings, req) or service.poll_close_out(
        db, settings, req
    )
    if req.status == S.SENT.value:
        moved = bool(pallet_service.poll_pallets(db, settings)) or moved
    if moved:
        req = _get_request(db, request_id)
    return _request_out(db, settings, req, authed)


class LinesIn(BaseModel):
    # same ceiling as CreateRequestIn — this replaces the whole line set
    lines: list[LineIn] = Field(min_length=1, max_length=500)
    note: str = ""


@router.put("/{request_id}/lines", response_model=RequestOut)
def replace_lines(
    request_id: int,
    body: LinesIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> RequestOut:
    req = _get_request(db, request_id)
    if req.status != S.REQUESTED.value:
        raise HTTPException(409, "Lines can only change while the request is still 'requested'.")
    if req.picking_status == OdooWriteOutcome.CREATED.value:
        raise HTTPException(
            409,
            f"This request lives in Odoo as {req.odoo_picking_name} — edit the draft there; "
            "sent quantities are read back from it.",
        )
    validated = _validate_lines(db, body.lines)
    for line in list(req.lines):
        db.delete(line)
    db.flush()
    for product, qty in validated:
        db.add(
            TransferRequestLine(request_id=req.id, product_id=product.id, qty_requested=qty)
        )
    _event(
        db, req, TransferEventKind.LINES_EDITED, authed,
        note=body.note or f"list edited — now {len(validated)} item(s)",
    )
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


class NoteIn(BaseModel):
    note: str = ""


@router.post("/{request_id}/ack", response_model=RequestOut)
def acknowledge(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """Warehouse has laid eyes on it — 'working on it'."""
    req = _get_request(db, request_id)
    _check(req.status, S.WORKING.value, authed)
    req.status = S.WORKING.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "warehouse is working on it",
    )
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


@router.post("/{request_id}/sent", response_model=RequestOut)
def mark_sent(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """Warehouse is done picking: the stock is staged. Sent quantities are
    read back from Odoo.

    Where it's staged decides what happens next. Straight to floor staging
    (the direct path) → the count transfer is prepared here, as it always
    was. Into Staging2 → it waits for the pallet, and the DELIVERY's count
    covers it; preparing one per request as well would move the same units
    twice."""
    req = _get_request(db, request_id)
    _check(req.status, S.SENT.value, authed)
    countable = service.landed_at_floor_staging(db, settings, req)
    readback_note = service.refresh_sent_quantities(db, settings, req)
    req.status = S.SENT.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status,
        note=body.note
        or ("sent to floor staging" if countable else "staged, ready for the next pallet"),
    )
    _event(db, req, TransferEventKind.ODOO, authed, note=readback_note)

    if countable:
        service.prepare_count_transfer(db, settings, req, authed.id)
        if req.count_status in (
            OdooWriteOutcome.CREATED.value,
            OdooWriteOutcome.SIMULATED.value,
        ):
            req.status = S.COUNTING.value
            _event(
                db, req, TransferEventKind.STATUS, authed,
                status=req.status, note="ready to count",
            )
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


@router.post("/{request_id}/prepare-count", response_model=RequestOut)
def retry_prepare_count(
    request_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    req = _get_request(db, request_id)
    if req.status not in (S.SENT.value, S.COUNTING.value):
        raise HTTPException(409, "The count transfer applies once the request is sent.")
    if req.count_status == OdooWriteOutcome.CREATED.value:
        raise HTTPException(409, f"{req.count_picking_name} already exists — scan it in Odoo.")
    service.prepare_count_transfer(db, settings, req, authed.id)
    if req.status == S.SENT.value and req.count_status in (
        OdooWriteOutcome.CREATED.value,
        OdooWriteOutcome.SIMULATED.value,
    ):
        req.status = S.COUNTING.value
        _event(
            db, req, TransferEventKind.STATUS, authed,
            status=req.status, note="ready to count",
        )
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


@router.post("/{request_id}/mark-done", response_model=RequestOut)
def mark_done(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """Manual close for when there's no live count picking (writes gated,
    fixture mode, or the count failed): counted is taken as sent, so no
    discrepancies are invented."""
    req = _get_request(db, request_id)
    _check(req.status, S.DONE.value, authed)
    if req.count_status == OdooWriteOutcome.CREATED.value:
        raise HTTPException(
            409,
            f"{req.count_picking_name} is live in Odoo — validate it in the barcode app and "
            "the request will close itself.",
        )
    counted = {
        line.product.odoo_product_id: float(line.qty_sent or 0)
        for line in req.lines
        if line.product.odoo_product_id
    }
    service.finish_from_count(
        db, req, counted,
        source=body.note or "closed manually (no live count transfer)",
        actor_user_id=authed.id,
    )
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


@router.post("/{request_id}/cancel", response_model=RequestOut)
def cancel(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    req = _get_request(db, request_id)
    _check(req.status, S.CANCELLED.value, authed)
    req.status = S.CANCELLED.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "cancelled",
    )
    service.cancel_placement_draft(db, settings, req, authed.id)
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)


@router.post("/{request_id}/note", response_model=RequestOut)
def add_note(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    if not body.note.strip():
        raise HTTPException(422, "The note is empty.")
    req = _get_request(db, request_id)
    _event(db, req, TransferEventKind.NOTE, authed, note=body.note.strip())
    db.commit()
    return _request_out(db, settings, _get_request(db, req.id), authed)
