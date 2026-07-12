"""Odoo-facing side of the transfer flow: render the placement draft, read
sent quantities back, prepare the count transfer, and LISTEN for its
validation (polled politely — the UI's live refresh calls these on read).

All writes go through the OdooWriter; every outcome is recorded honestly on
the request (created / simulated / failed) so the flow keeps working — and
keeps telling the truth — when writes are gated or Odoo is unreachable.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Adjustment,
    OdooWriteOutcome,
    TransferEvent,
    TransferEventKind,
    TransferRequest,
    TransferRequestStatus,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.protocol import OdooConnection, safe_fields
from ..odoo.urls import odoo_record_url
from ..odoo.writer import OdooWriter

log = logging.getLogger("transfers")


def _event(
    db: Session,
    req: TransferRequest,
    kind: TransferEventKind,
    note: str,
    actor_user_id: int | None = None,
    status: str = "",
) -> None:
    db.add(
        TransferEvent(
            request_id=req.id,
            kind=kind.value,
            status=status,
            note=note,
            actor_user_id=actor_user_id,
        )
    )


def barcode_url(settings: Settings, picking_id: int) -> str:
    base = settings.odoo_base_url.rstrip("/") or "https://odoo.fixture.invalid"
    return settings.odoo_barcode_url_template.format(base=base, id=picking_id)


# ------------------------------------------------------------ placement draft
def render_placement_draft(
    db: Session, settings: Settings, req: TransferRequest, actor_user_id: int | None
) -> None:
    """Create the BWHSE→STAGING draft the moment the floor places the
    request. The request adopts the picking's Odoo name."""
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    reference = req.picking_reference or new_reference("TR")
    req.picking_reference = reference
    try:
        result = writer.create_internal_transfer(
            source_key="bwhse",
            dest_key="staging",
            lines=[
                {"product_id": line.product_id, "qty": line.qty_requested}
                for line in req.lines
            ],
            note=f"Floor transfer request #{req.id}",
            reference=reference,
        )
    except OdooWriteError as e:
        req.picking_status = OdooWriteOutcome.FAILED.value
        req.picking_error = str(e)
        _event(db, req, TransferEventKind.ODOO, f"Odoo draft FAILED: {e}", actor_user_id)
        return
    req.picking_error = ""
    if result.dry_run:
        req.picking_status = OdooWriteOutcome.SIMULATED.value
        _event(
            db, req, TransferEventKind.ODOO,
            f"Odoo draft simulated ({result.dry_run_reason}) — nothing was written",
            actor_user_id,
        )
    else:
        req.picking_status = OdooWriteOutcome.CREATED.value
        req.odoo_picking_id = result.record_ids[0] if result.record_ids else None
        req.odoo_picking_name = result.record_name
        req.odoo_picking_url = result.deep_link
        _event(
            db, req, TransferEventKind.ODOO,
            f"draft {result.record_name or ''} created in Odoo".strip(),
            actor_user_id,
        )


# --------------------------------------------------------- sent-qty readback
def refresh_sent_quantities(
    db: Session, settings: Settings, req: TransferRequest
) -> str:
    """At 'sent', the warehouse's numbers live in the Odoo picking (they may
    have validated it, edited quantities, or both). Read them back; fall back
    to the requested quantities when there's no live picking."""
    if req.picking_status != OdooWriteOutcome.CREATED.value or not req.odoo_picking_id:
        for line in req.lines:
            if line.qty_sent is None:
                line.qty_sent = line.qty_requested
        return "sent quantities assumed from the request (no live Odoo picking)"
    try:
        conn = get_connection(settings, read_only=True)
        by_product = _move_quantities(conn, req.odoo_picking_id)
    except OdooError as e:
        for line in req.lines:
            if line.qty_sent is None:
                line.qty_sent = line.qty_requested
        return f"could not read the picking back ({e}); assumed requested quantities"
    matched = 0
    for line in req.lines:
        odoo_pid = line.product.odoo_product_id
        if odoo_pid in by_product:
            line.qty_sent = by_product[odoo_pid]
            matched += 1
        elif line.qty_sent is None:
            line.qty_sent = 0.0  # line dropped in Odoo -> nothing sent
    return f"sent quantities read back from {req.odoo_picking_name} ({matched} line(s))"


def _move_quantities(conn: OdooConnection, picking_id: int) -> dict[int, float]:
    """product odoo id -> quantity. Prefers the actual done quantity
    (v17+ `quantity`), falling back to the demand."""
    fields = safe_fields(conn, "stock.move", ["product_id", "quantity", "product_uom_qty", "state"])
    moves = conn.search_read("stock.move", [["picking_id", "=", picking_id]], fields)
    out: dict[int, float] = {}
    for m in moves:
        pid_field = m.get("product_id")
        odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
        if not isinstance(odoo_pid, int):
            continue
        qty = m.get("quantity")
        if qty in (None, False):
            qty = m.get("product_uom_qty") or 0.0
        out[odoo_pid] = out.get(odoo_pid, 0.0) + float(qty or 0.0)
    return out


