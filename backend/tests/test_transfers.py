"""The Odoo-native BWHSE→Floor flow:

  place (draft renders immediately) → working on it → sent (qtys read back,
  count transfer prepared: copy → To Do → check availability) → counting
  (floor scans in Odoo barcode) → done (validation detected, discrepancies
  reconciled into the adjustments queue).
"""
from __future__ import annotations

from app.models import Role, StockLevel
from app.odoo.simulator import OdooSimulator

from .util import login, mk_product, mk_user, set_flag


def _setup(db):
    copper = mk_product(db, "CA0023000009", "Copper Water Bottle", odoo_id=201)
    incense = mk_product(db, "IN0000000777", "Sandalwood Incense", odoo_id=203)
    water = mk_product(db, "MAN-WATER", "Spring Water", source="manual", stock_tracked=False)
    db.add(StockLevel(product_id=copper.id, location_key="floor", qty=3))
    db.add(StockLevel(product_id=copper.id, location_key="bwhse", qty=120))
    db.commit()
    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))
    mk_user(db, "wh@test.io", (Role.WAREHOUSE, None, None))
    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, None))
    return copper, incense, water


def _sync_locations(db, settings):
    from app.sync.runner import run_domain

    sim = OdooSimulator(settings.fixtures_path, read_only=True)
    run_domain(db, settings, "products", conn=sim, trigger="manual")
    run_domain(db, settings, "stock", conn=sim, trigger="manual")


def _wire_simulator(live_env, monkeypatch) -> OdooSimulator:
    """One shared read-write simulator for writer AND read connections, so
    the whole flow (draft → copy → validate → poll) sees the same 'Odoo'."""
    sim_rw = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim_rw
    )
    monkeypatch.setattr(
        "app.transfers.service.get_connection", lambda settings, read_only=True: sim_rw
    )
    return sim_rw


