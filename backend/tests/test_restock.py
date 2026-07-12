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

from datetime import date, timedelta

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
    # crossing then makes a NEW line rather than growing the closed one
    row = db.get(RestockLine, line.line_id)
    from app.models import utcnow

    row.checked_off_at = utcnow()
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
