"""Org-wide OOS and Coming Soon lists — live views over the stock snapshot
and pending incoming moves.

These are the Phase-5 org-level lists (warehouse + floor + admin, and the
skubot API): "what can nobody sell right now" and "what's arriving when".
They reuse the center-catalog availability vocabulary (`expected_back_label`,
incoming states) so the whole app speaks one language about stock honesty.
The floor OOS *board* (`app/oos`) stays separate — that one is an actionable
mark/adjust workflow, not a report.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..catalog.search import matches_search
from ..center_orders.catalog import (
    INCOMING_PENDING_STATES,
    expected_back_label,
    incoming_by_product,
)
from ..config import Settings
from ..models import (
    IncomingMove,
    Product,
    StockLevel,
    StockSnapshot,
    SyncState,
    not_blacklisted,
    utcnow,
)

OOS_SCOPES = ("org", "bwhse", "floor")


@dataclass
class AvailabilityItem:
    product_id: int
    sku: str
    barcode: str
    name: str
    category: str
    bwhse_qty: float
    floor_qty: float
    staging_qty: float
    incoming_qty: float
    incoming_expected: date | None
    incoming_label: str
    last_in_stock_on: date | None = None  # OOS lists only, from snapshot history
    low_count_caveat: bool = False

    @property
    def total_qty(self) -> float:
        return self.bwhse_qty + self.floor_qty + self.staging_qty

    def as_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "barcode": self.barcode,
            "name": self.name,
            "category": self.category,
            "bwhse_qty": self.bwhse_qty,
            "floor_qty": self.floor_qty,
            "staging_qty": self.staging_qty,
            "total_qty": self.total_qty,
            "incoming_qty": self.incoming_qty,
            "incoming_expected": (
                self.incoming_expected.isoformat() if self.incoming_expected else None
            ),
            "incoming_label": self.incoming_label,
            "last_in_stock_on": (
                self.last_in_stock_on.isoformat() if self.last_in_stock_on else None
            ),
            "low_count_caveat": self.low_count_caveat,
        }


def _stock_buckets(db: Session, product_ids: list[int] | None = None) -> dict[int, dict[str, float]]:
    stmt = select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty)
    if product_ids is not None:
        stmt = stmt.where(StockLevel.product_id.in_(product_ids))
    out: dict[int, dict[str, float]] = {}
    for pid, key, qty in db.execute(stmt):
        out.setdefault(pid, {})[key] = float(qty or 0)
    return out


def _matches(product: Product, category: str | None, q: str | None) -> bool:
    if category and (product.category or "").lower() != category.lower():
        return False
    return matches_search(
        q, product.name, product.global_sku, product.odoo_internal_ref, product.barcode
    )


def _scope_qty(buckets: dict[str, float], scope: str) -> float:
    if scope == "bwhse":
        return buckets.get("bwhse", 0.0)
    if scope == "floor":
        return buckets.get("floor", 0.0)
    return sum(buckets.get(k, 0.0) for k in ("bwhse", "floor", "staging", "staging2"))


def oos_items(
    db: Session,
    settings: Settings,
    *,
    scope: str = "org",
    category: str | None = None,
    q: str | None = None,
    today: date | None = None,
    include_never_stocked: bool = False,
) -> list[AvailabilityItem]:
    """Products with nothing left in `scope` (org = bwhse+floor+staging).
    Non-retail POS items (`restock_exclude`) are noise here and stay out.

    Items the snapshot history has NEVER seen in stock (in scope) are hidden
    by default — they didn't "go out of stock", the app has just never known
    them stocked (fast movers between weekly snapshots, digital goods,
    clothing variants); Noah's 2026-07-27 call. `include_never_stocked`
    is the peek switch."""
    today = today or utcnow().date()
    if scope not in OOS_SCOPES:
        scope = "org"
    products = (
        db.execute(
            select(Product).where(
                Product.is_active.is_(True),
                Product.is_stock_tracked.is_(True),
                Product.restock_exclude.is_(False),
                Product.source == "odoo",
                not_blacklisted(),
            )
        )
        .scalars()
        .all()
    )
    buckets = _stock_buckets(db)
    out_products = [
        p
        for p in products
        if _scope_qty(buckets.get(p.id, {}), scope) <= 0 and _matches(p, category, q)
    ]
    ids = {p.id for p in out_products}
    incoming = incoming_by_product(db, ids)
    last_in_stock = _last_in_stock(db, ids, scope)

    items: list[AvailabilityItem] = []
    for p in sorted(out_products, key=lambda p: ((p.category or ""), p.name)):
        if not include_never_stocked and p.id not in last_in_stock:
            continue
        b = buckets.get(p.id, {})
        inc = incoming.get(p.id, [])
        inc_dates = sorted(d for _, d in inc if d is not None)
        soonest = inc_dates[0] if inc_dates else None
        items.append(
            AvailabilityItem(
                product_id=p.id,
                sku=p.odoo_internal_ref or p.global_sku,
                barcode=p.barcode or "",
                name=p.name,
                category=p.category or "",
                bwhse_qty=b.get("bwhse", 0.0),
                floor_qty=b.get("floor", 0.0),
                staging_qty=b.get("staging", 0.0),
                incoming_qty=sum(q_ for q_, _ in inc),
                incoming_expected=soonest,
                incoming_label=expected_back_label(soonest if inc else None, today),
                last_in_stock_on=last_in_stock.get(p.id),
            )
        )
    return items


def _last_in_stock(db: Session, product_ids: set[int], scope: str) -> dict[int, date]:
    """Most recent snapshot day each product had stock (in scope) — 'OOS since
    about …'. Empty until snapshot history accumulates; honest either way."""
    if not product_ids:
        return {}
    stmt = (
        select(StockSnapshot.product_id, func.max(StockSnapshot.snapshot_date))
        .where(StockSnapshot.product_id.in_(product_ids), StockSnapshot.qty > 0)
        .group_by(StockSnapshot.product_id)
    )
    if scope in ("bwhse", "floor"):
        stmt = stmt.where(StockSnapshot.location_key == scope)
    return {pid: d for pid, d in db.execute(stmt) if d is not None}


