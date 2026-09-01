"""What moved at this shelf since the count was taken, and why.

The re-read guard (service.read_baseline) can see that Odoo's quantity changed
since a count froze it. On its own that's not enough to decide: a shelf that
sold three units and a shelf that somebody else's count already corrected look
identical from the number alone, and they want opposite treatment.

  * SOLD (or transferred, or received) — the counter's discrepancy is still
    true. Odoo was wrong by (counted − captured) at count time, and the
    movements since are real. Applying that same difference on top of the new
    number lands exactly right, which is what the adjustment does anyway.
  * ALREADY CORRECTED — another count of the same shelf has been applied. The
    discrepancy has been fixed once; applying it again subtracts it twice.
    That is the 2026-08-22 bug, and it needs a person, not arithmetic.

Odoo's move ledger tells them apart. `stock.move.line` carries the date, the
two locations and the quantity of every completed movement, and Odoo's own
`stock.location.usage` names the counterpart's KIND — `inventory` is the
adjustment virtual location, `customer` a sale, `supplier` a receipt,
`internal` a transfer. Classifying on usage rather than on picking-type names
keeps this instance-independent: the names here are "USA-III: Inventory Adj
Reduction" and friends, but the semantics are Odoo's.

Reads only, and it never guesses: when Odoo doesn't answer, the caller is told
so and stays strict.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..config import Settings
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError

log = logging.getLogger("counting.ledger")

# Odoo stores datetimes as naive UTC strings.
ODOO_DATETIME = "%Y-%m-%d %H:%M:%S"

# Quantities are floats; compare like the rest of the app does.
EPSILON = 0.001


@dataclass(frozen=True)
class LedgerRead:
    """Signed movement at one location since a moment, split by cause."""

    available: bool  # False = Odoo didn't answer; decide without it
    corrected: float = 0.0  # net from inventory-adjustment moves
    moved: float = 0.0  # net from everything else (sales, transfers, receipts)
    sold: float = 0.0  # the part of `moved` that went to a customer
    adjustment_refs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def net(self) -> float:
        return round(self.corrected + self.moved, 3)

    def explains(self, drift: float) -> bool:
        """Does ordinary movement account for the whole change?

        The adjustment test is "did ANY happen", not "do they net to zero".
        Two corrections that cancel (+2 then −2, as the Relaxed Henley really
        had) still mean this shelf has been corrected twice already, and a
        third count's difference would ride on top of both."""
        return (
            self.available
            and not self.adjustment_refs
            and abs(self.moved - drift) < EPSILON
        )

    @property
    def summary(self) -> str:
        return ", ".join(self.reasons) if self.reasons else "no recorded movement"


def movements_since(
    settings: Settings,
    location_odoo_id: int,
    product_odoo_id: int,
    since: datetime,
) -> LedgerRead:
    """Every completed move touching this product at this location's subtree
    since `since`, netted from the LOCATION's point of view (out negative).

    Two reads rather than one: Odoo can't express "crossed this subtree's
    boundary" in a single domain, so outbound and inbound are fetched
    separately and lines that appear in both — a bin-to-bin move inside the
    same area — cancel to nothing, which is the truth."""
    if not location_odoo_id or not product_odoo_id:
        return LedgerRead(available=False)
    # Odoo stores naive UTC; our stamps are aware on Postgres and naive on
    # SQLite, so normalize before formatting or the window silently shifts.
    moment = since.astimezone(UTC).replace(tzinfo=None) if since.tzinfo else since
    cutoff = moment.strftime(ODOO_DATETIME)
    fields = ["id", "date", "quantity", "location_id", "location_dest_id", "reference"]
    base = [
        ["product_id", "=", product_odoo_id],
        ["state", "=", "done"],
        ["date", ">=", cutoff],
    ]
    try:
        conn = get_connection(settings, read_only=True)
        out = conn.search_read(
            "stock.move.line", [*base, ["location_id", "child_of", location_odoo_id]], fields
        )
        into = conn.search_read(
            "stock.move.line",
            [*base, ["location_dest_id", "child_of", location_odoo_id]],
            fields,
        )
        usages = _usages(conn, [*out, *into])
    except OdooError as e:
        log.warning("ledger read failed at location %s: %s", location_odoo_id, e)
        return LedgerRead(available=False)

    inside = {row["id"] for row in into}
    corrected = moved = sold = 0.0
    refs: list[str] = []
    counts: dict[str, float] = {}

    def note(kind: str, qty: float) -> None:
        counts[kind] = round(counts.get(kind, 0.0) + abs(qty), 3)

    def phrase(kind: str, qty: float) -> str:
        return f"{qty:g} {kind}"

    for row in out:
        if row["id"] in inside:
            continue  # moved within this area — the total here didn't change
        qty = -float(row.get("quantity") or 0)
        kind = usages.get(_m2o(row.get("location_dest_id")) or 0, "")
        if kind == "inventory":
            corrected += qty
            refs.append(str(row.get("reference") or ""))
        else:
            moved += qty
            if kind == "customer":
                sold += abs(qty)
                note("sold", qty)
            else:
                note("transferred out", qty)

    outside = {row["id"] for row in out}
    for row in into:
        if row["id"] in outside:
            continue
        qty = float(row.get("quantity") or 0)
        kind = usages.get(_m2o(row.get("location_id")) or 0, "")
        if kind == "inventory":
            corrected += qty
            refs.append(str(row.get("reference") or ""))
        else:
            moved += qty
            note("received or transferred in", qty)

    unique_refs = tuple(r for r in dict.fromkeys(refs) if r)
    reasons = tuple(phrase(kind, qty) for kind, qty in counts.items())
    if unique_refs:
        # counted, not summed: corrections can cancel each other out and still
        # both have happened
        n = len(unique_refs)
        reasons += (f"{n} adjustment{'s' if n != 1 else ''} from another count",)
    return LedgerRead(
        available=True,
        corrected=round(corrected, 3),
        moved=round(moved, 3),
        sold=round(sold, 3),
        adjustment_refs=unique_refs,
        reasons=reasons,
    )


def _m2o(value) -> int | None:
    if isinstance(value, list | tuple) and value:
        return int(value[0])
    return int(value) if value else None


def _usages(conn, rows: list[dict]) -> dict[int, str]:
    """location id -> Odoo's own usage. One read for the whole batch."""
    ids = {
        loc
        for row in rows
        for loc in (_m2o(row.get("location_id")), _m2o(row.get("location_dest_id")))
        if loc
    }
    if not ids:
        return {}
    return {
        int(r["id"]): str(r.get("usage") or "")
        for r in conn.search_read("stock.location", [["id", "in", sorted(ids)]], ["id", "usage"])
    }
