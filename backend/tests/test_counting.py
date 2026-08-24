"""Inventory counting: count → submit → review → recount → apply.

The rules worth locking down are the ones a future edit would quietly break:
the original count is never overwritten, the Odoo quantity is captured by the
SERVER at submit time, a reason is mandatory on reject/recount, recounts
outrank first counts in the queue, mixed outcomes roll up honestly, and only
an approval touches Odoo.
"""
from __future__ import annotations

from app.models import Product, Role, RoleAssignment, StockLevel, User
from app.odoo.errors import OdooWriteError
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
    """Approval is the only path that writes.

    With `write_validate_inventory_adjustment` OFF — the shipped default —
    it writes a DRAFT and stops, which is what every other write in the app
    does. The twin test below covers the flag being on."""
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
    # a DRAFT: with the posting flag off, a human still validates it
    state = sim.search_read("stock.picking", [["name", "=", item["picking_name"]]], ["state"])
    assert state and state[0]["state"] == "draft"
    assert approved.json()["status"] == "completed"


def test_approve_posts_the_adjustment_when_the_flag_is_on(client, db, live_env, monkeypatch):
    """The 2026-08-22 change: a reviewer's approval IS the validation.

    Counting is the app's one exception to draft-only, because the judgement
    a human would apply at the Validate button — is this counted number
    right? — is the judgement the reviewer just made."""
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr("app.odoo.writer.get_connection", lambda settings, read_only=False: sim)
    monkeypatch.setattr(
        "app.counting.locations.get_connection", lambda settings, read_only=True: sim
    )
    for flag in (
        "write_create_inventory_addition",
        "write_create_inventory_reduction",
        "write_validate_inventory_adjustment",
    ):
        set_flag(db, flag, True)
    _people(db)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")

    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()["quantities"][str(product.id)]
    count = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [{"product_id": product.id, "counted_qty": odoo_now + 6}],
        },
        headers=floor,
    ).json()

    approved = client.post(
        f"/api/v1/counts/items/{count['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert approved.status_code == 200, approved.text
    item = approved.json()["items"][0]
    assert item["picking_status"] == "validated"
    assert item["picking_name"] and item["picking_url"]  # the link is still recorded
    state = sim.search_read("stock.picking", [["name", "=", item["picking_name"]]], ["state"])
    assert state and state[0]["state"] == "done"


def test_a_failed_post_keeps_the_approval_and_the_draft(client, db, live_env, monkeypatch):
    """An approval is a DECISION. If Odoo refuses the posting, the item stays
    approved with its draft and the reason — the pre-2026-08-22 outcome — and
    never 422s the review away."""
    from app.odoo.simulator import OdooSimulator
    from app.odoo.writer import OdooWriter
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr("app.odoo.writer.get_connection", lambda settings, read_only=False: sim)
    monkeypatch.setattr(
        "app.counting.locations.get_connection", lambda settings, read_only=True: sim
    )
    for flag in (
        "write_create_inventory_addition",
        "write_create_inventory_reduction",
        "write_validate_inventory_adjustment",
    ):
        set_flag(db, flag, True)

    def boom(self, **kwargs):
        raise OdooWriteError("Odoo said no")

    monkeypatch.setattr(OdooWriter, "validate_adjustment", boom)
    _people(db)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")

    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()["quantities"][str(product.id)]
    count = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [{"product_id": product.id, "counted_qty": odoo_now + 2}],
        },
        headers=floor,
    ).json()

    approved = client.post(
        f"/api/v1/counts/items/{count['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert approved.status_code == 200, approved.text
    item = approved.json()["items"][0]
    assert item["status"] == "approved"  # the decision survives
    assert item["picking_status"] == "created"  # the draft stands
    assert "Odoo said no" in item["picking_error"]


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


# ------------------------------------------- "somebody just counted this"
def test_stock_at_warns_when_the_same_thing_is_already_counted(client, db, settings_env):
    """The 2026-08-22 duplicates in miniature: two people walk the same rack,
    and the second one is told before they write a number down."""
    _people(db)
    p = mk_product(db, "IN0000000010", "Incense-Stick-Rose", odoo_id=310)
    _stock(db, p, "floor", 9)
    first = login(client, "floorteam@test.io")
    client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 3}]},
        headers=first,
    )

    second = login(client, "wh@test.io")
    r = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [p.id]},
        headers=second,
    )
    assert r.status_code == 200, r.text
    warn = r.json()["recent"][str(p.id)]
    assert warn["applied"] is False  # the hazard: it hasn't reached Odoo
    assert warn["counted_qty"] == 3
    assert "floorteam" in warn["counted_by"]
    assert "not applied to Odoo yet" in warn["note"]


