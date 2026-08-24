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

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    CountEventKind,
    InventoryCountEvent,
    InventoryCountItem,
    OdooWriteOutcome,
)
from ..odoo.errors import OdooWriteError
from ..odoo.writer import OdooWriter, WriterValidationError
from ..oos.adjust import AdjustTooLarge, reconcile_floor_count
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


def apply_to_odoo(
    db: Session,
    settings: Settings,
    item: InventoryCountItem,
    location: CountLocation,
    actor_user_id: int | None,
) -> str:
    """Render the draft adjustment that moves this location's quantity to the
    counted one. Returns a sentence for the item's history.

    The count that gets applied is the LAST entry — the recount, when there was
    one — compared against the Odoo quantity captured with it."""
    entry = item.latest
    if entry is None:
        return "nothing to apply — this item has no count on it"
    product = item.product
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

    if outcome.status == OdooWriteOutcome.CREATED.value:
        posted = post_adjustment(db, settings, item, actor_user_id)
        if posted:
            return (
                f"{outcome.picking_name} posted in Odoo ({outcome.direction} "
                f"{outcome.qty:g} at {location.key}) — the count is live"
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
            f"{outcome.qty:g} at {location.key}) — validate it there"
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
