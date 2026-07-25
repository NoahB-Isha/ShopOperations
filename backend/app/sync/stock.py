"""Stock sync: quants under the three app locations -> stock_levels.

Quants are matched by location SUBTREE (`child_of`), because on the live
instance BWHSE stock actually sits in bin sub-locations like
III/Stock/BWHSE/A/1/1/1, and the floor has children too (Vending Machine —
still floor stock). Each quant is classified to its root by path prefix.

The whole snapshot is replaced inside the runner's transaction, so a failed
pull can never leave a half-written table — the last good snapshot survives.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    ODOO_LOCATION_NAMES,
    Center,
    OdooLocation,
    Product,
    StockLevel,
    StockSnapshot,
    StockSnapshotDay,
    SyncState,
    utcnow,
)
from ..odoo.protocol import OdooConnection

# Per-center internal locations live under this Odoo path; the leaf segment
# is matched (case-insensitively) against Center.name to map order-list
# destinations. Verified live 2026-07-10.
CITY_CENTER_PREFIX = "III/CityCenter/"


def sync_stock(db: Session, settings: Settings, conn: OdooConnection, state: SyncState) -> int:
    locations = conn.search_read(
        "stock.location",
        [["complete_name", "in", list(ODOO_LOCATION_NAMES.keys())]],
        ["complete_name"],
    )
    # key -> (complete_name, odoo id); a key may be reachable via several
    # spellings and the first match wins.
    roots: dict[str, tuple[str, int]] = {}
    for loc in locations:
        key = ODOO_LOCATION_NAMES[loc["complete_name"]]
        roots.setdefault(key, (loc["complete_name"], loc["id"]))
    missing = sorted(set(ODOO_LOCATION_NAMES.values()) - set(roots))
    if missing:
        raise RuntimeError(
            f"Odoo locations not found for keys: {missing}. The location names may "
            f"have changed — update ODOO_LOCATION_NAMES (looked for "
            f"{sorted(ODOO_LOCATION_NAMES)})."
        )

    existing_by_key = {loc.key: loc for loc in db.scalars(select(OdooLocation))}
    for key, (name, odoo_id) in roots.items():
        row = existing_by_key.get(key)
        if row is None:
            db.add(OdooLocation(odoo_id=odoo_id, complete_name=name, key=key))
        else:
            row.odoo_id = odoo_id
            row.complete_name = name
            row.synced_at = utcnow()

    quants = conn.search_read(
        "stock.quant",
        [["location_id", "child_of", [odoo_id for _, odoo_id in roots.values()]]],
        ["product_id", "location_id", "quantity"],
    )

    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(Product.odoo_product_id.is_not(None))
        )
    }

    # Longest root name first so a sibling whose name merely extends another's
    # (III-FLOOR vs III-FLOOR-STAGING) can never swallow its quants.
    ordered_roots = sorted(
        ((name, odoo_id, key) for key, (name, odoo_id) in roots.items()),
        key=lambda t: -len(t[0]),
    )

    def classify(loc_field) -> str | None:
        if isinstance(loc_field, list):
            loc_id, loc_name = loc_field[0], str(loc_field[1] or "")
        else:
            loc_id, loc_name = loc_field, ""
        for name, odoo_id, key in ordered_roots:
            if loc_id == odoo_id or loc_name == name or loc_name.startswith(name + "/"):
                return key
        return None

    totals: dict[tuple[int, str], float] = {}
    unknown = 0
    for q in quants:
        pid_field = q.get("product_id")
        odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
        product_id = id_by_odoo_pid.get(odoo_pid)
        if product_id is None:
            unknown += 1  # product not in catalog (not sale_ok) — fine to skip
            continue
        loc_key = classify(q.get("location_id"))
        if loc_key is None:
            continue
        totals[(product_id, loc_key)] = (
            totals.get((product_id, loc_key), 0.0) + (q.get("quantity") or 0.0)
        )

    db.execute(delete(StockLevel))
    now = utcnow()
    for (product_id, key), qty in totals.items():
        db.add(StockLevel(product_id=product_id, location_key=key, qty=qty, captured_at=now))

    _capture_history(db, settings, totals, now)

    center_stats = _map_center_locations(db, conn)
    state.extra = {**(state.extra or {}), "unknown_product_quants": unknown, **center_stats}
    return len(totals)


def _capture_history(
    db: Session, settings: Settings, totals: dict[tuple[int, str], float], now: datetime
) -> None:
    """Append today's totals to the stock history (the time machine's past
    view). Re-running within a day replaces that day — the last sync wins.
    Zero/negative rows are skipped; the StockSnapshotDay row marks the day as
    covered regardless, so 'absent on a covered day' honestly means zero."""
    today = now.date()
    db.execute(delete(StockSnapshot).where(StockSnapshot.snapshot_date == today))
    kept = 0
    for (product_id, key), qty in totals.items():
        if qty <= 0:
            continue
        db.add(
            StockSnapshot(
                snapshot_date=today,
                product_id=product_id,
                location_key=key,
                qty=qty,
                captured_at=now,
            )
        )
        kept += 1
    day = db.get(StockSnapshotDay, today)
    if day is None:
        db.add(StockSnapshotDay(snapshot_date=today, captured_at=now, rows=kept))
    else:
        day.captured_at = now
        day.rows = kept
    floor_date = today - timedelta(days=settings.stock_snapshot_retention_days)
    db.execute(delete(StockSnapshot).where(StockSnapshot.snapshot_date < floor_date))
    db.execute(delete(StockSnapshotDay).where(StockSnapshotDay.snapshot_date < floor_date))


def _map_center_locations(db: Session, conn: OdooConnection) -> dict:
    """Match III/CityCenter/<City> locations to centers by leaf name. The
    mapping is rebuilt every sync so renames in Odoo surface as 'unmapped'
    rather than as stale ids that would misroute a draft transfer."""
    locations = conn.search_read(
        "stock.location", [["complete_name", "like", CITY_CENTER_PREFIX]], ["complete_name"]
    )
    centers = db.scalars(select(Center)).all()
    if not centers:
        return {"center_locations_seen": len(locations), "center_locations_matched": 0}
    by_name = {c.name.strip().lower(): c for c in centers}
    for center in centers:
        center.odoo_location_id = None
        center.odoo_location_name = ""
    matched = 0
    for loc in locations:
        name = str(loc.get("complete_name") or "")
        if CITY_CENTER_PREFIX not in name:
            continue  # 'like' is a substring match; keep only true subtree hits
        leaf = name.rsplit("/", 1)[-1].strip().lower()
        match = by_name.get(leaf)
        if match is not None and match.odoo_location_id is None:
            match.odoo_location_id = loc["id"]
            match.odoo_location_name = name
            matched += 1
    return {"center_locations_seen": len(locations), "center_locations_matched": matched}
