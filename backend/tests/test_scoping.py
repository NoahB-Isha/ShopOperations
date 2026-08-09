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


def test_admin_can_edit_contact_and_roles(client, db):
    """The Users-page edit flow: change contact details and swap the whole role
    set in one PATCH, including multi-role users."""
    z1, z2, c1, *_ = _world(db)
    mk_user(db, "admin2@t.l", (Role.ADMIN, None, None))
    headers = login(client, "admin2@t.l")

    target = mk_user(db, "before@test.local", (Role.WAREHOUSE, None, None))
    tid = target.id

    # contact info: changed, and normalized on the way in
    r = client.patch(
        f"/api/v1/admin/users/{tid}",
        json={"display_name": "Renamed", "email": "After.Person@Test.Local", "phone": "(512) 555-0100"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Renamed"
    assert body["email"] == "after.person@test.local"
    assert body["phone"] == "+15125550100"

    # roles are a full replacement — a user can end up holding two at once
    r = client.patch(
        f"/api/v1/admin/users/{tid}",
        json={
            "roles": [
                {"role": "zone_coordinator", "zone_id": z1.id},
                {"role": "center_orderer", "center_id": c1.id},
            ]
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert {rr["role"] for rr in r.json()["roles"]} == {"zone_coordinator", "center_orderer"}

    # ...and replacing again drops the ones left out
    r = client.patch(
        f"/api/v1/admin/users/{tid}",
        json={"roles": [{"role": "zone_coordinator", "zone_id": z2.id}]},
        headers=headers,
    )
    assert [rr["zone_id"] for rr in r.json()["roles"]] == [z2.id]

    # a scoped role still needs its scope
    r = client.patch(
        f"/api/v1/admin/users/{tid}", json={"roles": [{"role": "center_orderer"}]}, headers=headers
    )
    assert r.status_code == 422


def test_editing_contact_uses_empty_string_to_clear_not_null(client, db):
    """The UI sends "" to clear a field: null means "leave unchanged", so a
    cleared box must not silently keep the old value. Clearing BOTH is refused —
    a user with no contact could never receive a sign-in code."""
    _world(db)
    mk_user(db, "admin3@t.l", (Role.ADMIN, None, None))
    headers = login(client, "admin3@t.l")

    target = mk_user(db, "both@test.local", (Role.WAREHOUSE, None, None))
    target.phone = "+15125550111"
    db.commit()
    tid = target.id

    # null leaves it alone
    r = client.patch(f"/api/v1/admin/users/{tid}", json={"email": None}, headers=headers)
    assert r.json()["email"] == "both@test.local"

    # "" actually clears it, because the phone still reaches them
    r = client.patch(f"/api/v1/admin/users/{tid}", json={"email": ""}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] is None
    assert r.json()["phone"] == "+15125550111"

    # clearing the last way to reach them is refused
    r = client.patch(f"/api/v1/admin/users/{tid}", json={"phone": ""}, headers=headers)
    assert r.status_code == 422
