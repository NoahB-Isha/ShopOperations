"""Outbound order emails — the placement email to Coimbatore (CSV + XLSX
attached) and per-vendor domestic POs.

Gate ladder, same shape as Odoo writes and notifications: NOTIFY_ENABLED kill
switch → `ordering_email_live` feature flag → SMTP configured → recipients
present. A gated send is recorded verbatim as SIMULATED — the rendered email
is stored on the thread either way, so dry-run mode shows exactly what would
have gone out (the phase-4 acceptance requirement).

Recipients are admin-editable data, not env config: the `ordering_email`
AppSetting row ({"india_to": [...], "cc": [...]}) for the quarterly import,
the vendor's contact/cc for domestic orders.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    AppSetting,
    EmailDirection,
    EmailStatus,
    FeatureFlag,
    OrderEmailMessage,
    PurchaseOrder,
    PurchaseOrderType,
    utcnow,
)
from ..notify.transport import SmtpEmailTransport, TransportError
from .export import line_qty, vendor_email_lines

log = logging.getLogger("ordering.email")

FLAG_ORDER_EMAIL = "ordering_email_live"
EMAIL_SETTING_KEY = "ordering_email"


def order_email_recipients(db: Session, order: PurchaseOrder) -> tuple[list[str], list[str]]:
    """(to, cc) for this order — vendor contact for domestic, the
    `ordering_email` AppSetting for imports."""
    def _split(raw: str) -> list[str]:
        return [e.strip() for e in (raw or "").split(",") if e.strip()]

    setting = db.get(AppSetting, EMAIL_SETTING_KEY)
    value = setting.value if setting and isinstance(setting.value, dict) else {}
    cc = [str(e).strip() for e in value.get("cc", []) if str(e).strip()]
    if order.order_type == PurchaseOrderType.DOMESTIC.value and order.vendor:
        return _split(order.vendor.contact_email), cc + _split(order.vendor.cc_emails)
    to = [str(e).strip() for e in value.get("india_to", []) if str(e).strip()]
    return to, cc


def gate_reason(db: Session, settings: Settings) -> str:
    """'' when the order email may go out live; otherwise why it's simulated."""
    if not settings.notify_enabled:
        return "kill switch (NOTIFY_ENABLED=false)"
    flag = db.get(FeatureFlag, FLAG_ORDER_EMAIL)
    if not (flag and flag.enabled):
        return f"feature flag {FLAG_ORDER_EMAIL} off"
    if not settings.smtp_host:
        return "smtp not configured (SMTP_HOST)"
    return ""


def compose_order_email(order: PurchaseOrder) -> tuple[str, str]:
    """Subject + plain-text body. The detail rides in the attached CSV/XLSX;
    the body is a human summary the vendor can read on a phone."""
    lines = [ln for ln in order.lines if line_qty(ln) > 0 and ln.line_status != "discontinued"]
    sea_units = sum(ln.final_sea_qty or 0 for ln in lines)
    air_units = sum(ln.final_air_qty or 0 for ln in lines)
    subject = f"Isha Life USA — Purchase Order {order.display_name} [{order.reference}]"
    body = [
        "Namaskaram,",
        "",
        f"Please find purchase order {order.display_name} attached (CSV and Excel).",
        "",
        f"  Line items: {len(lines)}",
        f"  Sea units:  {sea_units}",
        f"  Air units:  {air_units}",
        f"  Destination: {order.destination}",
    ]
    if order.order_type == PurchaseOrderType.DOMESTIC.value:
        vendor_name = (
            (order.vendor.contact_name or order.vendor.name) if order.vendor else "Vendor"
        )
        lines_text = [f"  {qty} × {name}" for name, qty in vendor_email_lines(order)]
        body = [
            f"Dear {vendor_name},",
            "",
            "We kindly request the following products:",
            "",
            *lines_text,
            "",
            "Please reply to this email with an invoice.",
            "",
            "Thank you,",
            "Isha Life USA — Shop Ops (powered by AI)",
            f"Reference: {order.reference}",
        ]
        subject = f"Isha Life USA — Order {order.display_name} [{order.reference}]"
        return subject, "\n".join(body)
    body += [
        "",
        "Reply to this email with any changes — quantities, substitutions,",
        "availability or shipping method. Replies are tracked against the order.",
        "",
        f"Reference: {order.reference}",
        "— Isha Life USA Shop Ops",
    ]
    return subject, "\n".join(body)


def dispatch_order_email(
    db: Session,
    settings: Settings,
    order: PurchaseOrder,
    attachments: list[tuple[str, bytes, str]],
) -> OrderEmailMessage:
    """Render, gate, (maybe) send, and record the placement email. Never
    raises — a failed live send is recorded as FAILED and surfaced in the
    timeline; the caller decides nothing based on delivery."""
    to, cc = order_email_recipients(db, order)
    subject, body = compose_order_email(order)
    reason = gate_reason(db, settings)
    message = OrderEmailMessage(
        order_id=order.id,
        direction=EmailDirection.OUT.value,
        sender=settings.smtp_from,
        recipients=", ".join(to + cc),
        subject=subject,
        body=body,
        occurred_at=utcnow(),
    )
    if not to:
        message.status = EmailStatus.SIMULATED.value
        message.body += "\n\n[no recipients configured — set them in Admin → Ordering]"
        db.add(message)
        db.flush()
        return message
    if reason:
        message.status = EmailStatus.SIMULATED.value
        message.body += f"\n\n[simulated: {reason}]"
        db.add(message)
        db.flush()
        return message
    try:
        message_id = SmtpEmailTransport(settings).send_with_attachments(
            to, subject, body, attachments, cc_emails=cc or None
        )
        message.message_id = message_id
        message.status = EmailStatus.SENT.value
    except TransportError as e:
        log.warning("order email for %s failed: %s", order.reference, e)
        message.status = EmailStatus.FAILED.value
        message.body += f"\n\n[send failed: {e}]"
    db.add(message)
    db.flush()
    return message
