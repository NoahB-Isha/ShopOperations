"""The Odoo side of counting: applying an approved count.

Approving an item means "make Odoo say what the shelf says". Every approval
runs through the shared core in oos/adjust — with one addition: counts happen
at a location that isn't always the floor, so the adjustment carries the
counted location's Odoo id. A whole-submission approval ("Approve all") is
applied as a BATCH: the increases sum onto one addition picking and the
decreases onto one reduction picking (Noah, 2026-09-05), each posted once —
never one picking per item. The single-item approval is the same machinery
with a batch of one.

Counting is the app's ONE exception to "the app never validates" (Noah,
2026-08-22): a reviewer has already held the counted number against Odoo's and
approved it, so leaving 49 pickings for a second person to click Validate adds
a queue, not a judgement. The adjustment is still created as a draft first and
still carries its ILAPP-CNT- reference and deep link; `post_adjustment` then
posts it through `OdooWriter.validate_adjustment`, behind its own feature flag
(`write_validate_inventory_adjustment`). With the flag off, the old behaviour
is exactly what happens: a draft, and a link for a human. Rejected counts and
counts still waiting write nothing either way.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    CountEventKind,
    InventoryCountEntry,
    InventoryCountEvent,
    InventoryCountItem,
    OdooWriteOutcome,
    utcnow,
)
from ..odoo.errors import OdooWriteError
from ..odoo.writer import OdooWriter, WriterValidationError
from ..oos.adjust import AdjustResult, CountedLine, reconcile_counts
from . import ledger, locations, recent
from .ledger import LedgerRead
from .locations import CountLocation

log = logging.getLogger("counting.service")


def event(
    db: Session,
    item: InventoryCountItem | None,
    count_id: int,
    kind: CountEventKind,
    note: str,
    actor_user_id: int | None,
) -> None:
    db.add(
        InventoryCountEvent(
            count_id=count_id,
            item_id=item.id if item else None,
            kind=kind.value,
            note=note,
            actor_user_id=actor_user_id,
        )
    )


class StaleBaseline(Exception):
    """Odoo's quantity has moved since this count was taken, so the difference
    the counter measured is no longer the difference to apply. Phrased for the
    reviewer (HTTP 422); nothing is written and the item stays open."""


@dataclass(frozen=True)
class Baseline:
    """What Odoo said when the count was taken, against what it says now."""

    captured: float  # frozen on the entry at submit time
    live: float | None  # None = Odoo didn't answer
    source: str  # live | snapshot
    counted: float
    # another count of the same thing whose adjustment is written but not yet
    # posted — a collision the live read cannot see, because nothing has moved
    pending_name: str = ""
    pending_count_id: int | None = None
    # what Odoo's move ledger says happened here since the count (read only
    # when the number actually drifted — no need to ask otherwise)
    ledger: LedgerRead | None = None

    @property
    def drift(self) -> float:
        return round(float(self.live or 0) - self.captured, 3)

    @property
    def explained(self) -> bool:
        """The shelf moved for reasons that leave the count's finding intact —
        it sold, or stock came in. Odoo was wrong by (counted − captured) then,
        those movements are real, and applying that same difference on top
        lands exactly right. The ledger says so; we don't infer it."""
        return bool(self.ledger and self.ledger.explains(self.drift))

    @property
    def blocked(self) -> bool:
        """Applying this count's difference now would land on a number nobody
        counted — because Odoo has already been corrected, is about to be, or
        moved for a reason nothing accounts for."""
        if self.pending_count_id is not None:
            return True
        if not self.drifted or self.settled:
            return False
        return not self.explained

    @property
    def drifted(self) -> bool:
        """Only a LIVE read can prove drift. A snapshot fallback is the last
        sync's figure, which is routinely behind for reasons that have nothing
        to do with this count — treating it as drift would block approvals
        every time Odoo went quiet."""
        return self.source == "live" and self.live is not None and self.live != self.captured

    @property
    def settled(self) -> bool:
        """Odoo already says what the shelf says — whatever moved in between,
        there is nothing left to correct."""
        return self.live is not None and self.live == self.counted

    def message(self, product_name: str) -> str:
        if self.pending_count_id is not None:
            where = f" ({self.pending_name})" if self.pending_name else ""
            return (
                f"Count #{self.pending_count_id} already approved {product_name} here and its "
                f"adjustment{where} hasn't posted in Odoo yet. Approving this one too would "
                f"take a second difference off the same starting number, so nothing was "
                f"written. Settle that adjustment in Odoo first, or ask for a recount."
            )
        why = ""
        if self.ledger is not None and self.ledger.available:
            why = f" Odoo's ledger since then: {self.ledger.summary}."
            if self.ledger.adjustment_refs:
                why += (
                    f" That correction has already been made ("
                    f"{', '.join(self.ledger.adjustment_refs)}), so making it again "
                    f"would take it off twice."
                )
        elif self.ledger is not None:
            why = " Odoo wouldn't say what moved, so this can't be checked."
        return (
            f"Odoo's quantity for {product_name} has changed since this count was taken: "
            f"it said {self.captured:g} when counted, and says {float(self.live or 0):g} now."
            f"{why} Nothing was written — ask for a recount."
        )

    def applied_note(self) -> str:
        """What to record when the shelf moved but the count still stands."""
        if not self.drifted or self.ledger is None:
            return ""
        return (
            f" (Odoo moved from {self.captured:g} to {float(self.live or 0):g} since the "
            f"count — {self.ledger.summary} — so the count's difference still applies)"
        )


