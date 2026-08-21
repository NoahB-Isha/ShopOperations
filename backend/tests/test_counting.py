"""Inventory counting: count → submit → review → recount → apply.

The rules worth locking down are the ones a future edit would quietly break:
the original count is never overwritten, the Odoo quantity is captured by the
SERVER at submit time, a reason is mandatory on reject/recount, recounts
outrank first counts in the queue, mixed outcomes roll up honestly, and only
an approval touches Odoo.
"""
from __future__ import annotations

from app.models import Product, Role, RoleAssignment, StockLevel, User
from sqlalchemy import select as sa_select

from .util import login, mk_product, mk_user, set_flag


def _people(db):
    mk_user(db, "flow@test.io", (Role.SHOPPE_FLOOR, None, None))  # Inventory Flow Manager
    mk_user(db, "floorteam@test.io", (Role.FLOOR_ROTATING, None, None))
    mk_user(db, "wh@test.io", (Role.WAREHOUSE, None, None))
    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, None))


def _stock(db, product, key, qty):
    row = db.scalars(
        sa_select(StockLevel).where(
            StockLevel.product_id == product.id, StockLevel.location_key == key
        )
    ).first()
    if row is None:
        row = StockLevel(product_id=product.id, location_key=key, qty=0)
        db.add(row)
    row.qty = qty
    db.commit()


def test_locations_default_per_role(client, db, settings_env):
    """Where each role starts, per the spec's table."""
    _people(db)
    flow = login(client, "flow@test.io")
    floor = login(client, "floorteam@test.io")
    wh = login(client, "wh@test.io")

    assert client.get("/api/v1/counts/locations", headers=flow).json()["default"] == "floor"
    assert client.get("/api/v1/counts/locations", headers=floor).json()["default"] == "floor"
    # the Warehouse Team stands at SHIP
    assert client.get("/api/v1/counts/locations", headers=wh).json()["default"] == "ship"
    # only the Inventory Flow Manager (of these three) may review
    assert client.get("/api/v1/counts/locations", headers=flow).json()["can_review"] is True
    assert client.get("/api/v1/counts/locations", headers=floor).json()["can_review"] is False


def test_counting_is_not_open_to_everyone(client, db, settings_env):
    _people(db)
    orderer = login(client, "orderer@test.io")
    assert client.get("/api/v1/counts/locations", headers=orderer).status_code == 403


def test_submit_captures_the_odoo_quantity_itself(client, db, settings_env):
    """The browser sends what was COUNTED; the server decides what Odoo said.
    That number is the evidence the reviewer judges against, so a client must
    not be able to choose it."""
    _people(db)
    incense = mk_product(db, "IN0000000001", "Incense-Stick-Sandal", odoo_id=301)
    _stock(db, incense, "floor", 39)
    floor = login(client, "floorteam@test.io")

    r = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": incense.id, "counted_qty": 42}]},
        headers=floor,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    item = body["items"][0]
    assert item["counted_qty"] == 42
    assert item["odoo_qty"] == 39  # from the app's own records, not the payload
    assert item["delta"] == 3
    assert item["attempts"] == 1
    assert item["counted_by"] != "unknown"
    assert body["status"] == "pending"


def test_a_product_appears_once_per_submission(client, db, settings_env):
    _people(db)
    p = mk_product(db, "IN0000000002", "Incense-Stick-Lotus", odoo_id=302)
    _stock(db, p, "floor", 10)
    floor = login(client, "floorteam@test.io")
    r = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [
                {"product_id": p.id, "counted_qty": 5},
                {"product_id": p.id, "counted_qty": 8},  # same product again
            ],
        },
        headers=floor,
    )
    assert r.status_code == 201, r.text
    items = r.json()["items"]
    assert len(items) == 1  # merged, not duplicated
    assert items[0]["counted_qty"] == 8  # the later quantity wins


def test_reject_needs_a_reason_and_never_touches_odoo(client, db, settings_env):
    _people(db)
    p = mk_product(db, "IN0000000003", "Sambrani in Cup", odoo_id=303)
    _stock(db, p, "floor", 4)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")
    count = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 99}]},
        headers=floor,
    ).json()
    item_id = count["items"][0]["id"]

    bare = client.post(f"/api/v1/counts/items/{item_id}/reject", json={}, headers=flow)
    assert bare.status_code == 422 and "reason is required" in bare.json()["detail"]

    ok = client.post(
        f"/api/v1/counts/items/{item_id}/reject",
        json={"note": "counted the display, not the shelf"},
        headers=flow,
    )
    assert ok.status_code == 200
    item = ok.json()["items"][0]
    assert item["status"] == "rejected"
    assert item["picking_status"] == "none"  # nothing written
    assert item["applied_qty"] is None
    assert ok.json()["status"] == "completed"  # the only item is decided
    assert any(e["kind"] == "rejected" for e in item["events"])