def test_a_settled_count_is_context_not_a_warning(client, db, live_env, monkeypatch):
    """Once it has reached Odoo the collision is gone — the system quantity
    already includes it — so it's said differently, and it fades after a week.

    "Applied" means ODOO HEARD IT, not that a reviewer approved it: with the
    posting flag off an approval leaves a draft, and a draft is still going to
    move stock against the old number. Hence the flag here."""
    from datetime import timedelta

    from app.counting import recent
    from app.models import utcnow
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr("app.odoo.writer.get_connection", lambda settings, read_only=False: sim)
    monkeypatch.setattr(
        "app.counting.locations.get_connection", lambda settings, read_only=True: sim
    )
    for flag in (
        "write_create_inventory_addition",
        "write_create_inventory_reduction",
        "write_validate_inventory_adjustment",
    ):
        set_flag(db, flag, True)
    _people(db)
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")

    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()["quantities"][str(product.id)]
    count = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [{"product_id": product.id, "counted_qty": odoo_now + 3}],
        },
        headers=floor,
    ).json()
    client.post(f"/api/v1/counts/items/{count['items'][0]['id']}/approve", json={}, headers=flow)

    warn = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=login(client, "wh@test.io"),
    ).json()["recent"][str(product.id)]
    assert warn["applied"] is True
    assert "already applied" in warn["note"]

    # …and an old settled count isn't worth mentioning at all
    stale = utcnow() - timedelta(days=recent.RECENT_DAYS + 1)
    from app.models import InventoryCountEntry

    for e in db.scalars(sa_select(InventoryCountEntry)):
        e.created_at = stale
    db.commit()
    again = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=login(client, "wh@test.io"),
    ).json()
    assert again["recent"] == {}


def test_a_count_never_warns_about_itself(client, db, settings_env):
    _people(db)
    p = mk_product(db, "IN0000000011", "Incense-Stick-Jasmine", odoo_id=311)
    _stock(db, p, "floor", 5)
    floor = login(client, "floorteam@test.io")
    count = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 4}]},
        headers=floor,
    ).json()
    item = client.get(f"/api/v1/counts/{count['id']}", headers=floor).json()["items"][0]
    assert item["also_counted"] is None


def test_the_reviewer_sees_the_other_count_before_approving(client, db, settings_env):
    """Where the stock actually moves. Two submissions, same product, same
    location: each one's review screen names the other."""
    _people(db)
    p = mk_product(db, "IN0000000012", "Incense-Stick-Amber", odoo_id=312)
    _stock(db, p, "floor", 9)
    a = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 3}]},
        headers=login(client, "floorteam@test.io"),
    ).json()
    b = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 6}]},
        headers=login(client, "wh@test.io"),
    ).json()

    flow = login(client, "flow@test.io")
    queue = {i["id"]: i for i in client.get("/api/v1/counts/queue", headers=flow).json()}
    a_item, b_item = a["items"][0]["id"], b["items"][0]["id"]
    assert queue[a_item]["also_counted"]["count_id"] == b["id"]
    assert queue[a_item]["also_counted"]["counted_qty"] == 6
    assert queue[b_item]["also_counted"]["count_id"] == a["id"]
    assert queue[b_item]["also_counted"]["counted_qty"] == 3
    # advisory only — the reviewer can still approve
    assert client.post(
        f"/api/v1/counts/items/{a_item}/approve", json={}, headers=flow
    ).status_code == 200


def test_a_rejected_count_is_not_worth_warning_about(client, db, settings_env):
    """A reviewer threw it out and Odoo never heard it — there is nothing to
    collide with."""
    _people(db)
    p = mk_product(db, "IN0000000013", "Incense-Stick-Cedar", odoo_id=313)
    _stock(db, p, "floor", 8)
    count = client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 2}]},
        headers=login(client, "floorteam@test.io"),
    ).json()
    rejected = client.post(
        f"/api/v1/counts/items/{count['items'][0]['id']}/reject",
        json={"note": "miscounted the shelf"},
        headers=login(client, "flow@test.io"),
    )
    assert rejected.status_code == 200, rejected.text

    r = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [p.id]},
        headers=login(client, "wh@test.io"),
    ).json()
    assert r["recent"] == {}


