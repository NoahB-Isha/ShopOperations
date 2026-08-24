"""The notification service — outbox rows in, honest delivery out.

Every notification is a row first (enqueued inside the same transaction as
the change it announces), then delivered best-effort: once inline right after
commit, and again by the worker's sweep until it lands or runs out of
attempts. WhatsApp is primary; email is the automatic fallback.

Like Odoo writes, live sends are GATED — the `notify_enabled` kill switch,
then per-channel feature flags (`notify_whatsapp_live`, `notify_email_live`),
then configuration. Gated sends are recorded as SIMULATED, never faked as
delivered; the order timeline and admin status page tell the truth either way.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Center,
    CenterOrder,
    CenterOrderEvent,
    CenterOrderEventKind,
    ChannelOutcome,
    FeatureFlag,
    Notification,
    NotificationKind,
    NotificationStatus,
    NotifyChannelState,
    Role,
    RoleAssignment,
    User,
    Zone,
    ZoneKind,
    utcnow,
)
from ..models.users import ZONE_SCOPED_ROLES
from .transport import (
    BridgeWhatsAppTransport,
    SmtpEmailTransport,
    TransportError,
    TransportHealth,
    WhatsAppTransport,
)

log = logging.getLogger("notify")

FLAG_WHATSAPP = "notify_whatsapp_live"
FLAG_EMAIL = "notify_email_live"

K = NotificationKind
O = ChannelOutcome  # noqa: E741 — reads well at call sites


# ------------------------------------------------------------------ helpers
def _flag_enabled(db: Session, key: str) -> bool:
    flag = db.get(FeatureFlag, key)
    return bool(flag and flag.enabled)


def _gate_reason(db: Session, settings: Settings, channel: str) -> str:
    """'' when the channel may send live; otherwise why it's simulated.
    Same ladder as Odoo writes: kill switch → feature flag → configuration."""
    if not settings.notify_enabled:
        return "kill switch (NOTIFY_ENABLED=false)"
    if channel == "whatsapp":
        if not _flag_enabled(db, FLAG_WHATSAPP):
            return f"feature flag {FLAG_WHATSAPP} off"
        if not settings.whatsapp_bridge_url:
            return "bridge not configured (WHATSAPP_BRIDGE_URL)"
    else:
        if not _flag_enabled(db, FLAG_EMAIL):
            return f"feature flag {FLAG_EMAIL} off"
        if not settings.smtp_host:
            return "smtp not configured (SMTP_HOST)"
    return ""


def _channel_state(db: Session, channel: str) -> NotifyChannelState:
    state = db.get(NotifyChannelState, channel)
    if state is None:
        state = NotifyChannelState(channel=channel)
        db.add(state)
        db.flush()
    return state


def _record_channel_result(db: Session, channel: str, ok: bool, error: str = "") -> None:
    state = _channel_state(db, channel)
    state.configured = True
    state.checked_at = utcnow()
    if ok:
        state.connected = True
        state.last_ok_at = utcnow()
        state.consecutive_failures = 0
        state.last_error = ""
    else:
        state.connected = False
        state.consecutive_failures += 1
        state.last_error = error[:2000]


# ---------------------------------------------------------------- templates
def _order_context(db: Session, order: CenterOrder) -> dict:
    center = db.get(Center, order.center_id)
    items = len(order.lines)
    units = sum(line.qty_final for line in order.lines)
    return {
        "order": order.display_name,
        "center": center.name if center else f"center {order.center_id}",
        "items": items,
        "units": round(units, 1),
    }


def _render(kind: str, ctx: dict, settings: Settings, note: str = "") -> tuple[str, str]:
    """(email subject, body). One plain-text body serves both channels —
    keep it template-simple for the official WhatsApp API later."""
    base = settings.app_public_url.rstrip("/")
    order, center = ctx["order"], ctx["center"]
    count = f"{ctx['items']} item(s), {ctx['units']:g} unit(s)"
    extra = f"\nNote: {note}" if note else ""
    if kind == K.ORDER_PLACED.value:
        return (
            f"New order {order} from {center}",
            f"🛒 New order {order} from {center} — {count}."
            f"{extra}\nReview: {base}/pending-orders",
        )
    if kind == K.ORDER_APPROVED.value:
        return (
            f"Order {order} approved",
            f"✅ Your order {order} for {center} was approved — {count}."
            f"{extra}\nHistory: {base}/order-history",
        )
    if kind == K.ORDER_REJECTED.value:
        return (
            f"Order {order} rejected",
            f"❌ Your order {order} for {center} was rejected."
            f"{extra}\nHistory: {base}/order-history",
        )
    if kind == K.ORDER_SHIPPED.value:
        return (
            f"Order {order} shipped",
            f"📦 Your order {order} for {center} has shipped — {count}."
            f"\nHistory: {base}/order-history",
        )
    return (f"Update on {order}", f"Update on {order} for {center}.{extra}")


def _recipients(db: Session, order: CenterOrder, kind: str) -> list[User]:
    if kind == K.ORDER_PLACED.value:
        center = db.get(Center, order.center_id)
        if center is None or center.zone_id is None:
            return []
        role_values = [r.value for r in ZONE_SCOPED_ROLES]
        users = list(
            db.scalars(
                select(User)
                .join(RoleAssignment, RoleAssignment.user_id == User.id)
                .where(
                    RoleAssignment.zone_id == center.zone_id,
                    RoleAssignment.role.in_(role_values),
                    User.is_active.is_(True),
                )
                .distinct()
            )
        )
        # A department's order is approved by whoever holds the add-on — a
        # shop team member with no zone on their assignment. Telling only the
        # review zone's coordinator would ping the person who no longer does
        # the job, and skip the person who does.
        zone = db.get(Zone, center.zone_id)
        if zone is not None and zone.kind == ZoneKind.DEPARTMENTS.value:
            approvers = db.scalars(
                select(User)
                .join(RoleAssignment, RoleAssignment.user_id == User.id)
                .where(
                    RoleAssignment.role == Role.DEPT_ORDER_APPROVER.value,
                    User.is_active.is_(True),
                )
                .distinct()
            )
            known = {u.id for u in users}
            users.extend(u for u in approvers if u.id not in known)
        return users
    creator = db.get(User, order.created_by_id) if order.created_by_id else None
    return [creator] if creator is not None and creator.is_active else []


# ------------------------------------------------------------------ enqueue
def enqueue_order_notifications(
    db: Session, settings: Settings, order: CenterOrder, kind: NotificationKind,
    note: str = "",
) -> list[Notification]:
    """Add outbox rows for everyone this event should reach. No commit — the
    caller's transaction owns atomicity. Deliver after commit with
    `deliver_now` (best-effort) or leave them for the worker sweep."""
    ctx = _order_context(db, order)
    subject, body = _render(kind.value, ctx, settings, note=note)
    rows: list[Notification] = []
    for user in _recipients(db, order, kind.value):
        rows.append(
            Notification(
                kind=kind.value,
                order_id=order.id,
                recipient_user_id=user.id,
                to_phone=user.phone or "",
                to_email=user.email or "",
                subject=subject,
                body=body,
                payload=ctx,
            )
        )
    db.add_all(rows)
    db.flush()
    return rows


# ----------------------------------------------------------------- delivery
def _try_whatsapp(
    db: Session, settings: Settings, notif: Notification,
    transport: WhatsAppTransport | None,
) -> None:
    if notif.whatsapp_outcome == O.SENT.value:
        return
    if not notif.to_phone:
        notif.whatsapp_outcome = O.SKIPPED.value
        notif.whatsapp_error = "no phone on file"
        return
    reason = _gate_reason(db, settings, "whatsapp")
    if reason:
        notif.whatsapp_outcome = O.SIMULATED.value
        notif.whatsapp_error = reason
        return
    transport = transport or BridgeWhatsAppTransport(settings)
    try:
        transport.send(notif.to_phone, notif.body)
    except TransportError as e:
        notif.whatsapp_outcome = O.FAILED.value
        notif.whatsapp_error = str(e)[:2000]
        _record_channel_result(db, "whatsapp", ok=False, error=str(e))
        return
    notif.whatsapp_outcome = O.SENT.value
    notif.whatsapp_error = ""
    _record_channel_result(db, "whatsapp", ok=True)


def _try_email(
    db: Session, settings: Settings, notif: Notification,
    transport: SmtpEmailTransport | None,
) -> None:
    if notif.email_outcome == O.SENT.value:
        return
    if not notif.to_email:
        notif.email_outcome = O.SKIPPED.value
        notif.email_error = "no email on file"
        return
    reason = _gate_reason(db, settings, "email")
    if reason:
        notif.email_outcome = O.SIMULATED.value
        notif.email_error = reason
        return
    transport = transport or SmtpEmailTransport(settings)
    try:
        transport.send(notif.to_email, notif.subject, notif.body)
    except TransportError as e:
        notif.email_outcome = O.FAILED.value
        notif.email_error = str(e)[:2000]
        _record_channel_result(db, "email", ok=False, error=str(e))
        return
    notif.email_outcome = O.SENT.value
    notif.email_error = ""
    _record_channel_result(db, "email", ok=True)


def _finalize(settings: Settings, notif: Notification) -> None:
    """Fold the two channel outcomes into the notification's status."""
    wa, em = notif.whatsapp_outcome, notif.email_outcome
    if O.SENT.value in (wa, em):
        notif.status = NotificationStatus.DELIVERED.value
        notif.final_channel = "whatsapp" if wa == O.SENT.value else "email"
        notif.delivered_at = utcnow()
        return
    if O.FAILED.value in (wa, em):
        # a live channel genuinely failed — worth retrying until the cap
        if notif.attempts >= settings.notify_max_attempts:
            notif.status = NotificationStatus.FAILED.value
        else:
            notif.status = NotificationStatus.PENDING.value
        return
    if O.SIMULATED.value in (wa, em):
        notif.status = NotificationStatus.SIMULATED.value
        notif.final_channel = "whatsapp" if wa == O.SIMULATED.value else "email"
        return
    # both skipped: recipient unreachable — admin follow-up, not a retry loop
    notif.status = NotificationStatus.SKIPPED.value


