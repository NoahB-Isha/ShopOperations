"""Phase-3 city-center & department ordering models.

A CenterOrder is what a volunteer places from their phone: quantities against
the center's granted catalogs (order lists), plus dept-orderable items for the
III Departments zone. The coordinator approves/adjusts/rejects it; approval
renders a DRAFT Odoo transfer (BWHSE→center location for field zones,
III-FLOOR-sourced for departments) exactly like the phase-2 transfer flow —
draft only, deep link shown, a human validates in Odoo. Orders whose lines
are all untracked (department water/snacks) never touch Odoo at all.

The reasonability assessment (rules + optional LLM polish) is computed at
placement and stored on the order — advisory only, never blocking.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
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


class CenterOrderStatus(str, enum.Enum):
    PENDING = "pending"  # placed; waiting on the coordinator
    APPROVED = "approved"  # coordinator approved; Odoo draft rendered (if applicable)
    SHIPPED = "shipped"  # the approval picking was validated in Odoo (polled)
    REJECTED = "rejected"
    CANCELLED = "cancelled"  # withdrawn by the orderer (or coordinator) while pending


class ReasonabilityLevel(str, enum.Enum):
    """Order-level severity for cheap list display. Advisory only."""

    NONE = ""  # not computed (old rows, engine failure)
    OK = "ok"
    INFO = "info"
    WARN = "warn"


class CenterOrder(Base, TimestampMixin):
    """A center's order, tracked against the draft Odoo transfer its approval
    renders. Statuses mirror the WhatsApp reality they replace: pending →
    approved/rejected, then shipped when the warehouse validates the picking."""

    __tablename__ = "center_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default=CenterOrderStatus.PENDING.value, index=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("center_orders.id"))

    # ---- coordinator decision ----
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")

    # ---- reasonability (advisory; computed at placement, refreshed on adjust) ----
    # {level, summary, source, order_badges: [...], lines: {product_id: [badge...]}}
    reasonability: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    reasonability_level: Mapped[str] = mapped_column(
        String(8), default=ReasonabilityLevel.NONE.value
    )

    # ---- the approval's Odoo draft transfer (rendered at approval) ----
    # 'none' + approved = legitimately no Odoo record (untracked dept lines).
    picking_status: Mapped[str] = mapped_column(String(12), default="none")
    picking_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-ORD-…
    picking_error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_picking_url: Mapped[str] = mapped_column(String(500), default="")
    source_location_key: Mapped[str] = mapped_column(String(20), default="")  # bwhse | floor
    # last time the app checked Odoo for the picking's validation (shipped poll)
    picking_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[CenterOrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="CenterOrderLine.id"
    )
    events: Mapped[list[CenterOrderEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="CenterOrderEvent.id"
    )

    @property
    def display_name(self) -> str:
        return f"ORD-{self.id}"


class CenterOrderLine(Base):
    __tablename__ = "center_order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_centerorder_product"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("center_orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    qty_requested: Mapped[float] = mapped_column(Float)
    qty_approved: Mapped[float | None] = mapped_column(Float)  # coordinator adjustment
    # price snapshot at placement so history totals survive catalog changes
    unit_price: Mapped[float] = mapped_column(Float, default=0)

    order: Mapped[CenterOrder] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship()

    @property
    def qty_final(self) -> float:
        return self.qty_requested if self.qty_approved is None else self.qty_approved


class CenterOrderEventKind(str, enum.Enum):
    STATUS = "status"
    NOTE = "note"
    LINES_EDITED = "lines_edited"
    ODOO = "odoo"  # the approval picking was rendered / went done
    REASONABILITY = "reasonability"
    NOTIFY = "notify"  # a notification left (or was simulated / failed)


class CenterOrderEvent(Base):
    """The shared timeline the orderer and coordinator both see."""

    __tablename__ = "center_order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("center_orders.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="")  # for kind == status
    note: Mapped[str] = mapped_column(Text, default="")
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    order: Mapped[CenterOrder] = relationship(back_populates="events")
