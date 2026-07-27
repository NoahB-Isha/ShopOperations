"""What a center may order, and how available each item honestly is.

Catalog resolution:
  * field centers — the union of order lists granted to the center by its
    coordinator (order_list_centers, the phase-2 grant chain);
  * department "centers" — additionally every dept_orderable product, which is
    how untracked items (water, snacks) become orderable without an Odoo record.

Availability is the OOS timeline: on-hand at the order's fulfillment source
(BWHSE for field zones, III-FLOOR for departments) plus, when out, the
earliest incoming shipment ("expected back mid-August"). Low counts carry the
honesty caveat — Odoo's small numbers are often wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Center,
    IncomingMove,
    OrderList,
    OrderListCenter,
    OrderListLine,
    Product,
    StockLevel,
    Zone,
    ZoneKind,
    not_blacklisted,
)

INCOMING_PENDING_STATES = {"assigned", "confirmed", "waiting", "partially_available"}


def source_location_key(zone_kind: str | None) -> str:
    """Departments are fulfilled from the Shoppe floor; field centers from
    the warehouse."""
    return "floor" if zone_kind == ZoneKind.DEPARTMENTS.value else "bwhse"


# ------------------------------------------------------------- availability
@dataclass
class Availability:
    status: str  # in | low | out | untracked
    qty: float | None  # on-hand at the fulfillment source (None when untracked)
    low_count_caveat: bool = False  # "low counts are often wrong; verify physically"
    incoming_qty: float = 0.0
    incoming_expected: date | None = None
    incoming_label: str = ""  # "expected back mid-August" | "restock overdue…" | ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "qty": self.qty,
            "low_count_caveat": self.low_count_caveat,
            "incoming_qty": self.incoming_qty,
            "incoming_expected": (
                self.incoming_expected.isoformat() if self.incoming_expected else None
            ),
            "incoming_label": self.incoming_label,
        }


def _month_part(d: date) -> str:
    month = d.strftime("%B")
    if d.day <= 10:
        return f"early {month}"
    if d.day <= 20:
        return f"mid-{month}"  # only "mid" hyphenates in English
    return f"late {month}"


def expected_back_label(expected: date | None, today: date) -> str:
    if expected is None:
        return "no restock scheduled yet"
    if expected < today:
        return f"restock overdue — was expected {_month_part(expected)}"
    return f"expected back {_month_part(expected)}"


def availability_for(
    *,
    product: Product,
    on_hand: float | None,
    incoming: list[tuple[float, date | None]],
    low_threshold: float,
    today: date,
) -> Availability:
    """Pure assessment for one product. `incoming` = (qty, expected_date)
    pairs for pending inbound moves."""
    if not product.is_stock_tracked or not product.odoo_product_id:
        return Availability(status="untracked", qty=None)
    qty = float(on_hand or 0.0)
    incoming_qty = sum(q for q, _ in incoming)
    dates = sorted(d for _, d in incoming if d is not None)
    soonest = dates[0] if dates else None
    if qty <= 0:
        return Availability(
            status="out",
            qty=qty,
            incoming_qty=incoming_qty,
            incoming_expected=soonest,
            incoming_label=expected_back_label(soonest if incoming else None, today),
        )
    low = qty <= low_threshold
    return Availability(
        status="low" if low else "in",
        qty=qty,
        low_count_caveat=low,
        incoming_qty=incoming_qty,
        incoming_expected=soonest,
    )


# ---------------------------------------------------------------- catalog
@dataclass
class CatalogItem:
    product: Product
    availability: Availability
    from_lists: list[str] = field(default_factory=list)  # which granted lists carry it


def orderable_product_ids(db: Session, center: Center) -> dict[int, list[str]]:
    """product_id -> names of the granted lists that carry it (dept-orderable
    items map to the pseudo-list name 'Department items'). Clothing is
    allowed on catalogs (Noah, 2026-07-26 — hand-curated menus decide);
    only the PURCHASING flows still exclude it."""
    out: dict[int, list[str]] = {}
    rows = db.execute(
        select(OrderListLine.product_id, OrderList.name)
        .join(OrderList, OrderList.id == OrderListLine.order_list_id)
        .join(OrderListCenter, OrderListCenter.order_list_id == OrderList.id)
        .join(Product, Product.id == OrderListLine.product_id)
        .where(
            OrderListCenter.center_id == center.id,
            OrderList.is_archived.is_(False),
            not_blacklisted(),
        )
        .order_by(OrderListLine.position)
    )
    for pid, list_name in rows:
        out.setdefault(pid, []).append(list_name)

    zone = db.get(Zone, center.zone_id) if center.zone_id else None
    if zone and zone.kind == ZoneKind.DEPARTMENTS.value:
        dept_ids = db.scalars(
            select(Product.id).where(
                Product.dept_orderable.is_(True),
                Product.is_active.is_(True),
                not_blacklisted(),
            )
        )
        for pid in dept_ids:
            out.setdefault(pid, []).append("Department items")
    return out


def stock_by_product(db: Session, product_ids: set[int], location_key: str) -> dict[int, float]:
    if not product_ids:
        return {}
    rows = db.execute(
        select(StockLevel.product_id, StockLevel.qty).where(
            StockLevel.product_id.in_(product_ids),
            StockLevel.location_key == location_key,
        )
    )
    return {pid: float(q) for pid, q in rows}


def incoming_by_product(
    db: Session, product_ids: set[int]
) -> dict[int, list[tuple[float, date | None]]]:
    if not product_ids:
        return {}
    out: dict[int, list[tuple[float, date | None]]] = {}
    rows = db.execute(
        select(IncomingMove.product_id, IncomingMove.qty, IncomingMove.expected_date).where(
            IncomingMove.product_id.in_(product_ids),
            IncomingMove.state.in_(INCOMING_PENDING_STATES),
        )
    )
    for pid, qty, expected in rows:
        out.setdefault(pid, []).append((float(qty or 0.0), expected))
    return out


def build_catalog(
    db: Session, settings: Settings, center: Center, today: date
) -> tuple[str, list[CatalogItem]]:
    """(source_location_key, orderable items with availability), sorted by
    category then name — the order form's entire menu in one read."""
    zone = db.get(Zone, center.zone_id) if center.zone_id else None
    source_key = source_location_key(zone.kind if zone else None)

    by_product = orderable_product_ids(db, center)
    if not by_product:
        return source_key, []
    products = [
        p
        for p in db.scalars(
            select(Product).where(
                Product.id.in_(by_product.keys()), Product.is_active.is_(True)
            )
        )
        # defense in depth: blacklisted items stay out of the menu even if
        # an old list line slipped one in
        if not p.blacklisted
    ]
    ids = {p.id for p in products}
    stock = stock_by_product(db, ids, source_key)
    incoming = incoming_by_product(db, ids)
    items = [
        CatalogItem(
            product=p,
            availability=availability_for(
                product=p,
                on_hand=stock.get(p.id),
                incoming=incoming.get(p.id, []),
                low_threshold=settings.catalog_low_stock_threshold,
                today=today,
            ),
            from_lists=by_product.get(p.id, []),
        )
        for p in products
    ]
    items.sort(key=lambda it: (it.product.category or "~", it.product.name))
    return source_key, items