def coming_soon_items(
    db: Session,
    settings: Settings,
    *,
    category: str | None = None,
    q: str | None = None,
    within_days: int | None = None,
    today: date | None = None,
) -> list[AvailabilityItem]:
    """Products with pending inbound shipments, soonest first. `within_days`
    keeps only arrivals expected inside the window (undated moves are treated
    as imminent, same as the ordering engine does)."""
    today = today or utcnow().date()
    pending: dict[int, list[tuple[float, date | None]]] = {}
    for pid, qty, expected in db.execute(
        select(IncomingMove.product_id, IncomingMove.qty, IncomingMove.expected_date).where(
            IncomingMove.product_id.is_not(None),
            IncomingMove.state.in_(INCOMING_PENDING_STATES),
        )
    ):
        pending.setdefault(pid, []).append((float(qty or 0), expected))
    if not pending:
        return []

    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(
                Product.id.in_(pending), Product.is_active.is_(True), not_blacklisted()
            )
        ).scalars()
    }
    buckets = _stock_buckets(db, list(products))

    items: list[AvailabilityItem] = []
    for pid, moves in pending.items():
        p = products.get(pid)
        if p is None or not _matches(p, category, q):
            continue
        dates = sorted(d for _, d in moves if d is not None)
        soonest = dates[0] if dates else None
        if within_days is not None and soonest is not None:
            if (soonest - today).days > within_days:
                continue
        b = buckets.get(pid, {})
        total = sum(b.get(k, 0.0) for k in ("bwhse", "floor", "staging", "staging2"))
        items.append(
            AvailabilityItem(
                product_id=pid,
                sku=p.odoo_internal_ref or p.global_sku,
                barcode=p.barcode or "",
                name=p.name,
                category=p.category or "",
                bwhse_qty=b.get("bwhse", 0.0),
                floor_qty=b.get("floor", 0.0),
                staging_qty=b.get("staging", 0.0),
                incoming_qty=sum(q_ for q_, _ in moves),
                incoming_expected=soonest,
                incoming_label=(
                    expected_back_label(soonest, today) if soonest else "arrival date TBD"
                ),
                low_count_caveat=0 < total <= settings.catalog_low_stock_threshold,
            )
        )
    items.sort(key=lambda i: (i.incoming_expected is None, i.incoming_expected or today, i.name))
    return items


def snapshot_freshness(db: Session) -> dict:
    """When the underlying snapshots were last refreshed — the lists must
    never pretend to be more live than the sync that feeds them."""
    out = {}
    for domain in ("stock", "incoming"):
        state = db.get(SyncState, domain)
        out[domain] = (
            state.last_success_at.isoformat()
            if state and state.last_success_at
            else None
        )
    return out