def test_placement_is_simulated_honestly_when_writes_off(client, db, settings_env):
    copper, *_ = _setup(db)
    _sync_locations(db, settings_env)
    floor = login(client, "floor@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={"notes": "morning cart", "lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "requested"
    assert req["placement"]["status"] == "simulated"
    assert req["display_name"] == f"#{req['id']}"  # no Odoo name without a live draft
    assert any("simulated" in e["note"] for e in req["events"] if e["kind"] == "odoo")
    # editable while nothing is live in Odoo
    assert req["actions"]["can_edit_lines"] is True


def test_full_flow_against_simulator_with_barcode_validation(
    client, db, live_env, monkeypatch
):
    """The acceptance path, Odoo-native: request 10 → warehouse sends 9 (in
    'Odoo') → count transfer prepared → human validates 8 in the barcode app
    → app detects it, closes the request, files the -1 adjustment."""
    copper, *_ = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    set_flag(db, "write_create_internal_transfer", True)
    set_flag(db, "write_prepare_count_transfer", True)
    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "0")  # no throttle in tests
    from app.config import get_settings

    get_settings.cache_clear()

    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")

    # ---- place: the draft IS the order
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    req = r.json()
    rid = req["id"]
    assert req["placement"]["status"] == "created"
    assert req["display_name"].startswith("III/INT/")
    assert req["placement"]["picking_name"] == req["display_name"]
    assert "stock.picking" in req["placement"]["url"]
    placement_id = req["placement"]["picking_id"]
    [picking] = sim.search_read("stock.picking", [["id", "=", placement_id]], ["state", "origin"])
    assert picking["state"] == "draft"
    # lines lock — the Odoo draft is the source of truth now
    assert req["actions"]["can_edit_lines"] is False
    r = client.put(
        f"/api/v1/transfer-requests/{rid}/lines",
        json={"lines": [{"product_id": copper.id, "qty": 1}]},
        headers=floor,
    )
    assert r.status_code == 409 and "edit the draft there" in r.json()["detail"]

    # ---- warehouse acknowledges
    r = client.post(f"/api/v1/transfer-requests/{rid}/ack", json={}, headers=wh)
    assert r.status_code == 200 and r.json()["status"] == "working_on_it"

    # warehouse trims the quantity to 9 IN ODOO (like they would while picking)
    [move] = sim.search_read("stock.move", [["picking_id", "=", placement_id]], ["id"])
    sim.call_kw("stock.move", "write", [[move["id"]], {"product_uom_qty": 9, "quantity": 9}])

    # ---- sent: qtys read back; count transfer prepared in the same motion
    r = client.post(f"/api/v1/transfer-requests/{rid}/sent", json={}, headers=wh)
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["status"] == "counting"
    assert req["lines"][0]["qty_sent"] == 9
    assert req["count"]["status"] == "created"
    assert req["count"]["picking_name"].startswith("III/INT/")
    assert req["count"]["barcode_url"].endswith(f"/odoo/barcode/{req['count']['picking_id']}")
    count_id = req["count"]["picking_id"]

    # the copy is STAGING→FLOOR, marked ready, and its moves were retargeted
    [count_pick] = sim.search_read(
        "stock.picking", [["id", "=", count_id]], ["state", "location_id", "location_dest_id"]
    )
    assert count_pick["state"] == "assigned"  # To Do + availability checked
    staging_id = next(
        loc["id"]
        for loc in sim.search_read("stock.location", [], ["complete_name"])
        if "STAGING" in str(loc["complete_name"])
    )
    assert count_pick["location_id"] == staging_id
    count_moves = sim.search_read(
        "stock.move", [["picking_id", "=", count_id]], ["location_id", "product_uom_qty"]
    )
    assert all(m["location_id"] == staging_id for m in count_moves)

    # manual close is refused while the count picking is live
    r = client.post(f"/api/v1/transfer-requests/{rid}/mark-done", json={}, headers=floor)
    assert r.status_code == 409

    # ---- a human counts 8 and validates in the barcode app (simulated here)
    for m in sim.search_read("stock.move", [["picking_id", "=", count_id]], ["id"]):
        sim.call_kw("stock.move", "write", [[m["id"]], {"quantity": 8, "state": "done"}])
    sim.call_kw("stock.picking", "write", [[count_id], {"state": "done"}])

    # ---- the app's listener picks it up on the next read
    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor)
    assert r.status_code == 200
    req = r.json()
    assert req["status"] == "done"
    assert req["lines"][0]["qty_counted"] == 8
    assert req["lines"][0]["delta"] == -1
    assert any(e["kind"] == "discrepancy" for e in req["events"])
    assert any("validated in Odoo" in e["note"] for e in req["events"])

    # ---- the -1 sits in the warehouse adjustments queue
    r = client.get("/api/v1/adjustments", headers=wh)
    [adj] = r.json()
    assert (adj["qty_expected"], adj["qty_counted"], adj["delta"]) == (9, 8, -1)
    assert adj["request_id"] == rid
    r = client.post(
        f"/api/v1/adjustments/{adj['id']}/resolve",
        json={"action": "resolved", "note": "found it under the cart"},
        headers=wh,
    )
    assert r.status_code == 200
    assert client.get("/api/v1/adjustments", headers=wh).json() == []


def test_simulated_flow_manual_close(client, db, settings_env):
    """With writes gated there's no Odoo picking to scan — the flow still
    moves and closes manually, with counted taken as sent (no invented
    discrepancies)."""
    copper, *_ = _setup(db)
    _sync_locations(db, settings_env)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 5}]},
        headers=floor,
    )
    rid = r.json()["id"]

    r = client.post(f"/api/v1/transfer-requests/{rid}/sent", json={}, headers=wh)
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["status"] == "counting"
    assert req["count"]["status"] == "simulated"
    assert req["lines"][0]["qty_sent"] == 5  # assumed from the request
    assert req["actions"]["can_mark_done"] is False  # closing is the floor's call
    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor)
    assert r.json()["actions"]["can_mark_done"] is True

    # warehouse can't close it — that's the floor's call
    assert (
        client.post(f"/api/v1/transfer-requests/{rid}/mark-done", json={}, headers=wh).status_code
        == 403
    )
    r = client.post(f"/api/v1/transfer-requests/{rid}/mark-done", json={}, headers=floor)
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["lines"][0]["delta"] == 0
    assert client.get("/api/v1/adjustments", headers=login(client, "wh@test.io")).json() == []


