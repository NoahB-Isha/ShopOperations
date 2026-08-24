"""Notification outbox + channel health.

WhatsApp is the primary channel (today: skubot's unofficial bridge, later the
official Cloud API — same transport interface). Email is the automatic
fallback. Every send is a row here first (outbox pattern): the API enqueues
inside the same transaction as the thing being notified about, attempts
delivery best-effort right after commit, and the worker sweeps up retries.

Like Odoo writes, live sends are gated (kill switch + per-channel feature
flags); gated sends are recorded honestly as 'simulated' rather than
pretending to deliver.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONVariant, utcnow


class NotificationKind(str, enum.Enum):
    ORDER_PLACED = "order_placed"  # → whoever reviews this center's orders
    ORDER_APPROVED = "order_approved"  # → the orderer
    ORDER_REJECTED = "order_rejected"  # → the orderer
    ORDER_SHIPPED = "order_shipped"  # → the orderer (picking validated in Odoo)


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"  # queued / retrying
    DELIVERED = "delivered"  # a live channel accepted it
    SIMULATED = "simulated"  # gates off — recorded, nothing actually sent
    FAILED = "failed"  # every live channel failed; retries exhausted
    SKIPPED = "skipped"  # recipient has no phone and no email on file


class ChannelOutcome(str, enum.Enum):
    """Per-channel honest outcome, same vocabulary as Odoo writes."""

    NONE = "none"  # not attempted yet
    SENT = "sent"
    SIMULATED = "simulated"  # kill switch / feature flag / not configured
    FAILED = "failed"
    SKIPPED = "skipped"  # no address for this channel


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("center_orders.id"), index=True)
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    to_phone: Mapped[str] = mapped_column(String(40), default="")  # resolved at enqueue
    to_email: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(200), default="")  # email subject line
    body: Mapped[str] = mapped_column(Text, default="")  # one text, both channels

    status: Mapped[str] = mapped_column(
        String(12), default=NotificationStatus.PENDING.value, index=True
    )
    final_channel: Mapped[str] = mapped_column(String(10), default="")  # whatsapp | email
    whatsapp_outcome: Mapped[str] = mapped_column(String(10), default=ChannelOutcome.NONE.value)
    whatsapp_error: Mapped[str] = mapped_column(Text, default="")
    email_outcome: Mapped[str] = mapped_column(String(10), default=ChannelOutcome.NONE.value)
    email_error: Mapped[str] = mapped_column(Text, default="")

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)  # render context


class NotifyChannelState(Base):
    """One row per channel ('whatsapp', 'email') — the admin status page's
    honest view of delivery health. The worker probes the WhatsApp bridge on
    its loop; email state updates opportunistically on sends."""

    __tablename__ = "notify_channel_state"

    channel: Mapped[str] = mapped_column(String(20), primary_key=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(Text, default="")  # bridge-reported state / last error
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
