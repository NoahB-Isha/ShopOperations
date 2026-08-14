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


def test_odoo_actions_drive_the_workflow(client, db, live_env, monkeypatch):
    """Two-way sync, OUTBOUND half: the warehouse never opens the app —
    reserving the picking in Odoo flips the request to working, validating
    it flips to sent (quantities read back) and stages the count transfer,
    and cancelling in Odoo cancels the request."""
    copper, *_ = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    set_flag(db, "write_create_internal_transfer", True)
    set_flag(db, "write_prepare_count_transfer", True)
    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "0")  # no throttle in tests
    from app.config import get_settings

    get_settings.cache_clear()

    floor = login(client, "floor@test.io")
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    picking_id = r.json()["placement"]["picking_id"]

    # warehouse reserves it IN ODOO → the board poll flips it to working
    sim.call_kw("stock.picking", "write", [[picking_id], {"state": "assigned"}])
    r = client.get("/api/v1/transfer-requests", headers=floor)
    row = next(x for x in r.json() if x["id"] == rid)
    assert row["status"] == "working_on_it"

    # warehouse trims to 7 and VALIDATES in Odoo — no app clicks
    [move] = sim.search_read("stock.move", [["picking_id", "=", picking_id]], ["id"])
    sim.call_kw(
        "stock.move", "write", [[move["id"]], {"product_uom_qty": 7, "quantity": 7, "state": "done"}]
    )
    sim.call_kw("stock.picking", "write", [[picking_id], {"state": "done"}])
    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor)
    req = r.json()
    assert req["status"] == "counting"  # sent → count transfer staged in one motion
    assert req["lines"][0]["qty_sent"] == 7
    assert req["count"]["status"] == "created"
    assert any("started" in e["note"] and "Odoo" in e["note"] for e in req["events"])
    assert any("validated in Odoo" in e["note"] for e in req["events"])

    # a second request, cancelled straight in Odoo
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 3}]},
        headers=floor,
    )
    rid2 = r.json()["id"]
    pid2 = r.json()["placement"]["picking_id"]
    sim.call_kw("stock.picking", "write", [[pid2], {"state": "cancel"}])
    r = client.get(f"/api/v1/transfer-requests/{rid2}", headers=floor)
    assert r.json()["status"] == "cancelled"
    assert any("cancelled in Odoo" in e["note"] for e in r.json()["events"])


def test_coming_soon_includes_native_odoo_transfers(client, db, settings_env):
    """Two-way sync, INBOUND half at the endpoint: a transfer drafted
    straight in Odoo toward floor staging shows on coming-soon alongside app
    requests, labeled with its picking."""
    from app.sync.runner import run_domain

    copper, incense, _water = _setup(db)
    _sync_locations(db, settings_env)
    sim = OdooSimulator(settings_env.fixtures_path, read_only=True)
    run = run_domain(db, settings_env, "transfers", conn=sim, trigger="manual")
    assert run.status == "success", run.error

    floor = login(client, "floor@test.io")
    # one app request for copper too — quantities must merge per product
    client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 5}]},
        headers=floor,
    )
    items = {
        i["sku"]: i
        for i in client.get("/api/v1/transfer-requests/coming-soon", headers=floor).json()
    }
    # the native picking carries incense 24 + copper 6 (fixture WH/INT/NATIVE1)
    assert items["IN0000000777"]["qty_on_the_way"] == 24
    assert items["IN0000000777"]["requests"] == []
    [pick] = items["IN0000000777"]["odoo_pickings"]
    assert pick["picking_name"] == "WH/INT/NATIVE1" and pick["state"] == "assigned"
    assert items["CA0023000009"]["qty_on_the_way"] == 11  # 5 requested + 6 native
    assert len(items["CA0023000009"]["requests"]) == 1
    assert len(items["CA0023000009"]["odoo_pickings"]) == 1


