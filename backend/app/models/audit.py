from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONVariant, utcnow


class OdooWriteAudit(Base):
    """One row per OdooWriter invocation — dry-runs and failures included.
    Because the app shares a Odoo account with a human, THIS log (plus the
    ILAPP- reference prefix on records) is the source of truth for what the
    app did, not Odoo's own logs."""

    __tablename__ = "odoo_write_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(60), index=True)
    reference: Mapped[str] = mapped_column(String(80), index=True, default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run_reason: Mapped[str] = mapped_column(String(60), default="")  # requested|kill_switch|feature_flag|fixture_mode
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    odoo_model: Mapped[str] = mapped_column(String(60), default="")
    odoo_record_ids: Mapped[list] = mapped_column(JSONVariant, default=list)
    request_payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    response: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class FeatureFlag(Base):
    """Write operations (and later email/WhatsApp sends) start life OFF and
    graduate only after their canary passes."""

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
