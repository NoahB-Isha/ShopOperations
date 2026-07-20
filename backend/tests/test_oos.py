"""Floor OOS board: computed zeros + manual marks, and the inventory-
reduction draft ("USA-III: Inventory Adj Reduction") that cleans up phantom
floor stock — draft only, human-validated, honestly simulated when gated.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.models import FloorOosMark, IncomingMove, OdooLocation, Role, StockLevel
from app.odoo.simulator import OdooSimulator
from sqlalchemy import select

from .util import login, mk_product, mk_user, set_flag


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
        # location mapping the writer needs (ids match the test fixtures),
        # WITHOUT running the stock sync — it would replace the quantities above
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
    assert items["IN0000000777"]["mark"] is None

    # scoped: orderers can't see the floor board
    orderer = login(client, "orderer@test.io")
    assert client.get("/api/v1/oos", headers=orderer).status_code == 403


def test_mark_with_phantom_stock_renders_simulated_reduction(client, db):
    phantom, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    r = client.post(
        "/api/v1/oos",
        json={"product_id": phantom.id, "note": "shelf empty, Odoo says 3"},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["mark"]["qty_removed"] == 3
    # flag ships OFF → honestly simulated, reference minted anyway
    assert item["mark"]["picking"]["status"] == "simulated"
    assert item["mark"]["picking"]["reference"].startswith("ILAPP-OOS-")

    # marked rows lead the board even though Odoo still says 3 on the floor
    board = client.get("/api/v1/oos", headers=floor).json()
    assert board[0]["sku"] == "CA0023000009"
    assert board[0]["floor_qty"] == 3

    # one open mark per product
    r = client.post("/api/v1/oos", json={"product_id": phantom.id}, headers=floor)
    assert r.status_code == 409


def test_mark_with_nothing_to_remove_is_bookkeeping_only(client, db):
    _, zero, _ = _setup(db)
    floor = login(client, "floor@test.io")
    r = client.post("/api/v1/oos", json={"product_id": zero.id}, headers=floor)
    assert r.status_code == 201, r.text
    mark = r.json()["mark"]
    assert mark["qty_removed"] == 0
    assert mark["picking"]["status"] == "none"  # nothing to reduce — no draft, honestly
    assert db.scalar(select(FloorOosMark)).picking_reference == ""


def test_live_mark_creates_draft_reduction_and_unmark_removes_it(
    client, db, live_env, monkeypatch
):
    """Against the simulator: the draft lands on the reduction operation type
    with the floor as source and the type's default destination; unmark
    removes the still-draft picking."""
    phantom, *_ = _setup(db)
    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim
    )
    monkeypatch.setattr(
        "app.oos.router.get_connection", lambda settings, read_only=True: sim
    )
    set_flag(db, "write_create_inventory_reduction", True)
    floor = login(client, "floor@test.io")

    r = client.post(
        "/api/v1/oos", json={"product_id": phantom.id, "note": "empty"}, headers=floor
    )
    assert r.status_code == 201, r.text
    picking = r.json()["mark"]["picking"]
    assert picking["status"] == "created"
    assert picking["picking_name"]
    assert "stock.picking" in picking["url"]

    [row] = sim.search_read(
        "stock.picking", [["id", "=", picking["picking_id"]]],
        ["state", "picking_type_id", "location_id", "location_dest_id", "origin"],
    )
    assert row["state"] == "draft"  # the app NEVER validates
    type_field = row["picking_type_id"]
    assert (type_field[0] if isinstance(type_field, list) else type_field) == 7  # the reduction type
    dest = row["location_dest_id"]
    assert (dest[0] if isinstance(dest, list) else dest) == 31  # type's default dest
    assert row["origin"].startswith("ILAPP-OOS-")

    # the reduction quantity is exactly what Odoo claimed was on the floor
    [move] = sim.search_read(
        "stock.move", [["picking_id", "=", picking["picking_id"]]], ["product_uom_qty"]
    )
    assert move["product_uom_qty"] == 3

    # unmark: the draft goes away with the mark
    mark_id = r.json()["mark"]["id"]
    r = client.delete(f"/api/v1/oos/{mark_id}", headers=floor)
    assert r.status_code == 204
    assert sim.search_read("stock.picking", [["id", "=", picking["picking_id"]]], ["id"]) == []
    assert db.scalar(select(FloorOosMark)) is None


def test_back_in_stock_reconciles_via_addition_or_reduction(
    client, db, live_env, monkeypatch
):
    """Counted > Odoo → 'Adding Qty' draft for the difference (loss → floor);
    counted < Odoo → reduction; the mark goes away either way."""
    phantom, *_ = _setup(db)  # Odoo floor: 3
    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim
    )
    monkeypatch.setattr(
        "app.oos.router.get_connection", lambda settings, read_only=True: sim
    )
    set_flag(db, "write_create_inventory_reduction", True)
    set_flag(db, "write_create_inventory_addition", True)
    floor = login(client, "floor@test.io")

    # mark it out, then find MORE than Odoo thinks: count 5 vs 3 → add 2
    mark_id = client.post(
        "/api/v1/oos", json={"product_id": phantom.id}, headers=floor
    ).json()["mark"]["id"]
    r = client.post(
        f"/api/v1/oos/{mark_id}/restock", json={"counted_qty": 5}, headers=floor
    )
    assert r.status_code == 200, r.text
    adj = r.json()["adjustment"]
    assert r.json()["floor_qty_before"] == 3
    assert adj["direction"] == "add" and adj["qty"] == 2
    assert adj["status"] == "created" and adj["picking_name"]
    [row] = sim.search_read(
        "stock.picking", [["origin", "=", adj["reference"]]],
        ["picking_type_id", "location_id", "location_dest_id", "state"],
    )
    assert row["state"] == "draft"
    tf = row["picking_type_id"]
    assert (tf[0] if isinstance(tf, list) else tf) == 8  # the Adding Qty type (double-space name)
    src = row["location_id"]
    assert (src[0] if isinstance(src, list) else src) == 31  # type's default source
    dest = row["location_dest_id"]
    assert (dest[0] if isinstance(dest, list) else dest) == 14  # the floor
    assert db.scalar(select(FloorOosMark)) is None  # mark gone

    # again, but count LESS than Odoo: 1 vs 3 → reduce 2
    mark_id = client.post(
        "/api/v1/oos", json={"product_id": phantom.id}, headers=floor
    ).json()["mark"]["id"]
    adj = client.post(
        f"/api/v1/oos/{mark_id}/restock", json={"counted_qty": 1}, headers=floor
    ).json()["adjustment"]
    assert adj["direction"] == "reduce" and adj["qty"] == 2

    # count agrees with Odoo → nothing to render
    mark_id = client.post(
        "/api/v1/oos", json={"product_id": phantom.id}, headers=floor
    ).json()["mark"]["id"]
    r = client.post(
        f"/api/v1/oos/{mark_id}/restock", json={"counted_qty": 3}, headers=floor
    )
    assert r.json()["adjustment"] is None


def test_back_in_stock_without_count_is_a_plain_unmark(client, db):
    phantom, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    mark_id = client.post(
        "/api/v1/oos", json={"product_id": phantom.id}, headers=floor
    ).json()["mark"]["id"]
    r = client.post(f"/api/v1/oos/{mark_id}/restock", json={}, headers=floor)
    assert r.status_code == 200 and r.json()["adjustment"] is None
    assert db.scalar(select(FloorOosMark)) is None