def test_staging2_pallet_flow(client, db, live_env, monkeypatch):
    """The warehouse's REAL process: transfers get retargeted to III/Staging2
    and validated there (request goes SENT but the count WAITS), staging2
    accumulates, then 'Send all' renders ONE pallet draft — and when the
    pallet is validated in Odoo, the waiting requests flip to counting."""
    copper, *_ = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    monkeypatch.setattr(
        "app.transfers.pallet.get_connection", lambda settings, read_only=True: sim
    )
    set_flag(db, "write_create_internal_transfer", True)
    set_flag(db, "write_prepare_count_transfer", True)
    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "0")  # no throttle in tests
    from app.config import get_settings

    get_settings.cache_clear()

    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")

    # ---- place; warehouse retargets the picking to staging2 and validates
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    rid = r.json()["id"]
    picking_id = r.json()["placement"]["picking_id"]
    staging2_id = next(
        loc["id"]
        for loc in sim.search_read("stock.location", [], ["complete_name"])
        if str(loc["complete_name"]) == "III/Staging2"
    )
    [move] = sim.search_read("stock.move", [["picking_id", "=", picking_id]], ["id"])
    sim.call_kw("stock.move", "write", [[move["id"]], {"quantity": 10, "state": "done"}])
    sim.call_kw(
        "stock.picking", "write",
        [[picking_id], {"state": "done", "location_dest_id": staging2_id}],
    )

    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor)
    req = r.json()
    assert req["status"] == "sent"  # NOT counting — goods sit in staging2
    assert req["count"]["status"] == "none"
    assert any("waiting for the pallet" in e["note"] for e in req["events"])

    # ---- the staging2 page shows the fixture's consolidation stock
    r = client.get("/api/v1/transfer-requests/staging2", headers=wh)
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["source"] == "live"
    assert {i["sku"] for i in view["items"]} >= {"RU0000000005", "OC0000000042"}
    assert view["total_units"] >= 51  # 15 mala + 36 toothpaste

    # floor can look, but only the warehouse presses the big button
    assert client.post(
        "/api/v1/transfer-requests/staging2/send-all", headers=floor
    ).status_code == 403

    # ---- Send all → ONE draft pallet staging2 → floor staging
    r = client.post("/api/v1/transfer-requests/staging2/send-all", headers=wh)
    assert r.status_code == 200, r.text
    [pallet] = r.json()["pallets"]
    assert pallet["status"] == "open" and pallet["picking_status"] == "created"
    assert pallet["line_count"] == len(view["items"])
    pallet_picking = sim.search_read(
        "stock.picking", [["name", "=", pallet["picking_name"]]],
        ["location_id", "location_dest_id", "origin", "state"],
    )[0]
    staging_id = next(
        loc["id"]
        for loc in sim.search_read("stock.location", [], ["complete_name"])
        if "STAGING" in str(loc["complete_name"])
    )
    assert pallet_picking["location_id"] == staging2_id
    assert pallet_picking["location_dest_id"] == staging_id
    assert str(pallet_picking["origin"]).startswith("ILAPP-PLT-")

    # ---- a human validates the pallet in Odoo → waiting request flips to
    # counting on the next board read
    sim.call_kw(
        "stock.picking", "write", [[pallet_picking["id"]], {"state": "done"}]
    )
    r = client.get("/api/v1/transfer-requests", headers=floor)
    row = next(x for x in r.json() if x["id"] == rid)
    assert row["status"] == "counting"
    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor)
    req = r.json()
    assert req["count"]["status"] == "created"
    assert req["lines"][0]["qty_sent"] == 10
    assert any("pallet" in e["note"] and "landed" in e["note"] for e in req["events"])

    # the pallet reads validated on the staging2 page
    r = client.get("/api/v1/transfer-requests/staging2", headers=wh)
    assert r.json()["pallets"][0]["status"] == "validated"


