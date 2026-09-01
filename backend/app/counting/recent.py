"""Has somebody just counted this?

Two counts of one product are only dangerous when the second is taken — or
judged, or applied — without knowing about the first. That is exactly what
happened on 2026-08-22: three people walked the same rack inside a minute,
each submission froze the SAME Odoo quantity (9), two of the reductions were
approved, and both came off that 9. The shelf record went to zero on a product
whose counts were 3, 6 and 5.

`inventory_count_items` has a unique constraint on (count_id, product_id), so
a product can't appear twice in ONE submission. Nothing stops two submissions,
and nothing should — a genuine recount is a different submission. What was
missing is that nobody could SEE the other count.

So this module answers one question for a set of products at a location: who
else counted this lately, and is their count still going to move stock? Two
answers, deliberately different in weight:

  * `applied=False` — the other count hasn't reached Odoo yet, so both counts
    are about to be measured against the same starting number. This is the
    warning; it holds no matter how old the count is, because an unapplied
    count from three weeks ago collides just as hard as one from this morning.
  * `applied=True` — it already moved stock, and Odoo's number now includes
    it. Not a hazard, just worth saying so the counter knows why the system
    quantity changed under them. Only recent ones are worth mentioning.

Rejected counts are neither: a reviewer threw them out and Odoo never heard.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    CountItemStatus,
    InventoryCount,
    InventoryCountItem,
    OdooWriteOutcome,
    User,
    utcnow,
)

# How far back an ALREADY-APPLIED count is still worth mentioning. Unapplied
# ones ignore this entirely — see the module docstring.
RECENT_DAYS = 7

# Applied means "Odoo has heard it": posted, or legitimately nothing to post
# (the count agreed with Odoo). A draft that failed to post is still pending
# as far as stock is concerned.
SETTLED_PICKING = {OdooWriteOutcome.VALIDATED.value, OdooWriteOutcome.NONE.value}


@dataclass(frozen=True)
class RecentCount:
    """Somebody else's count of the same product at the same location."""

    product_id: int
    count_id: int
    item_id: int
    counted_by: str
    counted_at: datetime
    counted_qty: float
    status: str  # the item's status (pending / recount_requested / approved)
    applied: bool  # has it reached Odoo?

    @property
    def note(self) -> str:
        """One line, in the vocabulary of whoever is standing at the shelf."""
        who = self.counted_by or "someone"
        qty = f"{self.counted_qty:g}"
        if not self.applied:
            waiting = (
                "waiting for a recount"
                if self.status == CountItemStatus.RECOUNT.value
                else "not applied to Odoo yet"
            )
            return f"{who} counted {qty} here — {waiting}"
        return f"{who} counted {qty} here, already applied to Odoo"


def recent_counts(
    db: Session,
    location_key: str,
    product_ids: list[int],
    *,
    exclude_count_id: int | None = None,
    now: datetime | None = None,
) -> dict[int, RecentCount]:
    """The one other count per product worth knowing about — for the counter
    adding products to a sheet. `exclude_count_id` keeps a submission from
    warning about itself."""
    found: dict[int, RecentCount] = {}
    for pid, candidates in _candidates(db, location_key, product_ids, now).items():
        best = _best(c for c in candidates if c.count_id != exclude_count_id)
        if best is not None:
            found[pid] = best
    return found


def for_items(
    db: Session,
    items: Sequence[InventoryCountItem],
    *,
    now: datetime | None = None,
) -> dict[int, RecentCount]:
    """item id -> the other count worth flagging, for a review screen.

    Done for the whole list at once: a queue is dozens of items, and asking
    per row would be dozens of round trips. Each item excludes its OWN
    submission, which is why this can't just be one shared product map."""
    by_location: dict[str, list[InventoryCountItem]] = {}
    for item in items:
        by_location.setdefault(item.count.location_key, []).append(item)

    out: dict[int, RecentCount] = {}
    for location_key, group in by_location.items():
        candidates = _candidates(
            db, location_key, [i.product_id for i in group], now
        )
        for item in group:
            best = _best(
                c
                for c in candidates.get(item.product_id, [])
                if c.count_id != item.count_id
            )
            if best is not None:
                out[item.id] = best
    return out


