"""The Odoo side of counting: applying an approved count.

Approving an item means "make Odoo say what the shelf says". That is the same
motion as the OOS board's back-in-stock and the floor-count edit, so it runs
through the same shared core (oos/adjust.reconcile_floor_count) rather than a
third copy — with one addition: counts happen at a location that isn't always
the floor, so the adjustment carries the counted location's Odoo id.

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
    InventoryCountEvent,
    InventoryCountItem,
    OdooWriteOutcome,
    utcnow,
)
from ..odoo.errors import OdooWriteError
from ..odoo.writer import OdooWriter, WriterValidationError
from ..oos.adjust import AdjustTooLarge, reconcile_floor_count
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
) -> Baseline:
    """Re-read Odoo NOW and hold it against the number frozen at count time.

    This is the guard the 2026-08-22 duplicates needed: the adjustment moves a
    DIFFERENCE (counted − what Odoo said then), and applying that difference is
    only correct while Odoo still says the same thing. Two counts of one shelf
    both measured against 9; both differences were applied to it; the shelf
    record ended at 0 on a product counted 3, 6 and 5."""
    entry = item.latest
    captured = float(entry.odoo_qty) if entry else 0.0
    counted = float(entry.counted_qty) if entry else 0.0
    qtys, source = locations.quantities_at(db, settings, location, [item.product_id])
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


def apply_to_odoo(
    db: Session,
    settings: Settings,
    item: InventoryCountItem,
    location: CountLocation,
    actor_user_id: int | None,
    baseline: Baseline | None = None,
) -> str:
    """Render the adjustment that moves this location's quantity to the counted
    one, and (flag permitting) post it. Returns a sentence for the item's
    history.

    The count that gets applied is the LAST entry — the recount, when there was
    one — compared against the Odoo quantity captured with it.

    `baseline` is the caller's already-done re-read (read_baseline). The
    caller does it BEFORE deciding, because a stale baseline should stop the
    approval rather than fail after it."""
    entry = item.latest
    if entry is None:
        return "nothing to apply — this item has no count on it"
    product = item.product
    if baseline is not None and baseline.settled and baseline.drifted:
        # Odoo moved, and moved to exactly what was counted. Nothing to write,
        # and saying so beats a silent no-op.
        item.applied_qty = float(entry.counted_qty)
        item.picking_status = OdooWriteOutcome.NONE.value
        return (
            f"Odoo already shows {entry.counted_qty:g} at {location.key} "
            f"(it was {baseline.captured:g} when counted) — approved with nothing to adjust"
        )
    try:
        outcome = reconcile_floor_count(
            db,
            settings,
            product,
            floor_qty=float(entry.odoo_qty),
            counted_qty=float(entry.counted_qty),
            actor_user_id=actor_user_id,
            note=(
                f"Inventory count #{item.count_id} at {location.key} — counted "
                f"{entry.counted_qty:g}, Odoo showed {entry.odoo_qty:g} — "
                f"{product.global_sku} {product.name}"
            ),
            reference_kind="CNT",
            location_odoo_id=location.odoo_id,
        )
    except (AdjustTooLarge, WriterValidationError) as e:
        # An approval is a DECISION; it must not be lost because Odoo can't be
        # written right now (an unmapped location, a gated flag, Odoo down).
        # Record the failure on the item so a reviewer can see it and retry,
        # rather than 422-ing the review away.
        item.picking_status = OdooWriteOutcome.FAILED.value
        item.picking_error = str(e)
        return f"could not apply: {e}"

    item.applied_qty = float(entry.counted_qty)
    item.picking_status = outcome.status
    item.picking_reference = outcome.reference
    item.picking_error = outcome.error
    item.odoo_picking_id = outcome.picking_id
    item.odoo_picking_name = outcome.picking_name
    item.odoo_picking_url = outcome.url

    drift_note = baseline.applied_note() if baseline is not None else ""
    if outcome.status == OdooWriteOutcome.CREATED.value:
        posted = post_adjustment(db, settings, item, actor_user_id)
        if posted:
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

    if outcome.status == "none":
        return (
            f"Odoo already showed {entry.counted_qty:g} at {location.key} — approved with "
            "nothing to adjust"
        )
    if outcome.status == OdooWriteOutcome.CREATED.value:
        return (
            f"draft {outcome.picking_name} created in Odoo ({outcome.direction} "
            f"{outcome.qty:g} at {location.key}) — validate it there{drift_note}"
        )
    if outcome.status == OdooWriteOutcome.SIMULATED.value:
        return (
            "adjustment simulated — the inventory-adjustment feature flag is off, so nothing "
            "was written to Odoo"
        )
    return f"Odoo refused the adjustment: {outcome.error}"


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
