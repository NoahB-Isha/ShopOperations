"""Restock engine vs a hand-computed fixture (the phase-2 acceptance spec).

Timeline (threshold = 4, all POS channel):

  day T-3   A sells 2                     accum: A=2
  day T-2   A sells 1, B sells 5          accum: A=3; B crosses -> FLAG B 5, B=0
  day T-1   A sells 2, B sells 2,         accum: A=5 -> FLAG A 5, A=0; B=2
            C sells 3, D(manual) 10,      C=3 (below); D ignored (untracked)
            E(inactive) 10                E ignored

  floor list at T: [A qty 5 (T-1), B qty 5 (T-2)] — C accumulating quietly.

Back list (window 28d, low cover 7d, target 14d):
  A: 28 units -> avg 1.0/d, floor 3, bwhse 50 -> cover 3.0d,
     suggested = min(50, ceil(14*1.0 - 3)) = 11
  B: 14 units -> avg 0.5/d, floor 10 -> 10 >= 3.5 (a week of cover) -> excluded
  C: avg 1.0/d but bwhse 0 -> excluded
  F: 56 units -> avg 2.0/d, floor 5, bwhse 3 -> cover 2.5d,
     suggested = min(3, ceil(28 - 5)) = 3 (capped by warehouse stock)
  G: 14 units -> avg 0.5/d, floor 0, bwhse 20 -> cover None,
     suggested = min(20, max(1, ceil(7 - 0))) = 7

  order: G (no floor stock), then F (2.5d), then A (3.0d).
"""
from __future__ import annotations

from datetime import UTC, date, timedelta

from app.config import get_settings
from app.models import (
    RestockAccum,
    RestockLine,
    Role,
    SalesDaily,
    StockLevel,
)
from app.restock.engine import back_list, floor_list, fold_floor_restock
from sqlalchemy import select

from .util import login, mk_product, mk_user

T = date(2026, 7, 12)


def _sale(db, product_id: int, day: date, units: float, channel: str = "pos"):
    db.add(SalesDaily(product_id=product_id, day=day, channel=channel, units=units))


def _stock(db, product_id: int, key: str, qty: float):
    db.add(StockLevel(product_id=product_id, location_key=key, qty=qty))


def _has_floor_stock(db, *products, qty: float = 25):
    """The floor list only shows what can actually be carried out: `floor` is
    III/Stock/III-FLOOR, the shop AND its back room, so nothing there means
    nothing to restock the shelf with. Tests about the accumulator still need
    that stock to exist, or every line is (correctly) filtered away."""
    for prod in products:
        db.add(StockLevel(product_id=prod.id, location_key="floor", qty=qty))
    db.commit()


def _fixture_products(db):
    a = mk_product(db, "SKU-A", "Copper Bottle", odoo_id=901)
    b = mk_product(db, "SKU-B", "Incense Pack", odoo_id=902)
    c = mk_product(db, "SKU-C", "Vibhuti Jar", odoo_id=903)
    d = mk_product(db, "SKU-D", "Spring Water", source="manual", stock_tracked=False)
    e = mk_product(db, "SKU-E", "Retired Mala", odoo_id=905)
    e.is_active = False
    db.commit()
    return a, b, c, d, e


def test_floor_fold_matches_hand_computed_fixture(db, settings_env):
    a, b, c, d, e = _fixture_products(db)
    _has_floor_stock(db, a, b, c)
    _sale(db, a.id, T - timedelta(days=3), 2)
    _sale(db, a.id, T - timedelta(days=2), 1)
    _sale(db, b.id, T - timedelta(days=2), 5)
    _sale(db, a.id, T - timedelta(days=1), 2)
    _sale(db, b.id, T - timedelta(days=1), 2)
    _sale(db, c.id, T - timedelta(days=1), 3)
    _sale(db, d.id, T - timedelta(days=1), 10)
    _sale(db, e.id, T - timedelta(days=1), 10)
    # online sales never touch the floor accumulator
    _sale(db, c.id, T - timedelta(days=1), 50, channel="online")
    db.commit()

    settings = get_settings()
    # first-ever fold starts at yesterday — so fold the history day by day
    # exactly as the daily script would have run
    fold_after_t3 = fold_floor_restock(db, settings, T - timedelta(days=2))
    assert fold_after_t3 == 0  # A at 2 — nothing flagged yet
    assert fold_floor_restock(db, settings, T - timedelta(days=1)) == 1  # B flags
    assert fold_floor_restock(db, settings, T) == 1  # A flags

    items = floor_list(db, T)
    got = {(i.product_id): (i.qty, i.flagged_on, i.checked) for i in items}
    assert got == {
        a.id: (5.0, T - timedelta(days=1), False),
        b.id: (5.0, T - timedelta(days=2), False),
    }

    # accumulators left exactly where the script would leave them
    accums = {r.product_id: r.accumulated for r in db.scalars(select(RestockAccum))}
    assert accums[a.id] == 0.0
    assert accums[b.id] == 2.0
    assert accums[c.id] == 3.0
    assert d.id not in accums and e.id not in accums

    # idempotent: folding the same day again changes nothing
    assert fold_floor_restock(db, settings, T) == 0
    assert len(floor_list(db, T)) == 2


