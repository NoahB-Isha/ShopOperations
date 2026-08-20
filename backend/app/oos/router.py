"""Floor out-of-stock board — data cleanup with teeth.

The list is honest about its two sources: products Odoo already shows at
zero on the floor (computed from the snapshot), and products the team MARKED
out because the shelf is empty no matter what Odoo says. Marking a product
with phantom floor stock renders a DRAFT picking on the inventory-reduction
operation type ("USA-III: Inventory Adj Reduction") removing that quantity —
a human validates it in Odoo; the app adjusts nothing itself.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..center_orders.catalog import expected_back_label, incoming_by_product
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    SHOPPE_CHANNELS,
    FloorOosMark,
    OdooWriteOutcome,
    Product,
    Role,
    SalesDaily,
    StockLevel,
    StockSnapshot,
    User,
    not_blacklisted,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter
from .adjust import NO_CHANGE, AdjustTooLarge, reconcile_floor_count

# Floor + rotating volunteers own the board and its actions; warehouse (and
# admin) can view the list — the page's Everywhere/Warehouse scopes matter to
# them too.
BOARD_ACTORS = (Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING)

router = APIRouter(
    prefix="/oos",
    tags=["oos"],
    dependencies=[Depends(require_roles(*BOARD_ACTORS, Role.WAREHOUSE))],
)

# What makes a product "floor-relevant" for the computed-zeros board: Shoppe
# sales inside this window, or floor stock at any point in this history window.
RELEVANT_SALES_DAYS = 30
RELEVANT_HISTORY_DAYS = 60


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
    _: AuthedUser = Depends(require_roles(*BOARD_ACTORS, Role.WAREHOUSE)),
) -> list[OosItemOut]:
    """Marked products first (newest on top), then every FLOOR-RELEVANT
    product Odoo shows at zero on the floor.

    "Floor-relevant" matters: Odoo vacuums zero quants, so a product that
    sold out yesterday often has NO floor stock row at all — the old
    row-must-exist query silently dropped exactly the items the team most
    needs to see (the live board was 20 rows while thousands of products
    existed). A product belongs on this board when it has a floor stock row
    today, floor snapshot history, or recent Shoppe sales — and its floor
    quantity is zero-or-missing. Non-retail POS items stay off."""
    today = utcnow().date()
    marks = db.scalars(
        select(FloorOosMark)
        .options(selectinload(FloorOosMark.product))
        .order_by(FloorOosMark.id.desc())
        .execution_options(populate_existing=True)
    ).all()
    marked_pids = {m.product_id for m in marks}

    floor_qty: dict[int, float] = {
        pid: float(qty or 0)
        for pid, qty in db.execute(
            select(StockLevel.product_id, StockLevel.qty).where(
                StockLevel.location_key == "floor"
            )
        )
    }
    relevant: set[int] = set(floor_qty)
    # sold at the Shoppe recently → belongs on the floor
    sales_floor_date = today - timedelta(days=RELEVANT_SALES_DAYS)
    relevant.update(
        pid
        for (pid,) in db.execute(
            select(SalesDaily.product_id)
            .where(
                SalesDaily.channel.in_(SHOPPE_CHANNELS),
                SalesDaily.day >= sales_floor_date,
            )
            .distinct()
        )
    )
    # had floor stock in recent history → belongs on the floor
    history_floor_date = today - timedelta(days=RELEVANT_HISTORY_DAYS)
    relevant.update(
        pid
        for (pid,) in db.execute(
            select(StockSnapshot.product_id)
            .where(
                StockSnapshot.location_key == "floor",
                StockSnapshot.snapshot_date >= history_floor_date,
                StockSnapshot.qty > 0,
            )
            .distinct()
        )
    )

    eligible = {
        pid
        for (pid,) in db.execute(
            select(Product.id).where(
                Product.id.in_(relevant or {-1}),
                Product.is_active.is_(True),
                Product.is_stock_tracked.is_(True),
                Product.restock_exclude.is_(False),
                not_blacklisted(),
            )
        )
    }
    zero_pids = {
        pid for pid in eligible if floor_qty.get(pid, 0.0) <= 0
    } - marked_pids

    pids = marked_pids | zero_pids
    stock = _stock_pairs(db, pids)
    incoming = incoming_by_product(db, pids)
    products = {
        p.id: p for p in db.scalars(select(Product).where(Product.id.in_(pids or {-1})))
    }

    items: list[OosItemOut] = []
    for m in marks:
        p = products.get(m.product_id) or m.product
        if p.blacklisted:  # blacklisted items show nowhere, marks included
            continue
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
    authed: AuthedUser = Depends(require_roles(*BOARD_ACTORS)),
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
    authed: AuthedUser = Depends(require_roles(*BOARD_ACTORS)),
) -> None:
    """Marked by mistake: plain undo, no stock adjustment."""
    mark = db.get(FloorOosMark, mark_id)
    if mark is None:
        raise HTTPException(404, "Mark not found.")
    _remove_mark_and_draft(db, settings, mark, authed.id)
    db.commit()


class RestockIn(BaseModel):
    # The freshly counted shelf quantity; omit for a plain unmark. Bounded and
    # finite: this number becomes a quantity on a real Odoo adjustment draft.
    counted_qty: float | None = Field(default=None, ge=0, le=100_000, allow_inf_nan=False)


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
    authed: AuthedUser = Depends(require_roles(*BOARD_ACTORS)),
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
        # one copy of the delta/ceiling/writer dance lives in oos/adjust.py,
        # shared with the floor-count edit on the product drawer
        try:
            outcome = reconcile_floor_count(
                db, settings, product,
                floor_qty=floor_qty,
                counted_qty=float(body.counted_qty),
                actor_user_id=authed.id,
                note=(
                    f"Back in stock — counted {body.counted_qty:g}, Odoo showed "
                    f"{floor_qty:g} — {product.global_sku} {product.name}"
                ),
                reference_kind="OOS",
            )
        except AdjustTooLarge as e:
            raise HTTPException(422, str(e)) from e
        if outcome is not NO_CHANGE:
            adjustment = AdjustmentOut(
                direction=outcome.direction,
                qty=outcome.qty,
                status=outcome.status,
                reference=outcome.reference,
                picking_name=outcome.picking_name,
                url=outcome.url,
                error=outcome.error,
            )

    _remove_mark_and_draft(db, settings, mark, authed.id)
    db.commit()
    return RestockOut(floor_qty_before=floor_qty, adjustment=adjustment)
