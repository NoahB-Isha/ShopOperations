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


def test_centers_carry_map_coordinates(client, db):
    """Every center the roster names should land somewhere on the map; the
    ones the gazetteer doesn't know fall back to their state, and a center
    with neither simply has no position (it stays on the list, off the map)."""
    _world(db)
    from app.models import Center

    db.add(Center(name="Nowhere-in-Particular", city="?", state="", is_active=True))
    db.commit()
    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    rows = {c["name"]: c for c in client.get("/api/v1/centers", headers=login(client, "admin@t.l")).json()}

    austin = rows["Austin"]
    assert austin["latitude"] is not None and austin["longitude"] is not None
    assert 29 < austin["latitude"] < 31 and -99 < austin["longitude"] < -96  # central Texas
    assert rows["Nowhere-in-Particular"]["latitude"] is None


def test_center_detail_names_the_reviewer_the_requester_and_the_shelf(client, db, settings_env):
    """The map panel's whole content: who reviews, who orders, what's there."""
    z1, _, austin, *_ = _world(db)
    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    mk_user(db, "lili@t.l", (Role.ZONE_COORDINATOR, z1.id, None))
    mk_user(db, "orderer@t.l", (Role.CENTER_ORDERER, None, austin.id))
    # a roster contact with no login still shows — often the only phone number
    from app.models import CenterContact

    db.add(CenterContact(center_id=austin.id, name="Shoppe Volunteer",
                         email="vol@t.l", phone="555-0100", role_note="Shoppe"))
    db.commit()

    r = client.get(f"/api/v1/centers/{austin.id}/detail", headers=login(client, "admin@t.l"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["email"] for p in body["reviewers"]] == ["lili@t.l"]
    assert [p["email"] for p in body["requesters"]] == ["orderer@t.l"]
    assert [p["name"] for p in body["contacts"]] == ["Shoppe Volunteer"]
    # no Odoo location mapped in this fixture -> say so, don't imply empty shelves
    assert body["stock_status"] == "unmapped"
    assert body["stock"] == [] and body["stock_note"]

    # scoping holds: an orderer at Austin can't read Chicago's panel
    chicago = {c["name"]: c for c in
               client.get("/api/v1/centers", headers=login(client, "admin@t.l")).json()}["Chicago"]
    orderer = login(client, "orderer@t.l")
    assert client.get(f"/api/v1/centers/{austin.id}/detail", headers=orderer).status_code == 200
    assert client.get(f"/api/v1/centers/{chicago['id']}/detail", headers=orderer).status_code == 404


def test_center_sales_compare_two_complete_months(client, db):
    """Dot size and the up/down arrow both come from this.

    City centers are pop-ups that set up about once a month, so the comparison
    is month over month — one setup against the previous one — and BOTH months
    are complete. Including the current month would show every center
    collapsing on the 3rd and recovered on the 30th.
    """
    from datetime import date

    from app.centers.sales import comparison_months, sales_by_center
    from app.models import SalesCenterMonthly

    _, _, austin, san_antonio, chicago = _world(db)
    today = date(2026, 8, 15)
    (ly, lm), (py, pm) = comparison_months(today)
    assert (ly, lm) == (2026, 7) and (py, pm) == (2026, 6)  # never August

    db.add_all([
        SalesCenterMonthly(config_name="Austin", center_id=austin.id, year=2026, month=7,
                           units=120, amount=2400),
        SalesCenterMonthly(config_name="Austin", center_id=austin.id, year=2026, month=6,
                           units=80, amount=1600),
        # a center whose only month is the one in progress — not a trend yet
        SalesCenterMonthly(config_name="SA", center_id=san_antonio.id, year=2026, month=8,
                           units=999, amount=9990),
        # sold last month, nothing before: growth from zero, not a missing center
        SalesCenterMonthly(config_name="Chicago", center_id=chicago.id, year=2026, month=7,
                           units=15, amount=300),
    ])
    db.commit()

    by_center = sales_by_center(db, today)
    assert by_center[austin.id].units == 120 and by_center[austin.id].prev_units == 80
    assert by_center[chicago.id].units == 15 and by_center[chicago.id].prev_units == 0
    assert san_antonio.id not in by_center  # August is not evidence of anything

    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    rows = {c["name"]: c for c in
            client.get("/api/v1/centers", headers=login(client, "admin@t.l")).json()}
    assert rows["Austin"]["sales_units"] == 120
    assert rows["Austin"]["sales_prev_units"] == 80
    assert rows["Austin"]["sales_month"] and rows["Austin"]["sales_prev_month"]
    # a center the rollup has never seen says so, rather than claiming a zero
    assert rows["San Antonio"]["sales_units"] is None


def test_admin_edits_a_center_and_its_roster(client, db):
    """The roster lives in the app now — the spreadsheet is something you bring
    to it. This is how a name, a zone or a phone number actually gets fixed."""
    z1, z2, austin, *_ = _world(db)
    from app.models import CenterContact

    db.add(CenterContact(center_id=austin.id, name="Old Volunteer", email="old@t.l",
                         phone="555-0000", role_note="Shoppe"))
    db.commit()
    mk_user(db, "admin@t.l", (Role.ADMIN, None, None))
    headers = login(client, "admin@t.l")

    r = client.patch(
        f"/api/v1/centers/{austin.id}",
        json={
            "city": "Austin",
            "state": "Texas",
            "zone_id": z2.id,
            "is_active": False,
            "stripe_terminal_name": "WPC-Austin-2",
            "contacts": [
                {"name": "New Volunteer", "email": "New@T.L", "phone": "555-0111",
                 "role_note": "Shoppe"},
                {"name": "", "email": "", "phone": "", "role_note": ""},  # blank row
            ],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "Texas" and body["zone_name"] == "Zone 2 (Mik)"
    assert body["is_active"] is False and body["activity_raw"] == "No"
    assert body["stripe_terminal_name"] == "WPC-Austin-2"
    # the roster is REPLACED, blank rows aren't people, and email is normalised
    assert [(c["name"], c["email"]) for c in body["contacts"]] == [("New Volunteer", "new@t.l")]

    # a center can be unassigned from every zone, which null alone can't say
    r = client.patch(f"/api/v1/centers/{austin.id}", json={"clear_zone": True}, headers=headers)
    assert r.json()["zone_id"] is None

    # names stay unique, and a non-admin can't edit at all
    r = client.patch(f"/api/v1/centers/{austin.id}", json={"name": "Chicago"}, headers=headers)
    assert r.status_code == 409
    mk_user(db, "floor2@t.l", (Role.SHOPPE_FLOOR, None, None))
    assert client.patch(f"/api/v1/centers/{austin.id}", json={"city": "Nope"},
                        headers=login(client, "floor2@t.l")).status_code == 403
    assert z1 is not None
