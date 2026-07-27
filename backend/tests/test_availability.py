"""Org-wide OOS + Coming Soon lists and the skubot API surface."""
from __future__ import annotations

from datetime import date, timedelta

from app.config import get_settings
from app.models import (
    IncomingMove,
    Role,
    StockLevel,
    StockSnapshot,
    StockSnapshotDay,
)

from .util import login, mk_product, mk_user

TODAY = date.today()


def _setup(db):
    gone = mk_product(db, "CA0000000001", "Copper Bottle", odoo_id=301)  # OOS everywhere
    back_only = mk_product(db, "RU0000000002", "Rudraksha Mala", odoo_id=302)  # bwhse zero, floor ok
    healthy = mk_product(db, "IN0000000003", "Sandalwood Incense", odoo_id=303)
    meals = mk_product(db, "SN0000000004", "Campus Meal", odoo_id=304)
    meals.restock_exclude = True  # non-retail POS item: never on the lists
    incoming_two = mk_product(db, "AY0000000005", "Neem Powder", odoo_id=305)
    db.add_all(
        [
            StockLevel(product_id=gone.id, location_key="bwhse", qty=0),
            StockLevel(product_id=back_only.id, location_key="bwhse", qty=0),
            StockLevel(product_id=back_only.id, location_key="floor", qty=5),
            StockLevel(product_id=healthy.id, location_key="bwhse", qty=50),
            StockLevel(product_id=incoming_two.id, location_key="floor", qty=2),
            IncomingMove(
                odoo_move_id=1, product_id=gone.id, qty=24,
                expected_date=TODAY + timedelta(days=40), state="assigned",
            ),
            IncomingMove(
                odoo_move_id=2, product_id=incoming_two.id, qty=48,
                expected_date=TODAY + timedelta(days=10), state="confirmed",
            ),
            IncomingMove(
                odoo_move_id=3, product_id=incoming_two.id, qty=24,
                expected_date=TODAY + timedelta(days=100), state="waiting",
            ),
            IncomingMove(  # done moves never count
                odoo_move_id=4, product_id=healthy.id, qty=99,
                expected_date=TODAY + timedelta(days=5), state="done",
            ),
            # history: `gone` last had stock 10 days ago → "OOS since"
            StockSnapshotDay(snapshot_date=TODAY - timedelta(days=10), rows=1),
            StockSnapshot(
                snapshot_date=TODAY - timedelta(days=10),
                product_id=gone.id, location_key="bwhse", qty=12,
            ),
        ]
    )
    db.commit()
    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))
    mk_user(db, "rotating@test.io", (Role.FLOOR_ROTATING, None, None))
    mk_user(db, "warehouse@test.io", (Role.WAREHOUSE, None, None))
    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, None))
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    return gone, back_only, healthy, meals, incoming_two


# ------------------------------------------------------------------- lists
def test_oos_org_scope_and_history(client, db):
    gone, back_only, healthy, meals, _ = _setup(db)
    floor = login(client, "floor@test.io")
    r = client.get("/api/v1/availability/oos", headers=floor)
    assert r.status_code == 200, r.text
    items = {i["sku"]: i for i in r.json()}
    assert set(items) == {"CA0000000001"}  # only truly-out products
    row = items["CA0000000001"]
    assert row["incoming_qty"] == 24
    assert "expected back" in row["incoming_label"]
    assert row["last_in_stock_on"] == (TODAY - timedelta(days=10)).isoformat()


def test_oos_bwhse_scope_includes_floor_covered(client, db):
    _setup(db)
    wh = login(client, "warehouse@test.io")
    # by default only items the snapshots have SEEN stocked (in scope) show —
    # the mala and neem have no bwhse history, so they wait behind the switch
    r = client.get("/api/v1/availability/oos", params={"scope": "bwhse"}, headers=wh)
    assert {i["sku"] for i in r.json()} == {"CA0000000001"}
    # the peek switch restores them: floor stock doesn't hide a bwhse-out —
    # the mala (floor 5) and the neem (floor 2) have NOTHING at the warehouse
    r = client.get(
        "/api/v1/availability/oos",
        params={"scope": "bwhse", "include_never_stocked": True},
        headers=wh,
    )
    skus = {i["sku"] for i in r.json()}
    assert skus == {"CA0000000001", "RU0000000002", "AY0000000005"}
    assert client.get(
        "/api/v1/availability/oos", params={"scope": "nope"}, headers=wh
    ).status_code == 422


