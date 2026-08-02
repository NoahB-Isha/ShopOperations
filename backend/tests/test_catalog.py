from __future__ import annotations

from app.models import Role

from .util import login, mk_product, mk_user


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
