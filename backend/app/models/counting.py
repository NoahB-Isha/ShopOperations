"""Inventory counting — count, submit, review, recount, apply.

The shape follows the spec's hardest rule: **never overwrite a previous
count**. So a counted product is three tables, not one:

    InventoryCount        one submission: a location and a moment
      InventoryCountItem  one product inside it, independently reviewable
        InventoryCountEntry  ONE act of counting — the original, then each
                             recount, append-only, each with its own counter,
                             its own quantity, and the Odoo quantity as it
                             stood when that count was made

An item's "current" numbers are therefore always its LAST entry, and its
history is every entry in order — which is what lets a reviewer see
Original → Recount 1 → Recount 2 side by side without anything being lost.

`InventoryCountEvent` is the other half of the audit: who reviewed what, when,
and why (a reason is mandatory on reject and recount), plus the record of the
Odoo adjustment an approval produced.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow
from .catalog import Product


class CountStatus(str, enum.Enum):
    """A submission's state, derived from its items (see flow.roll_up)."""

    PENDING = "pending"  # nothing reviewed yet
    PARTIAL = "partially_reviewed"  # some items decided, others not
    RECOUNT = "recount_required"  # at least one item is waiting on a recount
    COMPLETED = "completed"  # every item approved or rejected


class CountItemStatus(str, enum.Enum):
    PENDING = "pending"  # waiting for a reviewer
    RECOUNT = "recount_requested"  # a reviewer asked for another count
    APPROVED = "approved"  # counted quantity applied to Odoo
    REJECTED = "rejected"  # thrown out; Odoo untouched


class CountEventKind(str, enum.Enum):
    SUBMITTED = "submitted"
    RECOUNTED = "recounted"
    APPROVED = "approved"
    REJECTED = "rejected"
    RECOUNT_REQUESTED = "recount_requested"
    ODOO = "odoo"  # the adjustment draft this approval produced


class InventoryCount(Base, TimestampMixin):
    """One submission: a set of products counted at one location, at one time."""

    __tablename__ = "inventory_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # a countable location key (floor / bwhse / staging / staging2 / ship) —
    # see counting/locations.py, which owns what's countable and by whom
    location_key: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(24), default=CountStatus.PENDING.value, index=True)
    counted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list[InventoryCountItem]] = relationship(
        back_populates="count", cascade="all, delete-orphan", order_by="InventoryCountItem.id"
    )
    events: Mapped[list[InventoryCountEvent]] = relationship(
        back_populates="count", cascade="all, delete-orphan", order_by="InventoryCountEvent.id"
    )

    @property
    def display_name(self) -> str:
        return f"Count #{self.id}"


class InventoryCountItem(Base, TimestampMixin):
    """One product in a submission — the unit of review.

    A product may appear only ONCE per submission (the unique constraint is
    the backstop; the API merges instead of adding a second row), so "add it
    again" always means "change the quantity"."""

    __tablename__ = "inventory_count_items"
    __table_args__ = (UniqueConstraint("count_id", "product_id", name="uq_count_product"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("inventory_counts.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(24), default=CountItemStatus.PENDING.value, index=True
    )
    # who owes the next count, when a reviewer asked for one
    recount_assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # the Odoo adjustment an approval produced — the link between the decision
    # and the stock record it changed
    picking_status: Mapped[str] = mapped_column(String(12), default="none")
    picking_reference: Mapped[str] = mapped_column(String(40), default="")  # ILAPP-CNT…
    picking_error: Mapped[str] = mapped_column(Text, default="")
    odoo_picking_id: Mapped[int | None] = mapped_column(Integer)
    odoo_picking_name: Mapped[str] = mapped_column(String(80), default="")
    odoo_picking_url: Mapped[str] = mapped_column(String(500), default="")
    applied_qty: Mapped[float | None] = mapped_column(Float)  # what went to Odoo

    count: Mapped[InventoryCount] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()
    entries: Mapped[list[InventoryCountEntry]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="InventoryCountEntry.attempt"
    )
    events: Mapped[list[InventoryCountEvent]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="InventoryCountEvent.id"
    )

    @property
    def latest(self) -> InventoryCountEntry | None:
        """The count that stands right now — the last one performed."""
        return self.entries[-1] if self.entries else None


class InventoryCountEntry(Base):
    """ONE act of counting. Append-only: a recount adds a row, never edits one.

    `odoo_qty` is captured per entry rather than per item on purpose — a
    recount days later compares against whatever Odoo says THEN, and the
    reviewer needs to see both numbers as they were."""

    __tablename__ = "inventory_count_entries"
    __table_args__ = (UniqueConstraint("item_id", "attempt", name="uq_item_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_count_items.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)  # 1 = original, 2+ = recounts
    counted_qty: Mapped[float] = mapped_column(Float)
    odoo_qty: Mapped[float] = mapped_column(Float)  # system qty when counted
    # honest about where odoo_qty came from: a live quant read, or the last
    # stock sync when Odoo wasn't answering
    odoo_qty_source: Mapped[str] = mapped_column(String(12), default="live")
    counted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    # why this count was asked for (blank on the original)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[InventoryCountItem] = relationship(back_populates="entries")

    @property
    def delta(self) -> float:
        return round(float(self.counted_qty) - float(self.odoo_qty), 3)


class InventoryCountEvent(Base):
    """The review trail. Item-level when `item_id` is set, submission-level
    otherwise; a reason is required for rejections and recount requests."""

    __tablename__ = "inventory_count_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("inventory_counts.id"), index=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_count_items.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    note: Mapped[str] = mapped_column(Text, default="")
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    count: Mapped[InventoryCount] = relationship(back_populates="events")
    item: Mapped[InventoryCountItem | None] = relationship(back_populates="events")
