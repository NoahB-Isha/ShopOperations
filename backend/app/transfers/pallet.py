"""The staging2 pallet flow — the warehouse's REAL process.

Warehouse retargets outbound transfers to III/Staging2 (their consolidation
point), picks several into it, then sends ONE pallet to floor staging. The
app mirrors that: the staging2 page shows what's sitting there (live read —
this is an action screen, and the brief allows on-demand refresh), and
'Send all to III-FLORR-STAGING' renders the pallet as a DRAFT internal
transfer a human validates in Odoo.

Pallet validation is the signal that goods reached floor staging: the
listener then prepares count transfers for every request that was SENT and
still waiting (their goods rode the pallet).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    OdooLocation,
    OdooWriteOutcome,
    PalletTransfer,
    Product,
    StockLevel,
    TransferEventKind,
    TransferRequest,
    TransferRequestStatus,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter
from ..ordering.service import get_app_setting, set_app_setting
from .service import _event, prepare_count_transfer

log = logging.getLogger("transfers.pallet")


@dataclass
class Staging2Item:
    product_id: int
    sku: str
    barcode: str
    name: str
    qty: float


@dataclass
class Staging2Snapshot:
    items: list[Staging2Item]
    source: str  # "live" | "snapshot" | "unmapped"
    note: str = ""

    @property
    def total_units(self) -> float:
        return sum(i.qty for i in self.items)


def _staging2_location(db: Session) -> OdooLocation | None:
    return db.scalar(select(OdooLocation).where(OdooLocation.key == "staging2"))


def staging2_snapshot(db: Session, settings: Settings) -> Staging2Snapshot:
    """What's physically in III/Staging2 right now. Live read first (the
    page is an action screen); the last stock-sync snapshot is the honest
    fallback when Odoo is unreachable."""
    loc = _staging2_location(db)
    if loc is None:
        return Staging2Snapshot(
            items=[],
            source="unmapped",
            note=(
                "III/Staging2 isn't mapped yet — run a stock sync so the app "
                "discovers Odoo location ids."
            ),
        )
    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(
                Product.odoo_product_id.is_not(None)
            )
        )
    }
    totals: dict[int, float] = {}
    source, note = "live", ""
    try:
        conn = get_connection(settings, read_only=True)
        for q in conn.search_read(
            "stock.quant",
            [["location_id", "child_of", loc.odoo_id], ["quantity", ">", 0]],
            ["product_id", "quantity"],
        ):
            pid_field = q.get("product_id")
            odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
            product_id = id_by_odoo_pid.get(odoo_pid if isinstance(odoo_pid, int) else -1)
            if product_id is None:
                continue
            totals[product_id] = totals.get(product_id, 0.0) + float(q.get("quantity") or 0)
    except OdooError as e:
        source = "snapshot"
        note = f"Odoo unreachable ({e}) — showing the last stock sync instead"
        for pid, qty in db.execute(
            select(StockLevel.product_id, StockLevel.qty).where(
                StockLevel.location_key == "staging2", StockLevel.qty > 0
            )
        ):
            totals[pid] = float(qty or 0)

    products = {
        p.id: p
        for p in db.scalars(select(Product).where(Product.id.in_(totals or {-1})))
    }
    items = [
        Staging2Item(
            product_id=pid,
            sku=p.odoo_internal_ref or p.global_sku,
            barcode=p.barcode or "",
            name=p.name,
            qty=round(qty, 3),
        )
        for pid, qty in totals.items()
        if (p := products.get(pid)) is not None and qty > 0
    ]
    items.sort(key=lambda i: i.name)
    return Staging2Snapshot(items=items, source=source, note=note)


def open_pallet(db: Session) -> PalletTransfer | None:
    """The pallet still awaiting validation in Odoo, if there is one. 'Open'
    means the app rendered a picking (created live, or simulated when writes
    are gated) and the validation listener hasn't seen it land or cancel."""
    return db.scalars(
        select(PalletTransfer)
        .where(
            PalletTransfer.status == "open",
            PalletTransfer.picking_status.in_(
                (OdooWriteOutcome.CREATED.value, OdooWriteOutcome.SIMULATED.value)
            ),
        )
        .order_by(PalletTransfer.id.desc())
    ).first()


