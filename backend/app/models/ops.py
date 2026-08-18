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

from .base import Base, JSONVariant, TimestampMixin, utcnow
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
    """Status KEYS are stable storage; the labels the app shows moved on
    2026-08-17 with the delivery-form rework (frontend TRANSFER_LABELS):
    working_on_it reads "Seen by warehouse", sent reads "Staged". Renaming
    the keys would rewrite every transition table, test and stored row for
    no user-visible gain — don't."""

    REQUESTED = "requested"  # placed; Odoo draft rendered immediately
    WORKING = "working_on_it"  # warehouse has acted on it in Odoo ("seen")
    SENT = "sent"  # warehouse pulled it; stock sits in Staging2 / on a delivery
    COUNTING = "counting"  # legacy/direct path: this request has its own count
    DONE = "done"  # its delivery landed on the floor (or closed manually)
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
    # last time the app checked the OUTBOUND picking's state (the two-way
    # sync listener: warehouse actions in Odoo drive the app workflow)
    picking_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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


class PalletTransfer(Base, TimestampMixin):
    """One consolidated staging2 → floor-staging move — a DELIVERY, in the
    UI's words: the pallet that carries several requests' goods to the floor
    at once.

    Three ways one comes into being, all landing in this table:

      * the warehouse makes the transfer in Odoo (the normal path since
        2026-08-17) and DECLARES it on the app's delivery form — the form
        writes `declared_by_id`, the request links and the discrepancy
        reasons;
      * the /staging2 'Send all' button renders it as a draft picking
        (ILAPP-PLT- reference) for a human to validate;
      * the app discovers a validated staging2 → floor-staging picking
        nobody declared (`poll_manual_pallets`) — recorded so it can't be
        processed twice, and flagged as needing its details.

    Validation in Odoo is the signal that goods reached floor staging: the
    linked requests close as done against this delivery, and ONE count
    transfer (floor staging → floor) is prepared for the whole pallet."""

    __tablename__ = "pallet_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # open|validated|counting|counted|cancelled
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    picking_status: Mapped[str] = mapped_column(
        String(12), default=OdooWriteOutcome.NONE.value
    )
    picking_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-PLT-…
    picking_error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_picking_url: Mapped[str] = mapped_column(String(500), default="")

    # what rode the pallet, frozen when it was rendered or declared:
    # [{product_id, sku, name, qty}]
    lines: Mapped[list] = mapped_column(JSONVariant, default=list)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # poll throttle for the validation listener
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- the delivery form (who said what rode this pallet) ----
    note: Mapped[str] = mapped_column(Text, default="")
    declared_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    declared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- ONE count transfer per delivery (floor staging → floor) ----
    count_status: Mapped[str] = mapped_column(
        String(12), default=OdooWriteOutcome.NONE.value
    )
    count_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-CNT-…
    count_error: Mapped[str] = mapped_column(Text, default="")
    count_picking_id: Mapped[int | None] = mapped_column(Integer)
    count_picking_name: Mapped[str] = mapped_column(String(80), default="")
    count_picking_url: Mapped[str] = mapped_column(String(500), default="")
    count_barcode_url: Mapped[str] = mapped_column(String(500), default="")
    count_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    request_links: Mapped[list[PalletRequestLink]] = relationship(
        back_populates="pallet",
        cascade="all, delete-orphan",
        order_by="PalletRequestLink.id",
    )
    discrepancies: Mapped[list[PalletDiscrepancy]] = relationship(
        back_populates="pallet",
        cascade="all, delete-orphan",
        order_by="PalletDiscrepancy.id",
    )

    @property
    def display_name(self) -> str:
        return self.odoo_picking_name or self.picking_reference or f"delivery #{self.id}"

    @property
    def is_declared(self) -> bool:
        """Someone told the app what rode it. An undeclared delivery can't
        close anybody's request — the app refuses to guess."""
        return self.declared_at is not None