def test_oos_hides_never_stocked_by_default(client, db):
    """Noah's 2026-07-27 call: items with no stock history didn't 'go out of
    stock' — they clutter the list (fast movers, digital goods, uncarried
    variants). Hidden by default, one switch to peek."""
    _setup(db)
    ghost = mk_product(db, "HO0000000009", "Brass Lamp (never carried)", odoo_id=309)
    db.add(StockLevel(product_id=ghost.id, location_key="bwhse", qty=0))
    db.commit()
    floor = login(client, "floor@test.io")

    skus = {i["sku"] for i in client.get("/api/v1/availability/oos", headers=floor).json()}
    assert skus == {"CA0000000001"}  # the ghost stays hidden

    r = client.get(
        "/api/v1/availability/oos", params={"include_never_stocked": True}, headers=floor
    )
    items = {i["sku"]: i for i in r.json()}
    assert set(items) == {"CA0000000001", "HO0000000009"}
    assert items["HO0000000009"]["last_in_stock_on"] is None


def test_coming_soon_aggregates_and_windows(client, db):
    _setup(db)
    floor = login(client, "floor@test.io")
    r = client.get("/api/v1/availability/coming-soon", headers=floor)
    items = {i["sku"]: i for i in r.json()}
    assert set(items) == {"CA0000000001", "AY0000000005"}
    neem = items["AY0000000005"]
    assert neem["incoming_qty"] == 72  # both pending moves summed
    assert neem["incoming_expected"] == (TODAY + timedelta(days=10)).isoformat()  # soonest
    assert neem["low_count_caveat"] is True  # 2 on the floor — verify physically
    # soonest-first ordering
    assert [i["sku"] for i in r.json()] == ["AY0000000005", "CA0000000001"]
    # 30-day window keeps only the near arrival
    r = client.get(
        "/api/v1/availability/coming-soon", params={"within_days": 30}, headers=floor
    )
    assert [i["sku"] for i in r.json()] == ["AY0000000005"]


def test_availability_needs_ops_role(client, db):
    _setup(db)
    orderer = login(client, "orderer@test.io")
    assert client.get("/api/v1/availability/oos", headers=orderer).status_code == 403
    # rotating floor volunteers are ops: the merged OOS page's scopes work
    rotating = login(client, "rotating@test.io")
    assert client.get("/api/v1/availability/oos", headers=rotating).status_code == 200


def test_blacklisted_products_leave_availability(client, db):
    gone, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    assert {i["sku"] for i in client.get("/api/v1/availability/oos", headers=floor).json()} == {
        "CA0000000001"
    }
    gone.blacklisted = True
    db.commit()
    assert client.get("/api/v1/availability/oos", headers=floor).json() == []
    coming = {i["sku"] for i in client.get("/api/v1/availability/coming-soon", headers=floor).json()}
    assert "CA0000000001" not in coming  # its inbound shipment is hidden too


# ------------------------------------------------------------------ bot API
def test_bot_endpoints_key_auth_and_contract(client, db, monkeypatch):
    _setup(db)
    # unconfigured → 503 with a pointer to the env var
    r = client.get("/api/v1/bot/oos")
    assert r.status_code == 503
    monkeypatch.setenv("SKUBOT_API_KEY", "sekrit-bot-key")
    get_settings.cache_clear()
    try:
        assert client.get("/api/v1/bot/oos").status_code == 401
        assert client.get(
            "/api/v1/bot/oos", headers={"X-API-Key": "wrong"}
        ).status_code == 401
        r = client.get("/api/v1/bot/oos", headers={"X-API-Key": "sekrit-bot-key"})
        assert r.status_code == 200, r.text
        payload = r.json()
        # the JSON contract skubot codes against
        assert set(payload) == {"generated_at", "snapshot_freshness", "count", "items"}
        assert payload["count"] == 1
        item = payload["items"][0]
        assert {
            "product_id", "sku", "barcode", "name", "category",
            "bwhse_qty", "floor_qty", "staging_qty", "total_qty",
            "incoming_qty", "incoming_expected", "incoming_label",
            "last_in_stock_on", "low_count_caveat",
        } == set(item)
        assert item["sku"] == "CA0000000001"

        r = client.get(
            "/api/v1/bot/coming-soon",
            params={"within_days": 30},
            headers={"X-API-Key": "sekrit-bot-key"},
        )
        assert r.status_code == 200
        assert [i["sku"] for i in r.json()["items"]] == ["AY0000000005"]
        assert client.get(
            "/api/v1/bot/health", headers={"X-API-Key": "sekrit-bot-key"}
        ).json()["ok"] is True
    finally:
        monkeypatch.delenv("SKUBOT_API_KEY")
        get_settings.cache_clear()