def test_the_warning_is_per_location(client, db, settings_env):
    """Counting the warehouse says nothing about the floor — different
    shelves, different numbers."""
    _people(db)
    p = mk_product(db, "IN0000000014", "Incense-Stick-Vetiver", odoo_id=314)
    _stock(db, p, "floor", 4)
    _stock(db, p, "bwhse", 40)
    client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": p.id, "counted_qty": 2}]},
        headers=login(client, "floorteam@test.io"),
    )
    wh = login(client, "wh@test.io")
    assert client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "bwhse", "product_ids": [p.id]},
        headers=wh,
    ).json()["recent"] == {}
    assert client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [p.id]},
        headers=wh,
    ).json()["recent"] != {}


# ------------------------------------ the baseline is re-read at apply time
def _live_env(db, live_env, monkeypatch):
    """Products + locations synced, writes pointed at a writable simulator."""
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain

    sim = OdooSimulator(live_env.fixtures_path, read_only=False)
    run_domain(db, live_env, "products", conn=sim, trigger="manual")
    run_domain(db, live_env, "stock", conn=sim, trigger="manual")
    monkeypatch.setattr("app.odoo.writer.get_connection", lambda settings, read_only=False: sim)
    monkeypatch.setattr(
        "app.counting.locations.get_connection", lambda settings, read_only=True: sim
    )
    # the move-ledger read goes through its own module
    monkeypatch.setattr(
        "app.counting.ledger.get_connection", lambda settings, read_only=True: sim
    )
    for flag in (
        "write_create_inventory_addition",
        "write_create_inventory_reduction",
        "write_validate_inventory_adjustment",
    ):
        set_flag(db, flag, True)
    return sim


def _count(client, headers, product_id, qty):
    return client.post(
        "/api/v1/counts",
        json={"location_key": "floor", "items": [{"product_id": product_id, "counted_qty": qty}]},
        headers=headers,
    ).json()


