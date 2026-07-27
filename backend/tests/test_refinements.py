"""The pre-deploy refinement rounds: admin notices inbox, the app-wide
product blacklist (+ the bulk cleanup sweep), and the floor_rotating role
(floor minus creating transfer requests)."""
from __future__ import annotations

from datetime import date, timedelta

from app.models import Role, SalesMonthly, StockLevel, StockSnapshot

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


def test_blacklist_sweep_rules_and_exceptions(client, db):
    _users(db)
    today = date.today()
    # never stocked, odoo-sourced → rule A
    junk = mk_product(db, "FEE0000000001", "FBA Shipment Fee", odoo_id=411)
    # has snapshot history → kept
    stocked = mk_product(db, "CA0000000011", "Copper Bottle", odoo_id=412)
    db.add(
        StockSnapshot(
            snapshot_date=today - timedelta(days=5),
            product_id=stocked.id, location_key="bwhse", qty=12,
        )
    )
    # currently stocked (no history rows) → kept
    live = mk_product(db, "CA0000000012", "Copper Ring", odoo_id=413)
    db.add(StockLevel(product_id=live.id, location_key="floor", qty=3))
    # the explicit exception: no stock anywhere, still kept
    mk_product(db, "IL-Service", "IL Service", odoo_id=414)
    # manual items never sweep (rule A is odoo-only)
    mk_product(db, "MAN-CUPS", "Compostable Cups", source="manual", stock_tracked=False)
    # "-USA" duplicate → rule B even WITH stock
    usa = mk_product(db, "BC0007200006-USA", "Bergamot Soap - USA", odoo_id=415)
    db.add(StockLevel(product_id=usa.id, location_key="bwhse", qty=40))
    db.add(
        StockSnapshot(
            snapshot_date=today - timedelta(days=5),
            product_id=usa.id, location_key="bwhse", qty=40,
        )
    )
    # lowercase 'usa' inside a word is NOT the USA suffix pattern — kept
    # (but it has no stock, so rule A takes it: give it stock to isolate rule B)
    usable = mk_product(db, "CA0000000013", "Usable Yoga Mat", odoo_id=416)
    db.add(StockLevel(product_id=usable.id, location_key="bwhse", qty=9))
    # SELLS but never lands on a snapshot (fast mover / thin stock) — a real
    # trading item, never junk: the saree/kurta regression of 2026-07-26
    saree = mk_product(db, "CA0000000015", "Devi Saree Teal", odoo_id=417)
    db.add(SalesMonthly(product_id=saree.id, year=today.year, month=today.month,
                        channel="shoppe", units=2))
    db.commit()

    admin = login(client, "admin@test.io")
    floor = login(client, "floor@test.io")
    assert client.post(
        "/api/v1/products/blacklist/sweep", json={"apply": False}, headers=floor
    ).status_code == 403

    # preview: counts + sample, nothing changes
    r = client.post(
        "/api/v1/products/blacklist/sweep", json={"apply": False}, headers=admin
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["applied"] is False
    assert out["no_stock_history"] == 1  # the fee line only
    assert out["usa_items"] == 1
    assert out["total"] == 2
    assert "FBA Shipment Fee" in out["sample"] and "Bergamot Soap - USA" in out["sample"]
    db.refresh(junk)
    assert junk.blacklisted is False  # preview never writes

    # apply, then verify exactly the two junk items are hidden
    r = client.post(
        "/api/v1/products/blacklist/sweep", json={"apply": True}, headers=admin
    )
    assert r.json()["applied"] is True and r.json()["total"] == 2
    visible = {
        p["global_sku"]
        for p in client.get("/api/v1/products", headers=admin).json()["items"]
    }
    assert "FEE0000000001" not in visible and "BC0007200006-USA" not in visible
    assert {
        "CA0000000011",  # snapshot history
        "CA0000000012",  # stocked now
        "IL-Service",  # explicit exception
        "CA0000000013",  # lowercase 'usa' is not USA
        "CA0000000015",  # sells without ever snapshotting — real item
    } <= visible

    # re-running finds nothing new — the sweep is idempotent
    r = client.post(
        "/api/v1/products/blacklist/sweep", json={"apply": True}, headers=admin
    )
    assert r.json()["total"] == 0 and r.json()["applied"] is False


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
