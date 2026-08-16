"""Per-center monthly sales, for the size and trend of a dot on the map.

City centers are pop-ups: most set up once a month, sell for a day or two, and
pack away. That shapes both decisions here.

  * The comparison is MONTH over MONTH, because that is one setup against the
    previous setup. Week-over-week would compare a shop that happened to a shop
    that didn't.
  * Both months are COMPLETE months. Today's month is a half-written sentence —
    on the 3rd it would show every center as collapsing, and on the 30th as
    recovered, neither of which happened.

Source is `sales_center_monthly`, the pos.config-level rollup the sales sync
already keeps (no product dimension, which is all a dot needs).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SalesCenterMonthly


@dataclass(frozen=True)
class CenterSales:
    units: float
    amount: float | None
    prev_units: float
    prev_amount: float | None


def month_before(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def comparison_months(today: date) -> tuple[tuple[int, int], tuple[int, int]]:
    """(latest complete month, the one before it)."""
    latest = month_before(today.year, today.month)
    return latest, month_before(*latest)


def _totals(db: Session, bucket: tuple[int, int]) -> dict[int, tuple[float, float | None]]:
    year, month = bucket
    rows = db.execute(
        select(
            SalesCenterMonthly.center_id,
            func.sum(SalesCenterMonthly.units),
            func.sum(SalesCenterMonthly.amount),
        )
        .where(
            SalesCenterMonthly.year == year,
            SalesCenterMonthly.month == month,
            SalesCenterMonthly.center_id.is_not(None),
        )
        .group_by(SalesCenterMonthly.center_id)
    )
    return {
        center_id: (float(units or 0), float(amount) if amount is not None else None)
        for center_id, units, amount in rows
        if center_id is not None
    }


def sales_by_center(db: Session, today: date) -> dict[int, CenterSales]:
    """center id -> the two complete months. Centers with no rows are absent;
    a center that sold nothing and a center the rollup has never heard of are
    different facts, and the caller gets to tell them apart."""
    latest, previous = comparison_months(today)
    now, before = _totals(db, latest), _totals(db, previous)
    out: dict[int, CenterSales] = {}
    for center_id in set(now) | set(before):
        units, amount = now.get(center_id, (0.0, None))
        prev_units, prev_amount = before.get(center_id, (0.0, None))
        out[center_id] = CenterSales(
            units=units, amount=amount, prev_units=prev_units, prev_amount=prev_amount
        )
    return out
