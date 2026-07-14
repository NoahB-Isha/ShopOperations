"""Notifications: outbox honesty, gating, WhatsApp→email fallback, retries,
and the bridge health the admin status page shows.

No network anywhere — fake transports stand in for the bridge and SMTP.
"""
from __future__ import annotations

from datetime import timedelta

from app.models import (
    CenterOrder,
    CenterOrderEvent,
    CenterOrderLine,
    NotificationKind,
    NotificationStatus,
    NotifyChannelState,
    Role,
    utcnow,
)
from app.notify import service as notify
from app.notify.transport import TransportError, TransportHealth
from sqlalchemy import select

from .util import mk_center, mk_product, mk_user, mk_zone, set_flag

K = NotificationKind


class FakeWhatsApp:
    name = "fake-whatsapp"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    def send(self, to_phone: str, text: str) -> None:
        if self.fail:
            raise TransportError("bridge session dropped")
        self.sent.append((to_phone, text))

    def check_health(self) -> TransportHealth:
        return TransportHealth(connected=not self.fail, detail="fake")


class FakeEmail:
    name = "fake-smtp"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to_email: str, subject: str, text: str) -> None:
        if self.fail:
            raise TransportError("smtp refused")
        self.sent.append((to_email, subject, text))


def _order(db, *, phone="+15550001111", email="orderer@test.io"):
    zone = mk_zone(db, "Zone N")
    other = mk_zone(db, "Zone F")
    center = mk_center(db, "Nashville", zone_id=zone.id)
    orderer = mk_user(db, email, (Role.CENTER_ORDERER, None, center.id))
    orderer.phone = phone
    coord = mk_user(db, "coord@test.io", (Role.ZONE_COORDINATOR, zone.id, None))
    coord.phone = "+15550002222"
    mk_user(db, "farcoord@test.io", (Role.ZONE_COORDINATOR, other.id, None))
    p = mk_product(db, "SKU1", "Copper Bottle", odoo_id=11)
    order = CenterOrder(center_id=center.id, created_by_id=orderer.id,
                        source_location_key="bwhse")
    db.add(order)
    db.flush()
    db.add(CenterOrderLine(order_id=order.id, product_id=p.id,
                           qty_requested=4, unit_price=25))
    db.commit()
    db.refresh(order)
    return order, orderer, coord


def _live_settings(settings, **extra):
    """Settings where both channels are configured (flags still rule)."""
    return settings.model_copy(
        update={"whatsapp_bridge_url": "http://bridge.test",
                "smtp_host": "smtp.test", **extra}
    )


# ------------------------------------------------------------------ enqueue
def test_placed_goes_to_the_zones_coordinators_only(db, settings_env):
    order, _orderer, coord = _order(db)
    rows = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_PLACED)
    db.commit()
    assert [r.recipient_user_id for r in rows] == [coord.id]
    assert "ORD-" in rows[0].body and "Nashville" in rows[0].body
    assert rows[0].to_phone == "+15550002222"


def test_decisions_go_to_the_creator_with_the_note(db, settings_env):
    order, orderer, _coord = _order(db)
    rows = notify.enqueue_order_notifications(
        db, settings_env, order, K.ORDER_REJECTED, note="try after the festival"
    )
    db.commit()
    assert [r.recipient_user_id for r in rows] == [orderer.id]
    assert "try after the festival" in rows[0].body
    assert "❌" in rows[0].body


# ------------------------------------------------------------------- gating
def test_flags_off_records_simulated_and_a_timeline_event(db, settings_env):
    order, *_ = _order(db)
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    live = _live_settings(settings_env)  # configured, but flags are off
    notify.attempt_delivery(db, live, n)
    assert n.status == NotificationStatus.SIMULATED.value
    assert n.whatsapp_outcome == "simulated"
    assert "feature flag" in n.whatsapp_error
    events = db.scalars(
        select(CenterOrderEvent).where(CenterOrderEvent.order_id == order.id)
    ).all()
    assert any("SIMULATED" in e.note for e in events if e.kind == "notify")


def test_kill_switch_beats_flags(db, settings_env):
    order, *_ = _order(db)
    set_flag(db, notify.FLAG_WHATSAPP, True)
    set_flag(db, notify.FLAG_EMAIL, True)
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    dead = _live_settings(settings_env, notify_enabled=False)
    notify.attempt_delivery(db, dead, n, whatsapp=FakeWhatsApp(), email=FakeEmail())
    assert n.status == NotificationStatus.SIMULATED.value
    assert "kill switch" in n.whatsapp_error


