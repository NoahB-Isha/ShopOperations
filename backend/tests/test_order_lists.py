"""Order lists as CATALOGS: admin curates + grants to zones, coordinators
grant to their centers. No quantities, no approvals — those belong to
phase-3 orders."""
from __future__ import annotations

from app.models import Role

from .util import login, mk_center, mk_product, mk_user, mk_zone


def _setup(db):
    zone1 = mk_zone(db, "Zone 1 — Lili")
    zone2 = mk_zone(db, "Zone 2 — Mik")
    boston = mk_center(db, "Boston", zone_id=zone1.id)
    nyc = mk_center(db, "New York", zone_id=zone1.id)
    dallas = mk_center(db, "Dallas", zone_id=zone2.id)
    copper = mk_product(db, "CA0023000009", "Copper Water Bottle", odoo_id=201)
    incense = mk_product(db, "IN0000000777", "Sandalwood Incense", odoo_id=203)
    mala = mk_product(db, "RU0000000005", "Rudraksha Mala", odoo_id=202)
    db.commit()
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    mk_user(db, "lili@test.io", (Role.ZONE_COORDINATOR, zone1.id, None))
    mk_user(db, "mik@test.io", (Role.ZONE_COORDINATOR, zone2.id, None))
    return zone1, zone2, boston, nyc, dallas, copper, incense, mala


def _make_list(client, admin, name="Starter kit", product_ids=None) -> dict:
    r = client.post("/api/v1/order-lists", json={"name": name}, headers=admin)
    assert r.status_code == 201, r.text
    ol = r.json()
    if product_ids:
        r = client.put(
            f"/api/v1/order-lists/{ol['id']}/lines",
            json={"product_ids": product_ids},
            headers=admin,
        )
        assert r.status_code == 200, r.text
        ol = r.json()
    return ol


def test_admin_curates_catalogs_without_quantities(client, db, settings_env):
    _, _, _, _, _, copper, incense, mala = _setup(db)
    admin = login(client, "admin@test.io")

    ol = _make_list(client, admin, product_ids=[copper.id, incense.id])
    assert [line["sku"] for line in ol["lines"]] == ["CA0023000009", "IN0000000777"]
    assert "qty" not in ol["lines"][0]  # a menu, not an order

    # clone copies the products
    r = client.post(f"/api/v1/order-lists/{ol['id']}/clone", headers=admin)
    assert r.status_code == 201
    assert [line["sku"] for line in r.json()["lines"]] == ["CA0023000009", "IN0000000777"]

    # inactive products can't be ADDED (lists are "safe, currently active")
    mala.is_active = False
    db.commit()
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/lines",
        json={"product_ids": [copper.id, mala.id]},
        headers=admin,
    )
    assert r.status_code == 422 and "inactive" in r.json()["detail"]

    # …but items that go stale AFTER being listed are surfaced, not hidden
    incense.is_active = False
    db.commit()
    r = client.get(f"/api/v1/order-lists/{ol['id']}", headers=admin)
    assert r.json()["stale_line_count"] == 1

    # archive hides from the default listing
    r = client.patch(
        f"/api/v1/order-lists/{ol['id']}", json={"is_archived": True}, headers=admin
    )
    assert r.status_code == 200
    names = [x["name"] for x in client.get("/api/v1/order-lists", headers=admin).json()]
    assert ol["name"] not in names
    names = [
        x["name"]
        for x in client.get(
            "/api/v1/order-lists", params={"include_archived": True}, headers=admin
        ).json()
    ]
    assert ol["name"] in names


def test_zone_grants_scope_coordinator_visibility(client, db, settings_env):
    zone1, zone2, *_rest, copper, incense, mala = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")
    mik = login(client, "mik@test.io")

    ol = _make_list(client, admin, product_ids=[copper.id])

    # ungranted: neither coordinator sees it
    assert client.get("/api/v1/order-lists", headers=lili).json() == []
    assert client.get(f"/api/v1/order-lists/{ol['id']}", headers=lili).status_code == 403

    # grant to zone 1 only
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/zones", json={"zone_ids": [zone1.id]}, headers=admin
    )
    assert r.status_code == 200
    assert [z["zone_id"] for z in r.json()["zones"]] == [zone1.id]

    assert [x["id"] for x in client.get("/api/v1/order-lists", headers=lili).json()] == [ol["id"]]
    assert client.get("/api/v1/order-lists", headers=mik).json() == []
    assert client.get(f"/api/v1/order-lists/{ol['id']}", headers=mik).status_code == 403

    # coordinators can't touch admin verbs
    assert (
        client.put(
            f"/api/v1/order-lists/{ol['id']}/zones", json={"zone_ids": []}, headers=lili
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/v1/order-lists/{ol['id']}/lines",
            json={"product_ids": [copper.id]},
            headers=lili,
        ).status_code
        == 403
    )


def test_coordinator_grants_centers_within_their_zone(client, db, settings_env):
    zone1, zone2, boston, nyc, dallas, copper, *_ = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")
    mik = login(client, "mik@test.io")

    ol = _make_list(client, admin, product_ids=[copper.id])
    client.put(
        f"/api/v1/order-lists/{ol['id']}/zones",
        json={"zone_ids": [zone1.id, zone2.id]},
        headers=admin,
    )

    # Lili opens the list to her two centers
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/centers",
        json={"center_ids": [boston.id, nyc.id]},
        headers=lili,
    )
    assert r.status_code == 200, r.text
    assert {c["center_id"] for c in r.json()["centers"]} == {boston.id, nyc.id}

    # she can't grant Mik's center…
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/centers",
        json={"center_ids": [boston.id, dallas.id]},
        headers=lili,
    )
    assert r.status_code == 422

    # …and Mik granting HIS center leaves Lili's grants alone
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/centers",
        json={"center_ids": [dallas.id]},
        headers=mik,
    )
    assert r.status_code == 200
    assert {c["center_id"] for c in r.json()["centers"]} == {boston.id, nyc.id, dallas.id}

    # Lili narrowing to just Boston removes only NYC (zone-1 scope)
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/centers",
        json={"center_ids": [boston.id]},
        headers=lili,
    )
    assert {c["center_id"] for c in r.json()["centers"]} == {boston.id, dallas.id}

    # revoking zone 2 cascades Dallas's grant away
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/zones", json={"zone_ids": [zone1.id]}, headers=admin
    )
    assert {c["center_id"] for c in r.json()["centers"]} == {boston.id}
    # and Mik lost access entirely
    assert client.get(f"/api/v1/order-lists/{ol['id']}", headers=mik).status_code == 403


def test_orderers_have_no_list_management_access(client, db, settings_env):
    zone1, _, boston, *_rest, copper, _, _ = _setup(db)
    admin = login(client, "admin@test.io")
    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, boston.id))
    orderer = login(client, "orderer@test.io")

    ol = _make_list(client, admin, product_ids=[copper.id])
    assert client.get("/api/v1/order-lists", headers=orderer).status_code == 403
    assert (
        client.put(
            f"/api/v1/order-lists/{ol['id']}/centers",
            json={"center_ids": [boston.id]},
            headers=orderer,
        ).status_code
        == 403
    )