def test_two_counts_of_one_shelf_cannot_both_be_applied(client, db, live_env, monkeypatch):
    """The 2026-08-22 bug, reproduced: two people count the same product, each
    measuring against the same Odoo number. The first approval applies. The
    second is REFUSED — its difference would come off a number that already
    includes the first, landing on a quantity nobody counted."""
    _live_env(db, live_env, monkeypatch)
    _people(db)
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    flow = login(client, "flow@test.io")
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=login(client, "floorteam@test.io"),
    ).json()["quantities"][str(product.id)]

    a = _count(client, login(client, "floorteam@test.io"), product.id, odoo_now - 6)
    b = _count(client, login(client, "wh@test.io"), product.id, odoo_now - 3)

    first = client.post(
        f"/api/v1/counts/items/{a['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert first.status_code == 200, first.text
    assert first.json()["items"][0]["picking_status"] == "validated"

    second = client.post(
        f"/api/v1/counts/items/{b['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert second.status_code == 422
    detail = second.json()["detail"]
    assert "has changed since this count was taken" in detail
    assert "ask for a recount" in detail
    # and it names WHY: the ledger shows the correction already happened
    assert "already been made" in detail

    # refused, not half-done: the item is still open and Odoo untouched
    item = client.get(f"/api/v1/counts/{b['id']}", headers=flow).json()["items"][0]
    assert item["status"] == "pending"
    assert item["picking_status"] == "none"
    live = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=flow,
    ).json()["quantities"][str(product.id)]
    assert live == odoo_now - 6  # the first count's number, not 9 less both


def test_the_recount_after_a_refusal_applies_cleanly(client, db, live_env, monkeypatch):
    """The way out of the refusal: count it again. The fresh entry captures
    what Odoo says NOW, so its difference is measured against the truth."""
    _live_env(db, live_env, monkeypatch)
    _people(db)
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    flow = login(client, "flow@test.io")
    floor = login(client, "floorteam@test.io")
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()["quantities"][str(product.id)]

    a = _count(client, floor, product.id, odoo_now - 6)
    b = _count(client, login(client, "wh@test.io"), product.id, odoo_now - 3)
    client.post(f"/api/v1/counts/items/{a['items'][0]['id']}/approve", json={}, headers=flow)
    item_id = b["items"][0]["id"]
    assert client.post(
        f"/api/v1/counts/items/{item_id}/approve", json={}, headers=flow
    ).status_code == 422

    # the reviewer asks for another trip to the shelf…
    asked = client.post(
        f"/api/v1/counts/items/{item_id}/request-recount",
        json={"note": "another count says something else — please look again"},
        headers=flow,
    )
    assert asked.status_code == 200, asked.text
    # …it comes back agreeing with the first count's result
    recounted = client.post(
        f"/api/v1/counts/items/{item_id}/recount",
        json={"counted_qty": odoo_now - 6},
        headers=login(client, "wh@test.io"),
    )
    assert recounted.status_code == 200, recounted.text
    done = client.post(f"/api/v1/counts/items/{item_id}/approve", json={}, headers=flow)
    assert done.status_code == 200, done.text
    applied = done.json()["items"][0]
    # it matches Odoo now, so there is honestly nothing to adjust
    assert applied["status"] == "approved"
    assert applied["picking_status"] == "none"


def test_a_count_odoo_has_caught_up_with_is_approved_with_nothing_to_do(
    client, db, live_env, monkeypatch
):
    """Drift that lands on the counted number needs no adjustment and must not
    be refused — the shelf and Odoo now agree, which is the goal."""
    _live_env(db, live_env, monkeypatch)
    _people(db)
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    flow = login(client, "flow@test.io")
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=login(client, "floorteam@test.io"),
    ).json()["quantities"][str(product.id)]

    a = _count(client, login(client, "floorteam@test.io"), product.id, odoo_now - 4)
    b = _count(client, login(client, "wh@test.io"), product.id, odoo_now - 4)  # same answer
    client.post(f"/api/v1/counts/items/{a['items'][0]['id']}/approve", json={}, headers=flow)

    second = client.post(
        f"/api/v1/counts/items/{b['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert second.status_code == 200, second.text
    item = second.json()["items"][0]
    assert item["status"] == "approved"
    assert item["picking_status"] == "none"  # nothing written, and that's correct
    assert any("already shows" in e["note"] for e in item["events"])


def test_bulk_approval_skips_the_stale_row_and_keeps_the_rest(
    client, db, live_env, monkeypatch
):
    """One stale item must not throw away the other decisions — it is left
    open with the reason, and everything else is approved."""
    _live_env(db, live_env, monkeypatch)
    _people(db)
    products = db.scalars(
        sa_select(Product).where(Product.odoo_product_id.in_([201, 203]))
    ).all()
    stale, fine = products[0], products[1]
    flow = login(client, "flow@test.io")
    floor = login(client, "floorteam@test.io")
    qtys = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [stale.id, fine.id]},
        headers=floor,
    ).json()["quantities"]

    first = _count(client, floor, stale.id, qtys[str(stale.id)] - 5)
    both = client.post(
        "/api/v1/counts",
        json={
            "location_key": "floor",
            "items": [
                {"product_id": stale.id, "counted_qty": qtys[str(stale.id)] - 2},
                {"product_id": fine.id, "counted_qty": qtys[str(fine.id)] + 3},
            ],
        },
        headers=login(client, "wh@test.io"),
    ).json()
    client.post(f"/api/v1/counts/items/{first['items'][0]['id']}/approve", json={}, headers=flow)

    r = client.post(f"/api/v1/counts/{both['id']}/approve", json={}, headers=flow)
    assert r.status_code == 200, r.text
    by_product = {i["product_id"]: i for i in r.json()["items"]}
    assert by_product[fine.id]["status"] == "approved"
    assert by_product[fine.id]["picking_status"] == "validated"
    assert by_product[stale.id]["status"] == "pending"  # left for a human
    assert r.json()["status"] != "completed"


def test_a_snapshot_fallback_is_not_treated_as_drift():
    """Only a LIVE read can prove the baseline moved. When Odoo is quiet the
    fallback is the last sync, which is behind for reasons that have nothing
    to do with this count — blocking on it would stop approvals every time
    Odoo hiccuped."""
    from app.counting import service

    assert service.Baseline(captured=9, live=3, source="snapshot", counted=6).drifted is False
    assert service.Baseline(captured=9, live=3, source="live", counted=6).drifted is True
    # and drift that lands ON the counted number is settled, not a refusal
    assert service.Baseline(captured=9, live=6, source="live", counted=6).settled is True