def create_pallet(
    db: Session, settings: Settings, actor_user_id: int | None
) -> tuple[PalletTransfer, Staging2Snapshot]:
    """Render the pallet: ONE draft internal transfer moving everything in
    staging2 to floor staging. Draft only — a human validates in Odoo."""
    # One open pallet at a time. Each pallet gets its OWN ILAPP-PLT- reference,
    # so the writer's origin-keyed dedupe cannot catch a double send-all: two
    # clicks would render two drafts over the same staging2 stock. The check
    # has to live here.
    existing = open_pallet(db)
    if existing is not None:
        name = existing.odoo_picking_name or existing.picking_reference
        if existing.picking_status == OdooWriteOutcome.SIMULATED.value:
            raise ValueError(
                f"Pallet {name} is still open and was only simulated (Odoo writes are "
                "gated) — nothing reached Odoo, so a second pallet would be noise too."
            )
        raise ValueError(
            f"Pallet {name} is still waiting to be validated — validate or cancel it "
            "in Odoo, then send the next one."
        )
    snapshot = staging2_snapshot(db, settings)
    if snapshot.source == "unmapped":
        raise ValueError(snapshot.note)
    if not snapshot.items:
        raise ValueError("III/Staging2 is empty — nothing to send.")

    pallet = PalletTransfer(
        created_by_id=actor_user_id,
        picking_reference=new_reference("PLT"),
        lines=[
            {"product_id": i.product_id, "sku": i.sku, "name": i.name, "qty": i.qty}
            for i in snapshot.items
        ],
    )
    db.add(pallet)
    db.flush()

    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    try:
        result = writer.create_internal_transfer(
            source_key="staging2",
            dest_key="staging",
            lines=[{"product_id": i.product_id, "qty": i.qty} for i in snapshot.items],
            note=f"Pallet to floor staging ({len(snapshot.items)} item(s))",
            reference=pallet.picking_reference,
        )
    except OdooWriteError as e:
        pallet.picking_status = OdooWriteOutcome.FAILED.value
        pallet.picking_error = str(e)
        db.commit()
        return pallet, snapshot
    pallet.picking_error = ""
    if result.dry_run:
        pallet.picking_status = OdooWriteOutcome.SIMULATED.value
    else:
        pallet.picking_status = OdooWriteOutcome.CREATED.value
        pallet.odoo_picking_id = result.record_ids[0] if result.record_ids else None
        pallet.odoo_picking_name = result.record_name
        pallet.odoo_picking_url = result.deep_link
    db.commit()
    return pallet, snapshot


def poll_pallets(db: Session, settings: Settings) -> int:
    """Listener for pallet validation. When a pallet lands (picking done),
    every request sitting in SENT with no count transfer yet gets its count
    prepared — the goods just reached floor staging. Throttled per pallet;
    safe on every board refresh. Returns how many requests moved."""
    pallets = db.scalars(
        select(PalletTransfer).where(
            PalletTransfer.status == "open",
            PalletTransfer.picking_status == OdooWriteOutcome.CREATED.value,
            PalletTransfer.odoo_picking_id.is_not(None),
        )
    ).all()
    if not pallets:
        return 0
    now = utcnow()
    due = []
    for pallet in pallets:
        checked = pallet.checked_at
        if checked is not None and checked.tzinfo is None:
            checked = checked.replace(tzinfo=now.tzinfo)
        if checked is None or (now - checked).total_seconds() >= settings.odoo_count_poll_seconds:
            pallet.checked_at = now
            due.append(pallet)
    if not due:
        return 0
    db.commit()  # persist throttle stamps even if the read below fails

    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking",
            [["id", "in", [p.odoo_picking_id for p in due]]],
            ["state"],
        )
    except OdooError as e:
        log.warning("pallet poll failed: %s", e)
        return 0
    state_by_id = {r["id"]: str(r.get("state") or "") for r in rows}

    moved = 0
    for pallet in due:
        state = state_by_id.get(pallet.odoo_picking_id or -1, "")
        if state == "cancel":
            pallet.status = "cancelled"
            continue
        if state != "done":
            continue
        pallet.status = "validated"
        pallet.validated_at = now
        moved += _advance_waiting_requests(db, settings, pallet.odoo_picking_name)
    db.commit()
    return moved