def test_transitions_enforce_role_and_order(client, db, settings_env):
    copper, *_ = _setup(db)
    _sync_locations(db, settings_env)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")
    orderer = login(client, "orderer@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 5}]},
        headers=floor,
    )
    rid = r.json()["id"]

    # orderers see nothing; warehouse can't create
    assert client.get("/api/v1/transfer-requests", headers=orderer).status_code == 403
    assert (
        client.post(
            "/api/v1/transfer-requests",
            json={"lines": [{"product_id": copper.id, "qty": 1}]},
            headers=wh,
        ).status_code
        == 403
    )
    # floor can't ack or send
    assert client.post(f"/api/v1/transfer-requests/{rid}/ack", json={}, headers=floor).status_code == 403
    assert client.post(f"/api/v1/transfer-requests/{rid}/sent", json={}, headers=floor).status_code == 403
    # done can't be skipped to from requested
    assert (
        client.post(f"/api/v1/transfer-requests/{rid}/mark-done", json={}, headers=floor).status_code
        == 409
    )
    # cancel from requested is fine for the floor; nothing moves afterwards
    r = client.post(f"/api/v1/transfer-requests/{rid}/cancel", json={}, headers=floor)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/transfer-requests/{rid}/ack", json={}, headers=wh).status_code == 409


def test_cancel_unlinks_live_draft(client, db, live_env, monkeypatch):
    copper, *_ = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    set_flag(db, "write_create_internal_transfer", True)

    floor = login(client, "floor@test.io")
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 3}]},
        headers=floor,
    )
    req = r.json()
    assert req["placement"]["status"] == "created"
    picking_id = req["placement"]["picking_id"]
    assert sim.search_count("stock.picking", [["id", "=", picking_id]]) == 1

    r = client.post(f"/api/v1/transfer-requests/{req['id']}/cancel", json={}, headers=floor)
    assert r.status_code == 200
    assert sim.search_count("stock.picking", [["id", "=", picking_id]]) == 0
    assert any("removed from Odoo" in e["note"] for e in r.json()["events"])


def test_line_validation(client, db, settings_env):
    copper, _, water = _setup(db)
    _sync_locations(db, settings_env)
    floor = login(client, "floor@test.io")
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": water.id, "qty": 2}]},
        headers=floor,
    )
    assert r.status_code == 422 and "tracked in Odoo" in r.json()["detail"]
    r = client.post(
        "/api/v1/transfer-requests",
        json={
            "lines": [
                {"product_id": copper.id, "qty": 1},
                {"product_id": copper.id, "qty": 2},
            ]
        },
        headers=floor,
    )
    assert r.status_code == 422


def test_coming_soon_aggregates_active_requests(client, db, settings_env):
    """Coming soon via transfer: per-product totals across ACTIVE requests
    only — sent quantities preferred, done/cancelled excluded, floor-scoped."""
    copper, incense, _water = _setup(db)
    _sync_locations(db, settings_env)
    floor = login(client, "floor@test.io")

    r1 = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    ).json()
    client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 5}, {"product_id": incense.id, "qty": 3}]},
        headers=floor,
    )
    # a cancelled request must not count
    r3 = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 99}]},
        headers=floor,
    ).json()
    client.post(f"/api/v1/transfer-requests/{r3['id']}/cancel", json={}, headers=floor)
    # warehouse trimmed r1 to 8 in transit (sent qty wins over requested)
    from app.models import TransferRequestLine
    from sqlalchemy import select as sa_select

    line = db.scalar(
        sa_select(TransferRequestLine).where(TransferRequestLine.request_id == r1["id"])
    )
    line.qty_sent = 8
    db.commit()

    r = client.get("/api/v1/transfer-requests/coming-soon", headers=floor)
    assert r.status_code == 200, r.text
    items = {i["sku"]: i for i in r.json()}
    assert set(items) == {"CA0023000009", "IN0000000777"}
    assert items["CA0023000009"]["qty_on_the_way"] == 13  # 8 sent + 5 requested
    assert items["IN0000000777"]["qty_on_the_way"] == 3
    assert {req["qty"] for req in items["CA0023000009"]["requests"]} == {8, 5}
    assert items["CA0023000009"]["bwhse_qty"] > 0  # fixture stock, synced

    # orderers have no business here
    orderer = login(client, "orderer@test.io")
    assert client.get("/api/v1/transfer-requests/coming-soon", headers=orderer).status_code == 403
