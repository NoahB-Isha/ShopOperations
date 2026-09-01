"""Floor out-of-stock list — a searchable, read-only board.

Products Odoo shows at zero on the floor, computed from the snapshot. The
board takes no actions (marking and its Odoo drafts were removed 2026-08-24 —
counted numbers enter the app ONLY through the counting page); fixing a wrong
floor figure means counting the product there.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import AuthedUser, require_roles
from ..center_orders.catalog import expected_back_label, incoming_by_product
from ..db import get_db
from ..models import (
    SHOPPE_CHANNELS,
    Product,
    Role,
    SalesDaily,
    StockLevel,
    StockSnapshot,
    not_blacklisted,
    utcnow,
)

# Floor + rotating volunteers read the board; warehouse (and admin) can view
# the list — the page's Everywhere/Warehouse scopes matter to them too.
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
class OosItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str  # the identifier the team actually uses; sku is the fallback
    name: str
    category: str
    floor_qty: float  # what Odoo currently claims
    bwhse_qty: float
    incoming_label: str  # "expected back mid-August" | "no restock scheduled yet"


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


def _item(p: Product, stock: dict[str, float], incoming: list, today) -> OosItemOut:
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
    )


# ---------------------------------------------------------------- endpoints
@router.get("", response_model=list[OosItemOut])
def list_oos(
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(*BOARD_ACTORS, Role.WAREHOUSE)),
) -> list[OosItemOut]:
    """Every FLOOR-RELEVANT product Odoo shows at zero on the floor.

    "Floor-relevant" matters: Odoo vacuums zero quants, so a product that
    sold out yesterday often has NO floor stock row at all — the old
    row-must-exist query silently dropped exactly the items the team most
    needs to see (the live board was 20 rows while thousands of products
    existed). A product belongs on this board when it has a floor stock row
    today, floor snapshot history, or recent Shoppe sales — and its floor
    quantity is zero-or-missing. Non-retail POS items stay off."""
    today = utcnow().date()

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
    zero_pids = {pid for pid in eligible if floor_qty.get(pid, 0.0) <= 0}

    stock = _stock_pairs(db, zero_pids)
    incoming = incoming_by_product(db, zero_pids)
    products = {
        p.id: p
        for p in db.scalars(select(Product).where(Product.id.in_(zero_pids or {-1})))
    }

    items = [
        _item(products[pid], stock.get(pid, {}), incoming.get(pid, []), today)
        for pid in zero_pids
        if pid in products
    ]
    items.sort(key=lambda i: (i.category or "~", i.name))
    return items