def read_baseline(
    db: Session,
    settings: Settings,
    item: InventoryCountItem,
    location: CountLocation,
    stock: tuple[dict[int, float], str] | None = None,
) -> Baseline:
    """Re-read Odoo NOW and hold it against the number frozen at count time.

    This is the guard the 2026-08-22 duplicates needed: the adjustment moves a
    DIFFERENCE (counted − what Odoo said then), and applying that difference is
    only correct while Odoo still says the same thing. Two counts of one shelf
    both measured against 9; both differences were applied to it; the shelf
    record ended at 0 on a product counted 3, 6 and 5.

    `stock` is a caller's already-done quantities_at read covering this item's
    product — the bulk approval reads its whole submission in ONE Odoo call
    (per-item reads are the shape that once tripped Odoo's rate limiter)."""
    entry = item.latest
    captured = float(entry.odoo_qty) if entry else 0.0
    counted = float(entry.counted_qty) if entry else 0.0
    qtys, source = stock or locations.quantities_at(db, settings, location, [item.product_id])
    pending = recent.pending_adjustment(
        db, location.key, item.product_id, exclude_count_id=item.count_id
    )
    baseline = Baseline(
        captured=captured,
        live=qtys.get(item.product_id),
        source=source,
        counted=counted,
        pending_name=pending.odoo_picking_name if pending else "",
        pending_count_id=pending.count_id if pending else None,
    )
    if not baseline.drifted or baseline.settled:
        return baseline  # nothing moved, or it moved to the counted number

    # It moved. WHY decides what happens next, and only Odoo's move ledger
    # knows: a sale leaves the count's finding intact, another count's
    # adjustment does not. One extra read, on the uncommon path.
    return replace(
        baseline,
        ledger=ledger.movements_since(
            settings,
            location.odoo_id or 0,
            item.product.odoo_product_id or 0,
            entry.created_at if entry else utcnow(),
        ),
    )


@dataclass
class AppliedCount:
    """What a batch apply did: a history sentence per item id, and one line
    per shared picking for the submission-level event."""

    notes: dict[int, str]
    pickings: list[str]


def apply_to_odoo(
    db: Session,
    settings: Settings,
    item: InventoryCountItem,
    location: CountLocation,
    actor_user_id: int | None,
    baseline: Baseline | None = None,
) -> str:
    """Apply ONE approved item — the batch machinery with a batch of one.
    Returns a sentence for the item's history."""
    return apply_all_to_odoo(db, settings, [(item, baseline)], location, actor_user_id).notes[
        item.id
    ]


