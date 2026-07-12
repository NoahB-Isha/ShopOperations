"""BWHSE→Floor transfer flow — including the phase-2 acceptance scenario:
floor requests 10, warehouse fulfills 9, staging count finds 8, and the
1-unit discrepancy lands in the warehouse adjustments queue.
"""
from __future__ import annotations

from app.models import Role, StockLevel

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


def test_acceptance_flow_10_9_8_discrepancy(client, db, settings_env):
    copper, incense, _ = _setup(db)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")

    # floor requests 10
    r = client.post(
        "/api/v1/transfer-requests",
        json={"notes": "morning restock", "lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "requested"
    [line] = req["lines"]
    assert (line["qty_requested"], line["floor_qty"], line["bwhse_qty"]) == (10, 3, 120)
    assert req["actions"]["can_edit_lines"] is True
    assert req["actions"]["can_fulfill"] is False  # floor can't pick

    # warehouse fulfills 9
    r = client.post(
        f"/api/v1/transfer-requests/{req['id']}/fulfill",
        json={"lines": [{"line_id": line["id"], "qty_sent": 9}]},
        headers=wh,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "picked"
    assert r.json()["lines"][0]["qty_sent"] == 9

    # warehouse delivers to staging
    r = client.post(
        f"/api/v1/transfer-requests/{req['id']}/stage", json={}, headers=wh
    )
    assert r.status_code == 200 and r.json()["status"] == "in_staging"

    # floor counts 8 → discrepancy of -1
    r = client.post(
        f"/api/v1/transfer-requests/{req['id']}/count",
        json={"lines": [{"line_id": line["id"], "qty_counted": 8}]},
        headers=floor,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "counted"
    assert body["lines"][0]["delta"] == -1
    assert any(e["kind"] == "discrepancy" for e in body["events"])

    # the discrepancy is in the warehouse adjustments queue
    r = client.get("/api/v1/adjustments", headers=wh)
    assert r.status_code == 200
    [adj] = r.json()
    assert adj["sku"] == "CA0023000009"
    assert (adj["qty_expected"], adj["qty_counted"], adj["delta"]) == (9, 8, -1)
    assert adj["status"] == "open"

    # floor can't see the queue; warehouse resolves it
    assert client.get("/api/v1/adjustments", headers=floor).status_code == 403
    r = client.post(
        f"/api/v1/adjustments/{adj['id']}/resolve",
        json={"action": "resolved", "note": "found it under the cart"},
        headers=wh,
    )
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert client.get("/api/v1/adjustments", headers=wh).json() == []

    # shelve it
    r = client.post(
        f"/api/v1/transfer-requests/{req['id']}/complete", json={}, headers=floor
    )
    assert r.status_code == 200 and r.json()["status"] == "on_floor"

    # the shared timeline recorded every step
    kinds = [(e["kind"], e["status"]) for e in r.json()["events"]]
    statuses = [s for k, s in kinds if k == "status"]
    assert statuses == ["requested", "picked", "in_staging", "counted", "on_floor"]


def test_transitions_enforce_role_and_order(client, db, settings_env):
    copper, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")
    orderer = login(client, "orderer@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 5}]},
        headers=floor,
    )
    rid = r.json()["id"]
    line_id = r.json()["lines"][0]["id"]

    # orderers have no access at all
    assert client.get("/api/v1/transfer-requests", headers=orderer).status_code == 403
    # warehouse can't create requests
    assert (
        client.post(
            "/api/v1/transfer-requests",
            json={"lines": [{"product_id": copper.id, "qty": 1}]},
            headers=wh,
        ).status_code
        == 403
    )
    # floor can't fulfill; warehouse can't count later
    assert (
        client.post(
            f"/api/v1/transfer-requests/{rid}/fulfill", json={}, headers=floor
        ).status_code
        == 403
    )
    # skipping states is a conflict
    assert (
        client.post(
            f"/api/v1/transfer-requests/{rid}/count",
            json={"lines": [{"line_id": line_id, "qty_counted": 5}]},
            headers=floor,
        ).status_code
        == 409
    )
    # cancel from requested is fine for floor
    r = client.post(f"/api/v1/transfer-requests/{rid}/cancel", json={}, headers=floor)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # and nothing moves after cancellation
    assert (
        client.post(f"/api/v1/transfer-requests/{rid}/fulfill", json={}, headers=wh).status_code
        == 409
    )


def test_count_requires_every_sent_line(client, db, settings_env):
    copper, incense, _ = _setup(db)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")

    r = client.post(
        "/api/v1/transfer-requests",
        json={
            "lines": [
                {"product_id": copper.id, "qty": 4},
                {"product_id": incense.id, "qty": 6},
            ]
        },
        headers=floor,
    )
    rid = r.json()["id"]
    lines = {ln["sku"]: ln["id"] for ln in r.json()["lines"]}

    # warehouse zeroes the incense line (out of stock) and picks the copper
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/fulfill",
        json={"lines": [{"line_id": lines["IN0000000777"], "qty_sent": 0}]},
        headers=wh,
    )
    assert r.json()["lines"][1]["qty_sent"] in (0, 4)  # order-independent check below
    sent = {ln["sku"]: ln["qty_sent"] for ln in r.json()["lines"]}
    assert sent == {"CA0023000009": 4, "IN0000000777": 0}

    client.post(f"/api/v1/transfer-requests/{rid}/stage", json={}, headers=wh)

    # counting only part of the sent lines is rejected
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/count", json={"lines": [{"line_id": lines["IN0000000777"], "qty_counted": 0}]},
        headers=floor,
    )
    assert r.status_code == 422
    assert "needs a counted quantity" in r.json()["detail"]

    # counting the sent line works; the zero-sent line auto-counts to 0
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/count",
        json={"lines": [{"line_id": lines["CA0023000009"], "qty_counted": 4}]},
        headers=floor,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "counted"
    # no discrepancies: 4=4 sent/counted, 0=0
    assert client.get("/api/v1/adjustments", headers=wh).json() == []


def test_line_validation(client, db, settings_env):
    copper, _, water = _setup(db)
    floor = login(client, "floor@test.io")
    # untracked items can't ride a transfer
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": water.id, "qty": 2}]},
        headers=floor,
    )
    assert r.status_code == 422 and "stock-tracked" in r.json()["detail"]
    # duplicate products are rejected
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


def _request_picked(client, db, copper, floor, wh):
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 10}]},
        headers=floor,
    )
    rid, line_id = r.json()["id"], r.json()["lines"][0]["id"]
    client.post(
        f"/api/v1/transfer-requests/{rid}/fulfill",
        json={"lines": [{"line_id": line_id, "qty_sent": 9}]},
        headers=wh,
    )
    return rid, line_id