def test_recount_appends_and_never_overwrites(client, db, settings_env):
    """Original → Recount 1 → Recount 2, all three readable side by side."""
    _people(db)
    p = mk_product(db, "IN0000000004", "Incense-Stick-Jasmine", odoo_id=304)
    _stock(db, p, "floor", 15)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")
    count = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 12}]},
        headers=floor,
    ).json()
    item_id = count["items"][0]["id"]

    # a recount can only go to someone who may count

    floor_user = db.scalars(sa_select(User).where(User.email == "floorteam@test.io")).first()
    orderer = db.scalars(sa_select(User).where(User.email == "orderer@test.io")).first()
    bad = client.post(
        f"/api/v1/counts/items/{item_id}/request-recount",
        json={"note": "please look again", "assignee_id": orderer.id},
        headers=flow,
    )
    assert bad.status_code == 422 and "isn't allowed to perform counts" in bad.json()["detail"]

    asked = client.post(
        f"/api/v1/counts/items/{item_id}/request-recount",
        json={"note": "12 vs 15 — check the back shelf", "assignee_id": floor_user.id},
        headers=flow,
    )
    assert asked.status_code == 200
    assert asked.json()["items"][0]["status"] == "recount_requested"
    assert asked.json()["status"] == "recount_required"

    # the assignee sees it on their list
    mine = client.get("/api/v1/counts/my-recounts", headers=floor).json()
    assert [m["id"] for m in mine] == [item_id]

    # and performs it
    again = client.post(
        f"/api/v1/counts/items/{item_id}/recount", json={"counted_qty": 10}, headers=floor
    )
    assert again.status_code == 200, again.text
    item = again.json()["items"][0]
    assert item["status"] == "pending"  # back in the queue
    assert item["attempts"] == 2
    assert [e["counted_qty"] for e in item["entries"]] == [12, 10]  # original kept
    assert [e["attempt"] for e in item["entries"]] == [1, 2]
    assert item["entries"][1]["reason"].startswith("12 vs 15")
    assert item["counted_qty"] == 10  # the count that stands

    # a second recount stacks on top
    client.post(
        f"/api/v1/counts/items/{item_id}/request-recount",
        json={"note": "one more pair of eyes", "assignee_id": floor_user.id},
        headers=flow,
    )
    third = client.post(
        f"/api/v1/counts/items/{item_id}/recount", json={"counted_qty": 10}, headers=floor
    ).json()
    assert [e["counted_qty"] for e in third["items"][0]["entries"]] == [12, 10, 10]


def test_recounts_outrank_first_counts_in_the_queue(client, db, settings_env):
    _people(db)
    a = mk_product(db, "IN0000000005", "First count", odoo_id=305)
    b = mk_product(db, "IN0000000006", "Recounted twice", odoo_id=306)
    _stock(db, a, "floor", 5)
    _stock(db, b, "floor", 5)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")


    floor_user = db.scalars(sa_select(User).where(User.email == "floorteam@test.io")).first()

    # b is counted FIRST (lower id) but goes round the recount loop
    first = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": b.id, "counted_qty": 1}]},
        headers=floor,
    ).json()
    b_item = first["items"][0]["id"]
    client.post(
        f"/api/v1/counts/items/{b_item}/request-recount",
        json={"note": "double-check", "assignee_id": floor_user.id},
        headers=flow,
    )
    client.post(f"/api/v1/counts/items/{b_item}/recount", json={"counted_qty": 2}, headers=floor)

    # a is a plain first-time count, submitted later
    client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": a.id, "counted_qty": 4}]},
        headers=floor,
    )

    queue = client.get("/api/v1/counts/queue", headers=flow).json()
    assert queue[0]["id"] == b_item, "the recounted item should lead the queue"
    assert queue[0]["attempts"] == 2


def test_approve_applies_the_count_to_odoo_as_a_draft(client, db, live_env, monkeypatch):
    """Approval is the only path that writes, and it writes a DRAFT."""
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr("app.odoo.writer.get_connection", lambda settings, read_only=False: sim)
    for mod in ("locations",):
        monkeypatch.setattr(
            f"app.counting.{mod}.get_connection", lambda settings, read_only=True: sim
        )
    set_flag(db, "write_create_inventory_addition", True)
    set_flag(db, "write_create_inventory_reduction", True)
    _people(db)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")


    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    stock_at = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()
    odoo_now = stock_at["quantities"][str(product.id)]

    count = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [{"product_id": product.id, "counted_qty": odoo_now + 6}],
        },
        headers=floor,
    ).json()
    item_id = count["items"][0]["id"]

    approved = client.post(f"/api/v1/counts/items/{item_id}/approve", json={}, headers=flow)
    assert approved.status_code == 200, approved.text
    item = approved.json()["items"][0]
    assert item["status"] == "approved"
    assert item["applied_qty"] == odoo_now + 6
    assert item["picking_status"] == "created"
    assert item["picking_name"] and item["picking_url"]
    # a DRAFT: the app never validates a stock move
    state = sim.search_read("stock.picking", [["name", "=", item["picking_name"]]], ["state"])
    assert state and state[0]["state"] == "draft"
    assert approved.json()["status"] == "completed"


