"""Product sync: Odoo product.product -> the unified catalog.

Sync owns the synced columns of source='odoo' rows and never touches
app-managed fields (case_size, dept_orderable, tags) or manual products.

Sourcing classification: Odoo users declare a product's procurement origin
by tagging it "Domestic" or "India" (product tags — Sales tab on the product
form; tag names matched case-insensitively). The sync reads
`all_product_tag_ids` (template + variant tags united) and stores the
verdict in `products.sourcing`; domestic wins if both tags are present.
Renaming/removing the tag in Odoo reclassifies on the next sync.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import SOURCING_DOMESTIC, SOURCING_INDIA, Product, ProductSource, SyncState
from ..odoo.protocol import OdooConnection, parse_code, safe_fields

PRODUCT_FIELDS = [
    "default_code",
    "name",
    "categ_id",
    "standard_price",
    # `lst_price`, NOT `list_price`. On a variant, `list_price` is the
    # TEMPLATE's sales price, and this catalog prices sized goods through
    # attribute extras — Mens-Mangalgiri-Dhoti (CM233) carries list_price
    # -9.00 with a +35.00 price_extra per size, so the app showed -9 while the
    # register charged 26. `lst_price` = list_price + price_extra, which is
    # what the POS actually rings up (verified live 2026-08-14 on CM233,
    # CW219, CU514). For a product with no attributes the two are identical,
    # so this is a strict improvement, never a regression.
    "lst_price",
    "list_price",
    "barcode",
    "active",
    "all_product_tag_ids",
    "available_in_pos",
]

_PLACEHOLDER_CODES = {"", "---", "false", "none"}

SOURCING_TAG_NAMES = {
    "domestic": SOURCING_DOMESTIC,
    "india": SOURCING_INDIA,
}


def _sourcing_tag_ids(conn: OdooConnection) -> dict[int, str]:
    """product.tag id -> sourcing value, for tags named Domestic/India.
    Instances (or sparse fixture sets) without the model classify nothing."""
    try:
        records = conn.search_read("product.tag", [], ["name"])
    except Exception:
        return {}
    out: dict[int, str] = {}
    for rec in records:
        value = SOURCING_TAG_NAMES.get(str(rec.get("name") or "").strip().lower())
        if value:
            out[rec["id"]] = value
    return out


def _classify_sourcing(tag_ids: object, sourcing_tags: dict[int, str]) -> str:
    if not sourcing_tags or not isinstance(tag_ids, list):
        return ""
    values = {sourcing_tags.get(t) for t in tag_ids}
    if SOURCING_DOMESTIC in values:  # domestic wins a (mis)tagged conflict:
        return SOURCING_DOMESTIC  # better to leave it off the India order
    if SOURCING_INDIA in values:
        return SOURCING_INDIA
    return ""


def sync_products(
    db: Session, settings: Settings, conn: OdooConnection, state: SyncState
) -> int:
    fields = safe_fields(conn, "product.product", PRODUCT_FIELDS)
    records = conn.search_read("product.product", [["sale_ok", "=", True]], fields, order="id asc")
    sourcing_tags = _sourcing_tag_ids(conn)

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
        # lst_price first (the variant's real shelf price); list_price is the
        # fallback for instances or fixture sets that don't carry lst_price.
        price = rec.get("lst_price")
        if price in (None, False):
            price = rec.get("list_price")
        product.retail_price = price or 0
        product.is_active = bool(rec.get("active", True))
        # Missing field (older Odoo / safe_fields dropped it) reads as True:
        # better to show a SKU that shouldn't be than hide a live one.
        product.available_in_pos = bool(rec.get("available_in_pos", True))
        product.sourcing = _classify_sourcing(rec.get("all_product_tag_ids"), sourcing_tags)
        count += 1

    # Products that disappeared from Odoo (archived, deleted): deactivate, keep history.
    for odoo_id, product in by_odoo_id.items():
        if odoo_id not in seen_ids:
            product.is_active = False

    return count
