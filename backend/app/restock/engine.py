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
    SuggestionSnooze,
    SyncState,
    TransferRequest,
    TransferRequestLine,
    not_blacklisted,
    utcnow,
)
from ..transfers.flow import ACTIVE_STATUSES

# Floor restock counts SHOPPE-floor POS sales only: city-center and campus
# event POS sales don't deplete the floor. Legacy pre-split 'pos' rows count
# as Shoppe until a sales re-backfill reclassifies them.
FLOOR_LIST = "floor"
BACK_LIST = "back"


# --------------------------------------------------------------------- fold
def sales_covered_through(db: Session) -> date | None:
    """The last day the sales sync has certainly seen in full, or None when
    there is no sync state at all (fixtures, demo, a fresh install).

    Each sales sync re-pulls the whole current month, so a run that succeeded
    at time T holds every day BEFORE T's date complete; T's own date is still
    accumulating. Anything after that is a day the app simply hasn't been told
    about yet.
    """
    state = db.get(SyncState, "sales")
    if state is None:
        return None
    if state.last_success_at is None:
        return date.min  # a sync exists but has never succeeded — fold nothing
    return state.last_success_at.date() - timedelta(days=1)


def fold_floor_restock(db: Session, settings: Settings, today: date) -> int:
    """Fold every unfolded complete day (≤ yesterday) into the accumulators,
    flagging lines that cross the threshold. Returns the number of lines
    created or grown. Idempotent: each day folds exactly once, ever.

    A day is only folded once the SALES SYNC has covered it. Folding is a
    one-way door — `folded_through` moves forward and that day is never
    counted again — so folding a day whose sales haven't arrived yet burns it:
    the units land in `sales_daily` later and never reach an accumulator.
    That is not hypothetical. On the hosted stack (no worker; syncs only run
    when someone clicks) the sales sync last succeeded 2026-08-13 while the
    fold had already consumed 08-14, so a day of shop sales could never flag
    anything (found 2026-08-15).
    """
    state = db.get(RestockFoldState, 1)
    yesterday = today - timedelta(days=1)
    if state is None:
        state = RestockFoldState(id=1, folded_through=None)
        db.add(state)
    covered = sales_covered_through(db)
    # No sync state at all means fixture/demo data loaded straight into
    # sales_daily — there is nothing to be behind, so complete days fold.
    horizon = yesterday if covered is None else min(yesterday, covered)
    start = yesterday if state.folded_through is None else state.folded_through + timedelta(days=1)
    if start > horizon:
        return 0  # nothing new the sales sync has actually covered

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
    while day <= horizon:
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

    state.folded_through = horizon
    db.commit()
    return flagged


def expire_stale_lines(db: Session, settings: Settings, today: date) -> int:
    """Age out floor lines nobody checked off. Returns how many just expired.

    The old rule was "an open line survives until checked off", which sounded
    kind and read as a list that repeated every morning: on the live stack 20
    of 51 open lines were 15-19 days old and had been on every day's list since
    July. A line that has sat for `restock_line_max_age_days` is not a task
    anyone is about to do — the item keeps selling, so it will flag again on
    its own merits, with an honest fresh quantity.

    The row is kept and stamped rather than deleted: it is the record of what
    the floor was asked for and never did.
    """
    max_age = int(settings.restock_line_max_age_days)
    if max_age <= 0:
        return 0  # 0 disables ageing — lines live until checked off
    cutoff = today - timedelta(days=max_age)
    rows = db.scalars(
        select(RestockLine).where(
            RestockLine.list_type == FLOOR_LIST,
            RestockLine.checked_off_at.is_(None),
            RestockLine.expired_at.is_(None),
            RestockLine.flagged_on < cutoff,
        )
    ).all()
    now = utcnow()
    for line in rows:
        line.expired_at = now
    if rows:
        db.commit()
    return len(rows)


