"""Order Action Tracking — ingested replies, proposal decisions, and manual
timeline entry. The glue between parser.py (proposals) and timeline.py
(append-only state changes).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AttachmentSource,
    EmailDirection,
    EmailStatus,
    OrderAttachment,
    OrderEmailMessage,
    OrderEventKind,
    OrderEventProposal,
    ProposalStatus,
    PurchaseOrder,
    PurchaseOrderEvent,
    User,
    utcnow,
)
from ..odoo.operations import APP_REF_PREFIX
from .parser import parse_reply_message
from .service import OrderingError
from .timeline import add_event, apply_event, find_line

log = logging.getLogger("ordering.tracking")

_REFERENCE_RE = re.compile(rf"{re.escape(APP_REF_PREFIX)}PO-[A-F0-9]+", re.IGNORECASE)


# ---------------------------------------------------------------- ingestion
def find_order_for_inbound(
    db: Session, subject: str, body: str, in_reply_to: str = ""
) -> PurchaseOrder | None:
    """Match an inbound email to its order: the References/In-Reply-To header
    against our sent Message-IDs first, then the ILAPP-PO-… reference token
    anywhere in the subject or body."""
    if in_reply_to:
        for token in re.findall(r"<[^>]+>", in_reply_to):
            sent = db.execute(
                select(OrderEmailMessage).where(
                    OrderEmailMessage.message_id == token,
                    OrderEmailMessage.direction == EmailDirection.OUT.value,
                )
            ).scalar()
            if sent:
                return db.get(PurchaseOrder, sent.order_id)
    match = _REFERENCE_RE.search(subject or "") or _REFERENCE_RE.search(body or "")
    if match:
        reference = match.group(0).upper()
        return db.execute(
            select(PurchaseOrder).where(PurchaseOrder.reference == reference)
        ).scalar()
    return None


def ingest_email(
    db: Session,
    settings: Settings,
    order: PurchaseOrder,
    *,
    sender: str,
    subject: str,
    body: str,
    rfc_message_id: str = "",
    occurred_at: datetime | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> tuple[OrderEmailMessage, list[OrderEventProposal]]:
    """Store one reply verbatim on the order's thread, log it on the
    timeline, and parse it into pending proposals."""
    if rfc_message_id:
        existing = db.execute(
            select(OrderEmailMessage).where(
                OrderEmailMessage.message_id == rfc_message_id,
                OrderEmailMessage.order_id == order.id,
            )
        ).scalar()
        if existing:  # idempotent: the mailbox poller may see a message twice
            return existing, []
    message = OrderEmailMessage(
        order_id=order.id,
        direction=EmailDirection.IN.value,
        message_id=rfc_message_id,
        sender=sender,
        subject=subject,
        body=body,
        status=EmailStatus.RECEIVED.value,
        occurred_at=occurred_at or utcnow(),
    )
    db.add(message)
    db.flush()
    for filename, data, content_type in attachments or []:
        db.add(
            OrderAttachment(
                order_id=order.id,
                message_id=message.id,
                source=AttachmentSource.EMAIL.value,
                filename=filename,
                content_type=content_type,
                size_bytes=len(data),
                data=data,
                note=f"attached to email from {sender}",
            )
        )
    proposals = parse_reply_message(db, settings, order, message)
    add_event(
        db,
        order,
        OrderEventKind.EMAIL,
        status="received",
        note=(
            f"Reply from {sender or 'unknown sender'}: “{subject or '(no subject)'}” — "
            f"{len(proposals)} proposal(s) parsed, awaiting review."
        ),
        source_message=message,
        actor_label=sender or "vendor email",
    )
    return message, proposals


# ----------------------------------------------------------- proposal review
def decide_proposal(
    db: Session,
    order: PurchaseOrder,
    proposal: OrderEventProposal,
    *,
    accept: bool,
    actor: User | None,
    edited_payload: dict | None = None,
    edited_line_id: int | None = None,
    note: str = "",
) -> PurchaseOrderEvent | None:
    """Confirm (optionally with edits) or reject one parsed proposal.
    Confirmation applies the event to order state; rejection only records the
    decision. Either way the proposal row keeps its history."""
    if proposal.status != ProposalStatus.PENDING.value:
        raise OrderingError(f"proposal already {proposal.status}")
    proposal.decided_by_id = actor.id if actor else None
    proposal.decided_at = utcnow()
    if not accept:
        proposal.status = ProposalStatus.REJECTED.value
        return None

    payload = dict(edited_payload if edited_payload is not None else proposal.payload)
    payload.pop("product_hint", None)  # matching scaffolding, not event data
    line_id = edited_line_id if edited_line_id is not None else proposal.line_id
    line = find_line(order, line_id=line_id) if line_id else None
    source_message = (
        db.get(OrderEmailMessage, proposal.message_id) if proposal.message_id else None
    )
    summary = apply_event(db, order, proposal.kind, payload, line)
    event = add_event(
        db,
        order,
        proposal.kind,
        line=line,
        note=note or summary or proposal.quote,
        payload=payload,
        actor=actor,
        source_message=source_message,
        quote=proposal.quote,
        confidence=proposal.confidence,
    )
    proposal.status = ProposalStatus.CONFIRMED.value
    proposal.applied_event_id = event.id
    if edited_payload is not None:
        proposal.payload = {**proposal.payload, "edited": payload}
    if edited_line_id is not None:
        proposal.line_id = edited_line_id
    return event


# ------------------------------------------------------------- manual entry
def add_manual_event(
    db: Session,
    order: PurchaseOrder,
    *,
    kind: str,
    actor: User | None,
    line_id: int | None = None,
    payload: dict | None = None,
    note: str = "",
) -> PurchaseOrderEvent:
    """A human types what the parser missed — same append-only machinery,
    applied immediately (humans don't propose to themselves)."""
    valid = {k.value for k in OrderEventKind} - {OrderEventKind.STATUS.value,
                                                 OrderEventKind.EMAIL.value}
    if kind not in valid:
        raise OrderingError(f"kind must be one of {sorted(valid)}")
    line = find_line(order, line_id=line_id) if line_id else None
    if line_id and line is None:
        raise OrderingError("that line isn't on this order")
    summary = apply_event(db, order, kind, dict(payload or {}), line)
    return add_event(
        db,
        order,
        kind,
        line=line,
        note=note or summary,
        payload=payload or {},
        actor=actor,
    )


def add_attachment(
    db: Session,
    order: PurchaseOrder,
    *,
    filename: str,
    data: bytes,
    content_type: str,
    actor: User | None,
    note: str = "",
) -> OrderAttachment:
    attachment = OrderAttachment(
        order_id=order.id,
        source=AttachmentSource.UPLOAD.value,
        filename=filename,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        data=data,
        note=note,
        uploaded_by_id=actor.id if actor else None,
    )
    db.add(attachment)
    db.flush()
    add_event(
        db,
        order,
        OrderEventKind.ATTACHMENT,
        note=note or f"Attached {filename}",
        payload={"attachment_id": attachment.id, "filename": filename},
        actor=actor,
    )
    return attachment