class PalletRequestLink(Base):
    """Which transfer requests rode one delivery — the warehouse's answer to
    "which transfers are included in this bulk transfer?"."""

    __tablename__ = "pallet_requests"
    __table_args__ = (
        UniqueConstraint("pallet_id", "request_id", name="uq_pallet_request"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pallet_id: Mapped[int] = mapped_column(ForeignKey("pallet_transfers.id"), index=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("transfer_requests.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pallet: Mapped[PalletTransfer] = relationship(back_populates="request_links")
    request: Mapped[TransferRequest] = relationship()


class DiscrepancyReason(str, enum.Enum):
    """Why what's on the pallet differs from what was asked for. The
    warehouse picks any that apply; OTHER needs a note (enforced at the
    router), and a note is welcome on any of them."""

    NO_STOCK = "no_stock"  # "We don't have enough stock"
    FULL_CASE = "full_case"  # "Sending a full case"
    ANOTHER_TRANSFER = "another_transfer"  # "I'll include it in another transfer"
    OTHER = "other"


class PalletDiscrepancy(Base):
    """One product on a delivery whose quantity differs from what the linked
    requests asked for, with the warehouse's reason. Recorded per PRODUCT,
    not per request line: a pallet carries one pile of each item and the
    warehouse thinks about it that way. Requests that asked for the product
    are derivable from the links."""

    __tablename__ = "pallet_discrepancies"
    __table_args__ = (
        UniqueConstraint("pallet_id", "product_id", name="uq_pallet_discrepancy_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pallet_id: Mapped[int] = mapped_column(ForeignKey("pallet_transfers.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty_requested: Mapped[float] = mapped_column(Float)  # summed over linked requests
    qty_sent: Mapped[float] = mapped_column(Float)  # what's on the pallet
    reasons: Mapped[list] = mapped_column(JSONVariant, default=list)  # DiscrepancyReason values
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pallet: Mapped[PalletTransfer] = relationship(back_populates="discrepancies")
    product: Mapped[Product] = relationship()


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
    # a delivery's own count reconciles the whole pallet, so its adjustments
    # hang off the delivery instead of one request (request_id stays NULL)
    pallet_id: Mapped[int | None] = mapped_column(
        ForeignKey("pallet_transfers.id"), index=True
    )
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
    channel: Mapped[str] = mapped_column(String(20))  # SalesChannel value
    # NET units (sales minus same-day returns — restock math depends on net)
    units: Mapped[float] = mapped_column(Float, default=0)
    # gross revenue (tax-in); NULL on rows synced before amount capture
    amount: Mapped[float | None] = mapped_column(Float)
    # units on negative-qty lines (POS refunds), stored positive; NULL on rows
    # synced before returns capture — unknown, not zero. Gross sold = units + returned.
    returned_units: Mapped[float | None] = mapped_column(Float)
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
    the fold idempotent no matter how often syncs or reads trigger it. Also
    remembers the last "floor fully stocked" reset so the empty list can
    explain itself."""

    __tablename__ = "restock_fold_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    folded_through: Mapped[date | None] = mapped_column(Date)
    last_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reset_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


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
    # "Not today" — swiped away on the floor, back on the list tomorrow. Holds
    # the first date the line is visible again, so the row survives (with its
    # accumulated qty) instead of being deleted and re-flagged from zero.
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    # Aged out: nobody checked it off within restock_line_max_age_days. The row
    # stays (the history is worth keeping) but it leaves today's list, and the
    # product starts accumulating toward a fresh line.
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


# ------------------------------------------------------------ floor OOS marks
class FloorOosMark(Base, TimestampMixin):
    """The floor team's 'this shelf is actually empty' declaration. Marking a
    product renders a DRAFT inventory-reduction picking that removes whatever
    quantity Odoo still claims is on the floor — a human validates it in Odoo
    (data cleanup, the app never adjusts stock itself). A mark with nothing
    to remove (Odoo already says 0) is pure bookkeeping: picking stays 'none'."""

    __tablename__ = "floor_oos_marks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    qty_removed: Mapped[float] = mapped_column(Float, default=0)  # floor qty at mark time

    picking_status: Mapped[str] = mapped_column(String(12), default="none")
    picking_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-OOS-…
    picking_error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_picking_url: Mapped[str] = mapped_column(String(500), default="")

    product: Mapped[Product] = relationship()


# ------------------------------------------------------- floor team requests
class FloorRequestStatus(str, enum.Enum):
    OPEN = "open"  # waiting on the Inventory Flow Manager
    PICKED_UP = "picked_up"  # rolled into a transfer
    DISMISSED = "dismissed"  # looked at, not needed


class FloorRequest(Base, TimestampMixin):
    """"We need more of this" — raised by the Floor Team, who work the lists
    but can't create transfers themselves.

    It is deliberately NOT a transfer request: it's a person's ask, sitting
    next to the app's own computed suggestions on the Inventory Flow
    Manager's Suggested items page, where a human decides what actually gets
    pulled. One row per ask, always — two people asking for the same product
    are two entries, each carrying who raised it and how much they wanted."""

    __tablename__ = "floor_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(12), default=FloorRequestStatus.OPEN.value, index=True
    )
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # who took it off the board, and when — the floor can see their ask landed
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship()


class SuggestionSnooze(Base):
    """"Not this week" — a computed warehouse suggestion the Inventory Flow
    Manager swiped away.

    The app will keep finding this product as long as the numbers say so, and
    re-offering it every morning is noise once a human has judged it. One row
    per product, replaced on each swipe; the suggestion returns on its own
    when the date passes (the judgement was about this week, not forever —
    unlike a Floor Team ask, which a person can settle for good)."""

    __tablename__ = "suggestion_snoozes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True, index=True)
    snoozed_until: Mapped[date] = mapped_column(Date)  # first day it's visible again
    snoozed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    product: Mapped[Product] = relationship()
