"""Phase 3 — city-center & department ordering.

The acceptance spine: an orderer places from their center's granted catalog
(with honest availability), the coordinator approves (draft transfer to the
center's Odoo location, deep link included), the orderer is notified, SHIPPED
is detected when the warehouse validates, and a department's water order
flows end-to-end without ever touching Odoo.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.center_orders.catalog import availability_for, expected_back_label
from app.models import (
    IncomingMove,
    Notification,
    OdooWriteAudit,
    OrderList,
    OrderListCenter,
    OrderListLine,
    OrderListZone,
    Role,
    StockLevel,
    User,
    ZoneKind,
    utcnow,
)
from app.odoo.simulator import OdooSimulator
from sqlalchemy import select

from .util import login, mk_center, mk_product, mk_user, mk_zone, set_flag

TODAY = date(2026, 7, 13)


# ---------------------------------------------------------------- fixtures
def _setup(db):
    """A field zone (Austin + coordinator + orderer) and the departments zone
    (Kitchen + liaison + dept orderer), with one granted starter list."""
    zone = mk_zone(db, "Zone 1 (Test)")
    other_zone = mk_zone(db, "Zone 2 (Test)")
    dept_zone = mk_zone(db, "III Departments", kind=ZoneKind.DEPARTMENTS.value)

    austin = mk_center(db, "Austin", zone_id=zone.id)
    boston = mk_center(db, "Boston", zone_id=other_zone.id)
    kitchen = mk_center(db, "Kitchen", zone_id=dept_zone.id)

    copper = mk_product(db, "CA0023000009", "Copper Water Bottle", odoo_id=201, price=25)
    incense = mk_product(db, "IN0000000777", "Sandalwood Incense", odoo_id=203, price=8)
    rudraksha = mk_product(db, "RU0000000555", "Rudraksha Mala", odoo_id=205, price=40)
    water = mk_product(db, "MAN-WATER", "Spring Water 24-Pack",
                       source="manual", stock_tracked=False, price=6.5)
    toothpaste = mk_product(db, "TP0000000888", "Neem Toothpaste", odoo_id=207, price=5)
    for p in (water, toothpaste):
        p.dept_orderable = True
    db.add_all([
        StockLevel(product_id=copper.id, location_key="bwhse", qty=40),
        StockLevel(product_id=incense.id, location_key="bwhse", qty=3),
        StockLevel(product_id=rudraksha.id, location_key="bwhse", qty=0),
        StockLevel(product_id=toothpaste.id, location_key="floor", qty=12),
        # Relative to the REAL today, not the frozen fixture date: the catalog
        # endpoint labels an ETA against utcnow(), so a date pinned near TODAY
        # quietly slides into the past and the "expected back" label vanishes.
        # (It did — this test started failing on its own in August 2026.)
        IncomingMove(odoo_move_id=1, product_id=rudraksha.id, qty=24,
                     expected_date=utcnow().date() + timedelta(days=32), state="assigned"),
    ])

    starter = OrderList(name="Starter kit")
    db.add(starter)
    db.flush()
    for pos, p in enumerate((copper, incense, rudraksha)):
        db.add(OrderListLine(order_list_id=starter.id, product_id=p.id, position=pos))
    db.add(OrderListZone(order_list_id=starter.id, zone_id=zone.id))
    db.add(OrderListCenter(order_list_id=starter.id, center_id=austin.id))
    db.commit()

    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, austin.id))
    mk_user(db, "coord@test.io", (Role.ZONE_COORDINATOR, zone.id, None))
    mk_user(db, "othercoord@test.io", (Role.ZONE_COORDINATOR, other_zone.id, None))
    # departments people are ordinary requesters/reviewers whose scope is the
    # III Departments review zone (the dept roles merged 2026-08-13)
    mk_user(db, "kitchen@test.io", (Role.CENTER_ORDERER, None, kitchen.id))
    mk_user(db, "liaison@test.io", (Role.ZONE_COORDINATOR, dept_zone.id, None))
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    return {
        "zone": zone, "austin": austin, "boston": boston, "kitchen": kitchen,
        "copper": copper, "incense": incense, "rudraksha": rudraksha,
        "water": water, "toothpaste": toothpaste, "starter": starter,
    }


def _place(client, headers, center_id, lines, notes=""):
    return client.post(
        "/api/v1/center-orders",
        json={"center_id": center_id, "notes": notes, "lines": lines},
        headers=headers,
    )


def _sync_locations(db, settings):
    """Map III/CityCenter/<name> locations onto centers, like every real
    deployment does (seed and worker both run the stock sync)."""
    from app.sync.runner import run_domain

    sim = OdooSimulator(settings.fixtures_path, read_only=True)
    run_domain(db, settings, "products", conn=sim, trigger="manual")
    run_domain(db, settings, "stock", conn=sim, trigger="manual")


# ------------------------------------------------------- availability (pure)
def test_expected_back_labels():
    assert expected_back_label(date(2026, 8, 14), TODAY) == "expected back mid-August"
    assert expected_back_label(date(2026, 8, 3), TODAY) == "expected back early August"
    assert expected_back_label(date(2026, 8, 28), TODAY) == "expected back late August"
    assert expected_back_label(None, TODAY) == "no restock scheduled yet"
    assert "overdue" in expected_back_label(date(2026, 6, 20), TODAY)


def test_availability_states(db):
    p = mk_product(db, "X1", "Tracked", odoo_id=1)
    out = availability_for(product=p, on_hand=0, incoming=[(24, date(2026, 8, 15))],
                           low_threshold=4, today=TODAY)
    assert out.status == "out" and out.incoming_label == "expected back mid-August"
    assert out.incoming_qty == 24

    low = availability_for(product=p, on_hand=3, incoming=[], low_threshold=4, today=TODAY)
    assert low.status == "low" and low.low_count_caveat is True

    ok = availability_for(product=p, on_hand=40, incoming=[], low_threshold=4, today=TODAY)
    assert ok.status == "in" and ok.low_count_caveat is False

    manual = mk_product(db, "X2", "Untracked", source="manual", stock_tracked=False)
    un = availability_for(product=manual, on_hand=None, incoming=[], low_threshold=4, today=TODAY)
    assert un.status == "untracked" and un.qty is None


# ------------------------------------------------------------------ catalog
def test_catalog_is_the_granted_menu_with_oos_timeline(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    r = client.get(f"/api/v1/center-orders/catalog?center_id={s['austin'].id}", headers=orderer)
    assert r.status_code == 200, r.text
    cat = r.json()
    assert cat["source_key"] == "bwhse"
    skus = {i["sku"]: i for i in cat["items"]}
    assert set(skus) == {"CA0023000009", "IN0000000777", "RU0000000555"}  # the list, nothing else
    assert skus["CA0023000009"]["availability"]["status"] == "in"
    assert skus["IN0000000777"]["availability"]["status"] == "low"
    assert skus["IN0000000777"]["availability"]["low_count_caveat"] is True
    oos = skus["RU0000000555"]["availability"]
    assert oos["status"] == "out" and "expected back" in oos["incoming_label"]

    # the other zone's coordinator can't even read Austin's menu
    other = login(client, "othercoord@test.io")
    r = client.get(f"/api/v1/center-orders/catalog?center_id={s['austin'].id}", headers=other)
    assert r.status_code == 403


def test_clothing_is_curatable_but_never_purchasable(client, db):
    """Noah's 2026-07-26 call: catalogs are hand-curated menus — clothing
    (sarees, kurtas) CAN be put on a list and ordered by centers. The
    original out-of-scope rule survives only in PURCHASING (the India
    engine's candidate pool)."""
    from app.ordering.inputs import import_candidates

    s = _setup(db)
    shirt = mk_product(db, "CL0023000001", "Kurta Shirt",
                       category="Isha Life USA / Clothing & Accessories", odoo_id=299)
    db.add(StockLevel(product_id=shirt.id, location_key="bwhse", qty=8))
    db.commit()
    # curated onto a list…
    admin = login(client, "admin@test.io")
    r = client.put(
        f"/api/v1/order-lists/{s['starter'].id}/lines",
        json={"product_ids": [s["copper"].id, shirt.id]},
        headers=admin,
    )
    assert r.status_code == 200, r.text

    # …it reaches the center's menu and can be placed
    orderer = login(client, "orderer@test.io")
    cat = client.get(
        f"/api/v1/center-orders/catalog?center_id={s['austin'].id}", headers=orderer
    ).json()
    assert any(i["sku"] == "CL0023000001" for i in cat["items"])
    r = _place(client, orderer, s["austin"].id, [{"product_id": shirt.id, "qty": 1}])
    assert r.status_code == 201, r.text

    # …but the India purchasing engine still refuses clothing outright
    assert shirt.id not in {p.id for p in import_candidates(db)}


def test_dept_catalog_serves_dept_orderable_from_the_floor(client, db):
    s = _setup(db)
    kitchen_h = login(client, "kitchen@test.io")
    r = client.get(f"/api/v1/center-orders/catalog?center_id={s['kitchen'].id}", headers=kitchen_h)
    assert r.status_code == 200, r.text
    cat = r.json()
    assert cat["source_key"] == "floor"
    assert cat["center"]["zone_kind"] == "departments"
    skus = {i["sku"]: i for i in cat["items"]}
    assert set(skus) == {"MAN-WATER", "TP0000000888"}  # dept-orderable only
    assert skus["MAN-WATER"]["untracked"] is True
    assert skus["MAN-WATER"]["availability"]["status"] == "untracked"
    assert skus["TP0000000888"]["availability"]["qty"] == 12  # floor stock, not bwhse


# ----------------------------------------------------------------- placing
def test_place_order_pending_with_reasonability_and_coordinator_ping(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    r = _place(client, orderer, s["austin"].id,
               [{"product_id": s["copper"].id, "qty": 60}], notes="festival")
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "pending"
    assert order["display_name"] == f"ORD-{order['id']}"
    assert order["placement"]["status"] == "none"  # nothing in Odoo until approval
    # 60 > 40 on hand → the rules flagged it
    assert order["reasonability_level"] == "warn"
    badges = order["lines"][0]["badges"]
    assert any(b["code"] == "exceeds_stock" for b in badges)
    # the zone's coordinator was pinged (simulated — flags off), the other zone's wasn't
    notes = db.scalars(select(Notification)).all()
    assert len(notes) == 1
    assert notes[0].kind == "order_placed"
    assert notes[0].status == "simulated"
    assert any(e["kind"] == "notify" and "simulated" in e["note"].lower()
               for e in order["events"])


def test_placement_enforces_the_granted_catalog(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    # water is dept-orderable but NOT on Austin's granted lists
    r = _place(client, orderer, s["austin"].id, [{"product_id": s["water"].id, "qty": 2}])
    assert r.status_code == 422 and "catalog" in r.json()["detail"]
    # another center entirely
    r = _place(client, orderer, s["boston"].id, [{"product_id": s["copper"].id, "qty": 1}])
    assert r.status_code == 403
    # a center with no grants gets a helpful message
    bare = mk_center(db, "Bare", zone_id=s["zone"].id)
    coord = login(client, "coord@test.io")
    r = _place(client, coord, bare.id, [{"product_id": s["copper"].id, "qty": 1}])
    assert r.status_code == 422 and "no catalog granted" in r.json()["detail"]


# ------------------------------------------------------ approve / adjust / …
def test_approve_simulated_draft_notifies_orderer(client, db, settings_env):
    s = _setup(db)
    _sync_locations(db, settings_env)  # Austin gets its III/CityCenter location
    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id,
                 [{"product_id": s["copper"].id, "qty": 6}]).json()["id"]

    # the other zone's coordinator can't approve it
    other = login(client, "othercoord@test.io")
    r = client.post(f"/api/v1/center-orders/{oid}/approve", json={}, headers=other)
    assert r.status_code == 403

    r = client.post(f"/api/v1/center-orders/{oid}/approve",
                    json={"note": "looks good"}, headers=coord)
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["status"] == "approved"
    assert order["placement"]["status"] == "simulated"  # write flag off
    assert order["decided_by"] == "coord"
    approved = db.scalars(
        select(Notification).where(Notification.kind == "order_approved")
    ).all()
    assert len(approved) == 1 and approved[0].status == "simulated"
    # double-approval is a 409, not a second draft
    r = client.post(f"/api/v1/center-orders/{oid}/approve", json={}, headers=coord)
    assert r.status_code == 409


def test_approve_for_unmapped_field_center_fails_loudly(client, db, settings_env):
    """A field center whose name matches no III/CityCenter location can't
    render a transfer — the approval succeeds app-side but the picking outcome
    is an actionable FAILED, not a masking dry-run (an admin must fix the
    mapping either way)."""
    s = _setup(db)
    _sync_locations(db, settings_env)
    db.refresh(s["austin"])
    s["austin"].odoo_location_id = None  # the name-mismatch case
    db.commit()
    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id,
                 [{"product_id": s["copper"].id, "qty": 6}]).json()["id"]
    r = client.post(f"/api/v1/center-orders/{oid}/approve", json={}, headers=coord)
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["status"] == "approved"
    assert order["placement"]["status"] == "failed"
    assert "no odoo location" in order["placement"]["error"].lower()


def test_adjust_quantities_zero_lines_and_approve(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id, [
        {"product_id": s["copper"].id, "qty": 10},
        {"product_id": s["incense"].id, "qty": 5},
    ]).json()["id"]

    # standalone adjust while pending
    r = client.put(f"/api/v1/center-orders/{oid}/lines",
                   json=[{"product_id": s["copper"].id, "qty": 8}], headers=coord)
    assert r.status_code == 200, r.text
    lines = {ln["sku"]: ln for ln in r.json()["lines"]}
    assert lines["CA0023000009"]["qty_approved"] == 8
    assert lines["CA0023000009"]["qty_final"] == 8
    assert lines["IN0000000777"]["qty_final"] == 5  # untouched

    # orderers can't adjust
    r = client.put(f"/api/v1/center-orders/{oid}/lines",
                   json=[{"product_id": s["copper"].id, "qty": 1}], headers=orderer)
    assert r.status_code == 403

    # adjust-and-approve in one motion, zeroing the incense line
    r = client.post(f"/api/v1/center-orders/{oid}/approve",
                    json={"lines": [{"product_id": s["incense"].id, "qty": 0}]},
                    headers=coord)
    assert r.status_code == 200, r.text
    order = r.json()
    assert order["status"] == "approved"
    lines = {ln["sku"]: ln for ln in order["lines"]}
    assert lines["IN0000000777"]["qty_final"] == 0
    assert order["totals"]["units"] == 8


def test_approve_with_everything_zeroed_is_rejected(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id,
                 [{"product_id": s["copper"].id, "qty": 4}]).json()["id"]
    r = client.post(f"/api/v1/center-orders/{oid}/approve",
                    json={"lines": [{"product_id": s["copper"].id, "qty": 0}]},
                    headers=coord)
    assert r.status_code == 422 and "reject" in r.json()["detail"].lower()


def test_reject_needs_a_reason_and_notifies(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id,
                 [{"product_id": s["copper"].id, "qty": 4}]).json()["id"]
    r = client.post(f"/api/v1/center-orders/{oid}/reject", json={}, headers=coord)
    assert r.status_code == 422
    r = client.post(f"/api/v1/center-orders/{oid}/reject",
                    json={"note": "hold until after the festival"}, headers=coord)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    rejected = db.scalars(
        select(Notification).where(Notification.kind == "order_rejected")
    ).all()
    assert len(rejected) == 1
    assert "hold until after the festival" in rejected[0].body


def test_orderer_cancels_own_pending_but_not_other_centers(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id,
                 [{"product_id": s["copper"].id, "qty": 4}]).json()["id"]
    r = client.post(f"/api/v1/center-orders/{oid}/cancel", json={}, headers=orderer)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    # …and once cancelled, the coordinator can't approve it
    r = client.post(f"/api/v1/center-orders/{oid}/approve", json={}, headers=coord)
    assert r.status_code == 409

    # kitchen's orderer can't even see Austin's orders
    oid2 = _place(client, orderer, s["austin"].id,
                  [{"product_id": s["copper"].id, "qty": 2}]).json()["id"]
    kitchen_h = login(client, "kitchen@test.io")
    r = client.get(f"/api/v1/center-orders/{oid2}", headers=kitchen_h)
    assert r.status_code == 403


def test_lists_are_scoped_by_role(client, db):
    s = _setup(db)
    orderer = login(client, "orderer@test.io")
    kitchen_h = login(client, "kitchen@test.io")
    coord = login(client, "coord@test.io")
    _place(client, orderer, s["austin"].id, [{"product_id": s["copper"].id, "qty": 4}])
    _place(client, kitchen_h, s["kitchen"].id, [{"product_id": s["water"].id, "qty": 2}])

    assert {o["center_name"] for o in
            client.get("/api/v1/center-orders", headers=orderer).json()} == {"Austin"}
    assert {o["center_name"] for o in
            client.get("/api/v1/center-orders", headers=kitchen_h).json()} == {"Kitchen"}
    # the field coordinator sees their zone, not the departments
    assert {o["center_name"] for o in
            client.get("/api/v1/center-orders", headers=coord).json()} == {"Austin"}
    # pending filter serves the approvals board
    pending = client.get("/api/v1/center-orders?status=pending", headers=coord).json()
    assert all(o["status"] == "pending" for o in pending)


# ------------------------------------------- the department water acceptance
def test_dept_water_order_flows_end_to_end_without_odoo(client, db):
    s = _setup(db)
    kitchen_h = login(client, "kitchen@test.io")
    liaison = login(client, "liaison@test.io")
    r = _place(client, kitchen_h, s["kitchen"].id,
               [{"product_id": s["water"].id, "qty": 3}], notes="weekly water")
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["source_location_key"] == "floor"
    assert order["lines"][0]["untracked"] is True

    r = client.post(f"/api/v1/center-orders/{order['id']}/approve",
                    json={"note": "ok"}, headers=liaison)
    assert r.status_code == 200, r.text
    approved = r.json()
    assert approved["status"] == "approved"
    assert approved["placement"]["status"] == "none"  # legitimately no Odoo record
    assert any("no odoo transfer" in e["note"].lower() for e in approved["events"])
    # and truly nothing hit the writer — no audit rows at all
    assert db.scalar(select(OdooWriteAudit).limit(1)) is None


# ----------------------------------------------- live path (simulator-backed)
def test_approve_live_renders_draft_to_center_location_then_ships(
    client, db, live_env, monkeypatch
):
    """Approval creates the DRAFT picking BWHSE → III/CityCenter/Austin with a
    deep link; when 'the warehouse' validates it in Odoo, the next read flips
    the order to SHIPPED and the orderer is notified."""
    s = _setup(db)
    from app.sync.runner import run_domain

    sim_ro = OdooSimulator(live_env.fixtures_path, read_only=True)
    run_domain(db, live_env, "products", conn=sim_ro, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim_ro, trigger="manual")
    db.refresh(s["austin"])
    assert s["austin"].odoo_location_id, "stock sync should map III/CityCenter/Austin"

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    monkeypatch.setattr(
        "app.odoo.writer.get_connection", lambda settings, read_only=False: sim
    )
    monkeypatch.setattr(
        "app.center_orders.service.get_connection", lambda settings, read_only=True: sim
    )
    set_flag(db, "write_create_internal_transfer", True)
    monkeypatch.setenv("ORDER_SHIPPED_POLL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()

    orderer = login(client, "orderer@test.io")
    coord = login(client, "coord@test.io")
    oid = _place(client, orderer, s["austin"].id, [
        {"product_id": s["copper"].id, "qty": 6},
        {"product_id": s["incense"].id, "qty": 2},
    ]).json()["id"]

    r = client.post(f"/api/v1/center-orders/{oid}/approve", json={}, headers=coord)
    assert r.status_code == 200, r.text
    order = r.json()
    placement = order["placement"]
    assert placement["status"] == "created"
    assert placement["picking_name"].startswith("III/")
    assert "stock.picking" in placement["url"]
    assert placement["reference"].startswith("ILAPP-ORD-")

    [picking] = sim.search_read(
        "stock.picking", [["id", "=", placement["picking_id"]]],
        ["state", "location_dest_id", "origin"],
    )
    assert picking["state"] == "draft"  # the app NEVER validates
    dest = picking["location_dest_id"]
    dest_id = dest[0] if isinstance(dest, list) else dest
    assert dest_id == s["austin"].odoo_location_id

    # warehouse validates in Odoo → the next detail read detects it
    sim.call_kw("stock.picking", "write", [[placement["picking_id"]], {"state": "done"}])
    r = client.get(f"/api/v1/center-orders/{oid}", headers=orderer)
    assert r.status_code == 200
    assert r.json()["status"] == "shipped"
    shipped = db.scalars(
        select(Notification).where(Notification.kind == "order_shipped")
    ).all()
    assert len(shipped) == 1


# -------------------------------- the "Approve dept orders" add-on role
def _dept_approver(db, email="shopteam@test.io"):
    """A shop team member who also holds the add-on — the real shape: a role
    that does a job, plus the one extra permission."""
    return mk_user(
        db, email,
        (Role.SHOPPE_FLOOR, None, None),
        (Role.DEPT_ORDER_APPROVER, None, None),
    )


def test_the_add_on_approves_a_department_order(client, db):
    s = _setup(db)
    _dept_approver(db)
    kitchen_h = login(client, "kitchen@test.io")
    order = _place(client, kitchen_h, s["kitchen"].id,
                   [{"product_id": s["water"].id, "qty": 3}]).json()
    assert order["status"] == "pending"  # departments are reviewed, like everyone

    approver = login(client, "shopteam@test.io")
    seen = client.get(f"/api/v1/center-orders/{order['id']}", headers=approver).json()
    assert seen["actions"]["can_approve"] is True
    r = client.post(f"/api/v1/center-orders/{order['id']}/approve",
                    json={"note": "took it off the shelf"}, headers=approver)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


def test_the_add_on_reaches_departments_and_nothing_else(client, db):
    """It grants one job. A field center's order is still its Order
    Reviewer's, and the holder can't place orders for a department either."""
    s = _setup(db)
    # no SEE_EVERYTHING role here, or the scope question can't be asked
    mk_user(db, "deptonly@test.io", (Role.DEPT_ORDER_APPROVER, None, None))
    orderer = login(client, "orderer@test.io")
    field_order = _place(client, orderer, s["austin"].id,
                         [{"product_id": s["copper"].id, "qty": 2}]).json()
    kitchen_h = login(client, "kitchen@test.io")
    dept_order = _place(client, kitchen_h, s["kitchen"].id,
                        [{"product_id": s["water"].id, "qty": 1}]).json()

    approver = login(client, "deptonly@test.io")
    # the departments order is visible and approvable…
    visible = {o["id"] for o in client.get("/api/v1/center-orders", headers=approver).json()}
    assert dept_order["id"] in visible and field_order["id"] not in visible
    # …the field one is neither
    assert client.get(f"/api/v1/center-orders/{field_order['id']}",
                      headers=approver).status_code == 403
    assert client.post(f"/api/v1/center-orders/{field_order['id']}/approve",
                       json={}, headers=approver).status_code == 403
    # and approving is not ordering
    r = _place(client, approver, s["kitchen"].id, [{"product_id": s["water"].id, "qty": 1}])
    assert r.status_code == 403


def test_a_department_order_pings_the_add_on_holders(client, db):
    """The liaison is no longer the one who approves these, so the ping has to
    reach whoever does — otherwise the order waits for someone who isn't
    looking."""
    s = _setup(db)
    _dept_approver(db)
    kitchen_h = login(client, "kitchen@test.io")
    _place(client, kitchen_h, s["kitchen"].id, [{"product_id": s["water"].id, "qty": 2}])

    told = {
        db.get(User, n.recipient_user_id).email
        for n in db.scalars(select(Notification).where(Notification.kind == "order_placed"))
    }
    assert "shopteam@test.io" in told
    assert "liaison@test.io" in told  # still the review zone's coordinator


def test_a_field_order_never_pings_a_dept_approver(client, db):
    s = _setup(db)
    _dept_approver(db)
    orderer = login(client, "orderer@test.io")
    _place(client, orderer, s["austin"].id, [{"product_id": s["copper"].id, "qty": 2}])

    told = {
        db.get(User, n.recipient_user_id).email
        for n in db.scalars(select(Notification).where(Notification.kind == "order_placed"))
    }
    assert told == {"coord@test.io"}
