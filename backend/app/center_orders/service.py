"""Odoo-facing side of center orders: render the approval's draft transfer,
and LISTEN (politely, throttled) for the warehouse validating it — that's
what flips an order to SHIPPED and pings the orderer.

Approval is the only write. It goes through the existing
`OdooWriter.create_internal_transfer` operation — same feature flag, same
audit trail, same draft-only discipline as the phase-2 flow. Orders whose
lines are all untracked (department water/snacks), or department orders with
no Odoo location, legitimately create NOTHING in Odoo: picking_status stays
'none' and the timeline says why. That's the honest path, not a failure.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Center,
    CenterOrder,
    CenterOrderEvent,
    CenterOrderEventKind,
    CenterOrderStatus,
    NotificationKind,
    OdooWriteOutcome,
    Zone,
    ZoneKind,
    is_due,
    utcnow,
)
from ..notify import service as notify
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError, OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter, WriterValidationError

log = logging.getLogger("center_orders")


def _event(
    db: Session,
    order: CenterOrder,
    kind: CenterOrderEventKind,
    note: str,
    actor_user_id: int | None = None,
    status: str = "",
) -> None:
    db.add(
        CenterOrderEvent(
            order_id=order.id,
            kind=kind.value,
            status=status,
            note=note,
            actor_user_id=actor_user_id,
        )
    )


def _tracked_lines(order: CenterOrder) -> list[dict]:
    """Writer-shaped lines for the products that exist in Odoo, at their
    approved (or requested) quantities. Zeroed-out lines stay off the picking."""
    return [
        {"product_id": line.product_id, "qty": line.qty_final}
        for line in order.lines
        if line.product.is_stock_tracked
        and line.product.odoo_product_id
        and line.qty_final > 0
    ]


# ------------------------------------------------------------ approval draft
def render_approval_draft(
    db: Session, settings: Settings, order: CenterOrder, actor_user_id: int | None
) -> None:
    """Create the draft internal transfer the moment the coordinator approves.
    Field zones: BWHSE → the center's III/CityCenter location. Departments:
    III-FLOOR sourced — and usually no Odoo record at all."""
    center = db.get(Center, order.center_id)
    zone = db.get(Zone, center.zone_id) if center and center.zone_id else None
    is_dept = bool(zone and zone.kind == ZoneKind.DEPARTMENTS.value)

    tracked = _tracked_lines(order)
    untracked_count = len(order.lines) - len(
        [line for line in order.lines if line.product.is_stock_tracked and line.product.odoo_product_id]
    )

    if not tracked:
        order.picking_status = OdooWriteOutcome.NONE.value
        _event(
            db, order, CenterOrderEventKind.ODOO,
            "no Odoo transfer — nothing on this order is Odoo-tracked; "
            "fulfilled directly from the Shoppe floor",
            actor_user_id,
        )
        return
    if is_dept and not (center and center.odoo_location_id):
        order.picking_status = OdooWriteOutcome.NONE.value
        note = "no Odoo transfer — departments are fulfilled from the Shoppe floor"
        if untracked_count:
            note += f" ({untracked_count} untracked item(s) alongside)"
        _event(db, order, CenterOrderEventKind.ODOO, note, actor_user_id)
        return

    writer = OdooWriter(db, settings, actor_user_id=actor_user_id)
    reference = order.picking_reference or new_reference("ORD")
    order.picking_reference = reference
    note = f"Center order {order.display_name} — {center.name if center else ''}".strip()
    if untracked_count:
        note += f" (+{untracked_count} untracked item(s) fulfilled directly)"
    try:
        result = writer.create_internal_transfer(
            source_key=order.source_location_key or "bwhse",
            # 0 (not None) when unmapped → the writer's actionable
            # "no Odoo location mapped yet — run a stock sync" error
            dest_odoo_location_id=(center.odoo_location_id or 0) if center else 0,
            dest_label=center.name if center else f"center {order.center_id}",
            lines=tracked,
            note=note,
            reference=reference,
        )
    except (OdooWriteError, WriterValidationError) as e:
        order.picking_status = OdooWriteOutcome.FAILED.value
        order.picking_error = str(e)
        _event(
            db, order, CenterOrderEventKind.ODOO,
            f"Odoo draft FAILED: {e}", actor_user_id,
        )
        return
    order.picking_error = ""
    if result.dry_run:
        order.picking_status = OdooWriteOutcome.SIMULATED.value
        _event(
            db, order, CenterOrderEventKind.ODOO,
            f"Odoo draft simulated ({result.dry_run_reason}) — nothing was written",
            actor_user_id,
        )
    else:
        order.picking_status = OdooWriteOutcome.CREATED.value
        order.odoo_picking_id = result.record_ids[0] if result.record_ids else None
        order.odoo_picking_name = result.record_name
        order.odoo_picking_url = result.deep_link
        _event(
            db, order, CenterOrderEventKind.ODOO,
            f"draft {result.record_name or ''} created in Odoo — "
            "warehouse validates it there".strip(),
            actor_user_id,
        )


# ---------------------------------------------------------- shipped listener
def poll_shipped(db: Session, settings: Settings, order: CenterOrder) -> bool:
    """Check whether the approval picking was validated ('done') in Odoo; if
    so flip the order to SHIPPED and notify the orderer. Throttled per order;
    safe to call on every UI refresh. Returns True on the transition."""
    if (
        order.status != CenterOrderStatus.APPROVED.value
        or order.picking_status != OdooWriteOutcome.CREATED.value
        or not order.odoo_picking_id
    ):
        return False
    now = utcnow()
    if not is_due(order.picking_checked_at, settings.order_shipped_poll_seconds, now):
        return False
    order.picking_checked_at = now
    db.commit()  # persist the throttle stamp even if the read below fails

    try:
        conn = get_connection(settings, read_only=True)
        rows = conn.search_read(
            "stock.picking", [["id", "=", order.odoo_picking_id]], ["state", "name"]
        )
    except OdooError as e:
        log.warning("shipped poll failed for order %s: %s", order.id, e)
        return False
    if not rows:
        return False
    state = str(rows[0].get("state") or "")
    if state == "cancel":
        order.picking_status = OdooWriteOutcome.FAILED.value
        order.picking_error = "cancelled in Odoo"
        _event(
            db, order, CenterOrderEventKind.ODOO,
            f"{order.odoo_picking_name} was CANCELLED in Odoo — the order stays "
            "approved; sort it out with the warehouse",
        )
        db.commit()
        return False
    if state != "done":
        return False

    order.status = CenterOrderStatus.SHIPPED.value
    _event(
        db, order, CenterOrderEventKind.STATUS,
        f"{order.odoo_picking_name} validated in Odoo — shipped",
        status=CenterOrderStatus.SHIPPED.value,
    )
    rows_n = notify.enqueue_order_notifications(
        db, settings, order, NotificationKind.ORDER_SHIPPED
    )
    db.commit()
    notify.deliver_now(db, settings, rows_n)
    return True
