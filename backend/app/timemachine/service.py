"""Inventory time machine — pick a date, get one honest answer.

PAST dates replay the stock history the stock sync captures daily
(`stock_snapshots`): the nearest covered day at-or-before the requested date
is shown, with the gap disclosed. History only exists from the day the
feature shipped — the confidence indicator says so rather than pretending.

FUTURE dates (up to the engine's 6-month horizon) run the ordering engine's
own projection per product — the same `max(0, oh − demand + incoming)`
recurrence, the same forecasts, the same incoming-move bucketing as the India
review table (`_project_moh` is scale-free, so it runs here in units). The
two surfaces can never disagree about the future; the parity test locks it.

TODAY is the live snapshot, freshest of all three modes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Product,
    StockLevel,
    StockSnapshot,
    StockSnapshotDay,
    SyncState,
    Vendor,
    not_blacklisted,
    utcnow,
)
from ..ordering.engine import _project_moh
from ..ordering.inputs import snapshots_for_products
from ..ordering.service import load_rules

MODES = ("past", "today", "future")


@dataclass
class TimeMachineItem:
    product_id: int
    sku: str
    barcode: str
    name: str
    category: str
    total_qty: float
    bwhse_qty: float | None = None  # per-bucket detail (past/today modes)
    floor_qty: float | None = None
    staging_qty: float | None = None
    incoming_included: float = 0.0  # future mode: inbound units assumed received
    forecast_method: str = ""  # flat | trend | seasonal | analogy | none
    forecast_confidence: str = ""  # high | medium | low

    def as_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "barcode": self.barcode,
            "name": self.name,
            "category": self.category,
            "total_qty": round(self.total_qty, 2),
            "bwhse_qty": self.bwhse_qty,
            "floor_qty": self.floor_qty,
            "staging_qty": self.staging_qty,
            "incoming_included": round(self.incoming_included, 2),
            "forecast_method": self.forecast_method,
            "forecast_confidence": self.forecast_confidence,
        }


@dataclass
class TimeMachineView:
    mode: str
    requested_date: date
    effective_date: date  # snapshot day actually shown (past), else requested
    confidence: dict = field(default_factory=dict)
    items: list[TimeMachineItem] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "requested_date": self.requested_date.isoformat(),
            "effective_date": self.effective_date.isoformat(),
            "confidence": self.confidence,
            "items": [i.as_dict() for i in self.items],
        }


def project_units(
    on_hand: float, demand_units: list[float], incoming_units: list[float]
) -> list[float]:
    """The engine's recurrence, run in units. `_project_moh` is scale-free —
    dividing every input by avg-monthly-sales (what the engine does) and
    multiplying the output back is an identity, so this IS the engine's
    projection. Pass full-length lists: the recurrence's short-list default
    (1.0/month) only makes sense in MOH space."""
    return _project_moh(on_hand, demand_units, incoming_units)


def _month_delta(target: date, today: date) -> int:
    return (target.year * 12 + target.month) - (today.year * 12 + today.month)


def bounds(db: Session, settings: Settings, today: date | None = None) -> dict:
    """Slider bounds: covered history days and the future horizon. The past
    window opens `timemachine_min_past_days` back even before any history is
    captured — uncovered dates get the honest empty state, not a slider that
    refuses to move."""
    today = today or utcnow().date()
    days = sorted(db.scalars(select(StockSnapshotDay.snapshot_date)))
    horizon = load_rules(db).horizon
    # last day of the final projection month
    total = today.year * 12 + (today.month - 1) + horizon + 1
    first_after = date(total // 12, total % 12 + 1, 1)
    floor_date = today - timedelta(days=settings.timemachine_min_past_days)
    min_date = min(days[0], floor_date) if days else floor_date
    return {
        "today": today.isoformat(),
        "min_date": min_date.isoformat(),
        "max_date": (first_after - timedelta(days=1)).isoformat(),
        "history_days": [d.isoformat() for d in days],
        "horizon_months": horizon,
    }


def _matches(p: Product, category: str | None, q: str | None) -> bool:
    if category and (p.category or "").lower() != category.lower():
        return False
    if q:
        needle = q.lower()
        hay = " ".join(
            filter(None, (p.name, p.global_sku, p.odoo_internal_ref, p.barcode))
        ).lower()
        if needle not in hay:
            return False
    return True


def _item_base(p: Product) -> dict:
    return {
        "product_id": p.id,
        "sku": p.odoo_internal_ref or p.global_sku,
        "barcode": p.barcode or "",
        "name": p.name,
        "category": p.category or "",
    }


def view(
    db: Session,
    settings: Settings,
    target: date,
    *,
    category: str | None = None,
    q: str | None = None,
    today: date | None = None,
) -> TimeMachineView:
    today = today or utcnow().date()
    delta = _month_delta(target, today)
    if target <= today:
        if target == today:
            return _today_view(db, target, category, q)
        return _past_view(db, settings, target, category, q)
    horizon = load_rules(db).horizon
    if delta > horizon:
        raise ValueError(
            f"future projections reach {horizon} months out — pick a date before then"
        )
    return _future_view(db, target, max(delta, 1), category, q, today)


def _today_view(
    db: Session, target: date, category: str | None, q: str | None
) -> TimeMachineView:
    products = _active_products(db)
    buckets: dict[int, dict[str, float]] = {}
    for pid, key, qty in db.execute(
        select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty)
    ):
        buckets.setdefault(pid, {})[key] = float(qty or 0)
    state = db.get(SyncState, "stock")
    synced = (
        state.last_success_at.isoformat() if state and state.last_success_at else None
    )
    items = _bucket_items(products, buckets, category, q)
    return TimeMachineView(
        mode="today",
        requested_date=target,
        effective_date=target,
        confidence={
            "level": "high",
            "note": "live snapshot (Odoo numbers are authoritative, but low counts are often wrong — verify physically)",
            "stock_synced_at": synced,
        },
        items=items,
    )


def _past_view(
    db: Session, settings: Settings, target: date, category: str | None, q: str | None
) -> TimeMachineView:
    day_row = db.scalar(
        select(StockSnapshotDay)
        .where(StockSnapshotDay.snapshot_date <= target)
        .order_by(StockSnapshotDay.snapshot_date.desc())
        .limit(1)
    )
    if day_row is None:
        first = db.scalar(
            select(StockSnapshotDay.snapshot_date).order_by(StockSnapshotDay.snapshot_date).limit(1)
        )
        begins = (
            f"history begins {first.isoformat()}"
            if first
            else "history accumulates from the first stock sync"
        )
        return TimeMachineView(
            mode="past",
            requested_date=target,
            effective_date=target,
            confidence={
                "level": "none",
                "note": (
                    f"no snapshot history on or before this date — {begins}. An admin can "
                    "backfill weekly history from Odoo's move ledger."
                ),
                "gap_days": None,
            },
            items=[],
        )
    day = day_row.snapshot_date
    reconstructed = day_row.source == "reconstructed"
    gap = (target - day).days
    if gap == 0:
        level, note = "high", f"exact snapshot from {day.isoformat()}"
    elif gap <= settings.timemachine_max_gap_days:
        level, note = "medium", f"nearest snapshot is {gap} day(s) earlier ({day.isoformat()})"
    else:
        level, note = "low", (
            f"nearest snapshot is {gap} days earlier ({day.isoformat()}) — treat as approximate"
        )
    if reconstructed:
        # real numbers, computed after the fact — never claim live-capture confidence
        if level == "high":
            level = "medium"
        note += " · reconstructed from Odoo's move ledger (weekly backfill)"

    products = _active_products(db)
    buckets: dict[int, dict[str, float]] = {}
    for pid, key, qty in db.execute(
        select(StockSnapshot.product_id, StockSnapshot.location_key, StockSnapshot.qty).where(
            StockSnapshot.snapshot_date == day
        )
    ):
        buckets.setdefault(pid, {})[key] = float(qty or 0)
    items = _bucket_items(products, buckets, category, q, include_zero=False)
    return TimeMachineView(
        mode="past",
        requested_date=target,
        effective_date=day,
        confidence={
            "level": level,
            "note": note,
            "gap_days": gap,
            "source": day_row.source,
        },
        items=items,
    )


def _future_view(
    db: Session,
    target: date,
    month_index: int,
    category: str | None,
    q: str | None,
    today: date,
) -> TimeMachineView:
    rules = load_rules(db)
    products = [p for p in _active_products(db) if _matches(p, category, q)]
    vendors = {v.id: v for v in db.execute(select(Vendor)).scalars()}
    snaps = snapshots_for_products(db, rules, products, vendors, today=today)

    items: list[TimeMachineItem] = []
    method_counts: dict[str, int] = {}
    incoming_total = 0.0
    by_sku = {p.global_sku: p for p in products}
    for snap in snaps:
        p = by_sku.get(snap.product.global_sku)
        if p is None:
            continue
        demand = list(snap.forecast.monthly[: rules.horizon]) if snap.forecast else [0.0] * rules.horizon
        if snap.forecast and len(demand) < rules.horizon:
            demand.extend([snap.forecast.forecast_mean] * (rules.horizon - len(demand)))
        incoming = list(snap.incoming_units_by_month[: rules.horizon])
        incoming += [0.0] * (rules.horizon - len(incoming))
        proj = project_units(snap.on_hand, demand, incoming)
        method = snap.forecast.method if snap.forecast else "none"
        method_counts[method] = method_counts.get(method, 0) + 1
        inc_by_then = sum(incoming[:month_index])
        incoming_total += inc_by_then
        if proj[month_index - 1] <= 0 and snap.on_hand <= 0 and inc_by_then == 0 and not q:
            continue  # nothing, nothing coming — noise in an 1,100-row table
        items.append(
            TimeMachineItem(
                **_item_base(p),
                total_qty=proj[month_index - 1],
                incoming_included=inc_by_then,
                forecast_method=method,
                forecast_confidence=snap.forecast.confidence if snap.forecast else "low",
            )
        )
    items.sort(key=lambda i: (i.category, i.name))
    modeled = sum(n for m, n in method_counts.items() if m != "none")
    note = (
        f"projected {month_index} month(s) out by the ordering engine's forecast "
        f"({modeled} products modeled from sales history, "
        f"{method_counts.get('none', 0)} with no history held flat), "
        f"net of {incoming_total:g} incoming units expected by then"
    )
    return TimeMachineView(
        mode="future",
        requested_date=target,
        effective_date=target,
        confidence={
            "level": "medium" if modeled else "low",
            "note": note,
            "month_index": month_index,
            "method_mix": method_counts,
            "incoming_units_included": round(incoming_total, 1),
        },
        items=items,
    )


def _active_products(db: Session) -> list[Product]:
    return list(
        db.execute(
            select(Product)
            .where(
                Product.is_active.is_(True),
                Product.is_stock_tracked.is_(True),
                Product.source == "odoo",
                not_blacklisted(),
            )
            .order_by(Product.category, Product.name)
        ).scalars()
    )


def _bucket_items(
    products: list[Product],
    buckets: dict[int, dict[str, float]],
    category: str | None,
    q: str | None,
    include_zero: bool = False,
) -> list[TimeMachineItem]:
    items = []
    for p in products:
        if not _matches(p, category, q):
            continue
        b = buckets.get(p.id)
        if b is None and not (include_zero or q):
            continue
        b = b or {}
        total = sum(b.get(k, 0.0) for k in ("bwhse", "floor", "staging", "staging2"))
        items.append(
            TimeMachineItem(
                **_item_base(p),
                total_qty=total,
                bwhse_qty=b.get("bwhse", 0.0),
                floor_qty=b.get("floor", 0.0),
                staging_qty=b.get("staging", 0.0),
            )
        )
    return items