def test_flag_merges_into_open_line_and_daily_reset(db, settings_env):
    a, b, *_ = _fixture_products(db)
    _has_floor_stock(db, a, b)
    settings = get_settings()

    _sale(db, a.id, T - timedelta(days=1), 5)
    db.commit()
    fold_floor_restock(db, settings, T)
    [line] = floor_list(db, T)
    assert line.qty == 5.0

    # A crosses again today; its line is still open -> the line grows
    _sale(db, a.id, T, 4)
    db.commit()
    fold_floor_restock(db, settings, T + timedelta(days=1))
    [line] = floor_list(db, T + timedelta(days=1))
    assert line.qty == 9.0
    assert line.flagged_on == T

    # checked lines drop off the list the NEXT day (daily reset), and a fresh
    # crossing then makes a NEW line rather than growing the closed one.
    # Pin the check-off to the story's clock (day T+1), not the real one —
    # utcnow() here made the test fail once the calendar passed T+1.
    row = db.get(RestockLine, line.line_id)
    from datetime import datetime

    row.checked_off_at = datetime(T.year, T.month, T.day, 12, tzinfo=UTC) + timedelta(
        days=1
    )
    db.commit()
    _sale(db, a.id, T + timedelta(days=1), 6)
    db.commit()
    fold_floor_restock(db, settings, T + timedelta(days=2))
    items = floor_list(db, T + timedelta(days=2))
    assert len(items) == 1  # the checked one is history now
    assert items[0].qty == 6.0
    assert not items[0].checked


def test_back_list_matches_hand_computed_fixture(db, settings_env):
    a, b, c, d, e = _fixture_products(db)
    f = mk_product(db, "SKU-F", "Sandal Soap", odoo_id=906)
    g = mk_product(db, "SKU-G", "Neem Comb", odoo_id=907)

    window_start = T - timedelta(days=28)

    def spread(pid: int, total: float, days: int = 28):
        # spread sales evenly across the window (hand-math uses the total)
        per = total / days
        for n in range(days):
            _sale(db, pid, window_start + timedelta(days=n), per)

    spread(a.id, 28)  # avg 1.0
    spread(b.id, 14)  # avg 0.5
    spread(c.id, 28)  # avg 1.0, but no bwhse stock
    spread(f.id, 56)  # avg 2.0
    spread(g.id, 14)  # avg 0.5
    _sale(db, a.id, T, 999)  # today's sales must NOT count toward the average

    _stock(db, a.id, "floor", 3)
    _stock(db, a.id, "bwhse", 50)
    _stock(db, b.id, "floor", 10)
    _stock(db, b.id, "bwhse", 40)
    _stock(db, c.id, "floor", 1)  # bwhse absent
    _stock(db, f.id, "floor", 5)
    _stock(db, f.id, "bwhse", 3)
    _stock(db, g.id, "bwhse", 20)  # no floor stock at all
    db.commit()

    items = back_list(db, get_settings(), T)
    assert [i.product_id for i in items] == [g.id, f.id, a.id]

    by_id = {i.product_id: i for i in items}
    assert by_id[a.id].avg_daily == 1.0
    assert by_id[a.id].days_of_cover == 3.0
    assert by_id[a.id].suggested_qty == 11
    assert by_id[f.id].suggested_qty == 3  # capped by warehouse stock
    assert by_id[f.id].days_of_cover == 2.5
    assert by_id[g.id].days_of_cover is None
    assert by_id[g.id].suggested_qty == 7
    assert all(not i.checked for i in items)


