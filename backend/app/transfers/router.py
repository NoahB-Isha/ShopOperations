"""BWHSE→Floor transfer requests + the warehouse adjustments queue.

Replaces the WhatsApp "floor transfers" group: floor builds the request,
warehouse picks and stages, floor counts, and every sent-vs-counted mismatch
lands in the adjustments queue instead of a chat scrollback. Optionally, each
physical leg can be rendered as a DRAFT internal transfer in Odoo through the
OdooWriter (same feature flag + kill switch as every write).
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
    Product,
    Role,
    StockLevel,
    TransferEvent,
    TransferEventKind,
    TransferOdooDraft,
    TransferRequest,
    TransferRequestLine,
    TransferRequestStatus,
    User,
    utcnow,
)
from ..odoo.errors import OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter
from .flow import (
    ODOO_LEGS,
    InvalidTransition,
    NotAllowedError,
    check_leg,
    check_transition,
    reconcile,
)

PARTICIPANTS = (Role.SHOPPE_FLOOR, Role.WAREHOUSE)

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
    delta: float | None  # counted - sent, once counted
    floor_qty: float
    bwhse_qty: float


class EventOut(BaseModel):
    id: int
    kind: str
    status: str
    note: str
    actor: str
    created_at: datetime


class OdooDraftOut(BaseModel):
    id: int
    leg: str
    label: str
    status: str  # created | simulated | failed
    reference: str
    dry_run_reason: str
    error: str
    odoo_picking_id: int | None
    odoo_picking_name: str
    odoo_url: str
    created_at: datetime


class ActionsOut(BaseModel):
    can_edit_lines: bool = False
    can_fulfill: bool = False
    can_stage: bool = False
    can_count: bool = False
    can_complete: bool = False
    can_cancel: bool = False
    odoo_legs: list[str] = []


class RequestSummaryOut(BaseModel):
    id: int
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    line_count: int
    total_requested: float
    open_adjustments: int


class RequestOut(BaseModel):
    id: int
    status: str
    notes: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    lines: list[LineOut]
    events: list[EventOut]
    odoo_drafts: list[OdooDraftOut]
    actions: ActionsOut


# ------------------------------------------------------------------ helpers
def _event(
    db: Session,
    request: TransferRequest,
    kind: TransferEventKind,
    actor: AuthedUser | None,
    status: str = "",
    note: str = "",
) -> None:
    db.add(
        TransferEvent(
            request_id=request.id,
            kind=kind.value,
            status=status,
            note=note,
            actor_user_id=actor.id if actor else None,
        )
    )


def _get_request(db: Session, request_id: int) -> TransferRequest:
    # populate_existing: the session keeps objects across commits
    # (expire_on_commit=False), so re-reads must overwrite stale collections
    req = db.scalar(
        select(TransferRequest)
        .options(
            selectinload(TransferRequest.lines).selectinload(TransferRequestLine.product),
            selectinload(TransferRequest.events),
            selectinload(TransferRequest.odoo_drafts),
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


def _actions(req: TransferRequest, authed: AuthedUser) -> ActionsOut:
    roles = authed.role_names
    s = req.status
    S = TransferRequestStatus

    def may(to: str) -> bool:
        try:
            check_transition(s, to, roles)
            return True
        except (InvalidTransition, NotAllowedError):
            return False

    legs = []
    for leg in ODOO_LEGS:
        try:
            check_leg(leg, s, roles)
            legs.append(leg)
        except (InvalidTransition, NotAllowedError):
            continue
    is_floor = Role.SHOPPE_FLOOR.value in roles or Role.ADMIN.value in roles
    return ActionsOut(
        can_edit_lines=s == S.REQUESTED.value and is_floor,
        can_fulfill=may(S.PICKED.value),
        can_stage=may(S.IN_STAGING.value),
        can_count=may(S.COUNTED.value),
        can_complete=may(S.ON_FLOOR.value),
        can_cancel=may(S.CANCELLED.value),
        odoo_legs=legs,
    )


def _request_out(db: Session, req: TransferRequest, authed: AuthedUser) -> RequestOut:
    pids = {line.product_id for line in req.lines}
    stock = _stock_map(db, pids)
    names = _user_names(
        db, {req.created_by_id} | {e.actor_user_id for e in req.events}
    )
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
    drafts = [
        OdooDraftOut(
            id=d.id,
            leg=d.leg,
            label=ODOO_LEGS[d.leg]["label"] if d.leg in ODOO_LEGS else d.leg,
            status=d.status,
            reference=d.reference,
            dry_run_reason=d.dry_run_reason,
            error=d.error,
            odoo_picking_id=d.odoo_picking_id,
            odoo_picking_name=d.odoo_picking_name,
            odoo_url=d.odoo_url,
            created_at=d.created_at,
        )
        for d in req.odoo_drafts
    ]
    return RequestOut(
        id=req.id,
        status=req.status,
        notes=req.notes,
        created_by=names.get(req.created_by_id or 0, "unknown"),
        created_at=req.created_at,
        updated_at=req.updated_at,
        lines=lines,
        events=events,
        odoo_drafts=drafts,
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
        if not product.is_stock_tracked:
            raise HTTPException(
                422, f"'{product.name}' isn't stock-tracked — it can't go on a transfer."
            )
        out.append((product, float(line.qty)))
    return out


# ---------------------------------------------------------------- endpoints
@router.post("", response_model=RequestOut, status_code=201)
def create_request(
    body: CreateRequestIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> RequestOut:
    validated = _validate_lines(db, body.lines)
    req = TransferRequest(notes=body.notes.strip(), created_by_id=authed.id)
    db.add(req)
    db.flush()
    for product, qty in validated:
        db.add(
            TransferRequestLine(request_id=req.id, product_id=product.id, qty_requested=qty)
        )
    _event(
        db,
        req,
        TransferEventKind.STATUS,
        authed,
        status=TransferRequestStatus.REQUESTED.value,
        note=f"{len(validated)} item(s) requested",
    )
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


@router.get("", response_model=list[RequestSummaryOut])
def list_requests(
    status: str = "",
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> list[RequestSummaryOut]:
    q = (
        select(TransferRequest)
        .options(selectinload(TransferRequest.lines))
        .order_by(TransferRequest.id.desc())
    )
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        q = q.where(TransferRequest.status.in_(wanted))
    requests = db.scalars(q).all()

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
            status=r.status,
            created_by=names.get(r.created_by_id or 0, "unknown"),
            created_at=r.created_at,
            updated_at=r.updated_at,
            line_count=len(r.lines),
            total_requested=sum(line.qty_requested for line in r.lines),
            open_adjustments=open_by_request.get(r.id, 0),
        )
        for r in requests
    ]


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    return _request_out(db, _get_request(db, request_id), authed)


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1)
    note: str = ""


@router.put("/{request_id}/lines", response_model=RequestOut)
def replace_lines(
    request_id: int,
    body: LinesIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> RequestOut:
    req = _get_request(db, request_id)
    if req.status != TransferRequestStatus.REQUESTED.value:
        raise HTTPException(
            409, "Lines can only change while the request is still 'requested'."
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
        db,
        req,
        TransferEventKind.LINES_EDITED,
        authed,
        note=body.note or f"list edited — now {len(validated)} item(s)",
    )
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


class FulfillLineIn(BaseModel):
    line_id: int
    qty_sent: float = Field(ge=0, le=100000)


class FulfillIn(BaseModel):
    lines: list[FulfillLineIn] = []
    note: str = ""


@router.post("/{request_id}/fulfill", response_model=RequestOut)
def fulfill(
    request_id: int,
    body: FulfillIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """Warehouse picks the stock: records what's actually being sent
    (defaults to the requested quantity) and advances to 'picked'."""
    req = _get_request(db, request_id)
    _check(req.status, TransferRequestStatus.PICKED.value, authed)
    sent_by_line = {entry.line_id: entry.qty_sent for entry in body.lines}
    unknown = set(sent_by_line) - {line.id for line in req.lines}
    if unknown:
        raise HTTPException(422, f"Line ids {sorted(unknown)} aren't on this request.")
    total_sent = 0.0
    for line in req.lines:
        line.qty_sent = float(sent_by_line.get(line.id, line.qty_requested))
        total_sent += line.qty_sent
    req.status = TransferRequestStatus.PICKED.value
    short = [
        line for line in req.lines if float(line.qty_sent or 0) < float(line.qty_requested)
    ]
    note = body.note or (
        f"picked {total_sent:g} unit(s)"
        + (f" — {len(short)} line(s) short of the request" if short else "")
    )
    _event(db, req, TransferEventKind.STATUS, authed, status=req.status, note=note)
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


class NoteIn(BaseModel):
    note: str = ""


@router.post("/{request_id}/stage", response_model=RequestOut)
def stage(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """The picked stock physically arrived at III-FLOOR-STAGING."""
    req = _get_request(db, request_id)
    _check(req.status, TransferRequestStatus.IN_STAGING.value, authed)
    req.status = TransferRequestStatus.IN_STAGING.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "delivered to floor staging",
    )
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


class CountLineIn(BaseModel):
    line_id: int
    qty_counted: float = Field(ge=0, le=100000)


class CountIn(BaseModel):
    lines: list[CountLineIn] = Field(min_length=1)
    note: str = ""


@router.post("/{request_id}/count", response_model=RequestOut)
def count(
    request_id: int,
    body: CountIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """Floor counts the staged stock. Every line that was sent must be
    counted; sent-vs-counted mismatches become open adjustments for the
    warehouse queue."""
    req = _get_request(db, request_id)
    _check(req.status, TransferRequestStatus.COUNTED.value, authed)

    counted_by_line = {entry.line_id: entry.qty_counted for entry in body.lines}
    unknown = set(counted_by_line) - {line.id for line in req.lines}
    if unknown:
        raise HTTPException(422, f"Line ids {sorted(unknown)} aren't on this request.")
    missing = [
        line.id
        for line in req.lines
        if float(line.qty_sent or 0) > 0 and line.id not in counted_by_line
    ]
    if missing:
        raise HTTPException(
            422,
            "Every line that was sent needs a counted quantity "
            f"(missing line ids {missing}). Count zero if nothing arrived.",
        )
    for line in req.lines:
        if line.id in counted_by_line:
            line.qty_counted = float(counted_by_line[line.id])
        elif float(line.qty_sent or 0) == 0:
            line.qty_counted = 0.0

    discrepancies = reconcile(req.lines)
    for d in discrepancies:
        db.add(
            Adjustment(
                request_id=req.id,
                line_id=d.line_id,
                product_id=d.product_id,
                qty_expected=d.qty_expected,
                qty_counted=d.qty_counted,
                delta=d.delta,
                note=f"Staging count on request #{req.id}",
            )
        )
    req.status = TransferRequestStatus.COUNTED.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "staging count recorded",
    )
    if discrepancies:
        detail = "; ".join(
            f"line {d.line_id}: sent {d.qty_expected:g}, counted {d.qty_counted:g} ({d.delta:+g})"
            for d in discrepancies
        )
        _event(
            db, req, TransferEventKind.DISCREPANCY, authed,
            note=f"{len(discrepancies)} discrepancy(ies) → adjustments queue: {detail}",
        )
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


@router.post("/{request_id}/complete", response_model=RequestOut)
def complete(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    req = _get_request(db, request_id)
    _check(req.status, TransferRequestStatus.ON_FLOOR.value, authed)
    req.status = TransferRequestStatus.ON_FLOOR.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "shelved on the floor",
    )
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


@router.post("/{request_id}/cancel", response_model=RequestOut)
def cancel(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    req = _get_request(db, request_id)
    _check(req.status, TransferRequestStatus.CANCELLED.value, authed)
    req.status = TransferRequestStatus.CANCELLED.value
    _event(
        db, req, TransferEventKind.STATUS, authed,
        status=req.status, note=body.note or "cancelled",
    )
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


@router.post("/{request_id}/note", response_model=RequestOut)
def add_note(
    request_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    if not body.note.strip():
        raise HTTPException(422, "The note is empty.")
    req = _get_request(db, request_id)
    _event(db, req, TransferEventKind.NOTE, authed, note=body.note.strip())
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


class OdooDraftIn(BaseModel):
    leg: str


@router.post("/{request_id}/odoo-draft", response_model=RequestOut)
def create_odoo_draft(
    request_id: int,
    body: OdooDraftIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*PARTICIPANTS)),
) -> RequestOut:
    """Render this leg of the flow as a DRAFT internal transfer in Odoo (or a
    dry-run when writes are gated). The outcome — created, simulated, or
    failed — is recorded on the request either way."""
    req = _get_request(db, request_id)
    try:
        spec = check_leg(body.leg, req.status, authed.role_names)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    except NotAllowedError as e:
        raise HTTPException(403, str(e)) from e

    qty_field = spec["qty_field"]
    lines = [
        {"product_id": line.product_id, "qty": float(getattr(line, qty_field) or 0)}
        for line in req.lines
        if float(getattr(line, qty_field) or 0) > 0
    ]
    if not lines:
        raise HTTPException(422, f"No lines with a positive {qty_field} to transfer.")

    # reuse the leg's reference across retries so Odoo never gets duplicates
    previous = next((d for d in reversed(req.odoo_drafts) if d.leg == body.leg), None)
    reference = previous.reference if previous and previous.reference else new_reference("TR")

    writer = OdooWriter(db, settings, actor_user_id=authed.id)
    try:
        result = writer.create_internal_transfer(
            source_key=spec["source_key"],
            dest_key=spec["dest_key"],
            lines=lines,
            note=f"Floor transfer request #{req.id} — {spec['label']}",
            reference=reference,
        )
        draft = TransferOdooDraft(
            request_id=req.id,
            leg=body.leg,
            status="simulated" if result.dry_run else "created",
            reference=result.reference,
            dry_run_reason=result.dry_run_reason,
            odoo_picking_id=result.record_ids[0] if result.record_ids else None,
            odoo_picking_name="",
            odoo_url=result.deep_link,
            created_by_id=authed.id,
        )
        note = result.message
    except OdooWriteError as e:
        draft = TransferOdooDraft(
            request_id=req.id,
            leg=body.leg,
            status="failed",
            reference=reference,
            error=str(e),
            created_by_id=authed.id,
        )
        note = f"{spec['label']} draft FAILED: {e}"
    db.add(draft)
    _event(db, req, TransferEventKind.ODOO_DRAFT, authed, note=note)
    db.commit()
    return _request_out(db, _get_request(db, req.id), authed)


def _check(current: str, to: str, authed: AuthedUser) -> None:
    try:
        check_transition(current, to, authed.role_names)
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
    except NotAllowedError as e:
        raise HTTPException(403, str(e)) from e


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