def test_mixed_outcomes_roll_up_honestly(client, db, settings_env):
    """7 approved / 1 rejected / 2 recounts is a real submission state."""
    _people(db)
    products = [
        mk_product(db, f"IN000000001{i}", f"Item {i}", odoo_id=400 + i) for i in range(4)
    ]
    for p in products:
        _stock(db, p, "floor", 10)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")


    floor_user = db.scalars(sa_select(User).where(User.email == "floorteam@test.io")).first()

    count = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [{"product_id": p.id, "counted_qty": 9} for p in products],
        },
        headers=floor,
    ).json()
    ids = [i["id"] for i in count["items"]]

    client.post(f"/api/v1/counts/items/{ids[0]}/approve", json={}, headers=flow)
    body = client.post(
        f"/api/v1/counts/items/{ids[1]}/reject", json={"note": "miscounted"}, headers=flow
    ).json()
    assert body["status"] == "partially_reviewed"  # two decided, two not

    body = client.post(
        f"/api/v1/counts/items/{ids[2]}/request-recount",
        json={"note": "verify", "assignee_id": floor_user.id},
        headers=flow,
    ).json()
    # an outstanding recount is the most useful thing to say about it
    assert body["status"] == "recount_required"

    # finish the rest and it completes
    client.post(f"/api/v1/counts/items/{ids[3]}/approve", json={}, headers=flow)
    client.post(f"/api/v1/counts/items/{ids[2]}/recount", json={"counted_qty": 9}, headers=floor)
    r = client.post(f"/api/v1/counts/items/{ids[2]}/approve", json={}, headers=flow)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert [i["status"] for i in body["items"]] == [
        "approved",
        "rejected",
        "approved",
        "approved",
    ]


def test_whole_submission_actions_leave_decided_items_alone(client, db, settings_env):
    """An individual decision outranks a bulk one."""
    _people(db)
    products = [mk_product(db, f"IN00000000{i}2", f"Bulk {i}", odoo_id=500 + i) for i in range(3)]
    for p in products:
        _stock(db, p, "floor", 3)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")
    count = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [{"product_id": p.id, "counted_qty": 3} for p in products],
        },
        headers=floor,
    ).json()
    ids = [i["id"] for i in count["items"]]
    client.post(
        f"/api/v1/counts/items/{ids[0]}/reject", json={"note": "bad count"}, headers=flow
    )

    bare = client.post(f"/api/v1/counts/{count['id']}/reject", json={}, headers=flow)
    assert bare.status_code == 422  # a reason is required here too

    body = client.post(f"/api/v1/counts/{count['id']}/approve", json={}, headers=flow).json()
    statuses = [i["status"] for i in body["items"]]
    assert statuses == ["rejected", "approved", "approved"]
    assert body["status"] == "completed"


def test_a_counter_sees_only_their_own_submissions(client, db, settings_env):
    _people(db)
    p = mk_product(db, "IN0000000099", "Private", odoo_id=599)
    _stock(db, p, "floor", 1)
    floor = login(client, "floorteam@test.io")
    wh = login(client, "wh@test.io")
    count = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 1}]},
        headers=floor,
    ).json()
    assert client.get(f"/api/v1/counts/{count['id']}", headers=wh).status_code == 403
    assert client.get("/api/v1/counts", headers=wh).json() == []
    assert len(client.get("/api/v1/counts", headers=floor).json()) == 1


def test_inventory_wrangler_is_an_add_on_that_grants_review(client, db, settings_env):
    """The add-on gives a warehouse user the review queue without changing
    what they already are."""
    _people(db)
    wh_user = db.scalars(sa_select(User).where(User.email == "wh@test.io")).first()
    wh = login(client, "wh@test.io")
    assert client.get("/api/v1/counts/queue", headers=wh).status_code == 403

    db.add(RoleAssignment(user_id=wh_user.id, role=Role.INVENTORY_WRANGLER.value))
    db.commit()
    wh = login(client, "wh@test.io")  # fresh token: roles ride in it
    assert client.get("/api/v1/counts/queue", headers=wh).status_code == 200
    cfg = client.get("/api/v1/counts/locations", headers=wh).json()
    assert cfg["can_review"] is True
    assert cfg["default"] == "ship"  # still a warehouse user
