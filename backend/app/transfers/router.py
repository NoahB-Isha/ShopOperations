"""BWHSE→Floor transfer requests + the warehouse adjustments queue.

Odoo-native flow: placing a request immediately renders the BWHSE→STAGING
draft (the request adopts the picking's name). Warehouse taps "working on
it" then "sent" — their part ends there. The app prepares the STAGING→FLOOR
count transfer (duplicate → mark To Do → check availability), the floor
scans it in Odoo's barcode app, and the app listens for the validation to
close the request and reconcile sent-vs-counted into the adjustments queue.

The UI polls these endpoints (POS-board style); reads are cheap and the
Odoo validation check is throttled per request.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    Adjustment,
    AdjustmentStatus,
    OdooWriteOutcome,
    Product,
    Role,
    StockLevel,
    TransferEvent,
    TransferEventKind,
    TransferRequest,
    TransferRequestLine,
    TransferRequestStatus,
    User,
    utcnow,
)
from . import service
from .flow import InvalidTransition, NotAllowedError, check_transition

PARTICIPANTS = (Role.SHOPPE_FLOOR, Role.WAREHOUSE)
S = TransferRequestStatus

router = APIRouter(
    prefix="/transfer-requests",
    tags=["transfers"],
    dependencies=[Depends(require_roles(*PARTICIPANTS))],
)

adjustments_router = APIRouter(
    prefix="/adjustments",
    tags=["transfers"],
    dependencies=[Depends(require_roles(Role.WAREHOUSE))],
)


# ------------------------------------------------------------------ schemas
class LineIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)


class CreateRequestIn(BaseModel):
    notes: str = ""
    lines: list[LineIn] = Field(min_length=1)


class LineOut(BaseModel):
    id: int
    product_id: int
    sku: str
    name: str
    category: str
    qty_requested: float
    qty_sent: float | None
    qty_counted: float | None
    delta: float | None  # counted - sent, once done
    floor_qty: float
    bwhse_qty: float


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
    open_adjustments: int
    picking_status: str
    count_status: str


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


def _may(current: str, to: str, authed: AuthedUser) -> bool:
    try:
        check_transition(current, to, authed.role_names)
        return True
    except (InvalidTransition, NotAllowedError):
        return False


def _actions(req: TransferRequest, authed: AuthedUser) -> ActionsOut:
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
            and authed.has_role(Role.WAREHOUSE, Role.SHOPPE_FLOOR, Role.ADMIN)
        ),
        can_mark_done=(
            req.status in (S.SENT.value, S.COUNTING.value) and not count_live and is_floor
        ),
        can_cancel=_may(req.status, S.CANCELLED.value, authed),
    )


def _request_out(db: Session, settings: Settings, req: TransferRequest, authed: AuthedUser) -> RequestOut:
    pids = {line.product_id for line in req.lines}
    stock = _stock_map(db, pids)
    names = _user_names(db, {req.created_by_id} | {e.actor_user_id for e in req.events})
    lines = []
    for line in req.lines:
        p = line.product
        s = stock.get(line.product_id, {})
        delta = None
        if line.qty_counted is not None:
            delta = round(float(line.qty_counted) - float(line.qty_sent or 0), 3)
        lines.append(
            LineOut(
                id=line.id,
                product_id=line.product_id,
                sku=p.global_sku,
                name=p.name,
                category=p.category,
                qty_requested=line.qty_requested,
                qty_sent=line.qty_sent,
                qty_counted=line.qty_counted,
                delta=delta,
                floor_qty=s.get("floor", 0.0),
                bwhse_qty=s.get("bwhse", 0.0),
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
        actions=_actions(req, authed),
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

    # the board is the listener: check a few counting requests per refresh
    polled = 0
    for req in requests:
        if req.status == S.COUNTING.value and polled < 5:
            polled += 1
            service.poll_count_validation(db, settings, req)

    names = _user_names(db, {r.created_by_id for r in requests})
    open_by_request: dict[int, int] = {}
    if requests:
        rows = db.execute(
            select(Adjustment.request_id).where(
                Adjustment.request_id.in_([r.id for r in requests]),
                Adjustment.status == AdjustmentStatus.OPEN.value,
            )
        )
        for (rid,) in rows:
            open_by_request[rid] = open_by_request.get(rid, 0) + 1

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
            open_adjustments=open_by_request.get(r.id, 0),
            picking_status=r.picking_status,
            count_status=r.count_status,
        )
        for r in requests
    ]


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    req = _get_request(db, request_id)
    if service.poll_count_validation(db, settings, req):
        req = _get_request(db, request_id)
    return _request_out(db, settings, req, authed)


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1)
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
    """Warehouse is done: stock is at staging. Sent quantities are read back
    from the Odoo picking, and the count transfer is prepared for the
    barcode app in the same motion."""
    req = _get_request(db, request_id)
    _check(req.status, S.SENT.value, authed)
    readback_note = service.refresh_sent_quantities(db, settings, req)
    req.status = S.SENT.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "sent to floor staging",
    )
    _event(db, req, TransferEventKind.ODOO, authed, note=readback_note)

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


# ------------------------------------------------------------- adjustments
class AdjustmentOut(BaseModel):
    id: int
    request_id: int | None
    product_id: int
    sku: str
    name: str
    qty_expected: float
    qty_counted: float
    delta: float
    status: str
    note: str
    resolution_note: str
    resolved_by: str
    created_at: datetime
    resolved_at: datetime | None


def _adjustment_out(db: Session, rows: list[Adjustment]) -> list[AdjustmentOut]:
    products = {
        p.id: p
        for p in db.scalars(
            select(Product).where(Product.id.in_({a.product_id for a in rows}))
        )
    }
    names = _user_names(db, {a.resolved_by_id for a in rows})
    out = []
    for a in rows:
        p = products.get(a.product_id)
        out.append(
            AdjustmentOut(
                id=a.id,
                request_id=a.request_id,
                product_id=a.product_id,
                sku=p.global_sku if p else "",
                name=p.name if p else f"product {a.product_id}",
                qty_expected=a.qty_expected,
                qty_counted=a.qty_counted,
                delta=a.delta,
                status=a.status,
                note=a.note,
                resolution_note=a.resolution_note,
                resolved_by=names.get(a.resolved_by_id or 0, ""),
                created_at=a.created_at,
                resolved_at=a.resolved_at,
            )
        )
    return out


@adjustments_router.get("", response_model=list[AdjustmentOut])
def list_adjustments(
    status: str = "open",
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.WAREHOUSE)),
) -> list[AdjustmentOut]:
    q = select(Adjustment).order_by(Adjustment.id.desc())
    if status != "all":
        q = q.where(Adjustment.status == status)
    return _adjustment_out(db, list(db.scalars(q)))


class ResolveIn(BaseModel):
    action: str  # resolved | dismissed
    note: str = ""


@adjustments_router.post("/{adjustment_id}/resolve", response_model=AdjustmentOut)
def resolve_adjustment(
    adjustment_id: int,
    body: ResolveIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.WAREHOUSE)),
) -> AdjustmentOut:
    if body.action not in (AdjustmentStatus.RESOLVED.value, AdjustmentStatus.DISMISSED.value):
        raise HTTPException(422, "action must be 'resolved' or 'dismissed'.")
    adj = db.get(Adjustment, adjustment_id)
    if adj is None:
        raise HTTPException(404, "Adjustment not found.")
    if adj.status != AdjustmentStatus.OPEN.value:
        raise HTTPException(409, f"Already {adj.status}.")
    adj.status = body.action
    adj.resolution_note = body.note.strip()
    adj.resolved_by_id = authed.id
    adj.resolved_at = utcnow()
    db.commit()
    return _adjustment_out(db, [adj])[0]