def test_an_unposted_draft_from_another_count_blocks_too(client, db, live_env, monkeypatch):
    """The collision a live read cannot see. With the posting flag off an
    approval leaves a DRAFT: stock hasn't moved, so nothing has drifted — and
    that draft is still going to move it. Approving a second count of the same
    shelf would queue a second difference against the same starting number."""
    _live_env(db, live_env, monkeypatch)
    set_flag(db, "write_validate_inventory_adjustment", False)  # drafts, as before
    _people(db)
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    flow = login(client, "flow@test.io")
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=login(client, "floorteam@test.io"),
    ).json()["quantities"][str(product.id)]

    a = _count(client, login(client, "floorteam@test.io"), product.id, odoo_now - 6)
    b = _count(client, login(client, "wh@test.io"), product.id, odoo_now - 3)

    first = client.post(
        f"/api/v1/counts/items/{a['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert first.json()["items"][0]["picking_status"] == "created"  # a draft, not posted

    second = client.post(
        f"/api/v1/counts/items/{b['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert second.status_code == 422
    detail = second.json()["detail"]
    assert "hasn't posted in Odoo yet" in detail
    assert f"Count #{a['id']}" in detail  # names the one to settle first


def test_a_sale_since_the_count_does_not_block_the_approval(client, db, live_env, monkeypatch):
    """The whole point of reading the ledger. The shelf sold two units between
    the count and the review, so Odoo's number moved — but the counter's
    finding is untouched by that, and the adjustment applies on top of it.

    Blocking here would be the strict-but-wrong answer: sales happen all day,
    and a count reviewed in the afternoon would never be approvable."""
    sim = _live_env(db, live_env, monkeypatch)
    _people(db)
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()["quantities"][str(product.id)]

    count = _count(client, floor, product.id, odoo_now + 4)  # found 4 more than Odoo had

    # …then two sell off the floor, exactly as Odoo would record it
    sim.tables.setdefault("stock.move.line", []).append(
        {
            "id": 90001,
            "date": "2099-01-01 00:00:00",
            "state": "done",
            "quantity": 2,
            "product_id": [201, "Copper Water Bottle"],
            "location_id": [14, "III/Stock/III-FLOOR"],
            "location_dest_id": [32, "Partner Locations/Customers"],  # usage: customer
            "reference": "III/POS/00001",
        }
    )
    for q in sim.tables["stock.quant"]:
        if q["product_id"][0] == 201 and q["location_id"][0] == 14:
            q["quantity"] = float(q["quantity"]) - 2

    approved = client.post(
        f"/api/v1/counts/items/{count['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert approved.status_code == 200, approved.text
    item = approved.json()["items"][0]
    assert item["picking_status"] == "validated"  # applied, not refused
    # and the history says why it applied despite the number having moved
    assert any("2 sold" in e["note"] for e in item["events"]), [e["note"] for e in item["events"]]


def test_the_customer_location_is_what_makes_it_a_sale(client, db, live_env, monkeypatch):
    """Classification is Odoo's own `stock.location.usage`, not picking-type
    names: `inventory` is a correction, `customer` a sale. Same movement to an
    INVENTORY location is a correction and blocks."""
    sim = _live_env(db, live_env, monkeypatch)
    _people(db)
    product = db.scalars(sa_select(Product).where(Product.odoo_product_id == 201)).first()
    floor = login(client, "floorteam@test.io")
    flow = login(client, "flow@test.io")
    odoo_now = client.post(
        "/api/v1/counts/stock-at",
        json={"location_key": "floor", "product_ids": [product.id]},
        headers=floor,
    ).json()["quantities"][str(product.id)]
    count = _count(client, floor, product.id, odoo_now + 4)

    sim.tables.setdefault("stock.move.line", []).append(
        {
            "id": 90002,
            "date": "2099-01-01 00:00:00",
            "state": "done",
            "quantity": 2,
            "product_id": [201, "Copper Water Bottle"],
            "location_id": [14, "III/Stock/III-FLOOR"],
            # the adjustment virtual location — usage 'inventory'
            "location_dest_id": [31, "Virtual Locations/USA-III: Inventory adjustment"],
            "reference": "III/IAM/09999",
        }
    )
    for q in sim.tables["stock.quant"]:
        if q["product_id"][0] == 201 and q["location_id"][0] == 14:
            q["quantity"] = float(q["quantity"]) - 2

    r = client.post(
        f"/api/v1/counts/items/{count['items'][0]['id']}/approve", json={}, headers=flow
    )
    assert r.status_code == 422
    assert "III/IAM/09999" in r.json()["detail"]