def pending_adjustment(
    db: Session,
    location_key: str,
    product_id: int,
    *,
    exclude_count_id: int | None = None,
) -> InventoryCountItem | None:
    """Another count of this product here that is APPROVED but whose
    adjustment hasn't posted yet.

    A live quant read can't see this one: the stock hasn't moved, so nothing
    has drifted — and yet that draft is still going to move it. Approving a
    second count on top would queue a second difference against the same
    starting number, which is the same collision one step later.

    A merely PENDING count is not a hazard: it has created nothing, and when
    somebody does approve it, ITS approval will see ours and stop there."""
    q = (
        select(InventoryCountItem)
        .join(InventoryCount, InventoryCount.id == InventoryCountItem.count_id)
        .options(selectinload(InventoryCountItem.count))
        .where(
            InventoryCountItem.product_id == product_id,
            InventoryCount.location_key == location_key,
            InventoryCountItem.status == CountItemStatus.APPROVED.value,
            InventoryCountItem.picking_status.notin_(tuple(SETTLED_PICKING)),
        )
        .order_by(InventoryCountItem.id.desc())
    )
    if exclude_count_id is not None:
        q = q.where(InventoryCountItem.count_id != exclude_count_id)
    return db.scalars(q).first()


def _best(candidates) -> RecentCount | None:
    winner: RecentCount | None = None
    for c in candidates:
        if winner is None or _outranks(c, winner):
            winner = c
    return winner


def _candidates(
    db: Session,
    location_key: str,
    product_ids: list[int],
    now: datetime | None = None,
) -> dict[int, list[RecentCount]]:
    """Every count worth mentioning, per product. One query."""
    if not product_ids:
        return {}
    now = now or utcnow()
    cutoff = now - timedelta(days=RECENT_DAYS)

    rows = db.scalars(
        select(InventoryCountItem)
        .join(InventoryCount, InventoryCount.id == InventoryCountItem.count_id)
        .options(
            selectinload(InventoryCountItem.entries),
            selectinload(InventoryCountItem.count),
        )
        .where(
            InventoryCountItem.product_id.in_(product_ids),
            InventoryCount.location_key == location_key,
            InventoryCountItem.status != CountItemStatus.REJECTED.value,
        )
        .order_by(InventoryCountItem.id)
    ).all()

    names = _counter_names(db, rows)
    found: dict[int, list[RecentCount]] = {}
    for item in rows:
        entry = item.latest
        if entry is None:
            continue
        applied = (
            item.status == CountItemStatus.APPROVED.value
            and item.picking_status in SETTLED_PICKING
        )
        counted_at = entry.created_at
        if counted_at is not None and counted_at.tzinfo is None:
            counted_at = counted_at.replace(tzinfo=now.tzinfo)
        if applied and (counted_at is None or counted_at < cutoff):
            continue  # settled and old — nothing useful to say
        candidate = RecentCount(
            product_id=item.product_id,
            count_id=item.count_id,
            item_id=item.id,
            counted_by=names.get(entry.counted_by_id or 0, ""),
            counted_at=counted_at or now,
            counted_qty=float(entry.counted_qty),
            status=item.status,
            applied=applied,
        )
        found.setdefault(item.product_id, []).append(candidate)
    return found


def _outranks(candidate: RecentCount, held: RecentCount) -> bool:
    """Which of two counts is the one to mention: the most recent, EXCEPT
    that an unapplied count always beats a settled one — it's the one that
    can still collide."""
    if candidate.applied != held.applied:
        return held.applied
    return candidate.counted_at > held.counted_at


def _counter_names(db: Session, items: Sequence[InventoryCountItem]) -> dict[int, str]:
    ids = {e.counted_by_id for item in items for e in item.entries if e.counted_by_id}
    if not ids:
        return {}
    return {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(select(User).where(User.id.in_(ids)))
    }