def _sync_locations(db, settings):
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(settings.fixtures_path, read_only=True)
    run_domain(db, settings, "products", conn=sim, trigger="manual")
    run_domain(db, settings, "stock", conn=sim, trigger="manual")


def test_odoo_draft_simulated_when_writes_off(client, db, settings_env):
    """Kill switch off -> the draft renders as an honest dry-run."""
    copper, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")
    rid, _ = _request_picked(client, db, copper, floor, wh)

    # leg not available before its state (staging_floor needs a count first)
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/odoo-draft",
        json={"leg": "staging_floor"},
        headers=wh,
    )
    assert r.status_code == 409

    _sync_locations(db, settings_env)
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/odoo-draft",
        json={"leg": "bwhse_staging"},
        headers=wh,
    )
    assert r.status_code == 200, r.text
    [draft] = r.json()["odoo_drafts"]
    assert draft["status"] == "simulated"
    assert draft["dry_run_reason"] == "kill_switch"
    assert draft["reference"].startswith("ILAPP-TR-")

    # floor can't render the warehouse leg
    assert (
        client.post(
            f"/api/v1/transfer-requests/{rid}/odoo-draft",
            json={"leg": "bwhse_staging"},
            headers=floor,
        ).status_code
        == 403
    )


def test_odoo_draft_live_against_simulator(client, db, live_env, monkeypatch):
    """Writes enabled + feature flag on -> a real draft lands in (simulated)
    Odoo with the sent quantities, and a retry reuses the same reference."""
    from app.odoo.simulator import OdooSimulator

    copper, *_ = _setup(db)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")
    rid, _ = _request_picked(client, db, copper, floor, wh)
    _sync_locations(db, live_env)

    sim_rw = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim_rw
    )

    # flag still off -> simulated for that reason
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/odoo-draft",
        json={"leg": "bwhse_staging"},
        headers=wh,
    )
    assert r.json()["odoo_drafts"][-1]["dry_run_reason"] == "feature_flag"

    set_flag(db, "write_create_internal_transfer", True)
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/odoo-draft",
        json={"leg": "bwhse_staging"},
        headers=wh,
    )
    assert r.status_code == 200, r.text
    draft = r.json()["odoo_drafts"][-1]
    assert draft["status"] == "created"
    assert draft["odoo_picking_id"] is not None
    assert "stock.picking" in draft["odoo_url"]

    # the draft exists in "Odoo" with the SENT quantity (9, not 10)
    [picking] = sim_rw.search_read("stock.picking", [["origin", "=", draft["reference"]]], ["state"])
    assert picking["state"] == "draft"
    [move] = sim_rw.search_read(
        "stock.move", [["picking_id", "=", draft["odoo_picking_id"]]], ["product_uom_qty"]
    )
    assert move["product_uom_qty"] == 9

    # retrying the leg reuses the reference -> idempotent, no duplicate
    r = client.post(
        f"/api/v1/transfer-requests/{rid}/odoo-draft",
        json={"leg": "bwhse_staging"},
        headers=wh,
    )
    drafts = [d for d in r.json()["odoo_drafts"] if d["leg"] == "bwhse_staging"]
    assert drafts[-1]["reference"] == draft["reference"]
    assert sim_rw.search_count("stock.picking", []) == 1


def test_lines_lock_after_pick(client, db, settings_env):
    copper, incense, _ = _setup(db)
    floor = login(client, "floor@test.io")
    wh = login(client, "wh@test.io")
    r = client.post(
        "/api/v1/transfer-requests",
        json={"lines": [{"product_id": copper.id, "qty": 3}]},
        headers=floor,
    )
    rid = r.json()["id"]
    # editable while requested
    r = client.put(
        f"/api/v1/transfer-requests/{rid}/lines",
        json={"lines": [{"product_id": incense.id, "qty": 2}]},
        headers=floor,
    )
    assert r.status_code == 200 and r.json()["lines"][0]["sku"] == "IN0000000777"
    client.post(f"/api/v1/transfer-requests/{rid}/fulfill", json={}, headers=wh)
    r = client.put(
        f"/api/v1/transfer-requests/{rid}/lines",
        json={"lines": [{"product_id": copper.id, "qty": 1}]},
        headers=floor,
    )
    assert r.status_code == 409