# ----------------------------------------------------------- live + fallback
def test_whatsapp_delivers_and_email_is_never_touched(db, settings_env):
    order, *_ = _order(db)
    set_flag(db, notify.FLAG_WHATSAPP, True)
    set_flag(db, notify.FLAG_EMAIL, True)
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    wa, em = FakeWhatsApp(), FakeEmail()
    notify.attempt_delivery(db, _live_settings(settings_env), n, whatsapp=wa, email=em)
    assert n.status == NotificationStatus.DELIVERED.value
    assert n.final_channel == "whatsapp" and n.delivered_at is not None
    assert len(wa.sent) == 1 and em.sent == []
    assert n.email_outcome == "none"
    state = db.get(NotifyChannelState, "whatsapp")
    assert state.connected is True and state.consecutive_failures == 0
    # terminal rows are left alone on later passes
    notify.attempt_delivery(db, _live_settings(settings_env), n, whatsapp=wa, email=em)
    assert len(wa.sent) == 1 and n.attempts == 1


def test_bridge_failure_falls_back_to_email(db, settings_env):
    order, *_ = _order(db)
    set_flag(db, notify.FLAG_WHATSAPP, True)
    set_flag(db, notify.FLAG_EMAIL, True)
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    wa, em = FakeWhatsApp(fail=True), FakeEmail()
    notify.attempt_delivery(db, _live_settings(settings_env), n, whatsapp=wa, email=em)
    assert n.status == NotificationStatus.DELIVERED.value
    assert n.final_channel == "email"
    assert n.whatsapp_outcome == "failed" and "session dropped" in n.whatsapp_error
    assert len(em.sent) == 1 and em.sent[0][0] == "orderer@test.io"
    state = db.get(NotifyChannelState, "whatsapp")
    assert state.connected is False and state.consecutive_failures == 1


def test_no_phone_skips_whatsapp_and_uses_email(db, settings_env):
    order, *_ = _order(db, phone="")
    # the creator has no phone; strip it after user creation
    set_flag(db, notify.FLAG_WHATSAPP, True)
    set_flag(db, notify.FLAG_EMAIL, True)
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    assert n.to_phone == ""
    em = FakeEmail()
    notify.attempt_delivery(db, _live_settings(settings_env), n,
                            whatsapp=FakeWhatsApp(), email=em)
    assert n.whatsapp_outcome == "skipped"
    assert n.status == NotificationStatus.DELIVERED.value and n.final_channel == "email"


def test_unreachable_recipient_is_skipped_not_retried(db, settings_env):
    order, orderer, _ = _order(db, phone="")
    orderer.email = None
    db.commit()
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    notify.attempt_delivery(db, _live_settings(settings_env), n,
                            whatsapp=FakeWhatsApp(), email=FakeEmail())
    assert n.status == NotificationStatus.SKIPPED.value


def test_both_channels_failing_retries_until_the_cap(db, settings_env):
    order, *_ = _order(db)
    set_flag(db, notify.FLAG_WHATSAPP, True)
    set_flag(db, notify.FLAG_EMAIL, True)
    [n] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    db.commit()
    live = _live_settings(settings_env, notify_max_attempts=3)
    wa, em = FakeWhatsApp(fail=True), FakeEmail(fail=True)
    for expected_attempts in (1, 2):
        notify.attempt_delivery(db, live, n, whatsapp=wa, email=em)
        assert n.status == NotificationStatus.PENDING.value
        assert n.attempts == expected_attempts
    notify.attempt_delivery(db, live, n, whatsapp=wa, email=em)
    assert n.status == NotificationStatus.FAILED.value and n.attempts == 3


def test_sweep_respects_backoff(db, settings_env):
    order, *_ = _order(db)
    set_flag(db, notify.FLAG_WHATSAPP, True)
    [n1] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_APPROVED)
    [n2] = notify.enqueue_order_notifications(db, settings_env, order, K.ORDER_SHIPPED)
    # n1 just failed a moment ago (backoff says wait); n2 has never been tried
    n1.attempts = 1
    n1.last_attempt_at = utcnow() - timedelta(seconds=30)
    db.commit()
    wa = FakeWhatsApp()
    done = notify.deliver_pending(db, _live_settings(settings_env), whatsapp=wa,
                                  email=FakeEmail())
    assert done == 1  # only n2 was due
    assert n2.status == NotificationStatus.DELIVERED.value
    assert n1.status == NotificationStatus.PENDING.value


# ------------------------------------------------------------- bridge health
def test_probe_and_channels_payload(db, settings_env):
    notify.probe_whatsapp_bridge(db, settings_env)  # unconfigured
    state = db.get(NotifyChannelState, "whatsapp")
    assert state.configured is False and state.connected is False

    live = _live_settings(settings_env)
    notify.probe_whatsapp_bridge(db, live, transport=FakeWhatsApp())
    state = db.get(NotifyChannelState, "whatsapp")
    assert state.configured is True and state.connected is True

    notify.probe_whatsapp_bridge(db, live, transport=FakeWhatsApp(fail=True))
    state = db.get(NotifyChannelState, "whatsapp")
    assert state.connected is False and state.consecutive_failures == 1

    payload = notify.channels_payload(db, live)
    assert payload["enabled"] is True
    assert payload["whatsapp"]["configured"] is True
    assert payload["whatsapp"]["live"] is False  # flag still off
    assert "feature flag" in payload["whatsapp"]["gate"]
