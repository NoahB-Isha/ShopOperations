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
    ]
    picking_types = [
        {"id": 1, "name": "III: Receipts", "code": "incoming"},
        {"id": 5, "name": "III: Internal Transfers", "code": "internal"},
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
        {"id": 5001, "name": "III/POS/PREV", "date_order": prev, "state": "done"},
        {"id": 5002, "name": "III/POS/CUR", "date_order": cur, "state": "done"},
        {"id": 5003, "name": "III/POS/DRAFT", "date_order": cur, "state": "draft"},  # excluded
    ]
    pos_lines = [
        {"id": 1, "order_id": [5001, "III/POS/PREV"], "product_id": [201, "Copper"], "qty": 7},
        {"id": 2, "order_id": [5002, "III/POS/CUR"], "product_id": [201, "Copper"], "qty": 5},
        {"id": 3, "order_id": [5001, "III/POS/PREV"], "product_id": [203, "Incense"], "qty": 12},
        {"id": 4, "order_id": [5003, "III/POS/DRAFT"], "product_id": [201, "Copper"], "qty": 99},
        {"id": 5, "order_id": [5002, "III/POS/CUR"], "product_id": [999, "Ghost"], "qty": 4},
    ]
    sale_orders = [
        {"id": 8001, "name": "S-PREV", "date_order": prev, "state": "sale"},
        {"id": 8002, "name": "S-CANCEL", "date_order": cur, "state": "cancel"},  # excluded
    ]
    sale_lines = [
        {"id": 1, "order_id": [8001, "S-PREV"], "product_id": [201, "Copper"], "product_uom_qty": 3},
        {"id": 2, "order_id": [8001, "S-PREV"], "product_id": [205, "Toothpaste"], "product_uom_qty": 9},
        {"id": 3, "order_id": [8002, "S-CANCEL"], "product_id": [205, "Toothpaste"], "product_uom_qty": 50},
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
        "stock.move": ["id", "name", "product_id", "product_uom_qty", "product_qty", "date",
                       "state", "location_id", "location_dest_id", "picking_id", "picking_code"],
        "stock.picking.type": ["id", "name", "code"],
        "pos.order": ["id", "name", "date_order", "state"],
        "pos.order.line": ["id", "order_id", "product_id", "qty"],
        "sale.order": ["id", "name", "date_order", "state"],
        "sale.order.line": ["id", "order_id", "product_id", "product_uom_qty"],
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
        "expected_sales": {
            ("CA0023000009", py, pm, "pos"): 7.0,
            ("CA0023000009", cy, cm, "pos"): 5.0,
            ("IN0000000777", py, pm, "pos"): 12.0,
            ("CA0023000009", py, pm, "online"): 3.0,
            ("OC0000000042", py, pm, "online"): 9.0,
        },
        "incoming_count": 2,
        "months": {"current": (cy, cm), "previous": (py, pm)},
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
