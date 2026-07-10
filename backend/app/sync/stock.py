"""Stock sync: quants at the three app locations -> stock_levels.

The whole snapshot is replaced inside the runner's transaction, so a failed
pull can never leave a half-written table — the last good snapshot survives.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ODOO_LOCATION_NAMES, OdooLocation, Product, StockLevel, SyncState, utcnow
from ..odoo.protocol import OdooConnection


def sync_stock(db: Session, settings: Settings, conn: OdooConnection, state: SyncState) -> int:
    locations = conn.search_read(
        "stock.location",
        [["complete_name", "in", list(ODOO_LOCATION_NAMES.keys())]],
        ["complete_name"],
    )
    found = {loc["complete_name"]: loc["id"] for loc in locations}
    missing = sorted(set(ODOO_LOCATION_NAMES) - set(found))
    if missing:
        raise RuntimeError(
            f"Odoo locations not found: {missing}. The location names may have "
            "changed — update ODOO_LOCATION_NAMES."
        )

    existing = {loc.complete_name: loc for loc in db.scalars(select(OdooLocation))}
    for name, odoo_id in found.items():
        row = existing.get(name)
        if row is None:
            db.add(
                OdooLocation(
                    odoo_id=odoo_id, complete_name=name, key=ODOO_LOCATION_NAMES[name]
                )
            )
        else:
            row.odoo_id = odoo_id
            row.synced_at = utcnow()

    quants = conn.search_read(
        "stock.quant",
        [["location_id", "in", list(found.values())]],
        ["product_id", "location_id", "quantity"],
    )

    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(Product.odoo_product_id.is_not(None))
        )
    }
    key_by_loc_id = {found[name]: key for name, key in ODOO_LOCATION_NAMES.items()}

    totals: dict[tuple[int, str], float] = {}
    unknown = 0
    for q in quants:
        pid_field = q.get("product_id")
        odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
        product_id = id_by_odoo_pid.get(odoo_pid)
        if product_id is None:
            unknown += 1  # product not in catalog (not sale_ok) — fine to skip
            continue
        loc_field = q.get("location_id")
        loc_id = loc_field[0] if isinstance(loc_field, list) else loc_field
        key = key_by_loc_id.get(loc_id)
        if key is None:
            continue
        totals[(product_id, key)] = totals.get((product_id, key), 0.0) + (q.get("quantity") or 0.0)

    db.execute(delete(StockLevel))
    now = utcnow()
    for (product_id, key), qty in totals.items():
        db.add(StockLevel(product_id=product_id, location_key=key, qty=qty, captured_at=now))

    state.extra = {**(state.extra or {}), "unknown_product_quants": unknown}
    return len(totals)
