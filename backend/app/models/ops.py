"""Phase-2 internal-flow models.

Order lists are CATALOGS, not orders: curated sets of currently-active
products people order FROM. Admin curates and grants lists to zones; each
zone's coordinator decides which of those lists each of their centers can
order from. Quantities and approvals belong to actual orders (phase 3).

Transfer requests mirror Odoo: placing one immediately renders a DRAFT
BWHSE→STAGING picking (the request adopts the picking's name), warehouse
acknowledges ("working on it") and finishes ("sent"), then the app prepares
the STAGING→FLOOR count transfer for Odoo's barcode app and listens for its
validation. Sent-vs-counted mismatches land in the adjustments queue.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
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
class OrderList(Base, TimestampMixin):
    """A curated, orderable product set (no quantities — it's a menu)."""

    __tablename__ = "order_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    notes: Mapped[str] = mapped_column(Text, default="")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cloned_from_id: Mapped[int | None] = mapped_column(ForeignKey("order_lists.id"))

    lines: Mapped[list[OrderListLine]] = relationship(
        back_populates="order_list",
        cascade="all, delete-orphan",
        order_by="OrderListLine.position",
    )
    zone_grants: Mapped[list[OrderListZone]] = relationship(
        back_populates="order_list", cascade="all, delete-orphan"
    )
    center_grants: Mapped[list[OrderListCenter]] = relationship(
        back_populates="order_list", cascade="all, delete-orphan"
    )


class OrderListLine(Base):
    __tablename__ = "order_list_lines"
    __table_args__ = (
        UniqueConstraint("order_list_id", "product_id", name="uq_orderlist_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_list_id: Mapped[int] = mapped_column(ForeignKey("order_lists.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)

    order_list: Mapped[OrderList] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class OrderListZone(Base):
    """Admin grant: this zone's coordinator may use (and re-grant) the list."""

    __tablename__ = "order_list_zones"
    __table_args__ = (
        UniqueConstraint("order_list_id", "zone_id", name="uq_orderlist_zone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_list_id: Mapped[int] = mapped_column(ForeignKey("order_lists.id"), index=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id"), index=True)
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order_list: Mapped[OrderList] = relationship(back_populates="zone_grants")


class OrderListCenter(Base):
    """Coordinator grant: this center's orderers may order from the list
    (drives the phase-3 order form's catalog)."""

    __tablename__ = "order_list_centers"
    __table_args__ = (
        UniqueConstraint("order_list_id", "center_id", name="uq_orderlist_center"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_list_id: Mapped[int] = mapped_column(ForeignKey("order_lists.id"), index=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"), index=True)
    granted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order_list: Mapped[OrderList] = relationship(back_populates="center_grants")


# ------------------------------------------------- BWHSE→Floor transfer flow
class TransferRequestStatus(str, enum.Enum):
    REQUESTED = "requested"  # placed; Odoo draft rendered immediately
    WORKING = "working_on_it"  # warehouse has eyes on it
    SENT = "sent"  # warehouse done; stock physically at staging
    COUNTING = "counting"  # count transfer prepared; floor scans in Odoo barcode
    DONE = "done"  # count transfer validated in Odoo (or closed manually)
    CANCELLED = "cancelled"


class OdooWriteOutcome(str, enum.Enum):
    """Honest outcome of an app-rendered Odoo record."""

    NONE = "none"
    CREATED = "created"
    SIMULATED = "simulated"  # kill switch / feature flag / fixture mode
    FAILED = "failed"


class TransferRequest(Base, TimestampMixin):
    """A floor-initiated BWHSE→Floor stock request, tracked against its Odoo
    pickings. The BWHSE→STAGING draft is rendered at placement and gives the
    request its name; the STAGING→FLOOR count transfer is prepared at 'sent'
    and validated by a human in Odoo's barcode app."""

    __tablename__ = "transfer_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(
        String(20), default=TransferRequestStatus.REQUESTED.value, index=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    # ---- leg 1: BWHSE → STAGING (rendered at placement) ----
    picking_status: Mapped[str] = mapped_column(
        String(12), default=OdooWriteOutcome.NONE.value
    )
    picking_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-TR-…
    picking_error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_picking_url: Mapped[str] = mapped_column(String(500), default="")

    # ---- leg 2: STAGING → FLOOR count transfer (prepared at 'sent') ----
    count_status: Mapped[str] = mapped_column(
        String(12), default=OdooWriteOutcome.NONE.value
    )
    count_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-CNT-…
    count_error: Mapped[str] = mapped_column(Text, default="")
    count_picking_id: Mapped[int | None] = mapped_column(Integer)
    count_picking_name: Mapped[str] = mapped_column(String(80), default="")
    count_picking_url: Mapped[str] = mapped_column(String(500), default="")
    count_barcode_url: Mapped[str] = mapped_column(String(500), default="")
    # last time the app checked Odoo for the count picking's validation
    count_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[TransferRequestLine]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="TransferRequestLine.id"
    )
    events: Mapped[list[TransferEvent]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="TransferEvent.id"
    )

    @property
    def display_name(self) -> str:
        """The Odoo picking name IS the order's identity once it exists."""
        return self.odoo_picking_name or f"#{self.id}"


class TransferRequestLine(Base):
    __tablename__ = "transfer_request_lines"
    __table_args__ = (
        UniqueConstraint("request_id", "product_id", name="uq_transferreq_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("transfer_requests.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    qty_requested: Mapped[float] = mapped_column(Float)
    qty_sent: Mapped[float | None] = mapped_column(Float)  # read back from Odoo at 'sent'
    qty_counted: Mapped[float | None] = mapped_column(Float)  # from the validated count

    request: Mapped[TransferRequest] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()


class TransferEventKind(str, enum.Enum):
    STATUS = "status"
    NOTE = "note"
    LINES_EDITED = "lines_edited"
    ODOO = "odoo"  # a picking was rendered / refreshed / validated
    DISCREPANCY = "discrepancy"


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
    qty_counted: Mapped[float] = mapped_column(Float)  # what the count validated
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
