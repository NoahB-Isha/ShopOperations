from __future__ import annotations

from app.models import Role

from .util import login, mk_center, mk_user, mk_zone


def _world(db):
    z1 = mk_zone(db, "Zone 1 (Lili)")
    z2 = mk_zone(db, "Zone 2 (Mik)")
    a = mk_center(db, "Austin", z1.id)
    s = mk_center(db, "San Antonio", z1.id)
    c = mk_center(db, "Chicago", z2.id)
    return z1, z2, a, s, c


def test_admin_sees_all_centers(client, db):
    _world(db)
    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    r = client.get("/api/v1/centers", headers=login(client, "admin@t.l"))
    assert {c["name"] for c in r.json()} == {"Austin", "San Antonio", "Chicago"}


def test_coordinator_scoped_to_zone(client, db):
    z1, z2, *_ = _world(db)
    mk_user(db, "lili@t.l", (Role.ZONE_COORDINATOR, z1.id, None))
    r = client.get("/api/v1/centers", headers=login(client, "lili@t.l"))
    assert {c["name"] for c in r.json()} == {"Austin", "San Antonio"}
    zones = client.get("/api/v1/zones", headers=login(client, "lili@t.l")).json()
    assert [z["name"] for z in zones] == ["Zone 1 (Lili)"]


def test_orderer_scoped_to_center(client, db):
    z1, z2, austin, *_ = _world(db)
    mk_user(db, "orderer@t.l", (Role.CENTER_ORDERER, None, austin.id))
    r = client.get("/api/v1/centers", headers=login(client, "orderer@t.l"))
    assert [c["name"] for c in r.json()] == ["Austin"]


def test_warehouse_sees_all_but_no_admin_endpoints(client, db):
    _world(db)
    mk_user(db, "wh@t.l", (Role.WAREHOUSE, None, None))
    headers = login(client, "wh@t.l")
    assert len(client.get("/api/v1/centers", headers=headers).json()) == 3
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/status", headers=headers).status_code == 403


def test_admin_users_crud_and_role_validation(client, db):
    z1, *_ = _world(db)
    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    headers = login(client, "admin@t.l")

    r = client.post(
        "/api/v1/admin/users",
        json={
            "email": "New.Person@Test.Local",
            "display_name": "New Person",
            "roles": [{"role": "zone_coordinator", "zone_id": z1.id}],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "new.person@test.local"

    # coordinator role without a zone is rejected
    r = client.post(
        "/api/v1/admin/users",
        json={"email": "bad@test.local", "roles": [{"role": "zone_coordinator"}]},
        headers=headers,
    )
    assert r.status_code == 422

    # neither email nor phone is rejected
    r = client.post("/api/v1/admin/users", json={"display_name": "No Contact"}, headers=headers)
    assert r.status_code == 422

    # duplicate email is rejected
    r = client.post("/api/v1/admin/users", json={"email": "new.person@test.local"}, headers=headers)
    assert r.status_code == 409