def _timeline_note(db: Session, notif: Notification) -> str:
    user = db.get(User, notif.recipient_user_id) if notif.recipient_user_id else None
    who = (user.display_name or user.email or f"user {user.id}") if user else "recipient"
    label = notif.kind.replace("_", " ")
    s = notif.status
    if s == NotificationStatus.DELIVERED.value:
        return f"{label} notification → {who} via {notif.final_channel}"
    if s == NotificationStatus.SIMULATED.value:
        return (
            f"{label} notification to {who} SIMULATED "
            f"({notif.whatsapp_error or notif.email_error}) — nothing was sent"
        )
    if s == NotificationStatus.SKIPPED.value:
        return f"{label} notification skipped — {who} has no phone or email on file"
    if s == NotificationStatus.FAILED.value:
        return (
            f"{label} notification to {who} FAILED after {notif.attempts} attempt(s): "
            f"{notif.whatsapp_error or notif.email_error}"
        )
    return ""


def attempt_delivery(
    db: Session, settings: Settings, notif: Notification,
    whatsapp: WhatsAppTransport | None = None,
    email: SmtpEmailTransport | None = None,
) -> Notification:
    """One delivery pass: WhatsApp first, email fallback, honest bookkeeping.
    Commits. Safe to call repeatedly — terminal notifications are left alone."""
    if notif.status not in (NotificationStatus.PENDING.value,):
        return notif
    notif.attempts += 1
    notif.last_attempt_at = utcnow()
    _try_whatsapp(db, settings, notif, whatsapp)
    if notif.whatsapp_outcome != O.SENT.value:
        _try_email(db, settings, notif, email)
    _finalize(settings, notif)

    if notif.status != NotificationStatus.PENDING.value and notif.order_id:
        note = _timeline_note(db, notif)
        if note:
            db.add(
                CenterOrderEvent(
                    order_id=notif.order_id,
                    kind=CenterOrderEventKind.NOTIFY.value,
                    note=note,
                )
            )
    db.commit()
    return notif


