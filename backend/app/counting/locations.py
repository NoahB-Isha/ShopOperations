"""What can be counted, by whom, and what Odoo says is there.

Countable locations are the four the stock sync tracks PLUS III/Stock/SHIP,
which is deliberately not one of them: SHIP folds into bwhse everywhere else
in the app (~80k units of online-fulfillment stock), so it has no
`OdooLocation` row of its own and its id has to be resolved by name. It is
still a real shelf someone stands in front of — and it is the Warehouse Team's
default counting location, per the spec.

The Odoo quantity a counter compares against is read LIVE per location, not
taken from StockLevel: StockLevel has no `ship` key (it folds), and a count
wants the number as it stands right now, not as the last sync left it. When
Odoo won't answer we fall back to the synced totals and say so — `source` is
part of what gets stored with the count.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import OdooLocation, Product, Role, StockLevel
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError

log = logging.getLogger("counting.locations")

# key -> what the floor calls it. Order is the dropdown's order.
LOCATION_LABELS: dict[str, str] = {
    "floor": "Shoppe floor",
    "bwhse": "Blue Warehouse",
    "ship": "SHIP (online fulfillment)",
    "staging": "Floor staging",
    "staging2": "Staging 2",
}

# keys that aren't synced roots — resolved by complete_name instead
FOLDED_KEYS: dict[str, str] = {"ship": "III/Stock/SHIP"}

# Where each role starts. Everyone may switch; this is just the sensible
# default for where that person actually stands (spec §Location).
DEFAULT_BY_ROLE: list[tuple[Role, str]] = [
    (Role.WAREHOUSE, "ship"),
    (Role.SHOPPE_FLOOR, "floor"),
    (Role.FLOOR_ROTATING, "floor"),
    (Role.ADMIN, "floor"),
]

# Roles allowed to count at all (the assign-a-recount dropdown uses this too)
COUNTER_ROLES = (Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE, Role.ADMIN)
# Roles allowed to review. inventory_wrangler is the ADD-ON.
REVIEWER_ROLES = (Role.SHOPPE_FLOOR, Role.INVENTORY_WRANGLER, Role.ADMIN)


@dataclass
class CountLocation:
    key: str
    label: str
    odoo_id: int | None
    note: str = ""


def default_location(role_names: set[str]) -> str:
    for role, key in DEFAULT_BY_ROLE:
        if role.value in role_names:
            return key
    return "floor"


def countable_locations(db: Session, settings: Settings) -> list[CountLocation]:
    """Every location someone can count, with the Odoo id an approval needs.

    A location we can't resolve still appears, with `odoo_id=None` and a note —
    hiding it would look like the shelf doesn't exist."""
    rows = {loc.key: loc for loc in db.scalars(select(OdooLocation))}
    out: list[CountLocation] = []
    folded_ids = _folded_ids(settings)
    for key, label in LOCATION_LABELS.items():
        if key in FOLDED_KEYS:
            odoo_id = folded_ids.get(key)
            out.append(
                CountLocation(
                    key=key,
                    label=label,
                    odoo_id=odoo_id,
                    note=(
                        ""
                        if odoo_id
                        else f"Couldn't resolve {FOLDED_KEYS[key]} in Odoo — counts here can't "
                        "be applied until it answers."
                    ),
                )
            )
            continue
        loc = rows.get(key)
        out.append(
            CountLocation(
                key=key,
                label=label,
                odoo_id=loc.odoo_id if loc else None,
                note="" if loc else "Not mapped yet — run a stock sync.",
            )
        )
    return out


def _folded_ids(settings: Settings) -> dict[str, int]:
    """Resolve the folded locations (SHIP) by complete_name. One small read,
    and an empty answer is not an error — the caller reports it."""
    wanted = {name: key for key, name in FOLDED_KEYS.items()}
    if not wanted:
        return {}
    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.location",
            [["complete_name", "in", list(wanted)]],
            ["complete_name"],
        )
    except OdooError as e:
        log.warning("could not resolve folded count locations: %s", e)
        return {}
    out: dict[str, int] = {}
    for row in rows:
        key = wanted.get(str(row.get("complete_name") or ""))
        if key:
            out[key] = int(row["id"])
    return out


def quantities_at(
    db: Session,
    settings: Settings,
    location: CountLocation,
    product_ids: list[int],
) -> tuple[dict[int, float], str]:
    """What Odoo says is at this location for these products, right now.

    Live quant read over the location's SUBTREE (BWHSE is hundreds of bins, so
    an exact-location match would read zero for almost everything). Falls back
    to the last stock sync's totals — never to silence."""
    if not product_ids:
        return {}, "live"
    products = {
        p.id: p
        for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))
        if p.odoo_product_id
    }
    if location.odoo_id and products:
        by_odoo = {p.odoo_product_id: pid for pid, p in products.items()}
        try:
            conn = get_connection(settings, read_only=True)
            quants = conn.search_read(
                "stock.quant",
                [
                    ["product_id", "in", list(by_odoo)],
                    ["location_id", "child_of", location.odoo_id],
                ],
                ["product_id", "quantity"],
            )
        except OdooError as e:
            log.warning("live count read failed at %s: %s", location.key, e)
        else:
            totals: dict[int, float] = dict.fromkeys(product_ids, 0.0)
            for q in quants:
                field = q.get("product_id")
                odoo_pid = field[0] if isinstance(field, list) else field
                pid = by_odoo.get(odoo_pid)
                if pid is not None:
                    totals[pid] = round(totals.get(pid, 0.0) + float(q.get("quantity") or 0), 3)
            return totals, "live"

    # fallback: the synced buckets. `ship` has none of its own — it folds into
    # bwhse — so say bwhse rather than inventing a zero.
    key = "bwhse" if location.key == "ship" else location.key
    rows = db.execute(
        select(StockLevel.product_id, StockLevel.qty).where(
            StockLevel.product_id.in_(product_ids), StockLevel.location_key == key
        )
    )
    fallback: dict[int, float] = dict.fromkeys(product_ids, 0.0)
    for pid, qty in rows:
        fallback[pid] = float(qty)
    return fallback, "snapshot"