def test_floor_receipt_in_odoo_closes_the_request(client, db, live_env, monkeypatch):
    """The real receiving process: the floor DUPLICATES the placement picking,
    retargets it staging→floor, trims what came up short and raises a second
    transfer for extras. Odoo's duplicate carries `origin`, so the app's
    ILAPP-TR- reference rides along and the receipt can be matched to the
    request for certain.

    Runs with write_prepare_count_transfer OFF — production's actual state, and
    the reason requests used to sit in `counting` forever: the count poll only
    ever watches a picking the app itself created."""
    copper, incense, water = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    set_flag(db, "write_create_internal_transfer", True)
    set_flag(db, "write_prepare_count_transfer", False)  # as it is on live
    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    floor_hdr = login(client, "floor@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={
            "lines": [
                {"product_id": copper.id, "qty": 10},
                {"product_id": incense.id, "qty": 5},
            ]
        },
        headers=floor_hdr,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    picking_id = r.json()["placement"]["picking_id"]
    reference = r.json()["placement"]["reference"]
    assert reference.startswith("ILAPP-TR-")

    # warehouse validates the placement in Odoo → sent (count stays simulated)
    for mv in sim.search_read("stock.move", [["picking_id", "=", picking_id]], ["id"]):
        sim.call_kw("stock.move", "write", [[mv["id"]], {"state": "done"}])
    sim.call_kw("stock.picking", "write", [[picking_id], {"state": "done"}])
    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor_hdr)
    assert r.json()["status"] in ("sent", "counting")
    assert r.json()["count"]["status"] != "created"  # nothing for the old poll to watch

    # the floor's own receipt: copper SHORT (10 sent, 8 arrived), incense over
    # (5 sent, 6 arrived) — and a cancelled line that delivered nothing.
    from app.models import OdooLocation
    from sqlalchemy import select

    floor_loc = db.scalar(select(OdooLocation).where(OdooLocation.key == "floor"))
    staging = db.scalar(select(OdooLocation).where(OdooLocation.key == "staging"))
    recv = sim.call_kw(
        "stock.picking",
        "create",
        [
            {
                "origin": reference,  # what Odoo's duplicate carries over
                "location_id": staging.odoo_id,
                "location_dest_id": floor_loc.odoo_id,
                "state": "done",
            }
        ],
    )
    for pid, qty, state in (
        (copper.odoo_product_id, 8, "done"),
        (incense.odoo_product_id, 6, "done"),
        (water.odoo_product_id, 99, "cancel"),  # must not count as received
    ):
        sim.call_kw(
            "stock.move",
            "create",
            [{"picking_id": recv, "product_id": pid, "product_uom_qty": qty,
              "quantity": qty, "state": state}],
        )

    # the detail GET is the listener — no app clicks on the floor's side
    r = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor_hdr)
    body = r.json()
    assert body["status"] == "done", body["status"]
    by_sku = {ln["sku"]: ln for ln in body["lines"]}
    assert by_sku[copper.global_sku]["qty_counted"] == 8
    assert by_sku[copper.global_sku]["delta"] == -2  # short
    assert by_sku[incense.global_sku]["qty_counted"] == 6
    assert by_sku[incense.global_sku]["delta"] == 1  # over
    assert any(reference in e["note"] for e in body["events"])

    # both directions filed as adjustments for the queue (warehouse owns it)
    adj = client.get("/api/v1/adjustments", headers=login(client, "wh@test.io")).json()
    deltas = sorted(a["delta"] for a in adj if a["request_id"] == rid)
    assert deltas == [-2.0, 1.0]


def test_count_validation_survives_a_real_throttle(client, db, live_env, monkeypatch):
    """The count picking's validation must be seen even though the floor-receipt
    closer shares its throttle stamp.

    This is the control for a class of bug, not one incident. Both closers read
    `count_checked_at`, and each used to TAKE it before doing its own Odoo read —
    so the first one to run stamped the throttle and the second bailed on that
    fresh stamp forever. On live, III/INT/04691 was validated 2026-08-12 and its
    request stayed in `counting`. Every other transfer test sets
    ODOO_COUNT_POLL_SECONDS=0, which is precisely the setting that hides a stolen
    stamp — so this one keeps a REAL throttle.
    """
    copper, *_ = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    set_flag(db, "write_create_internal_transfer", True)
    set_flag(db, "write_prepare_count_transfer", True)
    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "600")  # as live runs it, not 0
    from app.config import get_settings

    get_settings.cache_clear()
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = client.post(f"/api/v1/transfer-requests/{rid}/sent", json={}, headers=wh)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "counting"
    count_id = r.json()["count"]["picking_id"]

    # a human counts 10 and validates the count transfer in the barcode app
    for m in sim.search_read("stock.move", [["picking_id", "=", count_id]], ["id"]):
        sim.call_kw("stock.move", "write", [[m["id"]], {"quantity": 10, "state": "done"}])
    sim.call_kw("stock.picking", "write", [[count_id], {"state": "done"}])

    # the board read is the listener, and it must not spend the throttle on the
    # floor-receipt check alone — one refresh closes the request
    r = client.get("/api/v1/transfer-requests", headers=floor)
    assert r.status_code == 200
    [row] = [x for x in r.json() if x["id"] == rid]
    assert row["status"] == "done", row["status"]

    body = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor).json()
    assert body["lines"][0]["qty_counted"] == 10
    assert any("validated in Odoo" in e["note"] for e in body["events"])