def _advance_waiting_requests(db: Session, settings: Settings, pallet_name: str) -> int:
    """A pallet landed at floor staging, so every request that was SENT and
    still waiting can be counted — their goods rode it. Deliberately not
    per-product: a pallet is 'the goods got there', and the actual sent-vs-
    counted reconciliation happens later at the staging→floor step."""
    waiting = db.scalars(
        select(TransferRequest).where(
            TransferRequest.status == TransferRequestStatus.SENT.value,
            TransferRequest.count_status.in_(
                (OdooWriteOutcome.NONE.value, OdooWriteOutcome.FAILED.value)
            ),
        )
    ).all()
    moved = 0
    for req in waiting:
        _event(
            db, req, TransferEventKind.ODOO,
            f"pallet {pallet_name} landed at floor staging",
        )
        prepare_count_transfer(db, settings, req, actor_user_id=None)
        if req.count_status in (
            OdooWriteOutcome.CREATED.value,
            OdooWriteOutcome.SIMULATED.value,
        ):
            req.status = TransferRequestStatus.COUNTING.value
            _event(db, req, TransferEventKind.STATUS, "ready to count", status=req.status)
            moved += 1
    return moved


MANUAL_PALLET_STATE_KEY = "manual_pallet_poll_state"


def poll_manual_pallets(db: Session, settings: Settings) -> int:
    """Detect a pallet the WAREHOUSE built themselves.

    `poll_pallets` only ever watches pallets the app rendered (a PalletTransfer
    row with an ILAPP-PLT- picking). When the warehouse instead makes a plain
    Odoo transfer staging2 → floor staging — which is normal, the /staging2
    button is a convenience, not the only route — nothing saw it and every
    request sat in SENT 'waiting for the pallet' forever.

    A validated staging2 → floor-staging picking means the same thing whoever
    made it, so it advances the waiting requests identically. The discovered
    picking is recorded as a PalletTransfer so it can't be processed twice and
    shows up in pallet history; picking_status stays NONE because the app
    didn't write it, which also keeps it out of open_pallet()'s way (that guard
    is about a pallet the app rendered and is still awaiting validation).
    """
    waiting_exists = db.scalar(
        select(TransferRequest.id).where(
            TransferRequest.status == TransferRequestStatus.SENT.value,
            TransferRequest.count_status.in_(
                (OdooWriteOutcome.NONE.value, OdooWriteOutcome.FAILED.value)
            ),
        )
    )
    if not waiting_exists:
        return 0  # nothing to advance — don't touch Odoo at all

    now = utcnow()
    state = get_app_setting(db, MANUAL_PALLET_STATE_KEY) or {}
    last = state.get("checked_at")
    if last:
        try:
            when = datetime.fromisoformat(str(last))
            if when.tzinfo is None:
                when = when.replace(tzinfo=now.tzinfo)
            if (now - when).total_seconds() < settings.odoo_count_poll_seconds:
                return 0
        except ValueError:
            pass  # unparseable stamp: treat as never checked
    set_app_setting(db, MANUAL_PALLET_STATE_KEY, {**state, "checked_at": now.isoformat()})
    db.commit()

    staging2 = db.scalar(select(OdooLocation).where(OdooLocation.key == "staging2"))
    staging = db.scalar(select(OdooLocation).where(OdooLocation.key == "staging"))
    if staging2 is None or staging is None:
        return 0

    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking",
            [
                ["location_id", "child_of", staging2.odoo_id],
                ["location_dest_id", "child_of", staging.odoo_id],
                ["state", "=", "done"],
            ],
            ["name", "origin"],
        )
    except OdooError as e:
        log.warning("manual pallet poll failed: %s", e)
        return 0

    known = {
        pid
        for (pid,) in db.execute(
            select(PalletTransfer.odoo_picking_id).where(
                PalletTransfer.odoo_picking_id.is_not(None)
            )
        )
    }
    moved = 0
    for row in rows:
        if row["id"] in known:
            continue  # already handled (app pallet, or seen on an earlier pass)
        if str(row.get("origin") or "").startswith("ILAPP-"):
            continue  # an app pallet whose row we somehow missed — not ours to adopt
        db.add(
            PalletTransfer(
                status="validated",
                picking_status=OdooWriteOutcome.NONE.value,  # the app wrote nothing
                odoo_picking_id=row["id"],
                odoo_picking_name=str(row.get("name") or ""),
                lines=[],  # built in Odoo; the app never froze its contents
                validated_at=now,
                checked_at=now,
            )
        )
        moved += _advance_waiting_requests(db, settings, str(row.get("name") or "a pallet"))
    db.commit()
    return moved
