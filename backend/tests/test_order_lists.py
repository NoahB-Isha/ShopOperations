"""Order lists: admin CRUD/clone/assign, coordinator scope, and the approval
write with every honest outcome (simulated / created / failed)."""
from __future__ import annotations

from app.models import Center, OdooWriteAudit, Role, StockLevel
from sqlalchemy import select

from .util import login, mk_center, mk_product, mk_user, mk_zone, set_flag


def _setup(db):
    zone1 = mk_zone(db, "Zone 1 — Lili")
    zone2 = mk_zone(db, "Zone 2 — Mik")
    austin = mk_center(db, "Austin", zone_id=zone1.id)
    dallas = mk_center(db, "Dallas", zone_id=zone2.id)
    copper = mk_product(db, "CA0023000009", "Copper Water Bottle", odoo_id=201)
    incense = mk_product(db, "IN0000000777", "Sandalwood Incense", odoo_id=203)
    db.add(StockLevel(product_id=copper.id, location_key="bwhse", qty=120))
    db.commit()
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    mk_user(db, "lili@test.io", (Role.ZONE_COORDINATOR, zone1.id, None))
    mk_user(db, "mik@test.io", (Role.ZONE_COORDINATOR, zone2.id, None))
    return zone1, zone2, austin, dallas, copper, incense


def _make_pending(client, db, admin, zone, center, copper, incense) -> dict:
    r = client.post(
        "/api/v1/order-lists", json={"name": "Austin summer refill"}, headers=admin
    )
    assert r.status_code == 201, r.text
    ol = r.json()
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/lines",
        json={
            "lines": [
                {"product_id": copper.id, "qty": 12},
                {"product_id": incense.id, "qty": 24},
            ]
        },
        headers=admin,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/v1/order-lists/{ol['id']}/assign",
        json={"zone_id": zone.id, "center_id": center.id},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_approval"
    return r.json()


def test_crud_clone_assign_and_scope(client, db, settings_env):
    zone1, zone2, austin, dallas, copper, incense = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")
    mik = login(client, "mik@test.io")

    ol = _make_pending(client, db, admin, zone1, austin, copper, incense)
    assert ol["zone_name"].startswith("Zone 1")
    assert ol["center_name"] == "Austin"
    assert ol["total_qty"] == 36

    # a coordinator sees only their zone's non-draft lists
    mine = client.get("/api/v1/order-lists", headers=lili).json()
    assert [x["id"] for x in mine] == [ol["id"]]
    assert client.get("/api/v1/order-lists", headers=mik).json() == []
    assert client.get(f"/api/v1/order-lists/{ol['id']}", headers=mik).status_code == 403

    # drafts stay invisible to coordinators entirely
    r = client.post("/api/v1/order-lists", json={"name": "WIP list"}, headers=admin)
    assert client.get(f"/api/v1/order-lists/{r.json()['id']}", headers=lili).status_code == 403

    # assigned lists lock their content
    assert (
        client.put(
            f"/api/v1/order-lists/{ol['id']}/lines",
            json={"lines": [{"product_id": copper.id, "qty": 1}]},
            headers=admin,
        ).status_code
        == 409
    )
    assert client.delete(f"/api/v1/order-lists/{ol['id']}", headers=admin).status_code == 409

    # clone copies lines and destination, resets the lifecycle
    r = client.post(f"/api/v1/order-lists/{ol['id']}/clone", headers=admin)
    assert r.status_code == 201
    clone = r.json()
    assert clone["status"] == "draft"
    assert clone["cloned_from_id"] == ol["id"]
    assert clone["total_qty"] == 36
    assert clone["write_status"] == "none"

    # assigning requires the center to be in the zone
    r = client.post(
        f"/api/v1/order-lists/{clone['id']}/assign",
        json={"zone_id": zone1.id, "center_id": dallas.id},
        headers=admin,
    )
    assert r.status_code == 422

    # coordinators can't touch admin verbs
    for verb, payload in [("assign", {"zone_id": zone1.id, "center_id": austin.id}), ("clone", None)]:
        resp = client.post(
            f"/api/v1/order-lists/{clone['id']}/{verb}", json=payload, headers=lili
        )
        assert resp.status_code == 403, verb


