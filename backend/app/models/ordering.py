"""Phase-4 purchase-ordering models — India imports, domestic vendor orders,
and the order-action timeline.

Design intent (carried from the ops reference project and the project brief):

  * A PurchaseOrder is an IMMUTABLE ORIGIN plus an APPEND-ONLY EVENT LOG.
    Line rows carry `origin_*` (frozen at creation), `suggested_/baseline_*`
    (the engine's frozen outputs, full detail in `suggestion_json`) and
    `final_*` (the living quantities that overrides and confirmed events
    move). The timeline reconstructs any historical state from the events.
  * Replies on the order's email thread are stored verbatim
    (OrderEmailMessage); the parser turns them into OrderEventProposal rows —
    each with the supporting quote and a confidence score — which a human
    confirms/edits/rejects. Only confirmation creates the real event and
    touches order state. An email is a fact to display, never a command.
  * One order fans out into OrderLegs ("Q3", "Q3 ADD", "Q3 ADD AIR" — the
    workbook's INC INV labelling), created at placement and by split events.
  * `destination` is the Canada seam: orders are for III (Tennessee) today;
    CAN marks the future USA→CAN flow (SO + transfer + customs paperwork)
    without building it out.
  * Exports and uploads live in OrderAttachment (bytes in Postgres — these
    are small spreadsheets); the CSV/XLSX actually emailed to Coimbatore is
    stored on the order forever, so "what did we send?" always has an answer.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JSONVariant, TimestampMixin, utcnow
from .catalog import Product


class VendorKind(str, enum.Enum):
    INDIA = "india"  # Isha Life Coimbatore (the quarterly import)
    US = "us"  # domestic vendors (Botanie soap, …)
    CANADA = "canada"
    OTHER = "other"


class Vendor(Base, TimestampMixin):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default=VendorKind.US.value)
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    contact_email: Mapped[str] = mapped_column(String(255), default="")
    cc_emails: Mapped[str] = mapped_column(String(500), default="")  # comma-separated
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(default=True)


class PurchaseOrderType(str, enum.Enum):
    IMPORT = "import"  # quarterly India order (sea/air engine)
    DOMESTIC = "domestic"  # per-vendor MOQ reorder


class PurchaseOrderStatus(str, enum.Enum):
    DRAFT = "draft"  # review table open; quantities editable
    PLACED = "placed"  # frozen + exported + order email dispatched (or dry-run)
    CLOSED = "closed"  # everything arrived / reconciled
    CANCELLED = "cancelled"


class OrderDestination(str, enum.Enum):
    III = "III"  # Isha Institute, Tennessee (the default)
    CAN = "CAN"  # Canada seam — modelled, not built out (project brief §7)


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)  # e.g. "Q3 2026"
    reference: Mapped[str] = mapped_column(String(40), unique=True)  # ILAPP-PO-…
    order_type: Mapped[str] = mapped_column(String(10), default=PurchaseOrderType.IMPORT.value)
    status: Mapped[str] = mapped_column(
        String(12), default=PurchaseOrderStatus.DRAFT.value, index=True
    )
    destination: Mapped[str] = mapped_column(String(3), default=OrderDestination.III.value)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), index=True)

    # Frozen context: the rules the suggestions were computed with, and when
    # the snapshot inputs were read (per-line detail in suggestion_json).
    rules_json: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_source: Mapped[str] = mapped_column(String(20), default="odoo")  # odoo | upload

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    placed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str] = mapped_column(Text, default="")

    vendor: Mapped[Vendor | None] = relationship()
    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PurchaseOrderLine.id"
    )
    events: Mapped[list[PurchaseOrderEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PurchaseOrderEvent.id"
    )
    legs: Mapped[list[OrderLeg]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderLeg.id"
    )

    @property
    def display_name(self) -> str:
        return self.name or f"PO-{self.id}"


class LineStatus(str, enum.Enum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"  # vendor can't supply it (confirmed event)
    SUBSTITUTED = "substituted"  # replaced by another SKU (confirmed event)


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (UniqueConstraint("order_id", "global_sku", name="uq_po_line_sku"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), index=True)
    global_sku: Mapped[str] = mapped_column(String(64), index=True)

    # engine outputs, frozen at order creation (full Suggestion in suggestion_json)
    suggested_sea_qty: Mapped[int] = mapped_column(Integer, default=0)
    suggested_air_qty: Mapped[int] = mapped_column(Integer, default=0)
    baseline_sea_qty: Mapped[int] = mapped_column(Integer, default=0)
    baseline_air_qty: Mapped[int] = mapped_column(Integer, default=0)
    # the immutable origin (what the order was created with)
    origin_sea_qty: Mapped[int] = mapped_column(Integer, default=0)
    origin_air_qty: Mapped[int] = mapped_column(Integer, default=0)
    # the living quantities: buyer overrides before placement, confirmed
    # timeline events after — the export always reflects these
    final_sea_qty: Mapped[int] = mapped_column(Integer, default=0)
    final_air_qty: Mapped[int] = mapped_column(Integer, default=0)

    line_status: Mapped[str] = mapped_column(String(14), default=LineStatus.ACTIVE.value)
    substitute_sku: Mapped[str] = mapped_column(String(64), default="")
    target_moh_used: Mapped[float] = mapped_column(Float, default=0)
    case_size: Mapped[int] = mapped_column(Integer, default=1)
    suggestion_json: Mapped[dict] = mapped_column(JSONVariant, default=dict)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship()


class OrderEventKind(str, enum.Enum):
    STATUS = "status"  # created / placed / closed / cancelled
    NOTE = "note"
    QTY_CHANGE = "qty_change"
    SUBSTITUTION = "substitution"
    DISCONTINUED = "discontinued"
    METHOD_CHANGE = "method_change"  # sea <-> air
    SPLIT = "split"  # a new shipment leg (Q3 ADD, Q3 ADD AIR…)
    AVAILABILITY = "availability"  # vendor confirmed availability / ETA
    EMAIL = "email"  # message sent or received on the thread
    ATTACHMENT = "attachment"


class PurchaseOrderEvent(Base):
    """Append-only lifecycle log — the timeline is built from these. Rows are
    only ever inserted; confirmed proposals, manual entries, and system
    actions (placement, exports, emails) all land here."""

    __tablename__ = "purchase_order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_lines.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="")  # for kind == status
    note: Mapped[str] = mapped_column(Text, default="")
    # structured deltas, e.g. {"sea": {"from": 500, "to": 200}} — shape per kind
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(120), default="")  # "vendor email", "system"
    # provenance when the event came from a parsed email
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("order_email_messages.id"))
    source_quote: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[PurchaseOrder] = relationship(back_populates="events")


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"  # applied (possibly with edited payload)
    REJECTED = "rejected"


class OrderEventProposal(Base, TimestampMixin):
    """A parser-suggested OrderLineEvent awaiting human review. LLM outputs
    that would change data are proposals requiring confirmation — never
    auto-applied (project brief, safety-critical)."""

    __tablename__ = "order_event_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("order_email_messages.id"))
    line_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_lines.id"))
    kind: Mapped[str] = mapped_column(String(20))  # OrderEventKind value
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    quote: Mapped[str] = mapped_column(Text, default="")  # supporting quote, verbatim
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    parsed_by: Mapped[str] = mapped_column(String(60), default="")  # model id | "heuristic"
    status: Mapped[str] = mapped_column(
        String(10), default=ProposalStatus.PENDING.value, index=True
    )
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_event_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_events.id"))


class EmailDirection(str, enum.Enum):
    IN = "in"
    OUT = "out"


class EmailStatus(str, enum.Enum):
    RECEIVED = "received"  # in
    PARSED = "parsed"  # in: proposals extracted
    SENT = "sent"  # out
    SIMULATED = "simulated"  # out: rendered under a gate, not delivered
    FAILED = "failed"  # out


class OrderEmailMessage(Base):
    """One message on an order's email thread — ingested replies (verbatim,
    untrusted input) and the app's own outbound order emails."""

    __tablename__ = "order_email_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))
    message_id: Mapped[str] = mapped_column(String(255), default="", index=True)  # RFC 5322 id
    sender: Mapped[str] = mapped_column(String(255), default="")
    recipients: Mapped[str] = mapped_column(String(500), default="")  # comma-separated
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default=EmailStatus.RECEIVED.value)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttachmentSource(str, enum.Enum):
    EXPORT = "export"  # generated by the app at placement (frozen artifact)
    UPLOAD = "upload"  # manually attached on the timeline
    EMAIL = "email"  # arrived on an ingested message


