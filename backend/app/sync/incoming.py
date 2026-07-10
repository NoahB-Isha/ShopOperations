"""Incoming stock moves (inbound shipments) -> incoming_moves, replaced whole."""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import IncomingMove, Product, SyncState
from ..odoo.protocol import OdooConnection

PENDING_STATES = ["assigned", "confirmed", "waiting", "partially_available"]


def _parse_date(value) -> date | None:
    s = str(value or "")[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def sync_incoming(db: Session, settings: Settings, conn: OdooConnection, state: SyncState) -> int:
    moves = conn.search_read(
        "stock.move",
        [["state", "in", PENDING_STATES], ["picking_code", "=", "incoming"]],
        ["product_id", "product_qty", "date", "state", "picking_id"],
    )

    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(Product.odoo_product_id.is_not(None))
        )
    }

    db.execute(delete(IncomingMove))
    count = 0
    for mv in moves:
        pid_field = mv.get("product_id")
        odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
        picking = mv.get("picking_id")
        db.add(
            IncomingMove(
                odoo_move_id=mv["id"],
                product_id=id_by_odoo_pid.get(odoo_pid),
                qty=mv.get("product_qty") or 0.0,
                expected_date=_parse_date(mv.get("date")),
                state=mv.get("state") or "",
                picking_ref=picking[1] if isinstance(picking, list) else "",
            )
        )
        count += 1
    return count