def _flag(db: Session, product_id: int, qty: float, day: date) -> None:
    open_line = db.scalar(
        select(RestockLine).where(
            RestockLine.product_id == product_id,
            RestockLine.list_type == FLOOR_LIST,
            RestockLine.checked_off_at.is_(None),
            # an aged-out line is closed: a new crossing starts a fresh one
            # rather than reviving a row from three weeks ago
            RestockLine.expired_at.is_(None),
        )
    )
    if open_line is not None:
        open_line.qty = round(open_line.qty + qty, 3)
        open_line.flagged_on = day
    else:
        db.add(
            RestockLine(list_type=FLOOR_LIST, product_id=product_id, qty=round(qty, 3), flagged_on=day)
        )
        # Flush so the SELECT above can see it next time. Sessions here are
        # autoflush=False, so without this a line added for one day is still
        # pending when the next day is folded — a catch-up fold covering
        # several days would add a SECOND open line for the same product
        # instead of growing the first, and the item shows twice on the floor
        # list. (Live: Devi Red Pendant Cord, lines flagged 07-27 and 07-29.)
        db.flush()


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
    lines = list(
        db.scalars(
            select(RestockLine)
            .join(Product, Product.id == RestockLine.product_id)
            .where(
                RestockLine.list_type == FLOOR_LIST,
                RestockLine.expired_at.is_(None),
                Product.restock_exclude.is_(False),
            )
            .order_by(RestockLine.flagged_on.desc(), RestockLine.id)
        )
    )
    # `floor` is III/Stock/III-FLOOR — the shop floor AND its back stockroom as
    # one location. Nothing there means there is nothing to carry out to the
    # shelf, so the line is noise on a picking list; that item is an
    # out-of-stock problem, which the OOS board owns.
    in_the_back = {
        pid: float(qty)
        for pid, qty in db.execute(
            select(StockLevel.product_id, StockLevel.qty).where(
                StockLevel.location_key == "floor",
                StockLevel.product_id.in_([line.product_id for line in lines] or [0]),
            )
        )
    }
    items: list[FloorItem] = []
    for line in lines:
        checked_at = line.checked_off_at
        if checked_at is not None and checked_at.date() < start_of_today:
            continue  # yesterday's completed work — gone from today's list
        if checked_at is None:
            # Only unchecked lines are filtered: something just ticked off
            # should stay struck-through for the rest of the day rather than
            # vanishing under the finger that tapped it.
            if in_the_back.get(line.product_id, 0.0) <= 0:
                continue
            if line.snoozed_until is not None and line.snoozed_until > today:
                continue  # swiped away for now — back on the list tomorrow
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


def snooze_floor_line(db: Session, line: RestockLine, today: date, *, snoozed: bool) -> None:
    """Hide an open line until tomorrow, or bring it back now. The line keeps
    its accumulated qty either way — this defers the work, it doesn't cancel
    it, and a snoozed item that sells more keeps growing in the background."""
    line.snoozed_until = (today + timedelta(days=1)) if snoozed else None
    db.commit()


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
    # "Not this week" — a manager swiped this suggestion away. The numbers
    # haven't changed, so it would otherwise be back tomorrow morning.
    snoozed = {
        pid
        for (pid,) in db.execute(
            select(SuggestionSnooze.product_id).where(SuggestionSnooze.snoozed_until > today)
        )
    }
    # Already asked for. The one action on this list is "turn it into a
    # transfer request", so anything riding an open request is handled —
    # leaving it visible just invites a second request for the same stock.
    # It comes back if that request is cancelled, since only ACTIVE ones count.
    on_open_request = {
        pid
        for (pid,) in db.execute(
            select(TransferRequestLine.product_id)
            .join(TransferRequest, TransferRequest.id == TransferRequestLine.request_id)
            .where(TransferRequest.status.in_(ACTIVE_STATUSES))
        )
    }

    low_cover = float(settings.restock_low_cover_days)
    target_cover = float(settings.restock_target_cover_days)
    items: list[BackItem] = []
    for pid, units in sold.items():
        if pid not in eligible or pid in on_open_request or pid in snoozed:
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