class OrderAttachment(Base):
    __tablename__ = "order_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("order_email_messages.id"))
    source: Mapped[str] = mapped_column(String(10), default=AttachmentSource.UPLOAD.value)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    note: Mapped[str] = mapped_column(Text, default="")
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LegMethod(str, enum.Enum):
    SEA = "sea"
    AIR = "air"


class LegStatus(str, enum.Enum):
    PLANNED = "planned"
    SHIPPED = "shipped"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"


class OrderLeg(Base, TimestampMixin):
    """A shipment leg an order fans out into — "Q3", "Q3 ADD", "Q3 ADD AIR"
    (the INC INV labelling convention). Placement creates the initial sea/air
    legs; confirmed split events add more."""

    __tablename__ = "purchase_order_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    label: Mapped[str] = mapped_column(String(60))
    method: Mapped[str] = mapped_column(String(4), default=LegMethod.SEA.value)
    status: Mapped[str] = mapped_column(String(10), default=LegStatus.PLANNED.value)
    eta: Mapped[date | None] = mapped_column()
    # {global_sku: qty} — which units ride this leg
    line_quantities: Mapped[dict] = mapped_column(JSONVariant, default=dict)

    order: Mapped[PurchaseOrder] = relationship(back_populates="legs")


class AnalogyStatus(str, enum.Enum):
    ACTIVE = "active"
    GRADUATED = "graduated"  # enough real history accumulated; analogy retired
    DISMISSED = "dismissed"


class ForecastAnalogy(Base, TimestampMixin):
    """Forecast-by-analogy for a product with no sales history: either 'sell
    like this other product' (LLM-proposed, human-confirmed) or a hardcoded
    monthly estimate. Auto-graduates once real data accumulates."""

    __tablename__ = "forecast_analogies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), unique=True)
    analog_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"))
    monthly_estimate: Mapped[float | None] = mapped_column(Float)  # used when no analog
    rationale: Mapped[str] = mapped_column(Text, default="")  # why this analog fits
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual | llm
    status: Mapped[str] = mapped_column(String(10), default=AnalogyStatus.ACTIVE.value)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    graduated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    """Admin-editable structured settings (JSON per key) — the runtime-editable
    counterpart to env-only `Settings`. Phase 4 uses `ordering_rules`
    (category rules overridable without code changes) and
    `ordering_email_recipients`."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
