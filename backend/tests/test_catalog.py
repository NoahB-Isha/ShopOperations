from __future__ import annotations

from app.models import Role

from .util import login, mk_product, mk_user, set_flag


def _setup(client, db, n: int = 60):
    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    mk_user(db, "floor@t.l", (Role.SHOPPE_FLOOR, None, None))
    for i in range(n):
        cat = "Copper" if i % 2 == 0 else "Incense & Dhoop"
        mk_product(db, f"CA{i:010d}", f"Test Product {i:03d}", category=cat, odoo_id=1000 + i)
    return login(client, "admin@t.l"), login(client, "floor@t.l")


def test_search_and_pagination(client, db):
    admin, _ = _setup(client, db)
    r = client.get("/api/v1/products?search=Product 00&page_size=5", headers=admin)
    body = r.json()
    assert body["total"] == 10  # 000..009
    assert len(body["items"]) == 5
    r = client.get("/api/v1/products?search=CA0000000042", headers=admin)
    assert r.json()["total"] == 1

    r = client.get("/api/v1/products?category=Copper&page_size=200", headers=admin)
    assert r.json()["total"] == 30

    r = client.get("/api/v1/products/facets", headers=admin)
    assert "Copper" in r.json()["categories"]


def test_search_is_separator_and_order_insensitive(client, db):
    admin, _ = _setup(client, db, n=1)
    mk_product(db, "YM0000000001", "Yoga-Mat-Cotton-Brown", category="Home & Living", odoo_id=2001)

    for q in ("Yoga mat", "yoga-mat", "mat yoga", "brown cotton yoga", "Yoga-Mat-Cotton-Brown"):
        r = client.get("/api/v1/products", params={"search": q}, headers=admin)
        names = [i["name"] for i in r.json()["items"]]
        assert names == ["Yoga-Mat-Cotton-Brown"], f"search {q!r} -> {names}"

    # tokens must all land in ONE field — name+SKU mixing would make short
    # numeric tokens match nearly everything
    r = client.get("/api/v1/products", params={"search": "yoga 0000000001"}, headers=admin)
    assert r.json()["total"] == 0

    # punctuation-only queries filter nothing
    r = client.get("/api/v1/products", params={"search": "--"}, headers=admin)
    assert r.json()["total"] == 2


def test_matches_search_helper():
    from app.catalog.search import matches_search

    assert matches_search("yoga mat", "Yoga-Mat-Cotton-Brown", "YM01")
    assert matches_search("", "anything")
    assert matches_search(None, "anything")
    assert not matches_search("yoga 8901", "Yoga Mat", "8901234")  # no cross-field mixing
    assert matches_search("YOGA", None, "yoga-mat")  # None fields skipped
    assert not matches_search("yoga", None, "")


def test_requires_auth(client, db):
    assert client.get("/api/v1/products").status_code == 401


