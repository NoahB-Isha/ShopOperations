"""The Odoo side of counting: applying an approved count.

Approving an item means "make Odoo say what the shelf says". That is the same
motion as the OOS board's back-in-stock and the floor-count edit, so it runs
through the same shared core (oos/adjust.reconcile_floor_count) rather than a
third copy — with one addition: counts happen at a location that isn't always
the floor, so the adjustment carries the counted location's Odoo id.

As everywhere else in this app: the adjustment is a DRAFT, a human validates
it in Odoo, and the item records the link. Rejected counts and counts still
waiting write nothing.
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
from ..odoo.writer import WriterValidationError
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
    item.odoo_picking_name = outcome.picking_name
    item.odoo_picking_url = outcome.url

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
