"""Reconcile Odoo's floor quantity to a number a human just counted.

One copy of this, used by two callers: the OOS board's "back in stock" (which
has always done it) and the Inventory Flow Manager editing an item's floor
count from the product drawer (Noah, 2026-08-18). Both mean the same thing —
"the shelf says N" — and both must produce the same kind of record: a DRAFT
adjustment picking for a human to validate in Odoo, never a silent write.

Nothing here validates anything in Odoo, and nothing here invents an
adjustment when the numbers already agree.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import OdooWriteOutcome, Product, StockLevel
from ..odoo.errors import OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter

# Odoo's own number is unbounded (a bad sync, or a fat-fingered count made in
# Odoo, can make it enormous), so the DIFFERENCE carries the ceiling: an
# adjustment this size is a data problem, not a shelf count.
MAX_DELTA = 100_000


class AdjustTooLarge(ValueError):
    """The gap is too big to be a count — HTTP 422, phrased for the counter."""


@dataclass
class AdjustResult:
    direction: str  # add | reduce | none
    qty: float
    status: str  # created | simulated | failed | none
    reference: str = ""
    picking_name: str = ""
    url: str = ""
    error: str = ""


NO_CHANGE = AdjustResult(direction="none", qty=0.0, status="none")


def floor_qty_of(db: Session, product_id: int) -> float:
    """What the app currently believes is on the floor (last stock sync)."""
    return float(
        db.scalar(
            select(StockLevel.qty).where(
                StockLevel.product_id == product_id, StockLevel.location_key == "floor"
            )
        )
        or 0.0
    )


def reconcile_floor_count(
    db: Session,
    settings: Settings,
    product: Product,
    floor_qty: float,
    counted_qty: float,
    actor_user_id: int | None,
    note: str,
    reference_kind: str = "ADJ",
) -> AdjustResult:
    """Render the draft that moves Odoo's floor figure to `counted_qty`.

    Higher than Odoo → an addition; lower → a reduction; equal → nothing at
    all (NO_CHANGE), because a zero-quantity picking is noise for whoever has
    to review it."""
    delta = round(float(counted_qty) - float(floor_qty), 3)
    if abs(delta) > MAX_DELTA:
        raise AdjustTooLarge(
            f"That count ({float(counted_qty):g}) differs from Odoo's floor quantity "
            f"({float(floor_qty):g}) by {abs(delta):g} — too large to adjust from here. "
            "Fix the quantity in Odoo instead."
        )
    if delta == 0 or not product.is_stock_tracked or not product.odoo_product_id:
        return NO_CHANGE

    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    direction = "add" if delta > 0 else "reduce"
    op = writer.create_inventory_addition if delta > 0 else writer.create_inventory_reduction
    reference = new_reference(reference_kind)
    try:
        result = op(product_id=product.id, qty=abs(delta), note=note[:120], reference=reference)
    except OdooWriteError as e:
        return AdjustResult(
            direction=direction,
            qty=abs(delta),
            status=OdooWriteOutcome.FAILED.value,
            reference=reference,
            error=str(e),
        )
    return AdjustResult(
        direction=direction,
        qty=abs(delta),
        status=(
            OdooWriteOutcome.SIMULATED.value if result.dry_run else OdooWriteOutcome.CREATED.value
        ),
        reference=result.reference,
        picking_name=result.record_name,
        url=result.deep_link,
    )
