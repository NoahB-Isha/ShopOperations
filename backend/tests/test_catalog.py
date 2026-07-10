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
