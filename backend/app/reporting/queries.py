"""Sales dashboard aggregation — straight SQL over the app's own snapshot
(sales_monthly / sales_center_monthly), nothing live-per-request.

Revenue honesty: rows synced since Phase 5 carry real tax-in amounts; older
rows have amount NULL and are ESTIMATED at units × the product's current
retail price. Every payload reports `estimated_share` so the UI can label
estimates instead of passing them off as fact. Channel vocabulary comes from
SalesChannel; legacy 'pos' rows (pre-split) display as Shoppe with a legacy
flag until the admin re-runs the sales backfill.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import Session

from ..models import (
    CustomerFirstSeen,
    Product,
    SalesCenterMonthly,
    SalesChannel,
    SalesMonthly,
    SalesOrdersMonthly,
    utcnow,
)

PERIOD_KEYS = ("mtd", "last_month", "3m", "6m", "12m", "24m", "qtd", "ytd")

CHANNEL_LABELS = {
    SalesChannel.SHOPPE.value: "Shoppe",
    SalesChannel.CITY_CENTER.value: "City centers",
    SalesChannel.CAMPUS_OTHER.value: "Campus other",
    SalesChannel.ONLINE.value: "Online",
    SalesChannel.POS_LEGACY.value: "Shoppe (legacy rows)",
}

# Dashboard scope tabs → channel sets (None = everything). In-person is the
# campus registers: the Shoppe floor (incl. pre-split legacy rows) plus the
# snack/events/tent one-offs.
SCOPE_CHANNELS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "in_person": (
        SalesChannel.SHOPPE.value,
        SalesChannel.POS_LEGACY.value,
        SalesChannel.CAMPUS_OTHER.value,
    ),
    "online": (SalesChannel.ONLINE.value,),
    "city_center": (SalesChannel.CITY_CENTER.value,),
}
SCOPE_KEYS = tuple(SCOPE_CHANNELS)


@dataclass(frozen=True)
class Period:
    key: str
    months: list[tuple[int, int]]  # oldest → newest, inclusive
    prior_months: list[tuple[int, int]]  # same length, immediately before
    label: str

    @property
    def start(self) -> tuple[int, int]:
        return self.months[0]

    @property
    def end(self) -> tuple[int, int]:
        return self.months[-1]


def _month_shift(anchor: tuple[int, int], n: int) -> tuple[int, int]:
    total = anchor[0] * 12 + (anchor[1] - 1) + n
    return total // 12, total % 12 + 1


def _month_range(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out = []
    y, m = start
    while (y, m) <= end:
        out.append((y, m))
        y, m = _month_shift((y, m), 1)
    return out


def resolve_period(key: str, today: date | None = None) -> Period:
    today = today or utcnow().date()
    cur = (today.year, today.month)
    if key not in PERIOD_KEYS:
        key = "3m"
    if key == "mtd":
        months = [cur]
        label = today.strftime("%B %Y (to date)")
    elif key == "last_month":
        months = [_month_shift(cur, -1)]
        label = date(*_month_shift(cur, -1), 1).strftime("%B %Y")
    elif key == "qtd":
        q_start = (today.year, ((today.month - 1) // 3) * 3 + 1)
        months = _month_range(q_start, cur)
        label = f"Q{(today.month - 1) // 3 + 1} {today.year} (to date)"
    elif key == "ytd":
        months = _month_range((today.year, 1), cur)
        label = f"{today.year} year to date"
    else:
        n = int(key.rstrip("m"))
        months = _month_range(_month_shift(cur, -(n - 1)), cur)
        label = f"Last {n} months"
    prior = _month_range(_month_shift(months[0], -len(months)), _month_shift(months[0], -1))
    return Period(key=key, months=months, prior_months=prior, label=label)


def _ym_filter(col_year, col_month, months: list[tuple[int, int]]):
    """WHERE clause for a contiguous month range."""
    (y0, m0), (y1, m1) = months[0], months[-1]
    start = y0 * 100 + m0
    end = y1 * 100 + m1
    packed = col_year * 100 + col_month
    return packed.between(start, end)


# revenue = real amounts where captured, else units × current retail price
def _revenue_cols():
    est_price = func.coalesce(cast(Product.retail_price, Float), 0.0)
    revenue = func.sum(
        func.coalesce(SalesMonthly.amount, SalesMonthly.units * est_price)
    )
    estimated = func.sum(
        case((SalesMonthly.amount.is_(None), SalesMonthly.units * est_price), else_=0.0)
    )
    return revenue, estimated


def _grouped(
    db: Session,
    months: list[tuple[int, int]],
    *group_cols,
    channels: tuple[str, ...] | None = None,
):
    revenue, estimated = _revenue_cols()
    # the product join is always needed: estimating NULL-amount rows takes
    # the product's current retail price
    stmt = (
        select(
            *group_cols,
            func.sum(SalesMonthly.units).label("units"),
            revenue.label("revenue"),
            estimated.label("estimated"),
        )
        .join(Product, Product.id == SalesMonthly.product_id)
        # blacklisted items are invisible app-wide, reports included
        .where(Product.blacklisted.is_(False))
        .where(_ym_filter(SalesMonthly.year, SalesMonthly.month, months))
        .group_by(*group_cols)
    )
    if channels is not None:
        stmt = stmt.where(SalesMonthly.channel.in_(channels))
    return db.execute(stmt).all()


def _display_channel(channel: str) -> str:
    """Legacy 'pos' rows fold into Shoppe for display (flagged separately)."""
    return (
        SalesChannel.SHOPPE.value
        if channel == SalesChannel.POS_LEGACY.value
        else channel
    )


def sales_overview(db: Session, period: Period, scope: str = "all") -> dict:
    """Everything the dashboard's top half needs, in one payload — optionally
    scoped to one channel tab (in_person / online / city_center)."""
    channels_filter = SCOPE_CHANNELS.get(scope)
    cur = _grouped(
        db, period.months, SalesMonthly.year, SalesMonthly.month, SalesMonthly.channel,
        channels=channels_filter,
    )
    prior = _grouped(db, period.prior_months, SalesMonthly.channel, channels=channels_filter)

    total_units = sum(r.units or 0 for r in cur)
    total_revenue = sum(r.revenue or 0 for r in cur)
    total_estimated = sum(r.estimated or 0 for r in cur)
    prior_units = sum(r.units or 0 for r in prior)
    prior_revenue = sum(r.revenue or 0 for r in prior)
    has_legacy = any(r.channel == SalesChannel.POS_LEGACY.value for r in cur)

    # channel summary (legacy pos folded into shoppe for display)
    channels: dict[str, dict] = {}
    for r in cur:
        ch = _display_channel(r.channel)
        agg = channels.setdefault(
            ch, {"channel": ch, "label": CHANNEL_LABELS.get(ch, ch), "units": 0.0, "revenue": 0.0, "prior_revenue": 0.0}
        )
        agg["units"] += float(r.units or 0)
        agg["revenue"] += float(r.revenue or 0)
    for r in prior:
        ch = _display_channel(r.channel)
        if ch in channels:
            channels[ch]["prior_revenue"] += float(r.revenue or 0)
        else:
            channels.setdefault(
                ch,
                {"channel": ch, "label": CHANNEL_LABELS.get(ch, ch), "units": 0.0, "revenue": 0.0, "prior_revenue": float(r.revenue or 0)},
            )
    for agg in channels.values():
        agg["share"] = (agg["revenue"] / total_revenue) if total_revenue else 0.0
        agg["delta_pct"] = _delta_pct(agg["revenue"], agg["prior_revenue"])
        agg["units"] = round(agg["units"], 1)
        agg["revenue"] = round(agg["revenue"], 2)
        agg["prior_revenue"] = round(agg["prior_revenue"], 2)

    # monthly series per channel (chart)
    series: list[dict] = []
    for r in cur:
        series.append(
            {
                "month": f"{r.year:04d}-{r.month:02d}",
                "channel": _display_channel(r.channel),
                "units": round(float(r.units or 0), 1),
                "revenue": round(float(r.revenue or 0), 2),
            }
        )
    # merge duplicate (month, channel) rows created by the legacy fold
    merged: dict[tuple[str, str], dict] = {}
    for row in series:
        key = (row["month"], row["channel"])
        if key in merged:
            merged[key]["units"] += row["units"]
            merged[key]["revenue"] += row["revenue"]
        else:
            merged[key] = row
    series = sorted(merged.values(), key=lambda r: (r["month"], r["channel"]))

    top_categories = breakdown(db, period, dim="category", limit=12, scope=scope)
    top_products = breakdown(db, period, dim="product", limit=10, scope=scope)
    centers = (
        breakdown(db, period, dim="center", limit=10)
        if scope in ("all", "city_center")
        else []
    )

    return {
        "period": {"key": period.key, "label": period.label,
                   "months": [f"{y:04d}-{m:02d}" for y, m in period.months]},
        "scope": scope,
        "generated_at": utcnow().isoformat(),
        "orders": orders_summary(db, period, scope),
        "totals": {
            "units": round(total_units, 1),
            "revenue": round(total_revenue, 2),
            "prior_units": round(prior_units, 1),
            "prior_revenue": round(prior_revenue, 2),
            "revenue_delta_pct": _delta_pct(total_revenue, prior_revenue),
            "units_delta_pct": _delta_pct(total_units, prior_units),
            "estimated_share": (total_estimated / total_revenue) if total_revenue else 0.0,
            "has_legacy_channel_rows": has_legacy,
        },
        "channels": sorted(channels.values(), key=lambda c: -c["revenue"]),
        "series": series,
        "top_categories": top_categories,
        "top_products": top_products,
        "centers": centers,
    }


def breakdown(
    db: Session,
    period: Period,
    dim: str = "category",
    limit: int = 100,
    offset: int = 0,
    scope: str = "all",
) -> list[dict]:
    """Drill-down rows for one dimension over the period, revenue-sorted,
    with the prior period alongside for deltas."""
    if dim == "center":
        return _center_breakdown(db, period, limit, offset)
    channels_filter = SCOPE_CHANNELS.get(scope)

    group_cols: tuple
    if dim == "product":
        group_cols = (Product.id, Product.name, Product.category)
        key = lambda r: r[0]  # noqa: E731
    elif dim == "channel":
        group_cols = (SalesMonthly.channel,)
        key = lambda r: _display_channel(r[0])  # noqa: E731
    else:  # category
        group_cols = (Product.category,)
        key = lambda r: r[0] or "(uncategorized)"  # noqa: E731

    cur_rows = _grouped(db, period.months, *group_cols, channels=channels_filter)
    prior_rows = _grouped(db, period.prior_months, *group_cols, channels=channels_filter)
    prior_by_key: dict = {}
    for r in prior_rows:
        k = key(r)
        prev = prior_by_key.setdefault(k, {"units": 0.0, "revenue": 0.0})
        prev["units"] += float(r.units or 0)
        prev["revenue"] += float(r.revenue or 0)

    agg: dict = {}
    for r in cur_rows:
        k = key(r)
        row = agg.setdefault(
            k,
            {
                "key": str(k),
                "label": _row_label(dim, r),
                "units": 0.0,
                "revenue": 0.0,
                "estimated": 0.0,
                **({"sku": "", "category": r.category or ""} if dim == "product" else {}),
            },
        )
        row["units"] += float(r.units or 0)
        row["revenue"] += float(r.revenue or 0)
        row["estimated"] += float(r.estimated or 0)

    if dim == "product":
        skus = {
            pid: (ref or sku)
            for pid, sku, ref in db.execute(
                select(Product.id, Product.global_sku, Product.odoo_internal_ref).where(
                    Product.id.in_([int(k) for k in agg])
                )
            )
        }
        for k, row in agg.items():
            row["sku"] = skus.get(int(k), "")

    total_revenue = sum(r["revenue"] for r in agg.values()) or 1.0
    out = []
    for k, row in agg.items():
        prior = prior_by_key.get(k, {"units": 0.0, "revenue": 0.0})
        out.append(
            {
                **row,
                "units": round(row["units"], 1),
                "revenue": round(row["revenue"], 2),
                "estimated_share": (row["estimated"] / row["revenue"]) if row["revenue"] else 0.0,
                "share": row["revenue"] / total_revenue,
                "prior_units": round(prior["units"], 1),
                "prior_revenue": round(prior["revenue"], 2),
                "delta_pct": _delta_pct(row["revenue"], prior["revenue"]),
            }
        )
    out.sort(key=lambda r: -r["revenue"])
    return out[offset : offset + limit]


def _row_label(dim: str, r) -> str:
    if dim == "product":
        return r.name
    if dim == "channel":
        ch = _display_channel(r.channel)
        return CHANNEL_LABELS.get(ch, ch)
    return r.category or "(uncategorized)"


def _center_breakdown(db: Session, period: Period, limit: int, offset: int) -> list[dict]:
    def rows(months):
        return db.execute(
            select(
                SalesCenterMonthly.config_name,
                func.sum(SalesCenterMonthly.units).label("units"),
                func.sum(SalesCenterMonthly.amount).label("revenue"),
            )
            .where(_ym_filter(SalesCenterMonthly.year, SalesCenterMonthly.month, months))
            .group_by(SalesCenterMonthly.config_name)
        ).all()

    prior = {r.config_name: r for r in rows(period.prior_months)}
    cur = rows(period.months)
    total_revenue = sum(float(r.revenue or 0) for r in cur) or 1.0
    out = []
    for r in cur:
        p = prior.get(r.config_name)
        out.append(
            {
                "key": r.config_name,
                "label": r.config_name,
                "units": round(float(r.units or 0), 1),
                "revenue": round(float(r.revenue or 0), 2),
                "estimated_share": 0.0,  # center rollups only exist post-split, amounts real
                "share": float(r.revenue or 0) / total_revenue,
                "prior_units": round(float(p.units or 0), 1) if p else 0.0,
                "prior_revenue": round(float(p.revenue or 0), 2) if p else 0.0,
                "delta_pct": _delta_pct(float(r.revenue or 0), float(p.revenue or 0) if p else 0.0),
            }
        )
    out.sort(key=lambda r: -r["revenue"])
    return out[offset : offset + limit]


def orders_summary(db: Session, period: Period, scope: str = "all") -> dict:
    """Order-level facts for the scope: order counts, average order value,
    and the customer-loyalty picture. Customer numbers count orders WITH a
    partner on file (~96% at POS, 100% online on this instance) — walk-ins
    are orders, not customers, and the payload says so.

    Distinct-customer counts are per month (the monthly rollup has no partner
    dimension to dedupe across months); new customers are exact for the
    period via customer_first_seen. The returning share is reported for the
    latest COMPLETE month so a half-month never reads as churn."""
    channels_filter = SCOPE_CHANNELS.get(scope)

    def rows(months):
        stmt = (
            select(
                SalesOrdersMonthly.year,
                SalesOrdersMonthly.month,
                func.sum(SalesOrdersMonthly.orders).label("orders"),
                func.sum(SalesOrdersMonthly.amount).label("amount"),
                func.sum(SalesOrdersMonthly.orders_with_customer).label("with_customer"),
                func.sum(SalesOrdersMonthly.distinct_customers).label("customers"),
                # "new"/"returning" are reserved words in SQL — suffix them
                func.sum(SalesOrdersMonthly.new_customers).label("new_cnt"),
                func.sum(SalesOrdersMonthly.returning_customers).label("returning_cnt"),
            )
            .where(_ym_filter(SalesOrdersMonthly.year, SalesOrdersMonthly.month, months))
            .group_by(SalesOrdersMonthly.year, SalesOrdersMonthly.month)
        )
        if channels_filter is not None:
            stmt = stmt.where(SalesOrdersMonthly.channel.in_(channels_filter))
        return db.execute(stmt).all()

    cur = rows(period.months)
    prior = rows(period.prior_months)

    series: list[dict[str, Any]] = [
        {
            "month": f"{r.year:04d}-{r.month:02d}",
            "orders": int(r.orders or 0),
            "amount": round(float(r.amount or 0), 2),
            "aov": round(float(r.amount or 0) / r.orders, 2) if r.orders else None,
            "known_share": (r.with_customer / r.orders) if r.orders else None,
            "customers": int(r.customers or 0),
            "new_customers": int(r.new_cnt or 0),
            "returning_customers": int(r.returning_cnt or 0),
        }
        for r in sorted(cur, key=lambda r: (r.year, r.month))
    ]

    orders_total = sum(int(r.orders or 0) for r in cur)
    amount_total = sum(float(r.amount or 0) for r in cur)
    prior_orders = sum(int(r.orders or 0) for r in prior)
    prior_amount = sum(float(r.amount or 0) for r in prior)
    aov = amount_total / orders_total if orders_total else None
    prior_aov = prior_amount / prior_orders if prior_orders else None

    # exact period-level new customers from the first-seen memory
    (y0, m0), (y1, m1) = period.months[0], period.months[-1]
    new_stmt = select(func.count()).select_from(CustomerFirstSeen).where(
        (
            func.extract("year", CustomerFirstSeen.first_order_on) * 100
            + func.extract("month", CustomerFirstSeen.first_order_on)
        ).between(y0 * 100 + m0, y1 * 100 + m1)
    )
    if channels_filter is not None:
        new_stmt = new_stmt.where(CustomerFirstSeen.channel.in_(channels_filter))
    new_customers = int(db.scalar(new_stmt) or 0)

    # returning share from the latest complete month in the period
    today = utcnow().date()
    complete = [
        r for r in series if r["month"] != f"{today.year:04d}-{today.month:02d}"
    ] or series
    last_full = complete[-1] if complete else None
    returning_share = None
    if last_full and (last_full["new_customers"] + last_full["returning_customers"]):
        returning_share = last_full["returning_customers"] / (
            last_full["new_customers"] + last_full["returning_customers"]
        )

    known = sum(int(r.with_customer or 0) for r in cur)
    return {
        "series": series,
        "totals": {
            "orders": orders_total,
            "amount": round(amount_total, 2),
            "aov": round(aov, 2) if aov is not None else None,
            "prior_orders": prior_orders,
            "prior_aov": round(prior_aov, 2) if prior_aov is not None else None,
            "orders_delta_pct": _delta_pct(orders_total, prior_orders),
            "aov_delta_pct": _delta_pct(aov or 0, prior_aov or 0) if aov and prior_aov else None,
            "new_customers": new_customers,
            "returning_share_last_month": returning_share,
            "returning_share_month": last_full["month"] if last_full else None,
            "known_customer_share": (known / orders_total) if orders_total else None,
        },
        "caveat": (
            "Customer metrics count orders with a customer on file; walk-in "
            "sales count as orders but not customers."
        ),
    }


def _delta_pct(cur: float, prior: float) -> float | None:
    """Growth vs the prior period; None when there's no basis (prior = 0)."""
    if not prior:
        return None
    return (cur - prior) / prior
