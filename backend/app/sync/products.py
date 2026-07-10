"""Product sync: Odoo product.product -> the unified catalog.

Sync owns the synced columns of source='odoo' rows and never touches
app-managed fields (case_size, dept_orderable, tags) or manual products.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Product, ProductSource, SyncState
from ..odoo.protocol import OdooConnection, parse_code, safe_fields

PRODUCT_FIELDS = [
    "default_code",
    "name",
    "categ_id",
    "standard_price",
    "list_price",
    "barcode",
    "active",
]

_PLACEHOLDER_CODES = {"", "---", "false", "none"}


def sync_products(
    db: Session, settings: Settings, conn: OdooConnection, state: SyncState
) -> int:
    fields = safe_fields(conn, "product.product", PRODUCT_FIELDS)
    records = conn.search_read("product.product", [["sale_ok", "=", True]], fields, order="id asc")

    by_odoo_id = {
        p.odoo_product_id: p
        for p in db.scalars(select(Product).where(Product.source == ProductSource.ODOO.value))
    }
    seen_ids: set[int] = set()
    seen_skus: set[str] = set()
    count = 0
    for rec in records:
        code = parse_code(rec.get("default_code") or "")
        if code.lower() in _PLACEHOLDER_CODES:
            # No usable internal reference — track under a synthetic key so the
            # product is still visible rather than silently dropped.
            code = f"ODOO-{rec['id']}"
        if code in seen_skus:
            continue  # Odoo variants can share a default_code; first one wins
        seen_skus.add(code)
        seen_ids.add(rec["id"])

        categ = rec.get("categ_id")
        category = categ[1] if isinstance(categ, list) else ""
        product = by_odoo_id.get(rec["id"])
        if product is None:
            # A manual/legacy row with the same SKU would violate uniqueness;
            # adopt it instead of duplicating.
            adopted = db.scalar(select(Product).where(Product.global_sku == code))
            if adopted is not None:
                product = adopted
                product.odoo_product_id = rec["id"]
                product.source = ProductSource.ODOO.value
            else:
                product = Product(
                    global_sku=code,
                    us_sku=code,
                    odoo_product_id=rec["id"],
                    source=ProductSource.ODOO.value,
                    is_stock_tracked=True,
                )
                db.add(product)
        product.global_sku = code
        product.us_sku = product.us_sku or code
        product.odoo_internal_ref = rec.get("default_code") or ""
        product.barcode = rec.get("barcode") or ""
        product.name = rec.get("name") or ""
        product.category = category
        product.cost = rec.get("standard_price") or 0
        product.retail_price = rec.get("list_price") or 0
        product.is_active = bool(rec.get("active", True))
        count += 1

    # Products that disappeared from Odoo (archived, deleted): deactivate, keep history.
    for odoo_id, product in by_odoo_id.items():
        if odoo_id not in seen_ids:
            product.is_active = False

    return count
