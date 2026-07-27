"""Transfers sync — the INBOUND half of the two-way Odoo↔app transfer sync.

Discovers pickings headed to floor staging that were created DIRECTLY in
Odoo (drafts included — "drafted as going to floor staging" counts), and
snapshots their lines into `staging_inbound_moves` so the coming-soon list
shows everything on its way to the floor, not just app-placed requests.

App-placed requests are excluded by their picking id — they already
aggregate from `transfer_requests` (and get their own live status listener
in app/transfers/service.poll_outbound_status, the OUTBOUND half).

Replace-on-sync like the other snapshots: pickings that get validated
(done) or cancelled simply drop out of the pending search and vanish from
coming-soon — arrived stock isn't "coming" anymore.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    OdooLocation,
    Product,
    StagingInboundMove,
    SyncState,
    TransferRequest,
    utcnow,
)
from ..odoo.protocol import OdooConnection

log = logging.getLogger("sync.transfers")

# a picking still on its way (draft = "drafted as going to floor staging")
PENDING_PICKING_STATES = ("draft", "waiting", "confirmed", "assigned")


def _scheduled_date(raw: object) -> date | None:
    s = str(raw or "")
    if len(s) < 10 or not (s[:4].isdigit() and s[8:10].isdigit()):
        return None
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


def sync_transfers(
    db: Session, settings: Settings, conn: OdooConnection, state: SyncState
) -> int:
    staging = db.scalar(select(OdooLocation).where(OdooLocation.key == "staging"))
    if staging is None:
        raise RuntimeError(
            "staging location isn't mapped yet — run a stock sync first so the "
            "app discovers Odoo location ids"
        )
    # the warehouse's consolidation point counts too: a picking headed to
    # III/Staging2 is floor-bound (it rides the next pallet). Optional —
    # older environments may not have the location mapped.
    staging2 = db.scalar(select(OdooLocation).where(OdooLocation.key == "staging2"))
    dest_roots = [staging.odoo_id] + ([staging2.odoo_id] if staging2 else [])

    pickings = conn.search_read(
        "stock.picking",
        [
            ["location_dest_id", "child_of", dest_roots],
            ["state", "in", list(PENDING_PICKING_STATES)],
        ],
        ["name", "state", "scheduled_date", "origin"],
    )

    # app-created pickings never land here: requests already live on the
    # board + coming-soon (excluded by id), and app pallets would double-
    # count the SENT requests riding them (excluded by ILAPP- origin).
    app_picking_ids = {
        pid
        for (pid,) in db.execute(
            select(TransferRequest.odoo_picking_id).where(
                TransferRequest.odoo_picking_id.is_not(None)
            )
        )
    }
    native = [
        p
        for p in pickings
        if p["id"] not in app_picking_ids
        and not str(p.get("origin") or "").startswith("ILAPP-")
    ]

    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(
                Product.odoo_product_id.is_not(None)
            )
        )
    }

    rows: list[StagingInboundMove] = []
    unmapped = 0
    now = utcnow()
    native_by_id = {p["id"]: p for p in native}
    ids = sorted(native_by_id)
    for i in range(0, len(ids), 200):
        moves = conn.search_read(
            "stock.move",
            [["picking_id", "in", ids[i : i + 200]]],
            ["picking_id", "product_id", "product_uom_qty", "quantity"],
        )
        for m in moves:
            picking_field = m.get("picking_id")
            picking_id = (
                picking_field[0] if isinstance(picking_field, list) else picking_field
            )
            picking = native_by_id.get(picking_id if isinstance(picking_id, int) else -1)
            if picking is None:
                continue
            pid_field = m.get("product_id")
            odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
            product_id = id_by_odoo_pid.get(odoo_pid if isinstance(odoo_pid, int) else -1)
            if product_id is None:
                unmapped += 1
                continue
            qty = m.get("quantity")
            if qty in (None, False, 0, 0.0):
                qty = m.get("product_uom_qty") or 0.0
            if float(qty or 0) <= 0:
                continue
            rows.append(
                StagingInboundMove(
                    odoo_picking_id=picking["id"],
                    picking_name=str(picking.get("name") or ""),
                    picking_state=str(picking.get("state") or ""),
                    product_id=product_id,
                    qty=float(qty),
                    expected_date=_scheduled_date(picking.get("scheduled_date")),
                    synced_at=now,
                )
            )

    db.execute(delete(StagingInboundMove))
    db.add_all(rows)

    extra = dict(state.extra or {})
    extra["native_pickings"] = len(native_by_id)
    extra["app_pickings_excluded"] = len(pickings) - len(native)
    if unmapped:
        extra["unmapped_move_lines"] = unmapped
    else:
        extra.pop("unmapped_move_lines", None)
    state.extra = extra
    return len(rows)
