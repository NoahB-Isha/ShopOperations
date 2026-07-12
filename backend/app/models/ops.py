"""Phase-2 internal-flow models: order lists, BWHSE→Floor transfer requests
with staging reconciliation, the warehouse adjustments queue, and restock
state (daily sales buckets + the accumulator ported from ILscripts).
"""
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow
from .catalog import Product


# --------------------------------------------------------------- order lists
class OrderListStatus(str, enum.Enum):
    DRAFT = "draft"  # admin is editing
    PENDING_APPROVAL = "pending_approval"  # assigned, waiting on the coordinator
    APPROVED = "approved"  # approval ran (write outcome recorded separately)
    RETURNED = "returned"  # coordinator sent it back with a note


class OrderListWriteStatus(str, enum.Enum):
    """The honest write outcome shown in the UI. `simulated` covers every
    dry-run reason (kill switch, feature flag, fixture mode)."""

    NONE = "none"
    CREATED = "created"
    SIMULATED = "simulated"
    FAILED = "failed"


class OrderList(Base, TimestampMixin):
    """An admin-curated list of items destined for one center, approved by
    that center's zone coordinator. Approval creates a DRAFT internal
    transfer in Odoo (BWHSE → the center's location) via the OdooWriter."""

    __tablename__ = "order_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(20), default=OrderListStatus.DRAFT.value, index=True
    )

    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"), index=True)
    center_id: Mapped[int | None] = mapped_column(ForeignKey("centers.id"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cloned_from_id: Mapped[int | None] = mapped_column(ForeignKey("order_lists.id"))

    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    returned_note: Mapped[str] = mapped_column(Text, default="")

    # ---- Odoo write outcome (full history lives in odoo_write_audit) ----
    write_status: Mapped[str] = mapped_column(
        String(12), default=OrderListWriteStatus.NONE.value
    )
    write_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-OL-…
    write_dry_run_reason: Mapped[str] = mapped_column(String(30), default="")
    write_error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_url: Mapped[str] = mapped_column(String(500), default="")

    lines: Mapped[list[OrderListLine]] = relationship(
        back_populates="order_list",
        cascade="all, delete-orphan",
        order_by="OrderListLine.position",
    )


class OrderListLine(Base):
    __tablename__ = "order_list_lines"
    __table_args__ = (
        UniqueConstraint("order_list_id", "product_id", name="uq_orderlist_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_list_id: Mapped[int] = mapped_column(ForeignKey("order_lists.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty: Mapped[float] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer, default=0)

    order_list: Mapped[OrderList] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


# ------------------------------------------------- BWHSE→Floor transfer flow
class TransferRequestStatus(str, enum.Enum):
    REQUESTED = "requested"  # floor built the list
    PICKED = "picked"  # warehouse pulled stock (qty_sent fixed here)
    IN_STAGING = "in_staging"  # physically delivered to III-FLOOR-STAGING
    COUNTED = "counted"  # floor counted; discrepancies queued
    ON_FLOOR = "on_floor"  # shelved — flow complete
    CANCELLED = "cancelled"


class TransferRequest(Base, TimestampMixin):
    """A floor-initiated BWHSE→Floor stock request. One shared status
    timeline for both sides; the staging count reconciles sent vs counted and
    routes discrepancies into the warehouse adjustments queue."""

    __tablename__ = "transfer_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(
        String(20), default=TransferRequestStatus.REQUESTED.value, index=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    lines: Mapped[list[TransferRequestLine]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="TransferRequestLine.id"
    )
    events: Mapped[list[TransferEvent]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="TransferEvent.id"
    )
    odoo_drafts: Mapped[list[TransferOdooDraft]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="TransferOdooDraft.id"
    )


class TransferRequestLine(Base):
    __tablename__ = "transfer_request_lines"
    __table_args__ = (
        UniqueConstraint("request_id", "product_id", name="uq_transferreq_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("transfer_requests.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty_requested: Mapped[float] = mapped_column(Float)
    qty_sent: Mapped[float | None] = mapped_column(Float)  # set at pick time
    qty_counted: Mapped[float | None] = mapped_column(Float)  # set at staging count

    request: Mapped[TransferRequest] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class TransferEventKind(str, enum.Enum):
    STATUS = "status"  # status advanced (event.status = new status)
    NOTE = "note"
    LINES_EDITED = "lines_edited"
    ODOO_DRAFT = "odoo_draft"  # a draft transfer was rendered/created in Odoo
    DISCREPANCY = "discrepancy"  # staging count mismatch summary


class TransferEvent(Base):
    """The shared timeline both floor and warehouse see."""

    __tablename__ = "transfer_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("transfer_requests.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="")  # for kind == status
    note: Mapped[str] = mapped_column(Text, default="")
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    request: Mapped[TransferRequest] = relationship(back_populates="events")


class TransferOdooDraft(Base):
    """Outcome of one attempt to render/create a draft Odoo picking for a
    leg of the flow (BWHSE→STAGING at pick, STAGING→FLOOR after count).
    Attempts append; the newest row per leg is the current outcome."""

    __tablename__ = "transfer_odoo_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("transfer_requests.id"), index=True)
    leg: Mapped[str] = mapped_column(String(20))  # bwhse_staging | staging_floor
    status: Mapped[str] = mapped_column(String(12))  # created | simulated | failed
    reference: Mapped[str] = mapped_column(String(40), default="")
    dry_run_reason: Mapped[str] = mapped_column(String(30), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_url: Mapped[str] = mapped_column(String(500), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    request: Mapped[TransferRequest] = relationship(back_populates="odoo_drafts")


# --------------------------------------------------------- adjustments queue
class AdjustmentStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"  # warehouse fixed it in Odoo / physically
    DISMISSED = "dismissed"  # counted wrong, no action needed


class Adjustment(Base):
    """A stock discrepancy for the warehouse to review — today these vanish
    into WhatsApp; here they queue until someone closes them out."""

    __tablename__ = "adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfer_requests.id"), index=True
    )
    line_id: Mapped[int | None] = mapped_column(ForeignKey("transfer_request_lines.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty_expected: Mapped[float] = mapped_column(Float)  # what warehouse sent
    qty_counted: Mapped[float] = mapped_column(Float)  # what staging counted
    delta: Mapped[float] = mapped_column(Float)  # counted - expected
    status: Mapped[str] = mapped_column(
        String(12), default=AdjustmentStatus.OPEN.value, index=True
    )
    note: Mapped[str] = mapped_column(Text, default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped[Product] = relationship()


# ----------------------------------------------------------------- restock
class SalesDaily(Base):
    """Per-day sales units for the recent window only (restock math needs
    yesterday, not history — SalesMonthly keeps the long tail). Days are UTC
    dates from Odoo's date_order, matching the monthly buckets; rows older
    than the retention window are pruned by the sales sync."""

    __tablename__ = "sales_daily"
    __table_args__ = (
        UniqueConstraint("product_id", "day", "channel", name="uq_sales_daily_bucket"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    channel: Mapped[str] = mapped_column(String(20))  # pos | online
    units: Mapped[float] = mapped_column(Float, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RestockAccum(Base):
    """The ILscripts running counter: POS units accumulate per product day by
    day; crossing the threshold flags a RestockLine and resets the counter."""

    __tablename__ = "restock_accum"

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    accumulated: Mapped[float] = mapped_column(Float, default=0)
    last_flagged_on: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RestockFoldState(Base):
    """Single row: the last complete day folded into the accumulator. Keeps
    the fold idempotent no matter how often syncs or reads trigger it."""

    __tablename__ = "restock_fold_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    folded_through: Mapped[date | None] = mapped_column(Date)


class RestockLine(Base):
    """An open item on the floor restock checklist. Stays visible until
    checked off; new threshold crossings merge into an open line rather than
    duplicating it."""

    __tablename__ = "restock_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    list_type: Mapped[str] = mapped_column(String(10), default="floor", index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty: Mapped[float] = mapped_column(Float)
    flagged_on: Mapped[date] = mapped_column(Date)
    checked_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_off_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped[Product] = relationship()


class RestockCheckoff(Base):
    """A per-day tick on the computed back-stock list — keyed by day, so the
    checklist naturally resets every morning."""

    __tablename__ = "restock_checkoffs"
    __table_args__ = (
        UniqueConstraint("day", "list_type", "product_id", name="uq_restock_checkoff"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    list_type: Mapped[str] = mapped_column(String(10), default="back")
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    checked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
