"""Catch up approved inventory counts that Odoo never heard, or heard as a draft.

Approving a counted item now validates its adjustment in Odoo (see
counting/service.post_adjustment). This walks the rows approved before that,
which are in one of two states:

  * `created` — the adjustment is sitting in Odoo, ready and unposted, so the
    shelf figure it corrects is still wrong. It gets posted.
  * `failed`  — no Odoo record at all. On 2026-08-22 a 65-item review made one
    picking-type lookup per item and Odoo's proxy answered HTTP 429, so 16
    approvals wrote nothing. They get created AND posted (the lookup is cached
    now, so the burst that caused it can't recur).

Why a script and not a button: it is a one-off catch-up for a behaviour that
has since changed, and the deployed stack has no shell for a data migration
that needs a live Odoo read either way.

    # look first — reads Odoo, writes nothing (default)
    uv run python scripts/post_count_adjustments.py

    # then commit to it
    uv run python scripts/post_count_adjustments.py --apply

Point it at another database with DATABASE_URL (Supabase: the SESSION pooler
on 5432, not the transaction pooler):

    DATABASE_URL='postgresql+psycopg://...:5432/postgres?sslmode=require' \
        uv run python scripts/post_count_adjustments.py --apply

Safety is the writer's, not this script's: `validate_adjustment` refuses any
picking whose origin isn't app-prefixed and any picking that isn't on an
inventory-adjustment operation type — which is what keeps the floor's
STAGING→FLOOR count transfers (they share the ILAPP-CNT- prefix) out of it.
"""
from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.counting import locations
from app.counting.service import apply_to_odoo, post_adjustment
from app.db import get_sessionmaker
from app.models import FeatureFlag, InventoryCountItem, OdooWriteOutcome
from app.odoo.connection import get_connection
from sqlalchemy import select


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="actually post. Without this the run only reports what it would post.",
    )
    args = p.parse_args()

    settings = get_settings()
    if settings.odoo_mode != "live":
        print("REFUSING: Odoo is in fixture mode — there is nothing real to post.")
        return 2

    with get_sessionmaker()() as db:
        items = list(
            db.scalars(
                select(InventoryCountItem)
                .where(InventoryCountItem.picking_status == OdooWriteOutcome.CREATED.value)
                .order_by(InventoryCountItem.id)
            )
        )
        retries = list(
            db.scalars(
                select(InventoryCountItem)
                .where(InventoryCountItem.picking_status == OdooWriteOutcome.FAILED.value)
                .order_by(InventoryCountItem.id)
            )
        )
        if not items and not retries:
            print("Nothing to do: every approved count is already posted in Odoo.")
            return 0

        # Rows approved before 2026-08-22 never recorded the picking id (the
        # adjustment core didn't return one), so resolve it from the reference
        # the picking carries as its origin — authoritative, and it stamps the
        # id back so this only has to happen once.
        conn = get_connection(settings)
        missing = [i for i in items if not i.odoo_picking_id and i.picking_reference]
        if missing:
            found = {
                str(r["origin"]): int(r["id"])
                for r in conn.search_read(
                    "stock.picking",
                    [["origin", "in", [i.picking_reference for i in missing]]],
                    ["id", "origin"],
                )
            }
            for item in missing:
                item.odoo_picking_id = found.get(item.picking_reference)

        ready = [i for i in items if i.odoo_picking_id]
        orphans = [i for i in items if not i.odoo_picking_id]
        print(f"{len(items)} approved item(s) on an unposted adjustment; {len(ready)} resolvable.")
        for item in ready:
            qty = f"{item.applied_qty:g}" if item.applied_qty is not None else "?"
            print(
                f"  count #{item.count_id} item {item.id}: {item.picking_reference} "
                f"{item.odoo_picking_name or '?'} (#{item.odoo_picking_id}) → counted {qty}"
            )
        for item in orphans:
            print(
                f"  count #{item.count_id} item {item.id}: {item.picking_reference} — "
                "no matching picking in Odoo (deleted there?), skipping"
            )
        if retries:
            print(f"\n{len(retries)} approved item(s) whose adjustment was never created:")
            for item in retries:
                print(
                    f"  count #{item.count_id} item {item.id}: "
                    f"{(item.picking_error or '')[:90]}"
                )

        if not args.apply:
            print("\nDry run. Re-run with --apply to write these in Odoo.")
            db.commit()  # keep the ids we resolved; posting nothing
            return 0

        # Say so up front rather than reporting N identical "dry run" failures.
        flag = db.get(FeatureFlag, "write_validate_inventory_adjustment")
        if not (flag and flag.enabled):
            print(
                "\nREFUSING: the 'write_validate_inventory_adjustment' flag is off"
                f"{'' if flag else ' (and has no row yet — deploy the migration first)'}. "
                "Turn it on in Dev Tools → feature flags, then run this again."
            )
            return 3

        posted = failed = 0
        for item in ready:
            if post_adjustment(db, settings, item, actor_user_id=None):
                posted += 1
                print(f"  posted {item.odoo_picking_name or item.picking_reference}")
            else:
                failed += 1
                print(
                    f"  FAILED {item.odoo_picking_name or item.picking_reference}: "
                    f"{item.picking_error or 'flag off / dry run'}"
                )
        # the never-created ones go through the ordinary approve path, which
        # now creates AND posts — same code the app runs, not a second copy
        loc_by_key = {loc.key: loc for loc in locations.countable_locations(db, settings)}
        remade = 0
        for item in retries:
            loc = loc_by_key.get(item.count.location_key)
            if loc is None:
                failed += 1
                print(f"  FAILED item {item.id}: '{item.count.location_key}' isn't countable now")
                continue
            note = apply_to_odoo(db, settings, item, loc, actor_user_id=None)
            if item.picking_status == OdooWriteOutcome.VALIDATED.value:
                remade += 1
            else:
                failed += 1
            print(f"  item {item.id}: {note}")

        db.commit()
        print(f"\nposted {posted}, re-made {remade}, failed {failed}, skipped {len(orphans)}")
        return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
