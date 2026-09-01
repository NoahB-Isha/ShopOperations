"""Floor OOS list: computed zeros, floor-relevance, and role scoping.

Read-only since 2026-08-24 — marking and its adjustment drafts were removed;
counted numbers enter the app only through the counting page (test_counting).
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models import (
    IncomingMove,
    OdooLocation,
    Role,
    SalesDaily,
    StockLevel,
    StockSnapshot,
)

from .util import login, mk_product, mk_user


def _setup(db):
    phantom = mk_product(db, "CA0023000009", "Copper Water Bottle", odoo_id=201)
    zero = mk_product(db, "IN0000000777", "Sandalwood Incense", odoo_id=203)
    stocked = mk_product(db, "RU0000000555", "Rudraksha Mala", odoo_id=205)
    db.add_all([
        StockLevel(product_id=phantom.id, location_key="floor", qty=3),  # phantom stock
        StockLevel(product_id=phantom.id, location_key="bwhse", qty=40),
        StockLevel(product_id=zero.id, location_key="floor", qty=0),  # Odoo agrees: out
        StockLevel(product_id=stocked.id, location_key="floor", qty=9),  # healthy
        IncomingMove(odoo_move_id=1, product_id=zero.id, qty=24,
                     expected_date=date.today() + timedelta(days=40), state="assigned"),
        # location mapping (ids match the test fixtures) WITHOUT running the
        # stock sync — it would replace the quantities above
        OdooLocation(odoo_id=12, complete_name="III/Stock/BWHSE", key="bwhse"),
        OdooLocation(odoo_id=14, complete_name="III/Stock/III-FLOOR", key="floor"),
        OdooLocation(odoo_id=13, complete_name="III/Stock/III-FLOOR STAGING", key="staging"),
    ])
    db.commit()
    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))
    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, None))
    return phantom, zero, stocked


def test_list_shows_computed_zeros_with_incoming_label(client, db):
    phantom, zero, stocked = _setup(db)
    floor = login(client, "floor@test.io")
    r = client.get("/api/v1/oos", headers=floor)
    assert r.status_code == 200, r.text
    items = {i["sku"]: i for i in r.json()}
    assert "IN0000000777" in items  # floor qty 0 → on the board
    assert "RU0000000555" not in items  # healthy stock stays off
    assert "CA0023000009" not in items  # 3 on floor (phantom, but Odoo's number)
    assert "expected back" in items["IN0000000777"]["incoming_label"]

    # scoped: orderers can't see the floor board
    orderer = login(client, "orderer@test.io")
    assert client.get("/api/v1/oos", headers=orderer).status_code == 403


def test_board_catches_zeros_without_a_floor_row(client, db):
    """The live-stack bug: Odoo vacuums zero quants, so sold-out products
    often have NO floor stock row — they must still make the board when
    they're floor-relevant (recent Shoppe sales or floor history)."""
    _setup(db)
    today = date.today()
    sold_out = mk_product(db, "AY0000000801", "Neem Powder", odoo_id=801)
    was_stocked = mk_product(db, "HL0000000802", "Brass Lamp", odoo_id=802)
    mk_product(db, "GJ0000000803", "Gold Pendant", odoo_id=803)  # never floor-relevant
    meals = mk_product(db, "SN0000000804", "Campus Meal", odoo_id=804)
    meals.restock_exclude = True
    db.add_all(
        [
            # no floor StockLevel rows for any of these four
            SalesDaily(product_id=sold_out.id, day=today - timedelta(days=2),
                       channel="shoppe", units=4),
            StockSnapshot(snapshot_date=today - timedelta(days=10),
                          product_id=was_stocked.id, location_key="floor", qty=6),
            SalesDaily(product_id=meals.id, day=today - timedelta(days=1),
                       channel="shoppe", units=30),
        ]
    )
    db.commit()
    floor = login(client, "floor@test.io")
    items = {i["sku"]: i for i in client.get("/api/v1/oos", headers=floor).json()}
    assert "AY0000000801" in items  # sold recently, nothing on the floor now
    assert items["AY0000000801"]["floor_qty"] == 0.0
    assert "HL0000000802" in items  # had floor stock in history
    assert "GJ0000000803" not in items  # never floor-relevant — not noise
    assert "SN0000000804" not in items  # non-retail POS item stays off


def test_the_board_takes_no_actions(client, db):
    """Marking left the app (2026-08-24): the old mark/unmark/back-in-stock
    endpoints are gone, not just hidden — a stale client must get 404/405,
    never a write."""
    phantom, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    assert client.post(
        "/api/v1/oos", json={"product_id": phantom.id}, headers=floor
    ).status_code in (404, 405)
    assert client.delete("/api/v1/oos/1", headers=floor).status_code in (404, 405)
    assert client.post(
        "/api/v1/oos/1/restock", json={"counted_qty": 5}, headers=floor
    ).status_code in (404, 405)