def deliver_now(
    db: Session, settings: Settings, notifications: list[Notification],
    whatsapp: WhatsAppTransport | None = None,
    email: SmtpEmailTransport | None = None,
) -> None:
    """Best-effort inline delivery right after the caller's commit. Never
    raises — stragglers stay pending for the worker sweep."""
    for notif in notifications:
        try:
            attempt_delivery(db, settings, notif, whatsapp=whatsapp, email=email)
        except Exception as e:  # noqa: BLE001 — delivery must never break the request
            log.warning("inline delivery of notification %s failed: %s", notif.id, e)
            db.rollback()


def _due(notif: Notification) -> bool:
    if notif.last_attempt_at is None:
        return True
    last = notif.last_attempt_at
    now = utcnow()
    if last.tzinfo is None:
        last = last.replace(tzinfo=now.tzinfo)
    # 2, 4, 8… minute backoff between live retries
    return now >= last + timedelta(minutes=2 ** min(notif.attempts, 6))


def deliver_pending(
    db: Session, settings: Settings, limit: int = 25,
    whatsapp: WhatsAppTransport | None = None,
    email: SmtpEmailTransport | None = None,
) -> int:
    """The worker sweep: retry every due pending notification. Returns how
    many reached a terminal state this pass."""
    rows = db.scalars(
        select(Notification)
        .where(Notification.status == NotificationStatus.PENDING.value)
        .order_by(Notification.id)
        .limit(limit)
    ).all()
    done = 0
    for notif in rows:
        if not _due(notif):
            continue
        attempt_delivery(db, settings, notif, whatsapp=whatsapp, email=email)
        if notif.status != NotificationStatus.PENDING.value:
            done += 1
    return done


