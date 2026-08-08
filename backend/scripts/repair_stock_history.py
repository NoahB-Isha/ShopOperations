"""Repair reconstructed stock history for a date range.

Why this is a script and not an admin endpoint: the general backfill is a
polite drip-feed processed by the WORKER, one weekly date per loop pass. The
Render deployment runs no worker (free tier), so a queued backfill there would
sit pending forever — and this particular job overwrites live-captured days,
which is a deliberate one-off decision, not something to expose as a button.

Typical use (the 2026-08-04 III/Stock/SHIP fold left earlier days understated):

    # look first — reads Odoo, writes nothing
    uv run python scripts/repair_stock_history.py --start 2026-01-01 --end 2026-08-03

    # then commit to it, including days the sync captured
    uv run python scripts/repair_stock_history.py --start 2026-01-01 --end 2026-08-03 \
        --include-live --apply

Point it at another database with DATABASE_URL (session pooler for Supabase,
port 5432 — the transaction pooler is for the app, not long-running jobs):

    DATABASE_URL='postgresql+psycopg://...:5432/postgres?sslmode=require' \
        uv run python scripts/repair_stock_history.py ...

Each day costs one Odoo read per location root (~5), each returning every
product with nonzero stock. Reads are safe, but this is heavy for Odoo — run it
deliberately, not on a schedule.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from app.config import get_settings
from app.db import get_sessionmaker
from app.odoo.connection import get_connection
from app.timemachine.backfill import repair_range


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True, type=date.fromisoformat, help="first day (YYYY-MM-DD)")
    p.add_argument("--end", required=True, type=date.fromisoformat, help="last day, inclusive")
    p.add_argument(
        "--include-live",
        action="store_true",
        help="also overwrite days the live sync captured (they get re-marked 'reconstructed')",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually write. Without this the run is a dry run and only reports the diff.",
    )
    args = p.parse_args()

    settings = get_settings()
    if settings.odoo_mode != "live":
        print(
            "REFUSING: Odoo is in fixture mode — reconstructing from fixtures would "
            "overwrite real history with demo numbers. Set the ODOO_* credentials.",
            file=sys.stderr,
        )
        return 2

    db = get_sessionmaker()()
    try:
        conn = get_connection(settings)
        result = repair_range(
            db,
            settings,
            conn,
            args.start,
            args.end,
            include_live=args.include_live,
            dry_run=not args.apply,
        )
    finally:
        db.close()

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written)"
    print(f"\n{mode} — {result['start']} .. {result['end']}  include_live={result['include_live']}\n")
    print(f"{'date':<12} {'action':<22} {'units before':>13} {'units after':>12} {'delta':>10}")
    changed = 0
    skipped: dict[str, int] = {}
    for d in result["days"]:
        # Any action without unit counts is a skip; count them rather than
        # printing a line per day (a wide range is mostly uncovered dates).
        if "units_before" not in d:
            skipped[d["action"]] = skipped.get(d["action"], 0) + 1
            continue
        before, after = d["units_before"], d["units_after"]
        delta = after - before
        if delta:
            changed += 1
        print(f"{d['date']:<12} {d['action']:<22} {before:>13,} {after:>12,} {delta:>+10,}")
    for action, n in sorted(skipped.items()):
        print(f"{'':<12} {action:<22} {n:>13} day(s)")
    print(f"\n{changed} of {len(result['days'])} days differ.")
    if not args.apply and changed:
        print("Re-run with --apply (and --include-live if you need the sync days) to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