def test_tag_editing_rules(client, db):
    admin, floor = _setup(client, db, n=1)
    pid = client.get("/api/v1/products?search=Test Product 000", headers=admin).json()["items"][0]["id"]

    # non-admin cannot edit tags
    r = client.put(f"/api/v1/products/{pid}/tags", json={"tags": [{"tag": "gold"}]}, headers=floor)
    assert r.status_code == 403

    # expires requires a date
    r = client.put(f"/api/v1/products/{pid}/tags", json={"tags": [{"tag": "expires"}]}, headers=admin)
    assert r.status_code == 422

    # air+sea conflict rejected
    r = client.put(
        f"/api/v1/products/{pid}/tags",
        json={"tags": [{"tag": "air_only"}, {"tag": "sea_only"}]},
        headers=admin,
    )
    assert r.status_code == 422

    # valid set persists
    r = client.put(
        f"/api/v1/products/{pid}/tags",
        json={"tags": [{"tag": "gold"}, {"tag": "air_only"}, {"tag": "expires", "expires_on": "2027-03-01"}]},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    tags = {t["tag"]: t for t in r.json()["tags"]}
    assert set(tags) == {"gold", "air_only", "expires"}
    assert tags["expires"]["expires_on"] == "2027-03-01"

    # filter by tag
    r = client.get("/api/v1/products?tag=gold", headers=admin)
    assert r.json()["total"] == 1


def test_case_size_editable_but_synced_fields_locked(client, db):
    admin, _ = _setup(client, db, n=1)
    pid = client.get("/api/v1/products", headers=admin).json()["items"][0]["id"]
    r = client.patch(f"/api/v1/products/{pid}", json={"case_size": 24}, headers=admin)
    assert r.status_code == 200 and r.json()["case_size"] == 24
    r = client.patch(f"/api/v1/products/{pid}", json={"name": "Renamed"}, headers=admin)
    assert r.status_code == 422
    assert "Odoo" in r.json()["detail"]


def test_manual_item_lifecycle(client, db):
    admin, _ = _setup(client, db, n=1)
    r = client.post(
        "/api/v1/products",
        json={"name": "Spring Water — 24-Pack", "retail_price": 6.5},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source"] == "manual"
    assert body["is_stock_tracked"] is False
    assert body["dept_orderable"] is True
    assert body["global_sku"].startswith("MAN-")

    # manual items may be renamed
    r = client.patch(f"/api/v1/products/{body['id']}", json={"name": "Water 24pk"}, headers=admin)
    assert r.status_code == 200 and r.json()["name"] == "Water 24pk"


# ------------------------------------------------------------ stock history
def test_stock_history_series(client, db):
    """Covered days become points (absent rows = genuine zero), the last
    point is live StockLevel, reconstructed days are counted honestly."""
    from datetime import timedelta

    from app.models import StockLevel, StockSnapshot, StockSnapshotDay, utcnow

    admin, floor = _setup(client, db, n=1)
    pid = client.get("/api/v1/products", headers=admin).json()["items"][0]["id"]
    today = utcnow().date()
    d20, d2 = today - timedelta(days=20), today - timedelta(days=2)
    db.add_all(
        [
            StockSnapshotDay(snapshot_date=d20, rows=2, source="reconstructed"),
            StockSnapshotDay(snapshot_date=d2, rows=2),
            StockSnapshot(snapshot_date=d20, product_id=pid, location_key="bwhse", qty=5),
            StockSnapshot(snapshot_date=d20, product_id=pid, location_key="floor", qty=2),
            # d2 has NO rows for this product: covered day ⇒ genuinely zero
            StockLevel(product_id=pid, location_key="bwhse", qty=4),
            StockLevel(product_id=pid, location_key="staging2", qty=1),
        ]
    )
    db.commit()

    r = client.get(f"/api/v1/products/{pid}/stock-history", headers=floor)
    assert r.status_code == 200
    body = r.json()
    assert body["covered_days"] == 2
    assert body["reconstructed_days"] == 1
    assert body["first_covered"] == d20.isoformat()
    days = [(p["day"], p["total"], p["source"]) for p in body["points"]]
    assert days == [
        (d20.isoformat(), 7.0, "reconstructed"),
        (d2.isoformat(), 0.0, "sync"),
        (today.isoformat(), 5.0, "live"),
    ]
    live = body["points"][-1]
    assert live["bwhse"] == 4 and live["staging2"] == 1

    # the window honors ?days=
    r = client.get(f"/api/v1/products/{pid}/stock-history?days=14", headers=admin)
    body = r.json()
    assert [p["source"] for p in body["points"]] == ["sync", "live"]
    assert body["covered_days"] == 1


def test_stock_history_untracked_and_missing(client, db):
    from app.models import ProductSource

    admin, _ = _setup(client, db, n=1)
    manual = mk_product(
        db, "MAN-0009", "Spring Water", source=ProductSource.MANUAL.value, stock_tracked=False
    )
    r = client.get(f"/api/v1/products/{manual.id}/stock-history", headers=admin)
    assert r.status_code == 200
    assert r.json()["points"] == [] and r.json()["covered_days"] == 0

    assert client.get("/api/v1/products/99999/stock-history", headers=admin).status_code == 404


def test_all_skus_filters(client, db):
    """Price, barcode family, units-sold window, and the default 'hide old
    SKUs' — the register is the honest test of what the shop still sells."""
    from datetime import timedelta

    from app.models import Role, SalesDaily, utcnow

    live = mk_product(db, "CX-LIVE", "Devi Cord", price=25)
    live.barcode = "CX507"
    dear = mk_product(db, "JW-DEAR", "Copper Bracelet", price=400)
    dear.barcode = "JW109"
    retired = mk_product(db, "CX-OLD", "Retired Thing", price=25)
    retired.barcode = "CX999"
    retired.available_in_pos = False  # not on the register any more
    today = utcnow().date()
    db.add(SalesDaily(product_id=live.id, day=today - timedelta(days=3), channel="shoppe", units=9))
    db.commit()

    mk_user(db, "admin@t.io", (Role.ADMIN, None, None))
    h = login(client, "admin@t.io")

    def skus(**params):
        r = client.get("/api/v1/products", params=params, headers=h)
        assert r.status_code == 200, r.text
        return {i["global_sku"] for i in r.json()["items"]}

    # default hides the retired SKU without being asked
    assert skus() == {"CX-LIVE", "JW-DEAR"}
    assert "CX-OLD" in skus(in_pos_only=False)

    # price window
    assert skus(price_max=100) == {"CX-LIVE"}
    assert skus(price_min=100) == {"JW-DEAR"}

    # barcode family, case-insensitively
    assert skus(barcode_prefix="jw") == {"JW-DEAR"}
    assert skus(barcode_prefix="CX") == {"CX-LIVE"}  # CX-OLD still hidden by default

    # units sold in a window
    assert skus(sold_days=7) == {"CX-LIVE"}
    assert skus(sold_days=7, sold_min=50) == set()  # sold 9, wanted 50
    assert skus(sold_days=1) == set()  # sale was 3 days ago

    # the prefix list is derived from real barcodes, not hardcoded
    facets = client.get("/api/v1/products/facets", headers=h).json()
    assert "barcode_prefixes" in facets


def test_hide_oos_keeps_only_products_with_stock_somewhere(client, db):
    """'Hide OOS' means no quantity in ANY location — a product Odoo has
    vacuumed to no rows at all counts as out, not as unknown."""
    from app.models import Product, StockLevel

    stocked = Product(global_sku="OOS-IN", name="Has stock in staging", is_active=True,
                      available_in_pos=True, source="odoo")
    empty = Product(global_sku="OOS-ZERO", name="Zero everywhere", is_active=True,
                    available_in_pos=True, source="odoo")
    missing = Product(global_sku="OOS-NOROW", name="No stock rows at all", is_active=True,
                      available_in_pos=True, source="odoo")
    db.add_all([stocked, empty, missing])
    db.flush()
    db.add_all([
        StockLevel(product_id=stocked.id, location_key="bwhse", qty=0),
        StockLevel(product_id=stocked.id, location_key="staging", qty=3),
        StockLevel(product_id=empty.id, location_key="bwhse", qty=0),
        StockLevel(product_id=empty.id, location_key="floor", qty=0),
    ])
    db.commit()

    mk_user(db, "stock@test.io", (Role.ADMIN, None, None))
    headers = login(client, "stock@test.io")
    r = client.get("/api/v1/products", params={"search": "OOS-", "in_stock_only": True},
                   headers=headers)
    assert r.status_code == 200, r.text
    skus = {i["global_sku"] for i in r.json()["items"]}
    assert skus == {"OOS-IN"}

    r = client.get("/api/v1/products", params={"search": "OOS-"}, headers=headers)
    assert {"OOS-IN", "OOS-ZERO", "OOS-NOROW"} <= {i["global_sku"] for i in r.json()["items"]}


def test_barcode_lookup_is_exact_and_handles_the_upc_leading_zero(client, db):
    """A scan is an identity claim: exact match or nothing.

    The leading-zero case is the one that bites in the aisle — a 12-digit UPC-A
    label comes off the camera as a 13-digit EAN-13 with a zero in front, and
    Odoo may hold either form.
    """
    from app.models import Product

    mk_user(db, "scan@t.l", (Role.SHOPPE_FLOOR, None, None))
    h = login(client, "scan@t.l")

    upc = Product(global_sku="SCAN-UPC", name="Copper Bottle", barcode="012345678905",
                  is_active=True, source="odoo")
    hidden = Product(global_sku="SCAN-HIDDEN", name="Blacklisted but real", barcode="7350053850019",
                     is_active=True, blacklisted=True, source="odoo")
    db.add_all([upc, hidden])
    db.commit()

    # the padded EAN-13 form the camera reports finds the 12-digit record
    r = client.get("/api/v1/products/by-barcode/0012345678905", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["global_sku"] == "SCAN-UPC"
    # ...and the stored form still works unchanged
    assert client.get("/api/v1/products/by-barcode/012345678905",
                      headers=h).json()["global_sku"] == "SCAN-UPC"

    # holding the item beats hiding it — a blacklisted product still answers
    assert client.get("/api/v1/products/by-barcode/7350053850019",
                      headers=h).json()["global_sku"] == "SCAN-HIDDEN"

    # a shelf label carrying the internal reference in Code 128
    assert client.get("/api/v1/products/by-barcode/SCAN-UPC",
                      headers=h).json()["global_sku"] == "SCAN-UPC"

    # near-misses are misses, never a guess
    assert client.get("/api/v1/products/by-barcode/1234567890", headers=h).status_code == 404
    assert client.get("/api/v1/products/by-barcode/SCAN", headers=h).status_code == 404


# ------------------------------------------- every location an item sits in
def test_locations_lists_every_bin_and_rolls_them_up(client, db, live_env, monkeypatch):
    """The warehouse has to FIND the thing, and the stock sync deliberately
    collapses hundreds of BWHSE bins into one number. This reads quants live
    and says which synced area each bin belongs to."""
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr(
        "app.catalog.router.get_connection", lambda settings, read_only=True: sim
    )
    mk_user(db, "wh2@test.io", (Role.WAREHOUSE, None, None))
    wh = login(client, "wh2@test.io")

    from app.models import Product
    from sqlalchemy import select as sa_select

    # odoo product 201 is the one the test fixtures give a VIRTUAL
    # customer-location quant to (3,255 units), so this test always has one to
    # ignore — "the first product with an odoo id" made the assertion vacuous
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    assert product is not None, "fixture product 201 should exist"
    r = client.get(f"/api/v1/products/{product.id}/locations", headers=wh)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "live"
    # every row names a real Odoo path and rolls into one of the synced areas
    for row in body["locations"]:
        assert row["location"]
        assert row["qty"] != 0
    assert body["total"] == sum(row["qty"] for row in body["locations"])
    assert set(body["buckets"]) <= {"bwhse", "floor", "staging", "staging2"}
    # Odoo keeps quants on VIRTUAL locations too — on live, Partner
    # Locations/Customers held 3,255 units of one incense, which is stock
    # already sold, not a place anything sits. Those must never be listed.
    assert not [r for r in body["locations"] if "Partner" in r["location"]]


def test_locations_are_honest_when_odoo_is_silent(client, db, live_env, monkeypatch):
    from app.odoo.errors import OdooError
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")

    def dead(settings, read_only=True):
        raise OdooError("connection refused")

    monkeypatch.setattr("app.catalog.router.get_connection", dead)
    mk_user(db, "wh3@test.io", (Role.WAREHOUSE, None, None))
    wh = login(client, "wh3@test.io")

    from app.models import Product
    from sqlalchemy import select as sa_select

    product = db.scalars(
        sa_select(Product).where(Product.odoo_product_id.is_not(None))
    ).first()
    body = client.get(f"/api/v1/products/{product.id}/locations", headers=wh).json()
    assert body["source"] == "unavailable"
    assert "didn't answer" in body["note"]
    assert body["locations"] == []  # never pretend the item is nowhere
    assert body["buckets"]  # the last sync's totals still come back


# ------------------------------------------------- editing the floor count
def test_floor_count_renders_a_draft_adjustment_and_never_validates(
    client, db, live_env, monkeypatch
):
    """The Inventory Flow Manager counted the shelf. Odoo gets a DRAFT."""
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim
    )
    set_flag(db, "write_create_inventory_addition", True)
    set_flag(db, "write_create_inventory_reduction", True)
    mk_user(db, "flr@test.io", (Role.SHOPPE_FLOOR, None, None))
    floor = login(client, "flr@test.io")

    from app.models import Product, StockLevel
    from sqlalchemy import select as sa_select

    product = db.scalars(
        sa_select(Product).where(Product.odoo_product_id.is_not(None))
    ).first()
    # the stock sync may already have a floor row for this product — set it,
    # don't insert a second one
    row = db.scalars(
        sa_select(StockLevel).where(
            StockLevel.product_id == product.id, StockLevel.location_key == "floor"
        )
    ).first()
    if row is None:
        row = StockLevel(product_id=product.id, location_key="floor", qty=0)
        db.add(row)
    row.qty = 4
    db.commit()

    # counted MORE than Odoo -> an addition draft
    r = client.post(
        f"/api/v1/products/{product.id}/floor-count",
        json={"counted_qty": 9, "note": "counted the shelf"},
        headers=floor,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["floor_qty_before"] == 4 and body["delta"] == 5
    assert body["direction"] == "add" and body["status"] == "created"
    assert body["picking_name"] and body["url"]
    # it is a DRAFT: the app never validates a stock move
    state = sim.search_read(
        "stock.picking", [["name", "=", body["picking_name"]]], ["state"]
    )
    assert state and state[0]["state"] == "draft"

    # counted the same -> nothing written
    r2 = client.post(
        f"/api/v1/products/{product.id}/floor-count", json={"counted_qty": 4}, headers=floor
    )
    assert r2.json()["status"] == "none" and r2.json()["direction"] == "none"

    # a wild number is refused rather than adjusted
    r3 = client.post(
        f"/api/v1/products/{product.id}/floor-count", json={"counted_qty": 999_999}, headers=floor
    )
    assert r3.status_code == 422 and "too large" in r3.json()["detail"]


def test_floor_count_is_not_for_untracked_items_or_other_roles(client, db, settings_env):
    water = mk_product(db, "MAN-WATER-2", "Spring Water", source="manual", stock_tracked=False)
    mk_user(db, "flr2@test.io", (Role.SHOPPE_FLOOR, None, None))
    mk_user(db, "wh4@test.io", (Role.WAREHOUSE, None, None))
    floor = login(client, "flr2@test.io")
    wh = login(client, "wh4@test.io")

    r = client.post(
        f"/api/v1/products/{water.id}/floor-count", json={"counted_qty": 3}, headers=floor
    )
    assert r.status_code == 422 and "isn't tracked in Odoo" in r.json()["detail"]

    tracked = mk_product(db, "IN9999", "Incense-Stick-Test", odoo_id=9911)
    # the warehouse works in Odoo; the floor owns the floor count
    assert (
        client.post(
            f"/api/v1/products/{tracked.id}/floor-count", json={"counted_qty": 3}, headers=wh
        ).status_code
        == 403
    )