# -------------------------------------------------------------- bridge probe
def probe_whatsapp_bridge(
    db: Session, settings: Settings, transport: WhatsAppTransport | None = None
) -> TransportHealth:
    """Worker loop: keep the admin status page's bridge health honest."""
    state = _channel_state(db, "whatsapp")
    state.checked_at = utcnow()
    if not settings.whatsapp_bridge_url:
        state.configured = False
        state.connected = False
        state.detail = "bridge not configured"
        db.commit()
        return TransportHealth(False, "not configured")
    transport = transport or BridgeWhatsAppTransport(settings)
    health = transport.check_health()
    state.configured = True
    state.connected = health.connected
    state.detail = health.detail
    if health.connected:
        state.last_ok_at = utcnow()
        state.consecutive_failures = 0
        state.last_error = ""
    else:
        state.consecutive_failures += 1
        state.last_error = health.detail[:2000]
    db.commit()
    return health


def email_channel_snapshot(db: Session, settings: Settings) -> None:
    """Email has no cheap liveness probe; reflect configuration honestly."""
    state = _channel_state(db, "email")
    state.configured = bool(settings.smtp_host)
    if not state.configured:
        state.connected = False
        state.detail = "smtp not configured"
    state.checked_at = utcnow()
    db.commit()


# -------------------------------------------------------- admin status page
def channels_payload(db: Session, settings: Settings) -> dict:
    """The admin status page's notification block: gates + per-channel health."""
    states = {s.channel: s for s in db.scalars(select(NotifyChannelState))}

    def _one(channel: str, configured: bool) -> dict:
        s = states.get(channel)
        return {
            "configured": configured,
            "live": not _gate_reason(db, settings, channel),
            "gate": _gate_reason(db, settings, channel) or None,
            "connected": bool(s.connected) if s else False,
            "detail": s.detail if s else "",
            "last_ok_at": s.last_ok_at.isoformat() if s and s.last_ok_at else None,
            "checked_at": s.checked_at.isoformat() if s and s.checked_at else None,
            "consecutive_failures": s.consecutive_failures if s else 0,
            "last_error": s.last_error if s else "",
        }

    pending = db.scalar(
        select(Notification.id)
        .where(Notification.status == NotificationStatus.PENDING.value)
        .limit(1)
    )
    return {
        "enabled": settings.notify_enabled,
        "whatsapp": _one("whatsapp", bool(settings.whatsapp_bridge_url)),
        "email": _one("email", bool(settings.smtp_host)),
        "has_pending": pending is not None,
    }


def recent_notifications(db: Session, limit: int = 50) -> list[dict]:
    rows = db.scalars(
        select(Notification).order_by(Notification.id.desc()).limit(min(limit, 200))
    ).all()
    users = {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(
            select(User).where(
                User.id.in_({r.recipient_user_id for r in rows if r.recipient_user_id} or {-1})
            )
        )
    }
    return [
        {
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "kind": r.kind,
            "order_id": r.order_id,
            "recipient": users.get(r.recipient_user_id or 0, ""),
            "status": r.status,
            "final_channel": r.final_channel,
            "whatsapp_outcome": r.whatsapp_outcome,
            "whatsapp_error": r.whatsapp_error,
            "email_outcome": r.email_outcome,
            "email_error": r.email_error,
            "attempts": r.attempts,
            "body": r.body,
        }
        for r in rows
    ]
