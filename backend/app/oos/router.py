"""Floor out-of-stock board — data cleanup with teeth.

The list is honest about its two sources: products Odoo already shows at
zero on the floor (computed from the snapshot), and products the team MARKED
out because the shelf is empty no matter what Odoo says. Marking a product
with phantom floor stock renders a DRAFT picking on the inventory-reduction
operation type ("USA-III: Inventory Adj Reduction") removing that quantity —
a human validates it in Odoo; the app adjusts nothing itself.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..center_orders.catalog import expected_back_label, incoming_by_product
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    FloorOosMark,
    OdooWriteOutcome,
    Product,
    Role,
    StockLevel,
    User,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter

router = APIRouter(
    prefix="/oos",
    tags=["oos"],
    dependencies=[Depends(require_roles(Role.SHOPPE_FLOOR))],
)


# ------------------------------------------------------------------ schemas
class MarkPickingOut(BaseModel):
    status: str  # none | created | simulated | failed
    reference: str
    error: str
    picking_id: int | None
    picking_name: str
    url: str


class MarkOut(BaseModel):
    id: int
    note: str
    created_by: str
    created_at: datetime
    qty_removed: float
    picking: MarkPickingOut


class OosItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str  # the identifier the team actually uses; sku is the fallback
    name: str
    category: str
    floor_qty: float  # what Odoo currently claims
    bwhse_qty: float
    incoming_label: str  # "expected back mid-August" | "no restock scheduled yet"
    mark: MarkOut | None  # None = Odoo already says zero (computed row)


# ------------------------------------------------------------------ helpers
def _stock_pairs(db: Session, pids: set[int]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    if not pids:
        return out
    for pid, key, qty in db.execute(
        select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
            StockLevel.product_id.in_(pids)
        )
    ):
        out.setdefault(pid, {})[key] = float(qty)
    return out


def _mark_out(db: Session, mark: FloorOosMark) -> MarkOut:
    creator = db.get(User, mark.created_by_id) if mark.created_by_id else None
    return MarkOut(
        id=mark.id,
        note=mark.note,
        created_by=(creator.display_name or creator.email or "") if creator else "",
        created_at=mark.created_at,
        qty_removed=mark.qty_removed,
        picking=MarkPickingOut(
            status=mark.picking_status,
            reference=mark.picking_reference,
            error=mark.picking_error,
            picking_id=mark.odoo_picking_id,
            picking_name=mark.odoo_picking_name,
            url=mark.odoo_picking_url,
        ),
    )


def _item(
    db: Session, p: Product, stock: dict[str, float],
    incoming: list, today, mark: FloorOosMark | None,
) -> OosItemOut:
    dates = sorted(d for _, d in incoming if d is not None)
    return OosItemOut(
        product_id=p.id,
        sku=p.global_sku,
        barcode=p.barcode,
        name=p.name,
        category=p.category,
        floor_qty=stock.get("floor", 0.0),
        bwhse_qty=stock.get("bwhse", 0.0),
        incoming_label=expected_back_label(dates[0] if dates else None, today),
        mark=_mark_out(db, mark) if mark else None,
    )


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=list[OosItemOut])
def list_oos(
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> list[OosItemOut]:
    """Marked products first (newest on top), then everything Odoo already
    shows at zero on the floor."""
    today = utcnow().date()
    marks = db.scalars(
        select(FloorOosMark)
        .options(selectinload(FloorOosMark.product))
        .order_by(FloorOosMark.id.desc())
        .execution_options(populate_existing=True)
    ).all()
    marked_pids = {m.product_id for m in marks}

    zero_rows = db.execute(
        select(StockLevel.product_id)
        .join(Product, Product.id == StockLevel.product_id)
        .where(
            StockLevel.location_key == "floor",
            StockLevel.qty <= 0,
            Product.is_active.is_(True),
            Product.is_stock_tracked.is_(True),
        )
    )
    zero_pids = {pid for (pid,) in zero_rows} - marked_pids

    pids = marked_pids | zero_pids
    stock = _stock_pairs(db, pids)
    incoming = incoming_by_product(db, pids)
    products = {
        p.id: p for p in db.scalars(select(Product).where(Product.id.in_(pids or {-1})))
    }

    items: list[OosItemOut] = []
    for m in marks:
        p = products.get(m.product_id) or m.product
        items.append(_item(db, p, stock.get(p.id, {}), incoming.get(p.id, []), today, m))
    computed = [
        _item(db, products[pid], stock.get(pid, {}), incoming.get(pid, []), today, None)
        for pid in zero_pids
        if pid in products
    ]
    computed.sort(key=lambda i: (i.category or "~", i.name))
    return items + computed


class MarkIn(BaseModel):
    product_id: int
    note: str = ""


@router.post("", response_model=OosItemOut, status_code=201)
def mark_out_of_stock(
    body: MarkIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> OosItemOut:
    """'This shelf is actually empty.' Renders the draft reduction for
    whatever quantity Odoo still claims; with nothing to remove it's pure
    bookkeeping (picking stays 'none')."""
    product = db.get(Product, body.product_id)
    if product is None or not product.is_active:
        raise HTTPException(422, "Product not found or inactive.")
    existing = db.scalar(
        select(FloorOosMark).where(FloorOosMark.product_id == product.id)
    )
    floor_qty = float(
        db.scalar(
            select(StockLevel.qty).where(
                StockLevel.product_id == product.id, StockLevel.location_key == "floor"
            )
        )
        or 0.0
    )
    if existing is not None:
        raise HTTPException(
            409,
            f"'{product.name}' is already marked out of stock"
            + (
                f" — the reduction {existing.odoo_picking_name or existing.picking_reference} "
                "is still open in Odoo."
                if existing.picking_status == OdooWriteOutcome.CREATED.value
                else "."
            ),
        )

    mark = FloorOosMark(
        product_id=product.id,
        note=body.note.strip(),
        created_by_id=authed.id,
        qty_removed=max(0.0, floor_qty),
    )
    db.add(mark)
    db.flush()

    if floor_qty > 0 and product.is_stock_tracked and product.odoo_product_id:
        writer = OdooWriter(db, settings, actor_user_id=authed.id)
        reference = new_reference("OOS")
        mark.picking_reference = reference
        try:
            result = writer.create_inventory_reduction(
                product_id=product.id,
                qty=floor_qty,
                note=f"Floor OOS mark — {product.global_sku} {product.name}"[:120],
                reference=reference,
            )
        except OdooWriteError as e:
            mark.picking_status = OdooWriteOutcome.FAILED.value
            mark.picking_error = str(e)
        else:
            mark.picking_error = ""
            if result.dry_run:
                mark.picking_status = OdooWriteOutcome.SIMULATED.value
            else:
                mark.picking_status = OdooWriteOutcome.CREATED.value
                mark.odoo_picking_id = result.record_ids[0] if result.record_ids else None
                mark.odoo_picking_name = result.record_name
                mark.odoo_picking_url = result.deep_link
    db.commit()
    db.refresh(mark)

    today = utcnow().date()
    stock = _stock_pairs(db, {product.id}).get(product.id, {})
    incoming = incoming_by_product(db, {product.id}).get(product.id, [])
    return _item(db, product, stock, incoming, today, mark)


def _remove_mark_and_draft(
    db: Session, settings: Settings, mark: FloorOosMark, actor_user_id: int
) -> None:
    """Remove the mark, and remove the app-created reduction draft from Odoo
    while it's still a draft. Failures leave the draft for a human — never
    block the unmark."""
    if mark.picking_status == OdooWriteOutcome.CREATED.value and mark.odoo_picking_id:
        writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
        try:
            conn = get_connection(settings, read_only=True)
            rows = conn.search_read(
                "stock.picking", [["id", "=", mark.odoo_picking_id]], ["state"]
            )
            if rows and rows[0].get("state") == "draft":
                writer.unlink_app_record("stock.picking", mark.odoo_picking_id)
        except (OdooError, OdooWriteError, ValueError):
            pass  # audited by the writer; the human can delete the draft in Odoo
    db.delete(mark)


@router.delete("/{mark_id}", status_code=204)
def unmark(
    mark_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> None:
    """Marked by mistake: plain undo, no stock adjustment."""
    mark = db.get(FloorOosMark, mark_id)
    if mark is None:
        raise HTTPException(404, "Mark not found.")
    _remove_mark_and_draft(db, settings, mark, authed.id)
    db.commit()


class RestockIn(BaseModel):
    # the freshly counted shelf quantity; omit for a plain unmark
    counted_qty: float | None = None


class AdjustmentOut(BaseModel):
    direction: str  # add | reduce
    qty: float
    status: str  # created | simulated | failed
    reference: str
    picking_name: str
    url: str
    error: str


class RestockOut(BaseModel):
    floor_qty_before: float  # Odoo's number at the time (last stock sync)
    adjustment: AdjustmentOut | None  # None when counts already agreed


@router.post("/{mark_id}/restock", response_model=RestockOut)
def back_in_stock(
    mark_id: int,
    body: RestockIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> RestockOut:
    """The shelf has stock again. With a counted quantity, renders the draft
    that reconciles Odoo to the count — "USA-III: Inventory Adj  Adding Qty"
    when the count is higher than Odoo's number, a reduction when it's lower.
    Draft only; quantities are re-checkable in Odoo before anyone validates."""
    mark = db.get(FloorOosMark, mark_id)
    if mark is None:
        raise HTTPException(404, "Mark not found.")
    product = db.get(Product, mark.product_id)
    floor_qty = float(
        db.scalar(
            select(StockLevel.qty).where(
                StockLevel.product_id == mark.product_id,
                StockLevel.location_key == "floor",
            )
        )
        or 0.0
    )

    adjustment: AdjustmentOut | None = None
    if body.counted_qty is not None and product is not None:
        delta = round(float(body.counted_qty) - floor_qty, 3)
        if delta != 0 and product.is_stock_tracked and product.odoo_product_id:
            writer = OdooWriter(db, settings, actor_user_id=authed.id)
            direction = "add" if delta > 0 else "reduce"
            op = (
                writer.create_inventory_addition
                if delta > 0
                else writer.create_inventory_reduction
            )
            reference = new_reference("OOS")
            note = (
                f"Back in stock — counted {body.counted_qty:g}, Odoo showed "
                f"{floor_qty:g} — {product.global_sku} {product.name}"
            )[:120]
            try:
                result = op(
                    product_id=product.id, qty=abs(delta), note=note, reference=reference
                )
            except OdooWriteError as e:
                adjustment = AdjustmentOut(
                    direction=direction, qty=abs(delta),
                    status=OdooWriteOutcome.FAILED.value,
                    reference=reference, picking_name="", url="", error=str(e),
                )
            else:
                adjustment = AdjustmentOut(
                    direction=direction,
                    qty=abs(delta),
                    status=(
                        OdooWriteOutcome.SIMULATED.value
                        if result.dry_run
                        else OdooWriteOutcome.CREATED.value
                    ),
                    reference=result.reference,
                    picking_name=result.record_name,
                    url=result.deep_link,
                    error="",
                )

    _remove_mark_and_draft(db, settings, mark, authed.id)
    db.commit()
    return RestockOut(floor_qty_before=floor_qty, adjustment=adjustment)
