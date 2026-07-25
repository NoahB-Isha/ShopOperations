"""Spreadsheet matching + the catalogs import endpoint + India product list
scoping + the domestic quick-order flow (the 2026-07-15 UX rework)."""

from __future__ import annotations

import io

import openpyxl
from app.catalog.matching import match_products, parse_table
from app.models import Role, SalesMonthly, utcnow

from .util import login, mk_product, mk_user


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed_catalog(db):
    lamp = mk_product(db, "HO0000500400", "Brass Guru Puja Lamp Large", category="CONX")
    dhoop = mk_product(db, "IN0001200500", "Sambrani Dhoop Sticks", category="INCENSE")
    soap = mk_product(db, "US-BC0001", "Lavender Soap", category="BODY CARE")
    lamp.barcode = "8901234567890"
    for i, product in enumerate((lamp, dhoop, soap)):
        if product.odoo_product_id is None:
            product.odoo_product_id = 9100 + i  # catalogs carry Odoo-tracked items only
    db.commit()
    return lamp, dhoop, soap


# ------------------------------------------------------------- matching unit
def test_match_by_sku_barcode_and_name_in_any_column(db):
    lamp, dhoop, soap = _seed_catalog(db)
    rows = parse_table(
        (
            b"Item,Qty,Notes\n"
            b"HO0000500400,25,from last time\n"          # sku
            b"8901234567890,4,\n"                         # barcode
            b"Sambrani Dhoop Sticks,100,urgent\n"        # exact name
            b"lavender soap,12,\n"                        # case-insensitive name
            b"Mystery Widget,3,\n"                        # no match
        ),
        "list.csv",
    )
    report = match_products(db, rows)
    assert report.had_header
    assert report.total_rows == 5
    matched = {h.product.global_sku: h.matched_by for h in report.hits}
    assert matched == {
        lamp.global_sku: "sku",
        dhoop.global_sku: "name",
        soap.global_sku: "name",
    }
    # barcode row deduped into the lamp (already matched by sku on row 1)
    assert len(report.unmatched) == 1
    assert "Mystery Widget" in report.unmatched[0][1]


def test_match_excel_without_headers_and_qty_noise(db):
    lamp, dhoop, _ = _seed_catalog(db)
    data = _xlsx([[dhoop.name, 40], ["HO0000500400", 2], [15, 20]])
    report = match_products(db, parse_table(data, "anything.xlsx"))
    assert not report.had_header
    assert {h.product.id for h in report.hits} == {lamp.id, dhoop.id}
    assert len(report.unmatched) == 1  # the all-numbers row


def test_ambiguous_name_stays_unmatched(db):
    mk_product(db, "A1", "Copper Water Bottle Small", category="COPPER")
    mk_product(db, "A2", "Copper Water Bottle Large", category="COPPER")
    report = match_products(db, [["Copper Water Bottle"]])
    assert report.hits == []
    assert len(report.unmatched) == 1


def test_token_subset_matches_partial_names_uniquely(db):
    soap = mk_product(db, "B1", "Lavender & Comfrey Soap 100g", category="BODY CARE")
    mk_product(db, "B2", "Lavender Shampoo", category="BODY CARE")
    report = match_products(db, [["comfrey soap", 12]])
    assert [h.product.id for h in report.hits] == [soap.id]
    # a subset that fits BOTH lavender products stays unmatched
    report = match_products(db, [["lavender"]])  # single token: too weak on purpose
    assert report.hits == []


