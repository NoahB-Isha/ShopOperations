"""Read-only contract check against the live instance.

Fixtures can drift from production. When that's suspected, this re-validates
the handful of models/fields the app depends on using only `fields_get` and
`search_count` — reads are always safe. Runnable as a module
(`python -m app.odoo.contract`), from the admin status page, or as a pytest
marked `odoo_live` (skipped unless credentials are present).
"""
from __future__ import annotations

from .protocol import OdooConnection

CONTRACT: dict[str, list[str]] = {
    "product.product": [
        "default_code", "name", "categ_id", "list_price", "standard_price",
        "barcode", "sale_ok", "active", "all_product_tag_ids",
    ],
    # Domestic/India sourcing tags (verified live 2026-07-27: model exists,
    # all_product_tag_ids readable via search_read)
    "product.tag": ["name"],
    "stock.location": ["complete_name", "usage"],
    "stock.quant": ["product_id", "location_id", "quantity"],
    "stock.move": ["product_id", "product_qty", "date", "state", "picking_id", "picking_code",
                   "description_picking"],
    "stock.picking": ["origin", "state", "location_id", "location_dest_id", "picking_type_id"],
    "stock.picking.type": ["code", "name"],
    # config_id + the price fields drive channel classification and revenue
    # capture (verified live 2026-07-21); partner_id + amount_total drive the
    # order/customer metrics (verified live 2026-07-23)
    "pos.order": ["date_order", "state", "config_id", "partner_id", "amount_total"],
    "pos.order.line": ["product_id", "qty", "order_id", "price_subtotal_incl"],
    "sale.order": ["date_order", "state", "partner_id", "amount_total"],
    "sale.order.line": ["product_id", "product_uom_qty", "order_id", "price_total"],
}


def check_contract(conn: OdooConnection) -> list[dict]:
    results = []
    for model, wanted in CONTRACT.items():
        entry: dict = {"model": model, "ok": False, "missing": [], "count": None, "error": ""}
        try:
            have = set(conn.fields_get(model).keys())
            entry["missing"] = [f for f in wanted if f not in have]
            entry["count"] = conn.search_count(model, [])
            entry["ok"] = not entry["missing"]
        except Exception as e:  # noqa: BLE001 — report, don't crash the sweep
            entry["error"] = str(e)
        results.append(entry)
    return results


def main() -> int:
    from ..config import get_settings
    from .connection import get_connection

    settings = get_settings()
    conn = get_connection(settings, read_only=True)
    print(f"Contract check against {settings.odoo_mode} instance\n")
    results = check_contract(conn)
    failed = 0
    for r in results:
        mark = "ok " if r["ok"] else "FAIL"
        detail = r["error"] or (f"missing: {', '.join(r['missing'])}" if r["missing"] else "")
        print(f"  [{mark}] {r['model']:<22} count={r['count']}  {detail}")
        failed += 0 if r["ok"] else 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