def apply_all_to_odoo(
    db: Session,
    settings: Settings,
    approvals: list[tuple[InventoryCountItem, Baseline | None]],
    location: CountLocation,
    actor_user_id: int | None,
) -> AppliedCount:
    """Render and (flag permitting) post the adjustments for every approved
    item in ONE pass: the submission's increases sum onto one addition picking
    and its decreases onto one reduction picking (Noah, 2026-09-05), each
    validated once — never item-by-item pickings. Per-item guards (the
    baseline re-read) are the CALLER's job, done before any decision is
    recorded; per-item outcomes still land on each item.

    The count that gets applied is each item's LAST entry — the recount, when
    there was one — compared against the Odoo quantity captured with it. A
    failed write is recorded on the items, never raised: an approval is a
    DECISION and must not be lost because Odoo can't be written right now."""
    notes: dict[int, str] = {}
    to_write: list[tuple[InventoryCountItem, InventoryCountEntry, Baseline | None]] = []
    for item, baseline in approvals:
        entry = item.latest
        if entry is None:
            notes[item.id] = "nothing to apply — this item has no count on it"
            continue
        if baseline is not None and baseline.settled and baseline.drifted:
            # Odoo moved, and moved to exactly what was counted. Nothing to
            # write, and saying so beats a silent no-op.
            item.applied_qty = float(entry.counted_qty)
            item.picking_status = OdooWriteOutcome.NONE.value
            notes[item.id] = (
                f"Odoo already shows {entry.counted_qty:g} at {location.key} "
                f"(it was {baseline.captured:g} when counted) — approved with nothing to adjust"
            )
            continue
        to_write.append((item, entry, baseline))
    if not to_write:
        return AppliedCount(notes=notes, pickings=[])

    count_id = to_write[0][0].count_id
    results = reconcile_counts(
        db,
        settings,
        [
            CountedLine(
                product=item.product,
                odoo_qty=float(entry.odoo_qty),
                counted_qty=float(entry.counted_qty),
            )
            for item, entry, _ in to_write
        ],
        actor_user_id=actor_user_id,
        note=(
            f"Inventory count #{count_id} at {location.key} — "
            f"{len(to_write)} item(s) reviewed together"
        ),
        reference_kind="CNT",
        location_odoo_id=location.odoo_id,
    )

    # Record each item's share, then post each CREATED picking exactly once —
    # its outcome belongs to every item riding it.
    by_picking: dict[int, list[InventoryCountItem]] = {}
    for item, entry, _ in to_write:
        _record(item, entry, results[item.product_id])
        if item.picking_status == OdooWriteOutcome.CREATED.value and item.odoo_picking_id:
            by_picking.setdefault(int(item.odoo_picking_id), []).append(item)
    for members in by_picking.values():
        lead = members[0]
        post_adjustment(db, settings, lead, actor_user_id)
        for other in members[1:]:
            other.picking_status = lead.picking_status
            other.picking_error = lead.picking_error

    for item, entry, baseline in to_write:
        notes[item.id] = _item_note(item, entry, results[item.product_id], location, baseline)
    return AppliedCount(notes=notes, pickings=_picking_summary(to_write, results))


def _record(item: InventoryCountItem, entry: InventoryCountEntry, outcome: AdjustResult) -> None:
    """Copy a write outcome onto the item — the link between the decision and
    the stock record it changed. `applied_qty` means "what went to Odoo", so a
    failed write leaves it empty."""
    if outcome.status != OdooWriteOutcome.FAILED.value:
        item.applied_qty = float(entry.counted_qty)
    item.picking_status = outcome.status
    item.picking_reference = outcome.reference
    item.picking_error = outcome.error
    item.odoo_picking_id = outcome.picking_id
    item.odoo_picking_name = outcome.picking_name
    item.odoo_picking_url = outcome.url