# ------------------------------------------------------------- count transfer
def prepare_count_transfer(
    db: Session, settings: Settings, req: TransferRequest, actor_user_id: int | None
) -> None:
    """Duplicate the placement picking as STAGING→FLOOR, mark To Do, check
    availability — ready for the barcode app. Records the honest outcome."""
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    reference = req.count_reference or new_reference("CNT")
    req.count_reference = reference
    if req.picking_status != OdooWriteOutcome.CREATED.value or not req.odoo_picking_id:
        # nothing real to duplicate — an honest simulation (fixture/demo mode)
        req.count_status = OdooWriteOutcome.SIMULATED.value
        req.count_error = ""
        _event(
            db, req, TransferEventKind.ODOO,
            "count transfer simulated — no live placement picking to duplicate",
            actor_user_id,
        )
        return
    try:
        result = writer.prepare_count_transfer(
            source_picking_odoo_id=req.odoo_picking_id, reference=reference
        )
    except OdooWriteError as e:
        req.count_status = OdooWriteOutcome.FAILED.value
        req.count_error = str(e)
        _event(db, req, TransferEventKind.ODOO, f"count transfer FAILED: {e}", actor_user_id)
        return
    req.count_error = ""
    if result.dry_run:
        req.count_status = OdooWriteOutcome.SIMULATED.value
        _event(
            db, req, TransferEventKind.ODOO,
            f"count transfer simulated ({result.dry_run_reason}) — nothing was written",
            actor_user_id,
        )
    else:
        req.count_status = OdooWriteOutcome.CREATED.value
        req.count_picking_id = result.record_ids[0] if result.record_ids else None
        req.count_picking_name = result.record_name
        req.count_picking_url = result.deep_link
        req.count_barcode_url = (
            barcode_url(settings, req.count_picking_id) if req.count_picking_id else ""
        )
        _event(
            db, req, TransferEventKind.ODOO,
            f"count transfer {result.record_name} ready — scan it in the barcode app",
            actor_user_id,
        )


# ------------------------------------------------------- validation listener
def poll_count_validation(db: Session, settings: Settings, req: TransferRequest) -> bool:
    """Check Odoo for the count picking's validation; on 'done', pull the
    counted quantities, reconcile against sent, and close the request.
    Throttled per request; safe to call on every UI refresh. Returns True
    when the request just transitioned to done."""
    if (
        req.status != TransferRequestStatus.COUNTING.value
        or req.count_status != OdooWriteOutcome.CREATED.value
        or not req.count_picking_id
    ):
        return False
    now = utcnow()
    if req.count_checked_at is not None:
        checked = req.count_checked_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=now.tzinfo)
        if (now - checked).total_seconds() < settings.odoo_count_poll_seconds:
            return False
    req.count_checked_at = now
    db.commit()  # persist the throttle stamp even if the read below fails

    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking", [["id", "=", req.count_picking_id]], ["state", "name"]
        )
        if not rows:
            return False
        state = str(rows[0].get("state") or "")
        if state == "cancel":
            _event(
                db, req, TransferEventKind.ODOO,
                f"count transfer {req.count_picking_name} was CANCELLED in Odoo — "
                "prepare it again or close the request manually",
            )
            req.count_status = OdooWriteOutcome.FAILED.value
            req.count_error = "cancelled in Odoo"
            db.commit()
            return False
        if state != "done":
            return False
        counted = _move_quantities(conn, req.count_picking_id)
    except OdooError as e:
        log.warning("count poll failed for request %s: %s", req.id, e)
        return False

    finish_from_count(db, req, counted, source=f"{req.count_picking_name} validated in Odoo")
    db.commit()
    return True


def finish_from_count(
    db: Session,
    req: TransferRequest,
    counted_by_odoo_pid: dict[int, float],
    source: str,
    actor_user_id: int | None = None,
) -> None:
    """Apply counted quantities, file discrepancies, mark done."""
    from .flow import reconcile  # local import keeps flow.py pure/import-light

    for line in req.lines:
        odoo_pid = line.product.odoo_product_id
        line.qty_counted = float(counted_by_odoo_pid.get(odoo_pid or -1, 0.0))
    discrepancies = reconcile(req.lines)
    for d in discrepancies:
        db.add(
            Adjustment(
                request_id=req.id,
                line_id=d.line_id,
                product_id=d.product_id,
                qty_expected=d.qty_expected,
                qty_counted=d.qty_counted,
                delta=d.delta,
                note=f"Count on {req.display_name}",
            )
        )
    req.status = TransferRequestStatus.DONE.value
    _event(
        db, req, TransferEventKind.STATUS, source, actor_user_id,
        status=TransferRequestStatus.DONE.value,
    )
    if discrepancies:
        detail = "; ".join(
            f"sent {d.qty_expected:g}, counted {d.qty_counted:g} ({d.delta:+g})"
            for d in discrepancies
        )
        _event(
            db, req, TransferEventKind.DISCREPANCY,
            f"{len(discrepancies)} discrepancy(ies) → adjustments queue: {detail}",
            actor_user_id,
        )


def cancel_placement_draft(
    db: Session, settings: Settings, req: TransferRequest, actor_user_id: int | None
) -> None:
    """On cancel, remove the app-created draft from Odoo (drafts only, app
    reference verified by the writer). Failures just leave a note — a human
    can delete the draft in Odoo."""
    if req.picking_status != OdooWriteOutcome.CREATED.value or not req.odoo_picking_id:
        return
    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking", [["id", "=", req.odoo_picking_id]], ["state"]
        )
        if rows and rows[0].get("state") == "draft":
            writer.unlink_app_record("stock.picking", req.odoo_picking_id)
            _event(
                db, req, TransferEventKind.ODOO,
                f"draft {req.odoo_picking_name} removed from Odoo", actor_user_id,
            )
        elif rows:
            _event(
                db, req, TransferEventKind.ODOO,
                f"{req.odoo_picking_name} is already {rows[0].get('state')} in Odoo — "
                "left in place, review it there",
                actor_user_id,
            )
    except (OdooError, OdooWriteError, ValueError) as e:
        _event(
            db, req, TransferEventKind.ODOO,
            f"could not remove the Odoo draft ({e}) — delete it manually", actor_user_id,
        )


def picking_deep_link(settings: Settings, picking_id: int) -> str:
    return odoo_record_url(settings, "stock.picking", picking_id)
