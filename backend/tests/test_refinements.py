"""The pre-deploy refinement round: admin notices inbox, the app-wide
product blacklist, and the floor_rotating role (floor minus creating
transfer requests)."""
from __future__ import annotations

from app.models import Role, StockLevel

from .util import login, mk_product, mk_user


def _users(db):
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))
    mk_user(db, "rotating@test.io", (Role.FLOOR_ROTATING, None, None))
    mk_user(db, "warehouse@test.io", (Role.WAREHOUSE, None, None))


# ------------------------------------------------------------------ notices
def test_notices_inbox_lifecycle(client, db):
    _users(db)
    admin = login(client, "admin@test.io")
    floor = login(client, "floor@test.io")

    # only admins can post
    r = client.post("/api/v1/notices", json={"title": "nope"}, headers=floor)
    assert r.status_code == 403
    r = client.post(
        "/api/v1/notices",
        json={"title": "Holiday hours", "body": "Shoppe closes early Friday."},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    notice_id = r.json()["id"]
    assert r.json()["read"] is True  # the author has read their own notice

    # everyone sees it; it's unread for them until they open the inbox
    inbox = client.get("/api/v1/notices", headers=floor).json()
    assert inbox["unread"] == 1
    assert inbox["items"][0]["title"] == "Holiday hours"
    assert inbox["items"][0]["read"] is False
    assert inbox["items"][0]["author"] == "admin"

    inbox = client.post("/api/v1/notices/read", headers=floor).json()
    assert inbox["unread"] == 0
    assert inbox["items"][0]["read"] is True
    # read state is per user
    assert client.get("/api/v1/notices", headers=login(client, "rotating@test.io")).json()[
        "unread"
    ] == 1

    # admin can delete (read rows included)
    assert client.delete(f"/api/v1/notices/{notice_id}", headers=floor).status_code == 403
    assert client.delete(f"/api/v1/notices/{notice_id}", headers=admin).status_code == 204
    assert client.get("/api/v1/notices", headers=floor).json()["items"] == []


# ---------------------------------------------------------------- blacklist
def test_blacklist_hides_from_catalog_and_board(client, db):
    _users(db)
    keep = mk_product(db, "CA0000000001", "Copper Bottle", odoo_id=401)
    hide = mk_product(db, "CA0000000002", "Discontinued Lamp", odoo_id=402)
    db.add_all(
        [
            StockLevel(product_id=keep.id, location_key="floor", qty=0),
            StockLevel(product_id=hide.id, location_key="floor", qty=0),
        ]
    )
    db.commit()
    admin = login(client, "admin@test.io")
    floor = login(client, "floor@test.io")

    # only admins may toggle the flag
    r = client.patch(
        f"/api/v1/products/{hide.id}", json={"blacklisted": True}, headers=floor
    )
    assert r.status_code == 403
    r = client.patch(
        f"/api/v1/products/{hide.id}", json={"blacklisted": True}, headers=admin
    )
    assert r.status_code == 200 and r.json()["blacklisted"] is True

    # default catalog hides it; the manager view lists only blacklisted
    skus = {p["global_sku"] for p in client.get("/api/v1/products", headers=floor).json()["items"]}
    assert "CA0000000002" not in skus and "CA0000000001" in skus
    manager = client.get(
        "/api/v1/products", params={"blacklisted": True}, headers=admin
    ).json()["items"]
    assert [p["global_sku"] for p in manager] == ["CA0000000002"]

    # the floor OOS board (both products are floor zeros) skips it too
    board = {i["sku"] for i in client.get("/api/v1/oos", headers=floor).json()}
    assert board == {"CA0000000001"}

    # un-blacklist restores it everywhere
    client.patch(f"/api/v1/products/{hide.id}", json={"blacklisted": False}, headers=admin)
    board = {i["sku"] for i in client.get("/api/v1/oos", headers=floor).json()}
    assert board == {"CA0000000001", "CA0000000002"}


# ------------------------------------------------------------ floor_rotating
def test_floor_rotating_cannot_create_transfers(client, db, settings_env):
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    _users(db)
    p = mk_product(db, "CA0000000003", "Copper Tongue Cleaner", odoo_id=403)
    db.add(StockLevel(product_id=p.id, location_key="bwhse", qty=20))
    db.commit()
    # creating a request renders its Odoo draft immediately — the app needs
    # the location ids a stock sync discovers
    sim = OdooSimulator(settings_env.fixtures_path, read_only=True)
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")
    rotating = login(client, "rotating@test.io")
    floor = login(client, "floor@test.io")

    body = {"notes": "", "lines": [{"product_id": p.id, "qty": 2}]}
    # the regular floor role can create…
    r = client.post("/api/v1/transfer-requests", json=body, headers=floor)
    assert r.status_code == 201, r.text
    request_id = r.json()["id"]
    # …the rotating role cannot (create or edit lines), but participates
    assert client.post("/api/v1/transfer-requests", json=body, headers=rotating).status_code == 403
    assert client.put(
        f"/api/v1/transfer-requests/{request_id}/lines",
        json={"lines": [{"product_id": p.id, "qty": 5}]},
        headers=rotating,
    ).status_code == 403
    assert client.get("/api/v1/transfer-requests", headers=rotating).status_code == 200
    assert client.get(
        f"/api/v1/transfer-requests/{request_id}", headers=rotating
    ).status_code == 200

    # the rest of the floor toolkit works: restock list + OOS board actions
    assert client.get("/api/v1/restock", headers=rotating).status_code == 200
    r = client.post("/api/v1/oos", json={"product_id": p.id}, headers=rotating)
    assert r.status_code == 201, r.text