def test_warehouse_built_pallet_advances_waiting_requests(client, db, live_env, monkeypatch):
    """Two requests go out to Staging2, then the warehouse consolidates them
    with a PLAIN Odoo transfer (staging2 → floor staging) instead of the app's
    'Send all' button. Nothing used to see that: poll_pallets only watches
    pallets the app rendered, so both requests sat in SENT forever."""
    copper, incense, _ = _setup(db)
    _sync_locations(db, live_env)
    sim = _wire_simulator(live_env, monkeypatch)
    set_flag(db, "write_create_internal_transfer", True)
    monkeypatch.setattr(
        "app.transfers.pallet.get_connection", lambda settings, read_only=True: sim
    )
    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    floor_hdr = login(client, "floor@test.io")

    from app.models import OdooLocation
    from sqlalchemy import select as sa_select

    staging2 = db.scalar(sa_select(OdooLocation).where(OdooLocation.key == "staging2"))
    staging = db.scalar(sa_select(OdooLocation).where(OdooLocation.key == "staging"))

    rids = []
    for product, qty in ((copper, 10), (incense, 5)):
        r = client.post(
            "/api/v1/transfer-requests",
            json={"lines": [{"product_id": product.id, "qty": qty}]},
            headers=floor_hdr,
        )
        assert r.status_code == 201, r.text
        rids.append(r.json()["id"])
        pid = r.json()["placement"]["picking_id"]
        # warehouse retargets to staging2 and validates — the real process
        sim.call_kw("stock.picking", "write", [[pid], {"location_dest_id": staging2.odoo_id}])
        for mv in sim.search_read("stock.move", [["picking_id", "=", pid]], ["id"]):
            sim.call_kw("stock.move", "write", [[mv["id"]], {"state": "done"}])
        sim.call_kw("stock.picking", "write", [[pid], {"state": "done"}])

    # both land in SENT, waiting on a pallet that the app has not rendered
    for rid in rids:
        body = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor_hdr).json()
        assert body["status"] == "sent", body["status"]
        assert body["count"]["status"] == "none"

    # the warehouse's OWN consolidating transfer — no app reference anywhere
    manual = sim.call_kw("stock.picking", "create",
        [{"location_id": staging2.odoo_id, "location_dest_id": staging.odoo_id, "state": "done"}])
    for product, qty in ((copper, 10), (incense, 5)):
        sim.call_kw("stock.move", "create",
            [{"picking_id": manual, "product_id": product.odoo_product_id,
              "product_uom_qty": qty, "quantity": qty, "state": "done"}])

    # the board GET is the listener — both requests become countable
    client.get("/api/v1/transfer-requests", headers=floor_hdr)
    for rid in rids:
        body = client.get(f"/api/v1/transfer-requests/{rid}", headers=floor_hdr).json()
        assert body["status"] == "counting", (rid, body["status"])
        assert any("landed at floor staging" in e["note"] for e in body["events"])

    # and it is not processed twice — a second pass moves nothing new
    from app.transfers.pallet import poll_manual_pallets

    monkeypatch.setenv("ODOO_COUNT_POLL_SECONDS", "0")
    get_settings.cache_clear()
    assert poll_manual_pallets(db, get_settings()) == 0
