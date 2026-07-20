"""India-import & vendor purchase-ordering API (admin/office only).

The review screen IS the draft order: generation freezes the engine's
suggestions into lines, PATCHing a line is the buyer's override, placing
exports + emails + freezes. Everything after placement is the append-only
timeline: ingested replies become proposals, humans confirm/edit/reject,
manual events and attachments cover what the parser misses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    AnalogyStatus,
    ForecastAnalogy,
    OrderAttachment,
    OrderEmailMessage,
    OrderEventProposal,
    OrderLeg,
    Product,
    ProposalStatus,
    PurchaseOrder,
    PurchaseOrderEvent,
    PurchaseOrderLine,
    Role,
    Vendor,
    VendorKind,
)
from . import service, tracking
from .analogy import suggest_analog
from .emailer import EMAIL_SETTING_KEY, gate_reason
from .export import export_rows, rows_to_csv, rows_to_xlsx
from .rules import OrderingRules
from .service import OrderingError

router = APIRouter(
    prefix="/ordering",
    tags=["ordering"],
    dependencies=[Depends(require_roles(Role.ADMIN))],
)

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


# ------------------------------------------------------------------ schemas
class OrderSummaryOut(BaseModel):
    id: int
    name: str
    reference: str
    order_type: str
    status: str
    destination: str
    vendor_id: int | None
    vendor_name: str | None
    snapshot_source: str
    created_at: datetime
    placed_at: datetime | None
    line_count: int
    ordering_line_count: int  # lines with a final qty > 0
    sea_units: int
    air_units: int
    pending_proposals: int


class LineOut(BaseModel):
    id: int
    product_id: int | None
    global_sku: str
    line_status: str
    substitute_sku: str
    suggested_sea_qty: int
    suggested_air_qty: int
    baseline_sea_qty: int
    baseline_air_qty: int
    origin_sea_qty: int
    origin_air_qty: int
    final_sea_qty: int
    final_air_qty: int
    target_moh_used: float
    case_size: int
    suggestion: dict[str, Any]


class LegOut(BaseModel):
    id: int
    label: str
    method: str
    status: str
    eta: str | None
    line_quantities: dict[str, float]


class EventOut(BaseModel):
    id: int
    kind: str
    status: str
    note: str
    payload: dict[str, Any]
    actor_label: str
    line_id: int | None
    line_sku: str | None
    source_message_id: int | None
    source_quote: str
    confidence: float | None
    created_at: datetime


class ProposalOut(BaseModel):
    id: int
    order_id: int
    message_id: int | None
    line_id: int | None
    line_sku: str | None
    kind: str
    payload: dict[str, Any]
    quote: str
    confidence: float
    parsed_by: str
    status: str
    created_at: datetime


class EmailOut(BaseModel):
    id: int
    direction: str
    sender: str
    recipients: str
    subject: str
    body: str
    status: str
    occurred_at: datetime


class AttachmentOut(BaseModel):
    id: int
    source: str
    filename: str
    content_type: str
    size_bytes: int
    note: str
    message_id: int | None
    created_at: datetime


class OrderDetailOut(BaseModel):
    order: OrderSummaryOut
    rules: dict[str, Any]
    notes: str
    snapshot_at: datetime | None
    email_gate_reason: str  # '' = live sends enabled
    lines: list[LineOut]
    legs: list[LegOut]


class TimelineOut(BaseModel):
    order: OrderSummaryOut
    events: list[EventOut]
    proposals: list[ProposalOut]
    emails: list[EmailOut]
    attachments: list[AttachmentOut]
    legs: list[LegOut]


class CreateOrderIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    destination: str = "III"
    notes: str = ""


class OverrideIn(BaseModel):
    final_sea_qty: int | None = Field(default=None, ge=0)
    final_air_qty: int | None = Field(default=None, ge=0)


class NoteIn(BaseModel):
    note: str = ""


class IngestEmailIn(BaseModel):
    sender: str = ""
    subject: str = ""
    body: str = Field(min_length=1)
    message_id: str = ""


class ManualEventIn(BaseModel):
    kind: str
    line_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class DecideProposalIn(BaseModel):
    accept: bool
    payload: dict[str, Any] | None = None
    line_id: int | None = None
    note: str = ""


class VendorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = VendorKind.US.value
    contact_name: str = ""
    contact_email: str = ""
    cc_emails: str = ""
    notes: str = ""
    active: bool = True


class VendorOut(VendorIn):
    id: int
    product_count: int = 0


class VendorOrderIn(BaseModel):
    quantities: dict[str, int]
    name: str = ""
    destination: str = "III"
    # domestic reality: usually there's no review step — compose and email now
    send: bool = False


class VendorProductIn(BaseModel):
    product_id: int
    moq: int | None = Field(default=None, ge=1)


class VendorProductOut(BaseModel):
    product_id: int
    global_sku: str
    name: str
    category: str
    moq: int | None
    is_active: bool


class ProductListMetaOut(BaseModel):
    filename: str
    uploaded_at: str
    matched: int
    total_rows: int
    unmatched_rows: list[str]


class RulesOut(BaseModel):
    effective: dict[str, Any]
    overrides: dict[str, Any]


class EmailSettingsIn(BaseModel):
    india_to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)


class AnalogyIn(BaseModel):
    product_id: int
    analog_product_id: int | None = None
    monthly_estimate: float | None = Field(default=None, gt=0)
    rationale: str = ""
    source: str = "manual"


class AnalogyOut(BaseModel):
    id: int
    product_id: int
    product_sku: str
    product_name: str
    analog_product_id: int | None
    analog_sku: str | None
    analog_name: str | None
    monthly_estimate: float | None
    rationale: str
    source: str
    status: str


class AnalogSuggestionOut(BaseModel):
    analog_product_id: int
    analog_sku: str
    analog_name: str
    rationale: str
    source: str


# ------------------------------------------------------------------ helpers
def _get_order(db: Session, order_id: int) -> PurchaseOrder:
    order = db.get(
        PurchaseOrder,
        order_id,
        options=[selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.vendor)],
    )
    if order is None:
        raise HTTPException(404, "order not found")
    return order


def _pending_counts(db: Session, order_ids: list[int]) -> dict[int, int]:
    if not order_ids:
        return {}
    rows = db.execute(
        select(OrderEventProposal.order_id, func.count())
        .where(
            OrderEventProposal.order_id.in_(order_ids),
            OrderEventProposal.status == ProposalStatus.PENDING.value,
        )
        .group_by(OrderEventProposal.order_id)
    )
    return {int(oid): int(n) for oid, n in rows}


def _summary(order: PurchaseOrder, pending: int) -> OrderSummaryOut:
    active = [ln for ln in order.lines if ln.line_status != "discontinued"]
    return OrderSummaryOut(
        id=order.id,
        name=order.display_name,
        reference=order.reference,
        order_type=order.order_type,
        status=order.status,
        destination=order.destination,
        vendor_id=order.vendor_id,
        vendor_name=order.vendor.name if order.vendor else None,
        snapshot_source=order.snapshot_source,
        created_at=order.created_at,
        placed_at=order.placed_at,
        line_count=len(order.lines),
        ordering_line_count=sum(
            1 for ln in active if (ln.final_sea_qty or 0) + (ln.final_air_qty or 0) > 0
        ),
        sea_units=sum(ln.final_sea_qty or 0 for ln in active),
        air_units=sum(ln.final_air_qty or 0 for ln in active),
        pending_proposals=pending,
    )


def _line_out(line: PurchaseOrderLine) -> LineOut:
    return LineOut(
        id=line.id,
        product_id=line.product_id,
        global_sku=line.global_sku,
        line_status=line.line_status,
        substitute_sku=line.substitute_sku,
        suggested_sea_qty=line.suggested_sea_qty,
        suggested_air_qty=line.suggested_air_qty,
        baseline_sea_qty=line.baseline_sea_qty,
        baseline_air_qty=line.baseline_air_qty,
        origin_sea_qty=line.origin_sea_qty,
        origin_air_qty=line.origin_air_qty,
        final_sea_qty=line.final_sea_qty,
        final_air_qty=line.final_air_qty,
        target_moh_used=line.target_moh_used,
        case_size=line.case_size,
        suggestion=line.suggestion_json or {},
    )


def _leg_out(leg: OrderLeg) -> LegOut:
    return LegOut(
        id=leg.id,
        label=leg.label,
        method=leg.method,
        status=leg.status,
        eta=leg.eta.isoformat() if leg.eta else None,
        line_quantities=leg.line_quantities or {},
    )


def _sku_by_line(order: PurchaseOrder) -> dict[int, str]:
    return {ln.id: ln.global_sku for ln in order.lines}


# ------------------------------------------------------------------- orders
@router.post("/orders", response_model=OrderDetailOut, status_code=201)
def create_order(
    body: CreateOrderIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
):
    try:
        order = service.create_import_order(
            db,
            settings,
            name=body.name,
            destination=body.destination,
            created_by=authed.user,
            notes=body.notes,
        )
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    return order_detail(order.id, db, settings)


@router.post("/orders/upload", response_model=OrderDetailOut, status_code=201)
def create_order_from_upload(
    file: UploadFile = File(...),
    name: str = Form(...),
    destination: str = Form("III"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
):
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "upload too large")
    try:
        order = service.create_import_order(
            db,
            settings,
            name=name,
            destination=destination,
            created_by=authed.user,
            source="upload",
            upload=data,
            upload_name=file.filename or "",
            notes=notes,
        )
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    return order_detail(order.id, db, settings)


@router.get("/orders", response_model=list[OrderSummaryOut])
def list_orders(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.vendor))
        .order_by(PurchaseOrder.id.desc())
    )
    if status:
        query = query.where(PurchaseOrder.status == status)
    orders = db.execute(query).scalars().all()
    pending = _pending_counts(db, [o.id for o in orders])
    return [_summary(o, pending.get(o.id, 0)) for o in orders]


@router.get("/orders/{order_id}", response_model=OrderDetailOut)
def order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    order = _get_order(db, order_id)
    pending = _pending_counts(db, [order.id]).get(order.id, 0)
    legs = db.execute(
        select(OrderLeg).where(OrderLeg.order_id == order.id).order_by(OrderLeg.id)
    ).scalars()
    return OrderDetailOut(
        order=_summary(order, pending),
        rules=order.rules_json or {},
        notes=order.notes,
        snapshot_at=order.snapshot_at,
        email_gate_reason=gate_reason(db, settings),
        lines=[_line_out(ln) for ln in order.lines],
        legs=[_leg_out(leg) for leg in legs],
    )


@router.get("/orders/{order_id}/timeline", response_model=TimelineOut)
def order_timeline(order_id: int, db: Session = Depends(get_db)):
    """Everything the timeline view polls: events, proposals, emails,
    attachments, legs — no line table (that's the detail GET)."""
    order = _get_order(db, order_id)
    sku_by_line = _sku_by_line(order)
    events = db.execute(
        select(PurchaseOrderEvent)
        .where(PurchaseOrderEvent.order_id == order.id)
        .order_by(PurchaseOrderEvent.id)
    ).scalars()
    proposals = db.execute(
        select(OrderEventProposal)
        .where(OrderEventProposal.order_id == order.id)
        .order_by(OrderEventProposal.id)
    ).scalars()
    emails = db.execute(
        select(OrderEmailMessage)
        .where(OrderEmailMessage.order_id == order.id)
        .order_by(OrderEmailMessage.id)
    ).scalars()
    attachments = db.execute(
        select(OrderAttachment)
        .where(OrderAttachment.order_id == order.id)
        .order_by(OrderAttachment.id)
    ).scalars()
    legs = db.execute(
        select(OrderLeg).where(OrderLeg.order_id == order.id).order_by(OrderLeg.id)
    ).scalars()
    pending = _pending_counts(db, [order.id]).get(order.id, 0)
    return TimelineOut(
        order=_summary(order, pending),
        events=[
            EventOut(
                id=e.id,
                kind=e.kind,
                status=e.status,
                note=e.note,
                payload=e.payload or {},
                actor_label=e.actor_label,
                line_id=e.line_id,
                line_sku=sku_by_line.get(e.line_id) if e.line_id else None,
                source_message_id=e.source_message_id,
                source_quote=e.source_quote,
                confidence=e.confidence,
                created_at=e.created_at,
            )
            for e in events
        ],
        proposals=[
            ProposalOut(
                id=p.id,
                order_id=p.order_id,
                message_id=p.message_id,
                line_id=p.line_id,
                line_sku=sku_by_line.get(p.line_id) if p.line_id else None,
                kind=p.kind,
                payload=p.payload or {},
                quote=p.quote,
                confidence=p.confidence,
                parsed_by=p.parsed_by,
                status=p.status,
                created_at=p.created_at,
            )
            for p in proposals
        ],
        emails=[
            EmailOut(
                id=m.id,
                direction=m.direction,
                sender=m.sender,
                recipients=m.recipients,
                subject=m.subject,
                body=m.body,
                status=m.status,
                occurred_at=m.occurred_at,
            )
            for m in emails
        ],
        attachments=[
            AttachmentOut(
                id=a.id,
                source=a.source,
                filename=a.filename,
                content_type=a.content_type,
                size_bytes=a.size_bytes,
                note=a.note,
                message_id=a.message_id,
                created_at=a.created_at,
            )
            for a in attachments
        ],
        legs=[_leg_out(leg) for leg in legs],
    )


@router.patch("/orders/{order_id}/lines/{line_id}", response_model=LineOut)
def override_line(
    order_id: int,
    line_id: int,
    body: OverrideIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    order = _get_order(db, order_id)
    line = next((ln for ln in order.lines if ln.id == line_id), None)
    if line is None:
        raise HTTPException(404, "line not found on this order")
    try:
        service.override_line(
            db, order, line, sea=body.final_sea_qty, air=body.final_air_qty, actor=authed.user
        )
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    return _line_out(line)


@router.post("/orders/{order_id}/place", response_model=OrderDetailOut)
def place_order(
    order_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
):
    order = _get_order(db, order_id)
    try:
        service.place_order(db, settings, order, actor=authed.user)
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    return order_detail(order_id, db, settings)


@router.post("/orders/{order_id}/cancel", response_model=OrderSummaryOut)
def cancel_order(
    order_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    order = _get_order(db, order_id)
    try:
        service.cancel_order(db, order, authed.user, body.note)
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    return _summary(order, _pending_counts(db, [order.id]).get(order.id, 0))


@router.post("/orders/{order_id}/close", response_model=OrderSummaryOut)
def close_order(
    order_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    order = _get_order(db, order_id)
    try:
        service.close_order(db, order, authed.user, body.note)
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    return _summary(order, _pending_counts(db, [order.id]).get(order.id, 0))


# ------------------------------------------------------------------ exports
@router.get("/orders/{order_id}/export.csv")
def export_csv(order_id: int, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    rows = export_rows(order)
    return Response(
        content=rows_to_csv(rows),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{order.display_name} ORDER LIST.csv"'
        },
    )


@router.get("/orders/{order_id}/export.xlsx")
def export_xlsx(order_id: int, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    rows = export_rows(order)
    return Response(
        content=rows_to_xlsx(rows, order.display_name),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{order.display_name} ORDER LIST.xlsx"'
        },
    )


# ----------------------------------------------------------------- timeline
@router.post("/orders/{order_id}/ingest-email", response_model=TimelineOut, status_code=201)
def ingest_email(
    order_id: int,
    body: IngestEmailIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Paste (or simulate) a vendor reply — the manual counterpart of the
    worker's mailbox poll. The body is parsed into pending proposals."""
    order = _get_order(db, order_id)
    tracking.ingest_email(
        db,
        settings,
        order,
        sender=body.sender,
        subject=body.subject,
        body=body.body,
        rfc_message_id=body.message_id,
    )
    db.commit()
    return order_timeline(order_id, db)


@router.post("/orders/{order_id}/events", response_model=TimelineOut, status_code=201)
def add_manual_event(
    order_id: int,
    body: ManualEventIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    order = _get_order(db, order_id)
    try:
        tracking.add_manual_event(
            db,
            order,
            kind=body.kind,
            actor=authed.user,
            line_id=body.line_id,
            payload=body.payload,
            note=body.note,
        )
        db.commit()
    except (OrderingError, ValueError) as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    return order_timeline(order_id, db)


@router.post("/orders/{order_id}/attachments", response_model=TimelineOut, status_code=201)
def upload_attachment(
    order_id: int,
    file: UploadFile = File(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    order = _get_order(db, order_id)
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "upload too large")
    tracking.add_attachment(
        db,
        order,
        filename=file.filename or "attachment",
        data=data,
        content_type=file.content_type or "application/octet-stream",
        actor=authed.user,
        note=note,
    )
    db.commit()
    return order_timeline(order_id, db)


@router.get("/orders/{order_id}/attachments/{attachment_id}/download")
def download_attachment(order_id: int, attachment_id: int, db: Session = Depends(get_db)):
    attachment = db.get(OrderAttachment, attachment_id)
    if attachment is None or attachment.order_id != order_id:
        raise HTTPException(404, "attachment not found")
    return Response(
        content=attachment.data,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
    )


@router.post("/proposals/{proposal_id}/decide", response_model=TimelineOut)
def decide_proposal(
    proposal_id: int,
    body: DecideProposalIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    proposal = db.get(OrderEventProposal, proposal_id)
    if proposal is None:
        raise HTTPException(404, "proposal not found")
    order = _get_order(db, proposal.order_id)
    try:
        tracking.decide_proposal(
            db,
            order,
            proposal,
            accept=body.accept,
            actor=authed.user,
            edited_payload=body.payload,
            edited_line_id=body.line_id,
            note=body.note,
        )
        db.commit()
    except (OrderingError, ValueError) as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    return order_timeline(order.id, db)


# ------------------------------------------------------------------ vendors
@router.get("/vendors", response_model=list[VendorOut])
def list_vendors(db: Session = Depends(get_db)):
    counts = {
        int(vid): int(n)
        for vid, n in db.execute(
            select(Product.vendor_id, func.count())
            .where(Product.vendor_id.is_not(None))
            .group_by(Product.vendor_id)
        )
    }
    vendors = db.execute(select(Vendor).order_by(Vendor.name)).scalars()
    return [
        VendorOut(
            id=v.id,
            name=v.name,
            kind=v.kind,
            contact_name=v.contact_name,
            contact_email=v.contact_email,
            cc_emails=v.cc_emails,
            notes=v.notes,
            active=v.active,
            product_count=counts.get(v.id, 0),
        )
        for v in vendors
    ]


@router.post("/vendors", response_model=VendorOut, status_code=201)
def create_vendor(body: VendorIn, db: Session = Depends(get_db)):
    if body.kind not in {k.value for k in VendorKind}:
        raise HTTPException(400, "unknown vendor kind")
    if db.execute(select(Vendor).where(Vendor.name == body.name)).scalar():
        raise HTTPException(409, "a vendor with that name already exists")
    vendor = Vendor(**body.model_dump())
    db.add(vendor)
    db.commit()
    return VendorOut(id=vendor.id, product_count=0, **body.model_dump())


@router.patch("/vendors/{vendor_id}", response_model=VendorOut)
def update_vendor(vendor_id: int, body: VendorIn, db: Session = Depends(get_db)):
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "vendor not found")
    if body.kind not in {k.value for k in VendorKind}:
        raise HTTPException(400, "unknown vendor kind")
    for key, value in body.model_dump().items():
        setattr(vendor, key, value)
    db.commit()
    count = db.execute(
        select(func.count()).select_from(Product).where(Product.vendor_id == vendor.id)
    ).scalar_one()
    return VendorOut(id=vendor.id, product_count=count, **body.model_dump())


@router.get("/vendors/{vendor_id}/suggestions")
def vendor_suggestions(vendor_id: int, db: Session = Depends(get_db)):
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "vendor not found")
    from dataclasses import asdict

    return {
        "vendor": {
            "id": vendor.id,
            "name": vendor.name,
            "contact_email": vendor.contact_email,
        },
        "items": [asdict(s) for s in service.domestic_suggestions(db, vendor)],
    }


@router.post("/vendors/{vendor_id}/orders", response_model=OrderDetailOut, status_code=201)
def create_vendor_order(
    vendor_id: int,
    body: VendorOrderIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
):
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "vendor not found")
    try:
        order = service.create_domestic_order(
            db,
            vendor=vendor,
            quantities=body.quantities,
            name=body.name,
            destination=body.destination,
            created_by=authed.user,
        )
        if body.send:
            # the domestic reality: compose + email in one step (gate ladder
            # still applies — with ordering_email_live off it's a dry-run)
            service.place_order(db, settings, order, actor=authed.user)
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    return order_detail(order.id, db, settings)


# --------------------------------------------------- vendor product roster
@router.get("/vendors/{vendor_id}/products", response_model=list[VendorProductOut])
def vendor_products(vendor_id: int, db: Session = Depends(get_db)):
    if db.get(Vendor, vendor_id) is None:
        raise HTTPException(404, "vendor not found")
    rows = db.execute(
        select(Product).where(Product.vendor_id == vendor_id).order_by(Product.name)
    ).scalars()
    return [
        VendorProductOut(
            product_id=p.id,
            global_sku=p.global_sku,
            name=p.name,
            category=p.category,
            moq=p.moq,
            is_active=p.is_active,
        )
        for p in rows
    ]


@router.post("/vendors/{vendor_id}/products", response_model=list[VendorProductOut])
def add_vendor_product(
    vendor_id: int,
    body: VendorProductIn,
    db: Session = Depends(get_db),
):
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(404, "vendor not found")
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(404, "product not found")
    if product.vendor_id is not None and product.vendor_id != vendor_id:
        other = db.get(Vendor, product.vendor_id)
        raise HTTPException(
            409, f"'{product.name}' already belongs to {other.name if other else 'another vendor'}"
        )
    product.vendor_id = vendor_id
    if body.moq is not None:
        product.moq = body.moq
    db.commit()
    return vendor_products(vendor_id, db)


@router.delete("/vendors/{vendor_id}/products/{product_id}", response_model=list[VendorProductOut])
def remove_vendor_product(
    vendor_id: int,
    product_id: int,
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None or product.vendor_id != vendor_id:
        raise HTTPException(404, "that product isn't on this vendor")
    product.vendor_id = None
    db.commit()
    return vendor_products(vendor_id, db)


# --------------------------------------------------- India product list
@router.get("/product-list", response_model=ProductListMetaOut | None)
def get_product_list(db: Session = Depends(get_db)):
    return service.india_product_list_meta(db)


@router.put("/product-list", response_model=ProductListMetaOut)
def put_product_list(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    data = file.file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "upload too large (10 MB max)")
    try:
        meta = service.set_india_product_list(
            db, filename=file.filename or "product-list.csv", data=data, actor=authed.user
        )
        db.commit()
    except OrderingError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    return meta


@router.get("/product-list/download")
def download_product_list(db: Session = Depends(get_db)):
    stored = service.india_product_list_file(db)
    if stored is None:
        raise HTTPException(404, "no product list uploaded")
    filename, data = stored
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/product-list", status_code=204)
def delete_product_list(
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    service.clear_india_product_list(db, authed.user)
    db.commit()


# ----------------------------------------------------------- rules/settings
@router.get("/rules", response_model=RulesOut)
def get_rules(db: Session = Depends(get_db)):
    from dataclasses import asdict

    return RulesOut(
        effective=asdict(service.load_rules(db)),
        overrides=service.get_app_setting(db, service.RULES_SETTING_KEY),
    )


@router.put("/rules", response_model=RulesOut)
def put_rules(
    overrides: dict[str, Any],
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    # merged() is the validator: it ignores unknown keys and refuses bad
    # values, so a typo can't take the review screen down.
    OrderingRules().merged(overrides)
    service.set_app_setting(db, service.RULES_SETTING_KEY, overrides, authed.user)
    db.commit()
    return get_rules(db)


@router.get("/email-settings", response_model=EmailSettingsIn)
def get_email_settings(db: Session = Depends(get_db)):
    value = service.get_app_setting(db, EMAIL_SETTING_KEY)
    return EmailSettingsIn(
        india_to=[str(e) for e in value.get("india_to", [])],
        cc=[str(e) for e in value.get("cc", [])],
    )


@router.put("/email-settings", response_model=EmailSettingsIn)
def put_email_settings(
    body: EmailSettingsIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    service.set_app_setting(db, EMAIL_SETTING_KEY, body.model_dump(), authed.user)
    db.commit()
    return body


# ---------------------------------------------------------------- analogies
@router.get("/analogies", response_model=list[AnalogyOut])
def list_analogies(db: Session = Depends(get_db)):
    rows = db.execute(
        select(ForecastAnalogy).order_by(ForecastAnalogy.id.desc())
    ).scalars()
    out = []
    for a in rows:
        product = db.get(Product, a.product_id)
        analog = db.get(Product, a.analog_product_id) if a.analog_product_id else None
        out.append(
            AnalogyOut(
                id=a.id,
                product_id=a.product_id,
                product_sku=product.global_sku if product else "",
                product_name=product.name if product else "",
                analog_product_id=a.analog_product_id,
                analog_sku=analog.global_sku if analog else None,
                analog_name=analog.name if analog else None,
                monthly_estimate=a.monthly_estimate,
                rationale=a.rationale,
                source=a.source,
                status=a.status,
            )
        )
    return out


@router.post("/analogies", response_model=AnalogyOut, status_code=201)
def create_analogy(
    body: AnalogyIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
):
    product = db.get(Product, body.product_id)
    if product is None:
        raise HTTPException(404, "product not found")
    if bool(body.analog_product_id) == bool(body.monthly_estimate):
        raise HTTPException(400, "set exactly one of analog_product_id or monthly_estimate")
    analog = None
    if body.analog_product_id:
        analog = db.get(Product, body.analog_product_id)
        if analog is None:
            raise HTTPException(404, "analog product not found")
    existing = db.execute(
        select(ForecastAnalogy).where(ForecastAnalogy.product_id == product.id)
    ).scalar()
    if existing:
        # one analogy per product: replace in place, back to active
        existing.analog_product_id = body.analog_product_id
        existing.monthly_estimate = body.monthly_estimate
        existing.rationale = body.rationale
        existing.source = body.source
        existing.status = AnalogyStatus.ACTIVE.value
        existing.created_by_id = authed.id
        analogy = existing
    else:
        analogy = ForecastAnalogy(
            product_id=product.id,
            analog_product_id=body.analog_product_id,
            monthly_estimate=body.monthly_estimate,
            rationale=body.rationale,
            source=body.source if body.source in ("manual", "llm") else "manual",
            created_by_id=authed.id,
        )
        db.add(analogy)
    db.commit()
    return AnalogyOut(
        id=analogy.id,
        product_id=product.id,
        product_sku=product.global_sku,
        product_name=product.name,
        analog_product_id=analogy.analog_product_id,
        analog_sku=analog.global_sku if analog else None,
        analog_name=analog.name if analog else None,
        monthly_estimate=analogy.monthly_estimate,
        rationale=analogy.rationale,
        source=analogy.source,
        status=analogy.status,
    )


@router.delete("/analogies/{analogy_id}", status_code=204)
def dismiss_analogy(analogy_id: int, db: Session = Depends(get_db)):
    analogy = db.get(ForecastAnalogy, analogy_id)
    if analogy is None:
        raise HTTPException(404, "analogy not found")
    analogy.status = AnalogyStatus.DISMISSED.value
    db.commit()


@router.post("/analogies/suggest", response_model=AnalogSuggestionOut)
def suggest_analogy(
    body: dict[str, int],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    product = db.get(Product, int(body.get("product_id") or 0))
    if product is None:
        raise HTTPException(404, "product not found")
    suggestion = suggest_analog(db, settings, product)
    if suggestion is None:
        raise HTTPException(404, "no candidate products with sales history")
    analog, rationale, source = suggestion
    return AnalogSuggestionOut(
        analog_product_id=analog.id,
        analog_sku=analog.global_sku,
        analog_name=analog.name,
        rationale=rationale,
        source=source,
    )
