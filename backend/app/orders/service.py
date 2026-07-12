"""Order-list approval → one draft internal transfer in Odoo.

The write outcome is recorded on the list itself and shown honestly in the
UI: `created` (live draft, with deep link), `simulated` (dry-run — kill
switch, feature flag, or fixture mode), or `failed` (Odoo said no; the list
stays approvable so the coordinator can retry with the SAME reference —
idempotency means a retry can never duplicate the transfer).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..auth.deps import AuthedUser
from ..config import Settings
from ..models import (
    Center,
    OrderList,
    OrderListStatus,
    OrderListWriteStatus,
    utcnow,
)
from ..odoo.errors import OdooWriteError
from ..odoo.operations import new_reference
from ..odoo.writer import OdooWriter, WriterValidationError


def approve_order_list(
    db: Session,
    settings: Settings,
    authed: AuthedUser,
    order_list: OrderList,
    dry_run: bool = False,
) -> OrderList:
    """Caller has already checked role scope and status. Approving runs the
    write; only a non-failed outcome marks the list approved."""
    if not order_list.lines:
        raise WriterValidationError("The list has no lines to transfer.")
    center = db.get(Center, order_list.center_id) if order_list.center_id else None
    if center is None:
        raise WriterValidationError("The list needs a destination center before approval.")
    if not center.odoo_location_id:
        raise WriterValidationError(
            f"'{center.name}' has no Odoo location mapped yet — run a stock sync, or fix "
            "the III/CityCenter location name in Odoo so it matches the center."
        )

    # a stable reference persisted BEFORE the write attempt: retries reuse it,
    # so Odoo can never end up with two drafts for one approval
    if not order_list.write_reference:
        order_list.write_reference = new_reference("OL")
        db.commit()

    writer = OdooWriter(db, settings, actor_user_id=authed.id)
    try:
        result = writer.create_internal_transfer(
            source_key="bwhse",
            dest_odoo_location_id=center.odoo_location_id,
            dest_label=center.name,
            lines=[{"product_id": line.product_id, "qty": line.qty} for line in order_list.lines],
            note=f"Order list '{order_list.name}' — {center.name}",
            reference=order_list.write_reference,
            dry_run=dry_run,
        )
    except OdooWriteError as e:
        order_list.write_status = OrderListWriteStatus.FAILED.value
        order_list.write_error = str(e)
        order_list.write_dry_run_reason = ""
        db.commit()
        return order_list

    if dry_run:
        # a preview: record nothing on the list beyond the reference
        return order_list

    order_list.status = OrderListStatus.APPROVED.value
    order_list.approved_by_id = authed.id
    order_list.approved_at = utcnow()
    order_list.write_error = ""
    order_list.write_dry_run_reason = result.dry_run_reason
    order_list.write_status = (
        OrderListWriteStatus.SIMULATED.value
        if result.dry_run
        else OrderListWriteStatus.CREATED.value
    )
    if result.record_ids:
        order_list.odoo_picking_id = result.record_ids[0]
    order_list.odoo_url = result.deep_link
    order_list.odoo_picking_name = result.record_name
    db.commit()
    return order_list
