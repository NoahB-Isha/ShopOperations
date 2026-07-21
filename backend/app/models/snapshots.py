from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONVariant, utcnow


class LocationKey(str, enum.Enum):
    BWHSE = "bwhse"
    FLOOR = "floor"
    STAGING = "staging"


# Odoo complete_name -> app location key. Several spellings may map to one
# key (production's staging is hyphenated; older fixtures used a space) — the
# stock sync requires every KEY to resolve, not every name. Quants are matched
# by SUBTREE: BWHSE keeps stock in bin sub-locations (III/Stock/BWHSE/A/1/1/1),
# verified against the live instance 2026-07-10.
ODOO_LOCATION_NAMES = {
    "III/Stock/BWHSE": LocationKey.BWHSE.value,
    "III/Stock/III-FLOOR": LocationKey.FLOOR.value,
    # production renamed staging ~2026-07-17 — the FLORR typo is THEIRS (live
    # location id 2360); keep every spelling so old data and a future
    # rename-back both keep resolving
    "III/Stock/III-FLORR-STAGING": LocationKey.STAGING.value,  # production (sic)
    "III/Stock/III-FLOOR-STAGING": LocationKey.STAGING.value,  # pre-07-17 production
    "III/Stock/III-FLOOR STAGING": LocationKey.STAGING.value,  # legacy fixtures
}


class OdooLocation(Base):
    """Odoo stock.location ids discovered by sync, mapped to app keys."""

    __tablename__ = "odoo_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    odoo_id: Mapped[int] = mapped_column(Integer, unique=True)
    complete_name: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(20), index=True)  # LocationKey or 'other'
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StockLevel(Base):
    """Current on-hand per product per app location. Replaced wholesale by each
    successful stock sync inside one transaction — a failed sync can never
    leave a half-written snapshot (phase-4 order freezing will add batches)."""

    __tablename__ = "stock_levels"
    __table_args__ = (
        UniqueConstraint("product_id", "location_key", name="uq_stock_product_location"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    location_key: Mapped[str] = mapped_column(String(20))
    qty: Mapped[float] = mapped_column(Float, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SalesMonthly(Base):
    """Monthly sales units per product per channel, maintained incrementally
    (full 24-month backfill once, then current-month refreshes)."""

    __tablename__ = "sales_monthly"
    __table_args__ = (
        UniqueConstraint("product_id", "year", "month", "channel", name="uq_sales_bucket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)  # 1..12
    channel: Mapped[str] = mapped_column(String(20))  # pos | online
    units: Mapped[float] = mapped_column(Float, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IncomingMove(Base):
    """Incoming stock moves (inbound shipments), replaced by each sync."""

    __tablename__ = "incoming_moves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    odoo_move_id: Mapped[int] = mapped_column(Integer, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    qty: Mapped[float] = mapped_column(Float, default=0)
    expected_date: Mapped[date | None] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(30), default="")
    picking_ref: Mapped[str] = mapped_column(String(120), default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


SYNC_DOMAINS = ("products", "stock", "sales", "incoming")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(20), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled|manual|seed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="running")  # running|success|failure
    rows: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="")  # live | fixture


class SyncState(Base):
    """One row per sync domain: freshness pointers + domain-specific state.
    A failed run updates last_attempt/last_error but never last_success — the
    last good snapshot keeps serving."""

    __tablename__ = "sync_state"

    domain: Mapped[str] = mapped_column(String(20), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")
    auth_failed: Mapped[bool] = mapped_column(default=False)  # surfaced loudly on status page
    extra: Mapped[dict] = mapped_column(JSONVariant, default=dict)  # e.g. sales backfill markers