def test_return_flow(client, db, settings_env):
    zone1, _, austin, _, copper, incense = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")
    ol = _make_pending(client, db, admin, zone1, austin, copper, incense)

    r = client.post(
        f"/api/v1/order-lists/{ol['id']}/return",
        json={"note": "Austin still has plenty of incense — drop it?"},
        headers=lili,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "returned"

    # returned lists are editable and re-assignable
    r = client.put(
        f"/api/v1/order-lists/{ol['id']}/lines",
        json={"lines": [{"product_id": copper.id, "qty": 12}]},
        headers=admin,
    )
    assert r.status_code == 200
    r = client.post(
        f"/api/v1/order-lists/{ol['id']}/assign",
        json={"zone_id": zone1.id, "center_id": austin.id},
        headers=admin,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending_approval"
    assert body["returned_note"] == ""  # note cleared on reassignment


def test_approve_simulated_when_writes_off(client, db, settings_env):
    """Kill switch off -> approval succeeds with an honest 'simulated' badge
    and the full payload in the audit log."""
    zone1, _, austin, _, copper, incense = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")

    # map the center via the fixture stock sync (III/CityCenter/Austin)
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(settings_env.fixtures_path, read_only=True)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")
    db.expire_all()
    assert db.get(Center, austin.id).odoo_location_id == (
        settings_env._test_expectations["austin_location_id"]
    )

    ol = _make_pending(client, db, admin, zone1, austin, copper, incense)
    assert ol["center_mapped"] is True

    r = client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=lili)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["write_status"] == "simulated"
    assert body["write_dry_run_reason"] == "kill_switch"
    assert body["write_reference"].startswith("ILAPP-OL-")
    assert body["odoo_picking_id"] is None
    assert body["approved_by"]

    # audited as a dry-run with the exact payload
    audit = db.scalar(select(OdooWriteAudit).order_by(OdooWriteAudit.id.desc()))
    assert audit.operation == "create_internal_transfer"
    assert audit.dry_run is True
    dest_ids = {m[2]["location_dest_id"] for m in audit.request_payload["move_ids"]}
    assert dest_ids == {settings_env._test_expectations["austin_location_id"]}

    # approving an already-approved list is a conflict
    assert (
        client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=lili).status_code
        == 409
    )


def test_approve_live_and_failed_paths(client, db, live_env, monkeypatch):
    zone1, _, austin, _, copper, incense = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")
    mik = login(client, "mik@test.io")

    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=True)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")

    sim_rw = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim_rw
    )
    set_flag(db, "write_create_internal_transfer", True)

    ol = _make_pending(client, db, admin, zone1, austin, copper, incense)

    # only the right zone's coordinator may approve
    assert (
        client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=mik).status_code
        == 403
    )

    r = client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=lili)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["write_status"] == "created"
    assert body["odoo_picking_id"] is not None
    assert body["odoo_picking_name"].startswith("III/INT/")
    assert "stock.picking" in body["odoo_url"]

    # the draft in "Odoo" targets the center's location with both lines
    [picking] = sim_rw.search_read(
        "stock.picking", [["origin", "=", body["write_reference"]]], ["state", "location_dest_id"]
    )
    assert picking["state"] == "draft"
    assert picking["location_dest_id"] == live_env._test_expectations["austin_location_id"]
    moves = sim_rw.search_read(
        "stock.move", [["picking_id", "=", body["odoo_picking_id"]]], ["product_uom_qty"]
    )
    assert sorted(m["product_uom_qty"] for m in moves) == [12, 24]


def test_approve_failed_write_keeps_list_pending(client, db, live_env, monkeypatch):
    zone1, _, austin, _, copper, incense = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")

    from app.odoo.errors import OdooError
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=True)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    set_flag(db, "write_create_internal_transfer", True)

    class ExplodingConn(OdooSimulator):
        def _create(self, model, vals):
            raise OdooError("ValidationError: something Odoo didn't like")

    boom = ExplodingConn(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: boom
    )

    ol = _make_pending(client, db, admin, zone1, austin, copper, incense)
    r = client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=lili)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending_approval"  # NOT approved
    assert body["write_status"] == "failed"
    assert "didn't like" in body["write_error"]
    reference = body["write_reference"]

    # the retry reuses the reference once Odoo behaves again
    ok = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: ok
    )
    r = client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=lili)
    body = r.json()
    assert body["status"] == "approved"
    assert body["write_status"] == "created"
    assert body["write_reference"] == reference
    assert body["write_error"] == ""


def test_approve_unmapped_center_is_a_clear_422(client, db, settings_env):
    zone1, _, austin, _, copper, incense = _setup(db)
    admin = login(client, "admin@test.io")
    lili = login(client, "lili@test.io")
    # no stock sync ran -> Austin has no Odoo location mapped
    ol = _make_pending(client, db, admin, zone1, austin, copper, incense)
    assert ol["center_mapped"] is False
    r = client.post(f"/api/v1/order-lists/{ol['id']}/approve", json={}, headers=lili)
    assert r.status_code == 422
    assert "no Odoo location mapped" in r.json()["detail"]
