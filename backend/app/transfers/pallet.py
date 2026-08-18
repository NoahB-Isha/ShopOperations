"""The staging2 pallet flow — the warehouse's REAL process.

Warehouse pulls requests into III/Staging2 (their consolidation point),
accumulates, then sends ONE pallet to floor staging. The app mirrors that:
the staging2 page shows what's sitting there (live read — this is an action
screen, and the brief allows on-demand refresh), and 'Send all to
III-FLORR-STAGING' renders the pallet as a DRAFT internal transfer a human
validates in Odoo — a convenience, not the only route. Normally the
warehouse makes that transfer in Odoo themselves and tells the app about it
on the delivery form (delivery.py).

Pallet validation is the signal that goods reached floor staging. WHAT rode
it is a human's answer, not an inference: a declared pallet closes the
requests linked to it (delivery.land) and gets one count transfer; an
undeclared one is recorded and asks for its details.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..models import (
    OdooLocation,
    OdooWriteOutcome,
    PalletRequestLink,
    PalletTransfer,
    Product,
    StockLevel,
    TransferRequest,
    TransferRequestStatus,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.urls import odoo_record_url
from ..odoo.writer import OdooWriter
from ..ordering.service import get_app_setting, set_app_setting
from . import delivery

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
    means a picking exists for it — rendered by the app (created live, or
    simulated when writes are gated) or made by the warehouse and declared
    on the delivery form — and the validation listener hasn't seen it land or
    cancel. Either way its stock is still sitting in staging2, so 'Send all'
    would draft a second move over the same units."""
    return db.scalars(
        select(PalletTransfer)
        .where(
            PalletTransfer.status == "open",
            or_(
                PalletTransfer.picking_status.in_(
                    (OdooWriteOutcome.CREATED.value, OdooWriteOutcome.SIMULATED.value)
                ),
                PalletTransfer.odoo_picking_id.is_not(None),
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
    the requests DECLARED on it close as done and the pallet gets its one
    count transfer (delivery.land). Covers both kinds of open pallet: the
    ones the app rendered and the ones the warehouse made and declared.
    Throttled per pallet; safe on every board refresh. Returns how many
    requests closed."""
    pallets = db.scalars(
        select(PalletTransfer)
        .options(
            selectinload(PalletTransfer.request_links).selectinload(PalletRequestLink.request)
        )
        .where(
            PalletTransfer.status == "open",
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
        moved += delivery.land(db, settings, pallet)
    db.commit()
    return moved


MANUAL_PALLET_STATE_KEY = "manual_pallet_poll_state"


def poll_manual_pallets(db: Session, settings: Settings) -> int:
    """Discover a pallet the WAREHOUSE sent without telling the app.

    A validated staging2 → floor-staging picking means goods reached floor
    staging whoever made it. If it was declared on the delivery form,
    poll_pallets already lands it (its row carries the picking id). This is
    the OTHER case: nobody filled the form, so the app records the picking —
    undeclared, so it closes nobody's request — and the deliveries list asks
    for its details. Recording it also means it can't be processed twice;
    picking_status stays NONE because the app wrote nothing.

    A `discover_from` stamp in the poll state (set by the flow reset) is the
    floor of what counts as "new": pickings validated at or before it are
    somebody else's history — two weeks of testing, or the years of real
    staging2 → staging traffic that predate this feature. Without it, deleting
    a pallet row just means rediscovering the same picking on the next poll.

    Returns how many undeclared pallets it newly found.
    """
    waiting_exists = db.scalar(
        select(TransferRequest.id).where(
            TransferRequest.status == TransferRequestStatus.SENT.value
        )
    )
    if not waiting_exists:
        # Nobody is waiting on a delivery, so an undeclared one closes
        # nothing — don't touch Odoo at all (the delivery form reads live
        # when the warehouse actually opens it). Politeness, per the brief.
        return 0

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

    domain = [
        ["location_id", "child_of", staging2.odoo_id],
        ["location_dest_id", "child_of", staging.odoo_id],
        ["state", "=", "done"],
    ]
    discover_from = str(state.get("discover_from") or "")
    if discover_from:
        # Odoo filters it, so the app never even reads the old ones
        domain.append(["date_done", ">", discover_from])
    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read("stock.picking", domain, ["name", "origin", "date_done"])
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
    found = 0
    for row in rows:
        if row["id"] in known:
            continue  # already handled (app pallet, declared, or seen earlier)
        if str(row.get("origin") or "").startswith("ILAPP-"):
            continue  # an app pallet whose row we somehow missed — not ours to adopt
        try:
            contents = delivery.picking_contents(db, conn, row["id"])
        except OdooError:
            contents = {}
        products = {
            p.id: p
            for p in db.scalars(select(Product).where(Product.id.in_(contents or {-1})))
        }
        db.add(
            PalletTransfer(
                status="validated",
                picking_status=OdooWriteOutcome.NONE.value,  # the app wrote nothing
                odoo_picking_id=row["id"],
                odoo_picking_name=str(row.get("name") or ""),
                odoo_picking_url=odoo_record_url(settings, "stock.picking", row["id"]),
                # what it carried, so the deliveries list and the form can
                # show it without asking Odoo again. Nobody has said WHOSE
                # stock it is — that's what "needs details" means.
                lines=[
                    {
                        "product_id": pid,
                        "sku": (products[pid].odoo_internal_ref or products[pid].global_sku)
                        if pid in products
                        else "",
                        "name": products[pid].name if pid in products else f"product {pid}",
                        "qty": qty,
                    }
                    for pid, qty in sorted(contents.items())
                ],
                validated_at=now,
                checked_at=now,
            )
        )
        found += 1
    db.commit()
    return found