def _item_note(
    item: InventoryCountItem,
    entry: InventoryCountEntry,
    outcome: AdjustResult,
    location: CountLocation,
    baseline: Baseline | None,
) -> str:
    """The sentence for this item's history, written AFTER posting so it can
    say how that went. `outcome.qty` is this item's own share of the shared
    picking."""
    drift_note = baseline.applied_note() if baseline is not None else ""
    if outcome.status == "none":
        return (
            f"Odoo already showed {entry.counted_qty:g} at {location.key} — approved with "
            "nothing to adjust"
        )
    if outcome.status == OdooWriteOutcome.SIMULATED.value:
        return (
            "adjustment simulated — the inventory-adjustment feature flag is off, so nothing "
            "was written to Odoo"
        )
    if outcome.status == OdooWriteOutcome.FAILED.value:
        return f"could not apply: {outcome.error}"
    if item.picking_status == OdooWriteOutcome.VALIDATED.value:
        return (
            f"{outcome.picking_name} posted in Odoo ({outcome.direction} "
            f"{outcome.qty:g} at {location.key}) — the count is live{drift_note}"
        )
    if item.picking_error:
        return (
            f"draft {outcome.picking_name} created ({outcome.direction} "
            f"{outcome.qty:g} at {location.key}), but posting it failed: "
            f"{item.picking_error} — validate it in Odoo"
        )
    return (
        f"draft {outcome.picking_name} created in Odoo ({outcome.direction} "
        f"{outcome.qty:g} at {location.key}) — validate it there{drift_note}"
    )


def _picking_summary(
    to_write: list[tuple[InventoryCountItem, InventoryCountEntry, Baseline | None]],
    results: dict[int, AdjustResult],
) -> list[str]:
    """One line per direction that was actually written — the submission
    event's answer to "where did all of this land?"."""
    grouped: dict[str, list[tuple[InventoryCountItem, AdjustResult]]] = {}
    for item, _, _ in to_write:
        outcome = results[item.product_id]
        if outcome.reference and outcome.direction in ("add", "reduce"):
            grouped.setdefault(outcome.reference, []).append((item, outcome))
    lines: list[str] = []
    for members in grouped.values():
        lead_item, lead = members[0]
        total = round(sum(o.qty for _, o in members), 3)
        what = (
            f"{'adding' if lead.direction == 'add' else 'removing'} {total:g} "
            f"across {len(members)} item(s)"
        )
        name = lead.picking_name or lead.reference
        if lead_item.picking_status == OdooWriteOutcome.VALIDATED.value:
            lines.append(f"{name} posted in Odoo, {what}")
        elif lead_item.picking_status == OdooWriteOutcome.CREATED.value:
            lines.append(f"draft {name} created, {what} — validate it in Odoo")
        elif lead_item.picking_status == OdooWriteOutcome.SIMULATED.value:
            lines.append(f"{what} — simulated, the adjustment flag is off")
        else:
            lines.append(f"{what} — failed: {lead_item.picking_error or lead.error}")
    return lines


def post_adjustment(
    db: Session,
    settings: Settings,
    item: InventoryCountItem,
    actor_user_id: int | None,
) -> bool:
    """Validate the adjustment this item already created. True when Odoo says
    'done'.

    Failure is recorded on the item and never raised: the approval decision is
    already made, and the draft is still there with its deep link — the worst
    case is the behaviour the app had before it posted anything. A gated flag
    is not a failure either; it leaves the item CREATED, which is exactly what
    'a human validates it' looks like."""
    if not item.odoo_picking_id:
        return False
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    try:
        result = writer.validate_adjustment(
            picking_odoo_id=int(item.odoo_picking_id),
            reference=item.picking_reference,
        )
    except (OdooWriteError, WriterValidationError) as e:
        item.picking_error = str(e)
        log.warning("count item %s: could not post %s: %s", item.id, item.odoo_picking_name, e)
        return False
    if result.dry_run:
        return False  # flag off / kill switch — the draft stands, as before
    item.picking_status = OdooWriteOutcome.VALIDATED.value
    item.picking_error = ""
    return True
