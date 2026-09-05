"""Reconcile Odoo's quantity at a counted location to numbers a human counted.

One copy of the delta/ceiling/writer dance, serving inventory counting (its
only caller since the OOS board and the drawer's floor-count edit were
removed). The one door is `reconcile_counts`: a set of reviewed items becomes
at most TWO pickings — one addition carrying every increase and one reduction
carrying every decrease (Noah, 2026-09-05) — never one picking per item; a
reviewer's "Approve all" is one judgement about one shelf-walk, so Odoo gets
one record per direction, not thirty pickings for someone to chew through.
A single-item approval is simply a batch of one.

Nothing here validates anything in Odoo (counting/service posts through its
own flag), and nothing here invents an adjustment when the numbers already
agree.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import OdooWriteOutcome, Product
from ..odoo.errors import OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter, WriterValidationError

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
    picking_id: int | None = None
    url: str = ""
    error: str = ""


NO_CHANGE = AdjustResult(direction="none", qty=0.0, status="none")


@dataclass(frozen=True)
class CountedLine:
    """One reviewed item: what Odoo said at count time vs what the shelf said."""

    product: Product
    odoo_qty: float
    counted_qty: float


def _delta_of(product: Product, odoo_qty: float, counted_qty: float) -> float | None:
    """The signed difference an adjustment must move: higher than Odoo → an
    addition; lower → a reduction; None → nothing at all (the numbers agree,
    or the product isn't tracked), because a zero-quantity picking is noise
    for whoever has to review it. Raises AdjustTooLarge above the ceiling."""
    delta = round(float(counted_qty) - float(odoo_qty), 3)
    if abs(delta) > MAX_DELTA:
        raise AdjustTooLarge(
            f"That count ({float(counted_qty):g}) differs from Odoo's quantity "
            f"({float(odoo_qty):g}) by {abs(delta):g} — too large to adjust from here. "
            "Fix the quantity in Odoo instead."
        )
    if delta == 0 or not product.is_stock_tracked or not product.odoo_product_id:
        return None
    return delta


def _write_direction(
    db: Session,
    settings: Settings,
    *,
    actor_user_id: int | None,
    direction: str,  # add | reduce
    items: list[tuple[int, float]],  # (app product id, abs qty)
    note: str,
    reference: str,
    location_odoo_id: int | None,
) -> AdjustResult:
    """One writer call for one direction — every item rides the same picking.
    The result's qty is the group total; per-product callers overwrite it with
    their own line's share. WriterValidationError propagates (bad input is the
    caller's to explain); a refusal from Odoo comes back as a FAILED result."""
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    op = writer.create_inventory_addition if direction == "add" else writer.create_inventory_reduction
    total = round(sum(qty for _, qty in items), 3)
    try:
        result = op(
            lines=[{"product_id": pid, "qty": qty} for pid, qty in items],
            note=note[:120],
            reference=reference,
            location_odoo_id=location_odoo_id,
        )
    except OdooWriteError as e:
        return AdjustResult(
            direction=direction,
            qty=total,
            status=OdooWriteOutcome.FAILED.value,
            reference=reference,
            error=str(e),
        )
    return AdjustResult(
        direction=direction,
        qty=total,
        status=(
            OdooWriteOutcome.SIMULATED.value if result.dry_run else OdooWriteOutcome.CREATED.value
        ),
        reference=result.reference,
        picking_name=result.record_name,
        picking_id=result.record_ids[0] if result.record_ids else None,
        url=result.deep_link,
    )


def reconcile_counts(
    db: Session,
    settings: Settings,
    lines: list[CountedLine],
    actor_user_id: int | None,
    note: str,
    reference_kind: str = "CNT",
    location_odoo_id: int | None = None,
) -> dict[int, AdjustResult]:
    """Apply a whole submission in one motion: product id → what happened to
    its line, with every increase on ONE addition picking and every decrease
    on ONE reduction picking.

    A line that can't even be phrased (a gap over the ceiling) fails alone and
    keeps the rest writable; a direction that Odoo refuses fails together,
    because it IS one picking — no line of it is half-written."""
    results: dict[int, AdjustResult] = {}
    groups: dict[str, list[tuple[int, float]]] = {"add": [], "reduce": []}
    for line in lines:
        try:
            delta = _delta_of(line.product, line.odoo_qty, line.counted_qty)
        except AdjustTooLarge as e:
            gap = round(float(line.counted_qty) - float(line.odoo_qty), 3)
            results[line.product.id] = AdjustResult(
                direction="add" if gap > 0 else "reduce",
                qty=abs(gap),
                status=OdooWriteOutcome.FAILED.value,
                error=str(e),
            )
            continue
        if delta is None:
            results[line.product.id] = NO_CHANGE
            continue
        groups["add" if delta > 0 else "reduce"].append((line.product.id, abs(delta)))

    for direction, items in groups.items():
        if not items:
            continue
        try:
            shared = _write_direction(
                db,
                settings,
                actor_user_id=actor_user_id,
                direction=direction,
                items=items,
                note=note,
                reference=new_reference(reference_kind),
                location_odoo_id=location_odoo_id,
            )
        except WriterValidationError as e:
            # An unusable operation type or unmapped location sinks the whole
            # direction — honestly, on every line, rather than a 500.
            shared = AdjustResult(
                direction=direction,
                qty=round(sum(qty for _, qty in items), 3),
                status=OdooWriteOutcome.FAILED.value,
                error=str(e),
            )
        for pid, qty in items:
            results[pid] = replace(shared, qty=qty)
    return results
