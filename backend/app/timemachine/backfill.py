"""Reconstructed stock history — the time machine's past, before the app
existed to capture it.

Odoo can compute on-hand AS OF a past date (`qty_available` under a
`to_date` + `location` context — it replays the move ledger server-side;
verified live 2026-07-23). That's real data, not a guess, so backfilling is
allowed — but it's a heavy computation for Odoo, so the protocol is polite:

  * an ADMIN requests it (never automatic), picking how many weeks back;
  * the request becomes a queue in the `timemachine_backfill_state`
    AppSetting; the WORKER processes ONE weekly date per loop pass
    (one read per app location incl. folded ones, minutes apart in
    wall-clock terms);
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
    ODOO_FOLDED_LOCATION_NAMES,
    OdooLocation,
    Product,
    StockSnapshot,
    StockSnapshotDay,
    not_blacklisted,
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


def _read_day_totals(
    db: Session, conn: OdooConnection, day: date
) -> dict[tuple[int, str], float]:
    """Ask Odoo for as-of quantities per app location root (incl. folded ones)
    and fold them into {(product_id, location_key): qty}. Pure read — shared by
    the write path and by repair dry-runs."""
    reads = [(loc.key, loc.odoo_id) for loc in db.scalars(select(OdooLocation))]
    if not reads:
        raise RuntimeError("no Odoo locations mapped yet — run a stock sync first")
    # Folded locations (III/Stock/SHIP -> bwhse) count toward their key in
    # reconstructed history too, so past days mean the same thing live
    # captures do. Resolved by name here — they have no OdooLocation row.
    if ODOO_FOLDED_LOCATION_NAMES:
        for loc in conn.search_read(
            "stock.location",
            [["complete_name", "in", list(ODOO_FOLDED_LOCATION_NAMES)]],
            ["complete_name"],
        ):
            reads.append((ODOO_FOLDED_LOCATION_NAMES[loc["complete_name"]], loc["id"]))
    # Blacklisted products are excluded, and that is load-bearing rather than
    # cosmetic. The live sync reads stock.quant, which attributes stock to one
    # product; qty_available reports the same units under the "- USA" duplicate
    # records too, so including them double-counts (~115k units over 96
    # products, measured 2026-08-07). Filtering here is what makes a
    # reconstructed day mean the same thing as a live-captured one.
    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id)
            .where(Product.odoo_product_id.is_not(None))
            .where(not_blacklisted())
        )
    }

    totals: dict[tuple[int, str], float] = {}
    for key, odoo_loc_id in reads:
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
                totals[(product_id, key)] = totals.get((product_id, key), 0.0) + qty
    return totals


def _reconstruct_day(
    db: Session, conn: OdooConnection, day: date, *, allow_live_overwrite: bool = False
) -> int:
    """One date: ask Odoo for as-of quantities and replace that day's snapshot
    rows. Never touches a live-captured day unless the caller explicitly opts
    in — see repair_range() for the one situation that does."""
    existing = db.get(StockSnapshotDay, day)
    if existing is not None and existing.source != "reconstructed" and not allow_live_overwrite:
        return 0  # live capture wins, always

    totals = _read_day_totals(db, conn, day)
    # An empty read against a day that previously held rows is an Odoo hiccup,
    # not a day on which the business held no stock — replacing on that basis
    # would silently delete real history.
    if not totals and existing is not None and existing.rows:
        raise RuntimeError(
            f"refusing to replace {day}: Odoo returned no stock rows but the day "
            f"already holds {existing.rows}. Treating this as a failed read."
        )

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
        # These numbers now come from the move ledger, not from what the sync
        # saw that day. Say so — a repaired day must not keep claiming to be a
        # live capture, or the past view would overstate its confidence.
        existing.source = "reconstructed"
    db.commit()
    return len(totals)


def repair_range(
    db: Session,
    settings: Settings,
    conn: OdooConnection,
    start: date,
    end: date,
    *,
    include_live: bool = False,
    dry_run: bool = True,
) -> dict:
    """Re-reconstruct every day in [start, end] from Odoo's move ledger.

    This exists for one specific situation: a change to the synced location set
    (the 2026-08-04 III/Stock/SHIP fold) means days captured before it recorded
    a quantity the app now computes differently. Those rows aren't corrupt —
    they faithfully record what the app could see at the time — but they no
    longer mean the same thing as today's, so the series reads as a cliff.
    Reconstruction is what makes it comparable end to end.

    `include_live=True` is the deliberate opt-in that lets this overwrite
    sync-captured days; repaired days are re-marked 'reconstructed'. Kept off
    the worker queue and off any schedule on purpose — overwriting live capture
    is a decision someone makes about a known window, never something a
    scheduler does on its own.

    dry_run=True (the default) reads Odoo and reports the diff without writing.
    """
    if end < start:
        raise ValueError(f"end ({end}) is before start ({start})")
    changes: list[dict] = []
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    for day in days:
        existing = db.get(StockSnapshotDay, day)
        if existing is None:
            # Repair fixes days that exist; it does not invent new ones. Without
            # this, a wide range would reconstruct every uncovered date in it —
            # hundreds of extra heavy Odoo reads, and a daily history the app
            # never captured. Adding coverage is request_backfill()'s job.
            changes.append({"date": day.isoformat(), "action": "skipped-no-history"})
            continue
        is_live = existing.source != "reconstructed"
        if is_live and not include_live:
            changes.append({"date": day.isoformat(), "action": "skipped-live"})
            continue
        before = {
            (pid, key): qty
            for pid, key, qty in db.execute(
                select(StockSnapshot.product_id, StockSnapshot.location_key, StockSnapshot.qty)
                .where(StockSnapshot.snapshot_date == day)
            )
        }
        if dry_run:
            after = _read_day_totals(db, conn, day)
            if not after and existing is not None and existing.rows:
                changes.append({"date": day.isoformat(), "action": "would-fail-empty-read"})
                continue
        else:
            _reconstruct_day(db, conn, day, allow_live_overwrite=include_live)
            after = {
                (pid, key): qty
                for pid, key, qty in db.execute(
                    select(StockSnapshot.product_id, StockSnapshot.location_key, StockSnapshot.qty)
                    .where(StockSnapshot.snapshot_date == day)
                )
            }
        changes.append(
            {
                "date": day.isoformat(),
                "action": "dry-run" if dry_run else ("repaired-live" if is_live else "repaired"),
                "units_before": round(sum(before.values())),
                "units_after": round(sum(after.values())),
                "rows_before": len(before),
                "rows_after": len(after),
            }
        )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "dry_run": dry_run,
        "include_live": include_live,
        "days": changes,
    }
