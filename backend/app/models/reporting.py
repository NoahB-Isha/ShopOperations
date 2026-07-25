"""Phase-5 reporting models: stock history for the inventory time machine
and per-center sales rollups for the dashboard.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow


class StockSnapshot(Base):
    """Daily on-hand history per product per app location, appended by every
    stock sync (the last sync of a calendar day wins). Zero/negative rows are
    not stored — a product absent on a covered day was out of stock; whether a
    day is covered at all is `stock_snapshot_days`."""

    __tablename__ = "stock_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date", "product_id", "location_key", name="uq_stock_snapshot_bucket"
        ),
        Index("ix_stock_snapshots_product_date", "product_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    location_key: Mapped[str] = mapped_column(String(20))
    qty: Mapped[float] = mapped_column(Float, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StockSnapshotDay(Base):
    """One row per calendar day covered by stock history. Presence is the
    coverage marker: a day with a row but no StockSnapshot rows for a product
    means that product was genuinely at zero, not unknown. `source` says how
    the day was captured: 'sync' (live, as-it-happened) or 'reconstructed'
    (backfilled from Odoo's move ledger via the to_date context — real
    numbers, but computed after the fact)."""

    __tablename__ = "stock_snapshot_days"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="sync", server_default="sync")


class SalesCenterMonthly(Base):
    """Monthly city-center POS sales per center (pos.config-level rollup, no
    product dimension) — feeds the dashboard's centers panel and Q&A. Only
    configs classified `city_center` land here."""

    __tablename__ = "sales_center_monthly"
    __table_args__ = (
        UniqueConstraint("config_name", "year", "month", name="uq_sales_center_bucket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_name: Mapped[str] = mapped_column(String(120))  # Odoo pos.config name
    center_id: Mapped[int | None] = mapped_column(ForeignKey("centers.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)  # 1..12
    units: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float | None] = mapped_column(Float)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SalesOrdersMonthly(Base):
    """Monthly ORDER-level sales facts per channel (from pos/sale order
    headers): order counts, header revenue, and customer-loyalty splits.
    On this instance ~96% of POS orders and 100% of online orders carry a
    partner — walk-ins without one count in `orders` but not in the customer
    columns (verified live 2026-07-23)."""

    __tablename__ = "sales_orders_monthly"
    __table_args__ = (
        UniqueConstraint("year", "month", "channel", name="uq_sales_orders_bucket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)  # 1..12
    channel: Mapped[str] = mapped_column(String(20))  # SalesChannel value
    orders: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)  # header amount_total sum
    orders_with_customer: Mapped[int] = mapped_column(Integer, default=0)
    distinct_customers: Mapped[int] = mapped_column(Integer, default=0)
    new_customers: Mapped[int] = mapped_column(Integer, default=0)  # first order ever
    returning_customers: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CustomerFirstSeen(Base):
    """Earliest order date per (Odoo partner, channel) — the memory that
    makes new-vs-returning honest across incremental syncs. Only the partner
    id is stored, never names or contact details."""

    __tablename__ = "customer_first_seen"
    __table_args__ = (
        UniqueConstraint("partner_id", "channel", name="uq_customer_first_seen"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    partner_id: Mapped[int] = mapped_column(Integer, index=True)  # Odoo res.partner id
    channel: Mapped[str] = mapped_column(String(20))
    first_order_on: Mapped[date] = mapped_column(Date)


