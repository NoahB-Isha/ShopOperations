"""Tiny handwritten Odoo fixture set for tests — stable, precise, readable.

Dates are anchored to the current month so the sales window logic sees them;
the returned expectations dict tells tests exactly what the sync should
produce.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def _month_minus(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) - n
    return total // 12, total % 12 + 1


def build_test_fixtures(out_dir: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    cy, cm = now.year, now.month
    py, pm = _month_minus(cy, cm, 1)
    cur = f"{cy}-{cm:02d}-05 10:00:00"
    prev = f"{py}-{pm:02d}-15 10:00:00"

    products = [
        _p(201, "CA0023000009", "Copper Water Bottle — 950ml", "Copper", 34.0, 14.0),
        _p(202, "RU0000000005", "Rudraksha Mala — 5mm", "Rudraksha", 24.0, 8.0),
        _p(203, "IN0000000777", "Sandalwood Incense", "Incense & Dhoop", 9.0, 3.0),
        _p(204, "US-SN0001", "Banana Chips", "Snacks", 4.5, 1.8),
        _p(205, "OC0000000042", "Neem Toothpaste", "Oral Care", 6.0, 2.2),
        _p(206, "BL0000000021", "Bloom Ghee", "Bloom", 18.0, 9.0),
        _p(207, "", "Mystery Item (no code)", "Home & Living", 11.0, 5.0),
        _p(208, "AP0000000001", "Kurta", "Apparel", 28.0, 12.0),
        # duplicate default_code (Odoo variant) — sync must keep the first
        _p(209, "CA0023000009", "Copper Water Bottle — 950ml (Engraved)", "Copper", 36.0, 15.0),
    ]

    locations = [
        {"id": 11, "complete_name": "III/Stock", "usage": "view"},
        {"id": 12, "complete_name": "III/Stock/BWHSE", "usage": "internal"},
        # legacy space spelling on purpose — ODOO_LOCATION_NAMES must accept it
        {"id": 13, "complete_name": "III/Stock/III-FLOOR STAGING", "usage": "internal"},
        {"id": 14, "complete_name": "III/Stock/III-FLOOR", "usage": "internal"},
        # warehouse bin: quants here must roll up into bwhse (subtree matching)
        {"id": 15, "complete_name": "III/Stock/BWHSE/A/1/1/1", "usage": "internal"},
        # per-center locations: stock sync maps these to centers by leaf name
        {"id": 21, "complete_name": "III/CityCenter", "usage": "view"},
        {"id": 22, "complete_name": "III/CityCenter/Austin", "usage": "internal"},
        {"id": 23, "complete_name": "III/CityCenter/Ghost Town", "usage": "internal"},
        # the reduction operation type's default destination (virtual loss)
        {"id": 31, "complete_name": "Virtual Locations/USA-III: Inventory adjustment", "usage": "inventory"},
    ]
    picking_types = [
        {"id": 1, "name": "III: Receipts", "code": "incoming", "default_location_src_id": False, "default_location_dest_id": False},
        {"id": 5, "name": "III: Internal Transfers", "code": "internal", "default_location_src_id": False, "default_location_dest_id": False},
        {"id": 7, "name": "USA-III: Inventory Adj Reduction", "code": "internal",
         "default_location_src_id": False,
         "default_location_dest_id": [31, "Virtual Locations/USA-III: Inventory adjustment"]},
        # double space in the live name, on purpose — the config matches via %
        {"id": 8, "name": "USA-III: Inventory Adj  Adding Qty", "code": "internal",
         "default_location_src_id": [31, "Virtual Locations/USA-III: Inventory adjustment"],
         "default_location_dest_id": False},
    ]
    quants = [
        _q(1, 201, "Copper Water Bottle — 950ml", 12, "III/Stock/BWHSE", 120),
        _q(2, 201, "Copper Water Bottle — 950ml", 14, "III/Stock/III-FLOOR", 12),
        _q(3, 202, "Rudraksha Mala — 5mm", 12, "III/Stock/BWHSE", 40),
        _q(4, 203, "Sandalwood Incense", 14, "III/Stock/III-FLOOR", 6),
        _q(5, 203, "Sandalwood Incense", 13, "III/Stock/III-FLOOR STAGING", 4),
        _q(6, 204, "Banana Chips", 12, "III/Stock/BWHSE", 300),
        _q(7, 999, "Ghost Product", 12, "III/Stock/BWHSE", 55),  # unknown -> skipped
        _q(8, 201, "Copper Water Bottle — 950ml", 15, "III/Stock/BWHSE/A/1/1/1", 30),  # bin
    ]

    pos_orders = [
        # campus floor (Shoppe), a city-center config, and a campus one-off —
        # the sales sync classifies channels from config_id. Partner 9001
        # orders in BOTH months (returning customer); the Austin order is a
        # walk-in (no partner — counts as an order, not a customer).
        {"id": 5001, "name": "III/POS/PREV", "date_order": prev, "state": "done",
         "config_id": [2, "III Floor"], "partner_id": [9001, "Priya R"], "amount_total": 346.0},
        {"id": 5002, "name": "III/POS/CUR", "date_order": cur, "state": "done",
         "config_id": [2, "III Floor"], "partner_id": [9001, "Priya R"], "amount_total": 210.0},
        {"id": 5003, "name": "III/POS/DRAFT", "date_order": cur, "state": "draft",  # excluded
         "config_id": [2, "III Floor"], "partner_id": False, "amount_total": 3366.0},
        {"id": 5004, "name": "AUSTIN/PREV", "date_order": prev, "state": "done",
         "config_id": [22, "Austin"], "partner_id": False, "amount_total": 72.0},
        {"id": 5005, "name": "SNACK/CUR", "date_order": cur, "state": "done",
         "config_id": [60, "III-Snack"], "partner_id": [9002, "Arun K"], "amount_total": 45.0},
    ]
    pos_lines = [
        {"id": 1, "order_id": [5001, "III/POS/PREV"], "product_id": [201, "Copper"], "qty": 7,
         "price_subtotal_incl": 238.0},
        {"id": 2, "order_id": [5002, "III/POS/CUR"], "product_id": [201, "Copper"], "qty": 5,
         "price_subtotal_incl": 170.0},
        {"id": 3, "order_id": [5001, "III/POS/PREV"], "product_id": [203, "Incense"], "qty": 12,
         "price_subtotal_incl": 108.0},
        {"id": 4, "order_id": [5003, "III/POS/DRAFT"], "product_id": [201, "Copper"], "qty": 99,
         "price_subtotal_incl": 3366.0},
        {"id": 5, "order_id": [5002, "III/POS/CUR"], "product_id": [999, "Ghost"], "qty": 4,
         "price_subtotal_incl": 40.0},
        {"id": 6, "order_id": [5004, "AUSTIN/PREV"], "product_id": [202, "Mala"], "qty": 3,
         "price_subtotal_incl": 72.0},
        {"id": 7, "order_id": [5005, "SNACK/CUR"], "product_id": [204, "Chips"], "qty": 10,
         "price_subtotal_incl": 45.0},
    ]
    sale_orders = [
        {"id": 8001, "name": "S-PREV", "date_order": prev, "state": "sale",
         "partner_id": [9003, "Maya S"], "amount_total": 156.0},
        {"id": 8002, "name": "S-CANCEL", "date_order": cur, "state": "cancel",  # excluded
         "partner_id": [9003, "Maya S"], "amount_total": 300.0},
    ]
    sale_lines = [
        {"id": 1, "order_id": [8001, "S-PREV"], "product_id": [201, "Copper"], "product_uom_qty": 3,
         "price_total": 102.0},
        {"id": 2, "order_id": [8001, "S-PREV"], "product_id": [205, "Toothpaste"], "product_uom_qty": 9,
         "price_total": 54.0},
        {"id": 3, "order_id": [8002, "S-CANCEL"], "product_id": [205, "Toothpaste"], "product_uom_qty": 50,
         "price_total": 300.0},
    ]

    incoming = [
        {"id": 9001, "product_id": [201, "Copper"], "product_qty": 48.0,
         "date": f"{cy + 1}-01-15 00:00:00", "state": "assigned",
         "picking_id": [901, "III/IN/00901"], "picking_code": "incoming"},
        {"id": 9002, "product_id": [205, "Toothpaste"], "product_qty": 96.0,
         "date": f"{cy + 1}-02-01 00:00:00", "state": "confirmed",
         "picking_id": [902, "III/IN/00902"], "picking_code": "incoming"},
        {"id": 9003, "product_id": [202, "Mala"], "product_qty": 10.0,
         "date": cur, "state": "done",  # already received -> excluded
         "picking_id": [903, "III/IN/00903"], "picking_code": "incoming"},
    ]

    schema = {
        "product.product": ["id", "default_code", "name", "display_name", "categ_id",
                            "standard_price", "list_price", "barcode", "sale_ok", "active"],
        "stock.location": ["id", "complete_name", "usage"],
        "stock.quant": ["id", "product_id", "location_id", "quantity"],
        "stock.picking": ["id", "name", "origin", "state", "location_id", "location_dest_id",
                          "picking_type_id", "move_ids", "note"],
        "stock.move": ["id", "description_picking", "product_id", "product_uom_qty", "product_qty",
                       "date", "state", "location_id", "location_dest_id", "picking_id",
                       "picking_code"],
        "stock.picking.type": ["id", "name", "code", "default_location_src_id", "default_location_dest_id"],
        "pos.order": ["id", "name", "date_order", "state", "config_id", "partner_id", "amount_total"],
        "pos.order.line": ["id", "order_id", "product_id", "qty", "price_subtotal_incl"],
        "sale.order": ["id", "name", "date_order", "state", "partner_id", "amount_total"],
        "sale.order.line": ["id", "order_id", "product_id", "product_uom_qty", "price_total"],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "product.product": products,
        "stock.location": locations,
        "stock.picking.type": picking_types,
        "stock.quant": quants,
        "pos.order": pos_orders,
        "pos.order.line": pos_lines,
        "sale.order": sale_orders,
        "sale.order.line": sale_lines,
        "stock.move": incoming,
        "stock.picking": [],
    }
    for model, rows in files.items():
        (out_dir / f"{model}.json").write_text(json.dumps(rows, indent=1))
    (out_dir / "_schema.json").write_text(json.dumps(schema, indent=1))

    return {
        "product_count": 8,  # 9 records, one duplicate default_code collapsed
        "expected_stock": {
            ("CA0023000009", "bwhse"): 150.0,  # 120 at the root + 30 in bin A/1/1/1
            ("CA0023000009", "floor"): 12.0,
            ("RU0000000005", "bwhse"): 40.0,
            ("IN0000000777", "floor"): 6.0,
            ("IN0000000777", "staging"): 4.0,
            ("US-SN0001", "bwhse"): 300.0,
        },
        # channels assume a Center named "Austin" exists in the app DB (the
        # sales test seeds it); without one the Austin config would honestly
        # land in campus_other
        "expected_sales": {
            ("CA0023000009", py, pm, "shoppe"): 7.0,
            ("CA0023000009", cy, cm, "shoppe"): 5.0,
            ("IN0000000777", py, pm, "shoppe"): 12.0,
            ("RU0000000005", py, pm, "city_center"): 3.0,
            ("US-SN0001", cy, cm, "campus_other"): 10.0,
            ("CA0023000009", py, pm, "online"): 3.0,
            ("OC0000000042", py, pm, "online"): 9.0,
        },
        "expected_amounts": {
            ("CA0023000009", py, pm, "shoppe"): 238.0,
            ("RU0000000005", py, pm, "city_center"): 72.0,
            ("CA0023000009", py, pm, "online"): 102.0,
        },
        "expected_center_sales": {("Austin", py, pm): (3.0, 72.0)},
        # (y, m, channel) -> (orders, amount, with_customer, distinct, new, returning)
        # partner 9001 buys in both months → RETURNING in the current month
        "expected_orders": {
            (py, pm, "shoppe"): (1, 346.0, 1, 1, 1, 0),
            (cy, cm, "shoppe"): (1, 210.0, 1, 1, 0, 1),
            (py, pm, "city_center"): (1, 72.0, 0, 0, 0, 0),
            (cy, cm, "campus_other"): (1, 45.0, 1, 1, 1, 0),
            (py, pm, "online"): (1, 156.0, 1, 1, 1, 0),
        },
        "expected_sales_daily": {
            ("CA0023000009", f"{py}-{pm:02d}-15", "shoppe"): 7.0,
            ("CA0023000009", f"{cy}-{cm:02d}-05", "shoppe"): 5.0,
            ("IN0000000777", f"{py}-{pm:02d}-15", "shoppe"): 12.0,
            ("RU0000000005", f"{py}-{pm:02d}-15", "city_center"): 3.0,
            ("US-SN0001", f"{cy}-{cm:02d}-05", "campus_other"): 10.0,
            ("CA0023000009", f"{py}-{pm:02d}-15", "online"): 3.0,
            ("OC0000000042", f"{py}-{pm:02d}-15", "online"): 9.0,
        },
        "incoming_count": 2,
        "months": {"current": (cy, cm), "previous": (py, pm)},
        "austin_location_id": 22,
    }


def _p(pid: int, code: str, name: str, cat: str, price: float, cost: float) -> dict:
    return {
        "id": pid,
        "default_code": code,
        "name": name,
        "display_name": f"[{code}] {name}" if code else name,
        "categ_id": [1, cat],
        "list_price": price,
        "standard_price": cost,
        "barcode": f"890{pid:010d}",
        "sale_ok": True,
        "active": True,
    }


def _q(qid: int, pid: int, pname: str, loc_id: int, loc_name: str, qty: float) -> dict:
    return {
        "id": qid,
        "product_id": [pid, pname],
        "location_id": [loc_id, loc_name],
        "quantity": float(qty),
    }