# ------------------------------------------------------------ import endpoint
def test_import_creates_catalog_with_report(db, client):
    lamp, dhoop, _ = _seed_catalog(db)
    inactive = mk_product(db, "HO0000600400", "Retired Diya", category="CONX")
    inactive.is_active = False
    db.commit()
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    headers = login(client, "admin@test.io")

    csv_data = (
        "name,qty\n"
        f"{lamp.name},10\n"
        f"{dhoop.global_sku},5\n"
        "Retired Diya,2\n"
        "Totally Unknown Thing,9\n"
    ).encode()
    r = client.post(
        "/api/v1/order-lists/import",
        data={"name": "July popup kit"},
        files={"file": ("kit.csv", csv_data, "text/csv")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["matched"] == 2
    assert [ln["sku"] for ln in out["catalog"]["lines"]] == [lamp.global_sku, dhoop.global_sku]
    assert any("Retired Diya" in s for s in out["skipped"])
    assert any("Totally Unknown Thing" in s for s in out["unmatched_rows"])
    assert out["catalog"]["name"] == "July popup kit"


def test_import_with_nothing_usable_is_a_400(db, client):
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    headers = login(client, "admin@test.io")
    r = client.post(
        "/api/v1/order-lists/import",
        data={"name": "Empty"},
        files={"file": ("junk.csv", b"foo,bar\n1,2\n", "text/csv")},
        headers=headers,
    )
    assert r.status_code == 400


# --------------------------------------------------------- India product list
def _seed_sales(db, product, units=100.0, months=12):
    today = utcnow().date()
    total = today.year * 12 + (today.month - 1)
    for back in range(1, months + 1):
        ordinal = total - back
        db.add(
            SalesMonthly(
                product_id=product.id, year=ordinal // 12, month=ordinal % 12 + 1,
                channel="pos", units=units,
            )
        )
    db.commit()


def test_product_list_scopes_india_generation(db, client):
    lamp, dhoop, _ = _seed_catalog(db)
    _seed_sales(db, lamp)
    _seed_sales(db, dhoop)
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    headers = login(client, "admin@test.io")

    # no list yet
    assert client.get("/api/v1/ordering/product-list", headers=headers).json() is None

    r = client.put(
        "/api/v1/ordering/product-list",
        files={"file": ("july-products.csv", f"sku\n{lamp.global_sku}\n".encode(), "text/csv")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["filename"] == "july-products.csv"
    assert meta["matched"] == 1

    # generation is scoped to the list: dhoop stays out
    r = client.post("/api/v1/ordering/orders", json={"name": "Scoped"}, headers=headers)
    assert r.status_code == 201, r.text
    skus = {ln["global_sku"] for ln in r.json()["lines"]}
    assert skus == {lamp.global_sku}

    # the original file downloads byte-identical
    r = client.get("/api/v1/ordering/product-list/download", headers=headers)
    assert r.status_code == 200
    assert r.content == f"sku\n{lamp.global_sku}\n".encode()
    assert "july-products.csv" in r.headers["content-disposition"]

    # removing the list restores full-catalog generation
    assert client.delete("/api/v1/ordering/product-list", headers=headers).status_code == 204
    r = client.post("/api/v1/ordering/orders", json={"name": "Unscoped"}, headers=headers)
    skus = {ln["global_sku"] for ln in r.json()["lines"]}
    assert {lamp.global_sku, dhoop.global_sku} <= skus


# ------------------------------------------------------- domestic quick order
def test_domestic_send_in_one_step_with_requested_wording(db, client):
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    headers = login(client, "admin@test.io")
    r = client.post(
        "/api/v1/ordering/vendors",
        json={"name": "Botanie Soap", "kind": "us", "contact_name": "Caroline",
              "contact_email": "orders@botanie.test"},
        headers=headers,
    )
    vendor_id = r.json()["id"]
    soap = mk_product(db, "US-BC0002", "Comfrey Soap", category="BODY CARE")
    db.commit()

    # roster management: search-and-add (with MOQ), then list it back
    r = client.post(
        f"/api/v1/ordering/vendors/{vendor_id}/products",
        json={"product_id": soap.id, "moq": 540},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["global_sku"] == soap.global_sku
    assert r.json()[0]["moq"] == 540

    # one-step send: create + email immediately (dry-run while flag is off)
    r = client.post(
        f"/api/v1/ordering/vendors/{vendor_id}/orders",
        json={"quantities": {soap.global_sku: 540}, "send": True},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    detail = r.json()
    assert detail["order"]["status"] == "placed"
    order_id = detail["order"]["id"]

    timeline = client.get(
        f"/api/v1/ordering/orders/{order_id}/timeline", headers=headers
    ).json()
    outbound = [m for m in timeline["emails"] if m["direction"] == "out"]
    assert len(outbound) == 1
    body = outbound[0]["body"]
    assert body.startswith("Dear Caroline,")
    assert "We kindly request the following products:" in body
    assert "540 × Comfrey Soap" in body
    assert "Please reply to this email with an invoice." in body
    assert "sea" not in body.lower() and "air" not in body.lower()
    assert outbound[0]["recipients"].startswith("orders@botanie.test")

    # a product can't belong to two vendors
    r2 = client.post(
        "/api/v1/ordering/vendors",
        json={"name": "Other Vendor", "kind": "us"},
        headers=headers,
    )
    other_id = r2.json()["id"]
    r = client.post(
        f"/api/v1/ordering/vendors/{other_id}/products",
        json={"product_id": soap.id},
        headers=headers,
    )
    assert r.status_code == 409

    # remove from the roster
    r = client.delete(
        f"/api/v1/ordering/vendors/{vendor_id}/products/{soap.id}", headers=headers
    )
    assert r.status_code == 200
    assert r.json() == []
