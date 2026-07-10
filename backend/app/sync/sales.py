"""Sales-history sync — the one heavy query, handled politely.

On this instance the sale.report aggregate returns nothing (learned the hard
way in the ops project), so sales come from POS order lines + online sale
order lines, joined to their parent orders for dates, counting only confirmed
states.

Cadence policy: ONE full backfill (24 months) at setup; afterwards hourly
incrementals touch only the current month, and the previous month is
re-pulled once a day to catch late edits. Markers live in sync_state.extra.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Product, SalesMonthly, SyncState, utcnow
from ..odoo.protocol import OdooConnection

# (line model, qty field, parent order model, confirmed states, app channel)
SOURCES = [
    ("pos.order.line", "qty", "pos.order", ("paid", "done", "invoiced"), "pos"),
    ("sale.order.line", "product_uom_qty", "sale.order", ("sale", "done"), "online"),
]


def _first_of_month_minus(anchor: date, months: int) -> date:
    total = anchor.year * 12 + (anchor.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def _model_exists(conn: OdooConnection, model: str) -> bool:
    try:
        return bool(conn.fields_get(model))
    except Exception:
        return False


def sync_sales(db: Session, settings: Settings, conn: OdooConnection, state: SyncState) -> int:
    now = utcnow()
    today = now.date()
    extra = dict(state.extra or {})

    if not extra.get("backfill_done_at"):
        since = _first_of_month_minus(today, settings.sales_backfill_months - 1)
        window_label = f"backfill {settings.sales_backfill_months} months"
    elif extra.get("prev_month_synced_on") != today.isoformat():
        since = _first_of_month_minus(today, 1)  # previous month, once a day
        window_label = "incremental (current + previous month)"
    else:
        since = today.replace(day=1)  # hourly: current month only
        window_label = "incremental (current month)"

    since_str = f"{since.isoformat()} 00:00:00"

    id_by_odoo_pid = {
        odoo_id: pid
        for pid, odoo_id in db.execute(
            select(Product.id, Product.odoo_product_id).where(Product.odoo_product_id.is_not(None))
        )
    }

    # bucket[(product_id, year, month, channel)] = units
    bucket: dict[tuple[int, int, int, str], float] = {}
    any_source = False
    for line_model, qty_field, order_model, states, channel in SOURCES:
        if not _model_exists(conn, line_model):
            continue
        any_source = True
        lines = conn.search_read(
            line_model,
            [["order_id.date_order", ">=", since_str], ["order_id.state", "in", list(states)]],
            ["product_id", "order_id", qty_field],
        )
        if not lines:
            continue
        order_ids = sorted(
            {ln["order_id"][0] for ln in lines if isinstance(ln.get("order_id"), list)}
        )
        dates: dict[int, str] = {}
        for i in range(0, len(order_ids), 500):
            for o in conn.search_read(
                order_model, [["id", "in", order_ids[i : i + 500]]], ["date_order"]
            ):
                dates[o["id"]] = str(o.get("date_order") or "")
        for ln in lines:
            pid_field = ln.get("product_id")
            odoo_pid = pid_field[0] if isinstance(pid_field, list) else pid_field
            if not isinstance(odoo_pid, int):
                continue
            product_id = id_by_odoo_pid.get(odoo_pid)
            if product_id is None:
                continue
            order_field = ln.get("order_id")
            oid = order_field[0] if isinstance(order_field, list) else order_field
            if not isinstance(oid, int):
                continue
            d = dates.get(oid, "")
            if len(d) < 7 or not d[:4].isdigit():
                continue
            year, month = int(d[:4]), int(d[5:7])
            qty = float(ln.get(qty_field) or 0.0)
            key = (product_id, year, month, channel)
            bucket[key] = bucket.get(key, 0.0) + qty

    if not any_source:
        raise RuntimeError(
            "Neither pos.order.line nor sale.order.line is available — no sales source."
        )

    # Replace only the synced window (transactional; runner commits/rolls back).
    db.execute(
        delete(SalesMonthly).where(
            or_(
                SalesMonthly.year > since.year,
                and_(SalesMonthly.year == since.year, SalesMonthly.month >= since.month),
            )
        )
    )
    for (product_id, year, month, channel), units in bucket.items():
        db.add(
            SalesMonthly(
                product_id=product_id,
                year=year,
                month=month,
                channel=channel,
                units=round(units, 3),
                synced_at=now,
            )
        )

    extra.setdefault("backfill_done_at", now.isoformat())
    extra["prev_month_synced_on"] = today.isoformat() if since < today.replace(day=1) else extra.get("prev_month_synced_on")
    extra["last_window"] = window_label
    state.extra = extra
    return len(bucket)
