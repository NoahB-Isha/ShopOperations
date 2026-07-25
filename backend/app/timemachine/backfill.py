"""Reconstructed stock history — the time machine's past, before the app
existed to capture it.

Odoo can compute on-hand AS OF a past date (`qty_available` under a
`to_date` + `location` context — it replays the move ledger server-side;
verified live 2026-07-23). That's real data, not a guess, so backfilling is
allowed — but it's a heavy computation for Odoo, so the protocol is polite:

  * an ADMIN requests it (never automatic), picking how many weeks back;
  * the request becomes a queue in the `timemachine_backfill_state`
    AppSetting; the WORKER processes ONE weekly date per loop pass
    (~3 reads each, minutes apart in wall-clock terms);
  * reconstructed days are marked source='reconstructed' and the past view
    says so — they never impersonate live-captured snapshots, and a date the
    live capture already covers is never overwritten.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    OdooLocation,
    Product,
    StockSnapshot,
    StockSnapshotDay,
    utcnow,
)
from ..odoo.protocol import OdooConnection
from ..ordering.service import get_app_setting, set_app_setting

log = logging.getLogger("timemachine")

STATE_SETTING_KEY = "timemachine_backfill_state"
MAX_ATTEMPTS_PER_DATE = 3


def request_backfill(db: Session, settings: Settings, weeks: int | None = None) -> dict:
    """Queue weekly reconstruction dates (most recent first — most useful
    first). Dates already covered by live capture are skipped; previously
    reconstructed dates are refreshed. Returns the new state."""
    weeks = min(max(weeks or settings.timemachine_backfill_weeks, 1), 104)
    today = utcnow().date()
    covered_live = {
        d
        for d, source in db.execute(
            select(StockSnapshotDay.snapshot_date, StockSnapshotDay.source)
        )
        if source != "reconstructed"
    }
    pending = [
        (today - timedelta(weeks=k)).isoformat()
        for k in range(1, weeks + 1)
        if (today - timedelta(weeks=k)) not in covered_live
    ]
    state = {
        "pending": pending,
        "attempts": {},
        "errors": {},
        "requested_at": utcnow().isoformat(),
        "requested_weeks": weeks,
        "done": 0,
    }
    set_app_setting(db, STATE_SETTING_KEY, state)
    db.commit()
    return state


def backfill_state(db: Session) -> dict:
    return get_app_setting(db, STATE_SETTING_KEY)


def process_next(db: Session, settings: Settings, conn: OdooConnection) -> str | None:
    """Reconstruct ONE pending date (the worker calls this once per loop
    pass). Returns the date processed, or None when the queue is idle."""
    state = backfill_state(db)
    pending: list[str] = list(state.get("pending") or [])
    if not pending:
        return None
    day_iso = pending[0]
    day = date.fromisoformat(day_iso)

    try:
        rows = _reconstruct_day(db, conn, day)
    except Exception as e:  # noqa: BLE001 — the queue must survive an Odoo hiccup
        attempts = dict(state.get("attempts") or {})
        attempts[day_iso] = int(attempts.get(day_iso, 0)) + 1
        errors = dict(state.get("errors") or {})
        errors[day_iso] = str(e)[:300]
        if attempts[day_iso] >= MAX_ATTEMPTS_PER_DATE:
            pending.remove(day_iso)  # give up on this date, keep the rest moving
            log.warning("backfill %s abandoned after %s attempts: %s", day_iso, attempts[day_iso], e)
        else:
            # retry later — move it to the back of the queue
            pending = pending[1:] + [day_iso]
            log.warning("backfill %s failed (attempt %s): %s", day_iso, attempts[day_iso], e)
        set_app_setting(
            db, STATE_SETTING_KEY, {**state, "pending": pending, "attempts": attempts, "errors": errors}
        )
        db.commit()
        return None

    pending.remove(day_iso)
    set_app_setting(
        db,
        STATE_SETTING_KEY,
        {**state, "pending": pending, "done": int(state.get("done", 0)) + 1,
         "last_processed": day_iso},
    )
    db.commit()
    log.info("backfilled stock history for %s (%s rows)", day_iso, rows)
    return day_iso


def _reconstruct_day(db: Session, conn: OdooConnection, day: date) -> int:
    """One date: ask Odoo for as-of quantities per app location root and
    replace that day's snapshot rows. Never touches a live-captured day."""
    existing = db.get(StockSnapshotDay, day)
    if existing is not None and existing.source != "reconstructed":
        return 0  # live capture wins, always

    roots = {loc.key: loc.odoo_id for loc in db.scalars(select(OdooLocation))}
    if not roots:
        raise RuntimeError("no Odoo locations mapped yet — run a stock sync first")
    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(Product.odoo_product_id.is_not(None))
        )
    }

    totals: dict[tuple[int, str], float] = {}
    for key, odoo_loc_id in roots.items():
        records = conn.call_kw(
            "product.product",
            "search_read",
            [[["qty_available", "!=", 0]], ["qty_available"]],
            {"context": {"to_date": f"{day.isoformat()} 23:59:59", "location": odoo_loc_id}},
        )
        for r in records:
            product_id = id_by_odoo_pid.get(r.get("id"))
            if product_id is None:
                continue
            qty = float(r.get("qty_available") or 0.0)
            if qty > 0:
                totals[(product_id, key)] = qty

    now = utcnow()
    db.execute(delete(StockSnapshot).where(StockSnapshot.snapshot_date == day))
    for (product_id, key), qty in totals.items():
        db.add(
            StockSnapshot(
                snapshot_date=day,
                product_id=product_id,
                location_key=key,
                qty=qty,
                captured_at=now,
            )
        )
    if existing is None:
        db.add(
            StockSnapshotDay(
                snapshot_date=day, captured_at=now, rows=len(totals), source="reconstructed"
            )
        )
    else:
        existing.captured_at = now
        existing.rows = len(totals)
    db.commit()
    return len(totals)
