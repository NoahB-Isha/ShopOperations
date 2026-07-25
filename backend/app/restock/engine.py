"""Restock lists — the ILscripts `daily_restock_front.py` logic, absorbed.

The original script summed each day's POS units per product into a running
counter kept in a JSON file; when a counter reached the threshold (default 4)
the item was printed on the restock sheet and its counter reset to zero.
This module keeps that accumulator exactly, plus what the script never had:
persistence, honesty about data freshness, and a second list for pulling
stock forward from the warehouse.

FLOOR list (bring items from the back onto the shelves):
  * Every COMPLETE day D (UTC dates, matching sales_daily), each product's
    POS units are folded into `restock_accum.accumulated`.
  * When a counter reaches `restock_floor_threshold`, the product is flagged:
    an open `restock_lines` row is created for the accumulated quantity and
    the counter resets to 0 — the script's behavior, verbatim.
  * An open line survives until checked off (nothing vanishes overnight, an
    improvement on the script, which printed once). A product crossing the
    threshold again while its line is open grows that line instead of
    duplicating it. Checked lines drop off the list the next day, so the
    checklist reads fresh every morning.
  * The fold is idempotent (`restock_fold_state.folded_through`); the first
    fold ever starts at yesterday rather than replaying months of history.

BACK-STOCK list (pull items forward from BWHSE — computed, never stored):
  * avg_daily = trailing `restock_avg_window_days` of POS units / window days
  * candidate: active + stock-tracked + avg_daily > 0 + BWHSE qty > 0 and
    floor qty below `restock_low_cover_days` of cover
  * suggested = min(bwhse_qty, ceil(avg_daily * restock_target_cover_days
    - floor_qty)), floor-of-one
  * sorted worst cover first. Check-off rows are keyed by day, so this list
    resets daily by construction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    SHOPPE_CHANNELS,
    Product,
    RestockAccum,
    RestockCheckoff,
    RestockFoldState,
    RestockLine,
    SalesDaily,
    StockLevel,
    not_blacklisted,
    utcnow,
)

# Floor restock counts SHOPPE-floor POS sales only: city-center and campus
# event POS sales don't deplete the floor. Legacy pre-split 'pos' rows count
# as Shoppe until a sales re-backfill reclassifies them.
FLOOR_LIST = "floor"
BACK_LIST = "back"


# --------------------------------------------------------------------- fold
def fold_floor_restock(db: Session, settings: Settings, today: date) -> int:
    """Fold every unfolded complete day (≤ yesterday) into the accumulators,
    flagging lines that cross the threshold. Returns the number of lines
    created or grown. Idempotent: each day folds exactly once, ever."""
    state = db.get(RestockFoldState, 1)
    yesterday = today - timedelta(days=1)
    if state is None:
        state = RestockFoldState(id=1, folded_through=None)
        db.add(state)
    start = yesterday if state.folded_through is None else state.folded_through + timedelta(days=1)
    if start > yesterday:
        return 0  # nothing new to fold

    eligible = {
        pid
        for (pid,) in db.execute(
            select(Product.id).where(
                Product.is_active.is_(True),
                Product.is_stock_tracked.is_(True),
                # non-retail POS items (campus meals, prasadam…) never restock
                Product.restock_exclude.is_(False),
                not_blacklisted(),
            )
        )
    }
    accums = {a.product_id: a for a in db.scalars(select(RestockAccum))}
    threshold = float(settings.restock_floor_threshold)
    flagged = 0

    day = start
    while day <= yesterday:
        rows = db.execute(
            select(SalesDaily.product_id, SalesDaily.units).where(
                SalesDaily.day == day, SalesDaily.channel.in_(SHOPPE_CHANNELS)
            )
        )
        for product_id, units in rows:
            if product_id not in eligible or not units or units <= 0:
                continue
            accum = accums.get(product_id)
            if accum is None:
                accum = RestockAccum(product_id=product_id, accumulated=0.0)
                db.add(accum)
                accums[product_id] = accum
            accum.accumulated = round(accum.accumulated + float(units), 3)
            if accum.accumulated >= threshold:
                _flag(db, product_id, accum.accumulated, day)
                accum.accumulated = 0.0
                accum.last_flagged_on = day
                flagged += 1
        day += timedelta(days=1)

    state.folded_through = yesterday
    db.commit()
    return flagged


def _flag(db: Session, product_id: int, qty: float, day: date) -> None:
    open_line = db.scalar(
        select(RestockLine).where(
            RestockLine.product_id == product_id,
            RestockLine.list_type == FLOOR_LIST,
            RestockLine.checked_off_at.is_(None),
        )
    )
    if open_line is not None:
        open_line.qty = round(open_line.qty + qty, 3)
        open_line.flagged_on = day
    else:
        db.add(
            RestockLine(list_type=FLOOR_LIST, product_id=product_id, qty=round(qty, 3), flagged_on=day)
        )


# -------------------------------------------------------------------- lists
@dataclass
class FloorItem:
    line_id: int
    product_id: int
    qty: float
    flagged_on: date
    checked: bool


@dataclass
class BackItem:
    product_id: int
    floor_qty: float
    bwhse_qty: float
    avg_daily: float
    days_of_cover: float | None  # None = no floor stock at all
    suggested_qty: float
    checked: bool


def floor_list(db: Session, today: date) -> list[FloorItem]:
    """Open lines plus lines checked off today (shown struck-through until
    the day rolls over). Products excluded AFTER being flagged disappear
    immediately — the filter applies at read time too."""
    start_of_today = today  # checked_off_at is a datetime; compare on date
    items: list[FloorItem] = []
    for line in db.scalars(
        select(RestockLine)
        .join(Product, Product.id == RestockLine.product_id)
        .where(RestockLine.list_type == FLOOR_LIST, Product.restock_exclude.is_(False))
        .order_by(RestockLine.flagged_on.desc(), RestockLine.id)
    ):
        checked_at = line.checked_off_at
        if checked_at is not None and checked_at.date() < start_of_today:
            continue  # yesterday's completed work — gone from today's list
        items.append(
            FloorItem(
                line_id=line.id,
                product_id=line.product_id,
                qty=line.qty,
                flagged_on=line.flagged_on,
                checked=checked_at is not None,
            )
        )
    return items


def back_list(db: Session, settings: Settings, today: date) -> list[BackItem]:
    window = int(settings.restock_avg_window_days)
    since = today - timedelta(days=window)
    sold = {
        pid: float(units or 0)
        for pid, units in db.execute(
            select(SalesDaily.product_id, func.sum(SalesDaily.units))
            .where(
                SalesDaily.channel.in_(SHOPPE_CHANNELS),
                SalesDaily.day >= since,
                SalesDaily.day < today,
            )
            .group_by(SalesDaily.product_id)
        )
    }
    if not sold:
        return []

    stock: dict[int, dict[str, float]] = {}
    for pid, key, qty in db.execute(
        select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
            StockLevel.product_id.in_(sold)
        )
    ):
        stock.setdefault(pid, {})[key] = float(qty)

    eligible = {
        pid
        for (pid,) in db.execute(
            select(Product.id).where(
                Product.id.in_(sold),
                Product.is_active.is_(True),
                Product.is_stock_tracked.is_(True),
                Product.restock_exclude.is_(False),
                not_blacklisted(),
            )
        )
    }
    checked_today = {
        pid
        for (pid,) in db.execute(
            select(RestockCheckoff.product_id).where(
                RestockCheckoff.day == today, RestockCheckoff.list_type == BACK_LIST
            )
        )
    }

    low_cover = float(settings.restock_low_cover_days)
    target_cover = float(settings.restock_target_cover_days)
    items: list[BackItem] = []
    for pid, units in sold.items():
        if pid not in eligible:
            continue
        avg_daily = units / window
        if avg_daily <= 0:
            continue
        floor_qty = stock.get(pid, {}).get("floor", 0.0)
        bwhse_qty = stock.get(pid, {}).get("bwhse", 0.0)
        if bwhse_qty <= 0:
            continue
        if floor_qty >= avg_daily * low_cover:
            continue
        suggested = min(bwhse_qty, max(1.0, math.ceil(avg_daily * target_cover - floor_qty)))
        items.append(
            BackItem(
                product_id=pid,
                floor_qty=floor_qty,
                bwhse_qty=bwhse_qty,
                avg_daily=round(avg_daily, 3),
                days_of_cover=round(floor_qty / avg_daily, 1) if floor_qty > 0 else None,
                suggested_qty=suggested,
                checked=pid in checked_today,
            )
        )
    items.sort(key=lambda i: (i.days_of_cover is not None, i.days_of_cover or 0.0))
    return items


# -------------------------------------------------------------------- reset
def reset_floor(db: Session, today: date, actor_user_id: int | None = None) -> dict:
    """The floor was just physically fully restocked: wipe the checklist
    (open AND checked-off lines — the old list is void), zero every
    accumulator, and make TODAY an amnesty day, so counting resumes with
    tomorrow's sales. Records who reset and when, so the empty list can
    explain itself. Commits."""
    # SQLAlchemy types DML executes as Result[Any]; the runtime object is a
    # CursorResult carrying rowcount.
    n_lines = _dml_rowcount(
        db.execute(delete(RestockLine).where(RestockLine.list_type == FLOOR_LIST))
    )
    n_accums = _dml_rowcount(
        db.execute(
            update(RestockAccum).values(
                accumulated=0.0, last_flagged_on=None, updated_at=utcnow()
            )
        )
    )
    state = db.get(RestockFoldState, 1)
    if state is None:
        state = RestockFoldState(id=1)
        db.add(state)
    state.folded_through = today
    state.last_reset_at = utcnow()
    state.last_reset_by_id = actor_user_id
    db.commit()
    return {"lines_cleared": int(n_lines), "accumulators_zeroed": int(n_accums)}


def _dml_rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