def test_restock_api_roundtrip_and_checkoffs(client, db, settings_env):
    from app.models import utcnow

    today = utcnow().date()
    a, *_ = _fixture_products(db)
    # 12 units yesterday: avg 12/28 ≈ 0.43/day, floor 2 < 3.0 (a week of
    # cover) -> also lands on the back list
    _sale(db, a.id, today - timedelta(days=1), 12)
    _stock(db, a.id, "floor", 2)
    _stock(db, a.id, "bwhse", 90)
    db.commit()

    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))
    headers = login(client, "floor@test.io")

    r = client.get("/api/v1/restock", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["folded_through"] == (today - timedelta(days=1)).isoformat()
    [floor_item] = body["floor"]
    assert floor_item["sku"] == "SKU-A"
    assert floor_item["qty"] == 12.0
    # the row still SHOWS floor-only (restocking the shelf doesn't need the
    # warehouse number), but bwhse_qty rides along for the "request more"
    # swipe, which builds a transfer draft and must quote an honest figure
    assert floor_item["floor_qty"] == 2.0
    assert floor_item["bwhse_qty"] == 90.0
    [back_item] = body["back"]
    assert back_item["product_id"] == a.id
    assert back_item["floor_qty"] == 2.0

    # floor check-off round trip
    r = client.post(
        f"/api/v1/restock/floor/{floor_item['line_id']}/check",
        json={"checked": True},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["checked"] is True
    r = client.get("/api/v1/restock", headers=headers)
    assert r.json()["floor"][0]["checked"] is True  # struck through today, gone tomorrow

    # back check-off round trip (per-day row)
    r = client.post(
        f"/api/v1/restock/back/{a.id}/check", json={"checked": True}, headers=headers
    )
    assert r.status_code == 200
    assert client.get("/api/v1/restock", headers=headers).json()["back"][0]["checked"] is True

    # orderer roles can't see restock
    mk_user(db, "orderer@test.io", (Role.CENTER_ORDERER, None, None))
    other = login(client, "orderer@test.io")
    assert client.get("/api/v1/restock", headers=other).status_code == 403


def test_restock_exclude_flag_removes_items_everywhere(client, db, settings_env):
    """Non-retail POS items (campus meals, prasadam) sell through the same
    registers but never belong on the restock lists — excluded from the
    accumulator, from already-flagged lines, and from the back list."""
    from app.config import get_settings

    a, b, *_ = _fixture_products(db)
    _has_floor_stock(db, b)  # `a` gets its floor row below, with the back-list setup
    meals = mk_product(db, "ODOO-46478", "Adult Meals (Dinner)", odoo_id=908)
    meals.restock_exclude = True
    _sale(db, meals.id, T - timedelta(days=1), 50)  # would flag loudly
    _sale(db, a.id, T - timedelta(days=1), 5)
    # back-list candidates: both sell, both low on floor, both in the warehouse
    for pid in (a.id, meals.id):
        _stock(db, pid, "floor", 1)
        _stock(db, pid, "bwhse", 40)
    db.commit()

    settings = get_settings()
    fold_floor_restock(db, settings, T)
    assert [i.product_id for i in floor_list(db, T)] == [a.id]
    assert [i.product_id for i in back_list(db, settings, T)] == [a.id]

    # excluding AFTER a line was flagged hides it immediately
    b_line_sale = _sale(db, b.id, T, 9)  # noqa: F841 — folded next day
    db.commit()
    fold_floor_restock(db, settings, T + timedelta(days=1))
    assert {i.product_id for i in floor_list(db, T + timedelta(days=1))} == {a.id, b.id}
    b.restock_exclude = True
    db.commit()
    assert {i.product_id for i in floor_list(db, T + timedelta(days=1))} == {a.id}


def test_admin_toggles_restock_exclude_via_catalog(client, db, settings_env):
    a, *_ = _fixture_products(db)
    mk_user(db, "admin@test.io", (Role.ADMIN, None, None))
    admin = login(client, "admin@test.io")
    r = client.patch(
        f"/api/v1/products/{a.id}", json={"restock_exclude": True}, headers=admin
    )
    assert r.status_code == 200 and r.json()["restock_exclude"] is True


def test_floor_reset_wipes_list_and_gives_today_amnesty(client, db, settings_env):
    """'Floor fully stocked' reset: list emptied, counters zeroed, today's
    sales never fold — counting resumes with tomorrow's. Orderers can't."""
    from app.models import Role as R
    from app.models import utcnow

    today = utcnow().date()
    a, b, *_ = _fixture_products(db)
    _has_floor_stock(db, a, b)
    _sale(db, a.id, today - timedelta(days=1), 9)  # flags on the first read
    _sale(db, b.id, today - timedelta(days=1), 2)  # accumulating, below 4
    _sale(db, a.id, today, 50)  # TODAY: must be amnestied by the reset
    db.commit()
    mk_user(db, "floor@test.io", (R.SHOPPE_FLOOR, None, None))
    headers = login(client, "floor@test.io")

    body = client.get("/api/v1/restock", headers=headers).json()
    assert len(body["floor"]) == 1  # A flagged; B quietly at 2

    r = client.post("/api/v1/restock/floor/reset", headers=headers)
    assert r.status_code == 200, r.text
    reset = r.json()
    assert reset["lines_cleared"] == 1
    assert reset["accumulators_zeroed"] >= 2  # A and B both zeroed
    assert reset["meta"]["folded_through"] == today.isoformat()
    assert reset["meta"]["last_reset_by"] == "floor"

    body = client.get("/api/v1/restock", headers=headers).json()
    assert body["floor"] == []
    assert body["meta"]["last_reset_at"] is not None

    # tomorrow's read folds nothing (today was amnestied)…
    from app.config import get_settings
    from app.restock.engine import fold_floor_restock

    assert fold_floor_restock(db, get_settings(), today + timedelta(days=1)) == 0
    # …but a sale TOMORROW folds the day after: counting truly resumed
    _sale(db, b.id, today + timedelta(days=1), 5)
    db.commit()
    assert fold_floor_restock(db, get_settings(), today + timedelta(days=2)) == 1

    # scoped: an orderer can't reset the floor
    mk_user(db, "orderer@test.io", (R.CENTER_ORDERER, None, None))
    r = client.post("/api/v1/restock/floor/reset", headers=login(client, "orderer@test.io"))
    assert r.status_code == 403


def test_floor_list_hides_items_with_nothing_in_the_back(db, settings_env):
    """`floor` is III/Stock/III-FLOOR — the shop and its back room together.
    Zero there means there is nothing to carry out to the shelf, so the line is
    noise on a picking list; that item is the OOS board's problem instead."""
    from app.config import get_settings

    a, b, *_ = _fixture_products(db)
    _has_floor_stock(db, a)  # b deliberately has no floor row at all
    _sale(db, a.id, T - timedelta(days=1), 9)
    _sale(db, b.id, T - timedelta(days=1), 9)
    db.commit()
    fold_floor_restock(db, get_settings(), T)

    # both crossed the threshold, but only the one you can actually restock shows
    assert {i.product_id for i in floor_list(db, T)} == {a.id}

    # ...and it appears the moment stock lands in the back
    _stock(db, b.id, "floor", 4)
    db.commit()
    assert {i.product_id for i in floor_list(db, T)} == {a.id, b.id}


def test_snoozed_line_hides_until_tomorrow_and_keeps_its_qty(client, db, settings_env):
    """Swipe it away for now: gone from today's list, back tomorrow with the
    accumulated quantity intact — deferred, not cancelled."""
    from app.config import get_settings
    from app.models import Role as R
    from app.models import utcnow

    today = utcnow().date()
    a, *_ = _fixture_products(db)
    _has_floor_stock(db, a)
    _sale(db, a.id, today - timedelta(days=1), 9)
    db.commit()
    fold_floor_restock(db, get_settings(), today)
    [line] = floor_list(db, today)
    qty_before = line.qty

    mk_user(db, "floor@test.io", (R.SHOPPE_FLOOR, None, None))
    headers = login(client, "floor@test.io")
    r = client.post(f"/api/v1/restock/floor/{line.line_id}/snooze", json={"snoozed": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["snoozed"] is True

    assert floor_list(db, today) == []                       # gone today
    back = floor_list(db, today + timedelta(days=1))          # back tomorrow
    assert [i.product_id for i in back] == [a.id]
    assert back[0].qty == qty_before                          # nothing lost

    # and it can be pulled back immediately
    r = client.post(f"/api/v1/restock/floor/{line.line_id}/snooze", json={"snoozed": False}, headers=headers)
    assert r.status_code == 200, r.text
    assert [i.product_id for i in floor_list(db, today)] == [a.id]


def test_checked_line_cannot_be_snoozed(client, db, settings_env):
    """Nothing to defer once it's done — and the 409 keeps a stray swipe on a
    struck-through row from resurrecting it tomorrow."""
    from app.config import get_settings
    from app.models import Role as R
    from app.models import utcnow

    today = utcnow().date()
    a, *_ = _fixture_products(db)
    _has_floor_stock(db, a)
    _sale(db, a.id, today - timedelta(days=1), 9)
    db.commit()
    fold_floor_restock(db, get_settings(), today)
    [line] = floor_list(db, today)

    mk_user(db, "floor2@test.io", (R.SHOPPE_FLOOR, None, None))
    headers = login(client, "floor2@test.io")
    client.post(f"/api/v1/restock/floor/{line.line_id}/check", json={"checked": True}, headers=headers)
    r = client.post(f"/api/v1/restock/floor/{line.line_id}/snooze", json={"snoozed": True}, headers=headers)
    assert r.status_code == 409


def test_catch_up_fold_grows_one_line_instead_of_duplicating(db, settings_env):
    """The live duplicate: when one fold covers SEVERAL days and a product
    crosses the threshold on more than one of them, it must grow the open line,
    not add a second. Sessions are autoflush=False, so the line added on the
    first day is invisible to the second day's lookup unless it's flushed —
    that's exactly how Devi Red Pendant Cord ended up on the list twice."""
    from app.config import get_settings
    from app.models import RestockFoldState

    a, *_ = _fixture_products(db)
    _has_floor_stock(db, a)
    # two separate threshold crossings, both inside ONE unfolded stretch.
    # folded_through must be set: the FIRST fold ever deliberately starts at
    # yesterday instead of replaying history, so it can't catch up days.
    db.add(RestockFoldState(id=1, folded_through=T - timedelta(days=4)))
    _sale(db, a.id, T - timedelta(days=3), 9)
    _sale(db, a.id, T - timedelta(days=1), 9)
    db.commit()

    fold_floor_restock(db, get_settings(), T)

    items = floor_list(db, T)
    assert [i.product_id for i in items] == [a.id]  # ONE row, not two
    assert items[0].qty == 18  # both crossings landed on the same line
    open_rows = list(
        db.scalars(
            select(RestockLine).where(
                RestockLine.product_id == a.id, RestockLine.checked_off_at.is_(None)
            )
        )
    )
    assert len(open_rows) == 1


def test_back_list_hides_items_already_on_an_open_transfer(client, db, settings_env):
    """The one action on the back list is 'turn it into a transfer request', so
    anything already riding an open request drops off — otherwise the next
    person raises a second request for stock that's already coming."""
    from app.config import get_settings
    from app.models import Role as R

    a, b, *_ = _fixture_products(db)
    for prod in (a, b):
        _sale(db, prod.id, T - timedelta(days=1), 12)
        _stock(db, prod.id, "floor", 1)
        _stock(db, prod.id, "bwhse", 90)
    db.commit()

    settings = get_settings()
    assert {i.product_id for i in back_list(db, settings, T)} == {a.id, b.id}

    # an open request covering `a` (built directly — this is about the list
    # filter, not about rendering an Odoo draft)
    from app.models import TransferRequest, TransferRequestLine, TransferRequestStatus

    mk_user(db, "floor2@test.io", (R.SHOPPE_FLOOR, None, None))
    req = TransferRequest(status=TransferRequestStatus.REQUESTED.value)
    db.add(req)
    db.flush()
    db.add(TransferRequestLine(request_id=req.id, product_id=a.id, qty_requested=20))
    db.commit()

    # a is on its way; b still needs asking for
    assert {i.product_id for i in back_list(db, settings, T)} == {b.id}

    # cancelling puts it back — only ACTIVE requests hide an item
    req.status = TransferRequestStatus.CANCELLED.value
    db.commit()
    assert {i.product_id for i in back_list(db, settings, T)} == {a.id, b.id}


def test_swiping_a_suggestion_away_parks_it_for_a_week(client, db):
    """A computed suggestion can't be settled for good — the numbers keep
    saying the same thing — so it goes quiet for a week and comes back."""
    from datetime import UTC, datetime

    from app.models import SuggestionSnooze

    today = datetime.now(UTC).date()
    a = mk_product(db, "CA9000000001", "Snoozable Item", odoo_id=9001)
    _sale(db, a.id, today - timedelta(days=1), 12)
    _stock(db, a.id, "floor", 0)
    _stock(db, a.id, "bwhse", 90)
    db.commit()
    mk_user(db, "mgr@test.io", (Role.SHOPPE_FLOOR, None, None))
    headers = login(client, "mgr@test.io")

    back = client.get("/api/v1/restock", headers=headers).json()["back"]
    assert any(i["product_id"] == a.id for i in back)

    r = client.post(f"/api/v1/restock/back/{a.id}/snooze", json={"days": 7}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["snoozed_until"] == (today + timedelta(days=7)).isoformat()

    back = client.get("/api/v1/restock", headers=headers).json()["back"]
    assert not any(i["product_id"] == a.id for i in back), "swiped away — quiet for a week"

    # …and back on its own once the week is up
    row = db.scalar(select(SuggestionSnooze).where(SuggestionSnooze.product_id == a.id))
    row.snoozed_until = today
    db.commit()
    back = client.get("/api/v1/restock", headers=headers).json()["back"]
    assert any(i["product_id"] == a.id for i in back)
