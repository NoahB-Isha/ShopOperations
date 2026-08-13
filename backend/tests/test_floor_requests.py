"""Floor Team asks: raised by people who can't create transfers, resolved by
the Inventory Flow Manager on the Suggested items page."""
from __future__ import annotations

from app.models import Role

from .util import login, mk_product, mk_user


def _setup(client, db):
    a = mk_product(db, "CA0000000001", "Neem Powder 100g", odoo_id=901)
    b = mk_product(db, "CA0000000002", "Cotton Wicks", odoo_id=902)
    mk_user(db, "rotating@t.l", (Role.FLOOR_ROTATING, None, None))
    mk_user(db, "floor@t.l", (Role.SHOPPE_FLOOR, None, None))
    mk_user(db, "warehouse@t.l", (Role.WAREHOUSE, None, None))
    db.commit()
    return a, b, login(client, "rotating@t.l"), login(client, "floor@t.l")


def test_floor_team_raises_requests_and_the_manager_sees_them(client, db):
    a, b, rotating, floor = _setup(client, db)

    r = client.post(
        "/api/v1/floor-requests",
        json={"note": "shelf empty by lunch", "lines": [
            {"product_id": a.id, "qty": 6}, {"product_id": b.id, "qty": 12},
        ]},
        headers=rotating,
    )
    assert r.status_code == 201, r.text
    assert {i["name"] for i in r.json()} == {"Neem Powder 100g", "Cotton Wicks"}

    board = client.get("/api/v1/floor-requests", headers=floor).json()
    assert len(board) == 2
    first = board[0]
    assert first["status"] == "open"
    assert first["requested_by"]  # a person's name, not "the app"
    assert first["note"] == "shelf empty by lunch"


def test_every_ask_is_its_own_entry_with_its_own_name(client, db):
    """Two people flagging the same shelf are two entries. Who noticed, and
    how much each thought was needed, is the information."""
    a, _b, rotating, floor = _setup(client, db)
    mk_user(db, "second@t.l", (Role.FLOOR_ROTATING, None, None))
    db.commit()
    other = login(client, "second@t.l")

    client.post("/api/v1/floor-requests", json={"lines": [{"product_id": a.id, "qty": 4}]},
                headers=rotating)
    client.post("/api/v1/floor-requests", json={"lines": [{"product_id": a.id, "qty": 5}]},
                headers=other)

    board = client.get("/api/v1/floor-requests", headers=floor).json()
    assert len(board) == 2
    assert {i["qty"] for i in board} == {4, 5}
    assert len({i["requested_by"] for i in board}) == 2
    assert len({i["id"] for i in board}) == 2  # resolvable one at a time


def test_picking_up_and_dismissing_clear_the_board_and_show_the_outcome(client, db):
    a, b, rotating, floor = _setup(client, db)
    made = client.post(
        "/api/v1/floor-requests",
        json={"lines": [{"product_id": a.id, "qty": 3}, {"product_id": b.id, "qty": 2}]},
        headers=rotating,
    ).json()
    keep, drop = made[0]["id"], made[1]["id"]

    r = client.post(f"/api/v1/floor-requests/{keep}/picked-up", headers=floor)
    assert r.status_code == 200 and r.json()["status"] == "picked_up"
    assert r.json()["resolved_by"]
    client.post(f"/api/v1/floor-requests/{drop}/dismiss", headers=floor)

    assert client.get("/api/v1/floor-requests", headers=floor).json() == []
    # the floor still sees what happened to what they asked for
    mine = client.get(
        "/api/v1/floor-requests",
        params={"mine": True, "status": "open,picked_up,dismissed"},
        headers=rotating,
    ).json()
    assert {i["status"] for i in mine} == {"picked_up", "dismissed"}


def test_the_floor_team_cannot_resolve_its_own_asks(client, db):
    a, _b, rotating, _floor = _setup(client, db)
    made = client.post("/api/v1/floor-requests", json={"lines": [{"product_id": a.id, "qty": 1}]},
                       headers=rotating).json()
    r = client.post(f"/api/v1/floor-requests/{made[0]['id']}/picked-up", headers=rotating)
    assert r.status_code == 403


def test_the_warehouse_is_not_part_of_this_conversation(client, db):
    _setup(client, db)
    warehouse = login(client, "warehouse@t.l")
    assert client.get("/api/v1/floor-requests", headers=warehouse).status_code == 403
