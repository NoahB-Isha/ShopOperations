"""Inventory time machine: past reconstruction from snapshot history (the
acceptance fixture test), future projection parity with the ordering engine,
and the honesty of the confidence indicator."""
from __future__ import annotations

from datetime import date, timedelta

from app.config import get_settings
from app.models import (
    IncomingMove,
    Product,
    Role,
    SalesMonthly,
    StockLevel,
    StockSnapshot,
    StockSnapshotDay,
    utcnow,
)
from app.ordering.engine import suggest_one
from app.ordering.forecasting import METHOD_FLAT, METHOD_SEASONAL, METHOD_TREND
from app.ordering.inputs import build_snapshot_bundle
from app.ordering.service import load_rules
from app.timemachine.service import view
from sqlalchemy import select

from .util import login, mk_product, mk_user

# the service anchors "today" to utcnow().date() — match it, or evening runs
# in a western timezone would see every date one day off
TODAY = utcnow().date()


def _month_shift(anchor: date, n: int) -> tuple[int, int]:
    total = anchor.year * 12 + (anchor.month - 1) + n
    return total // 12, total % 12 + 1


def _users(db):
    mk_user(db, "warehouse@test.io", (Role.WAREHOUSE, None, None))
    mk_user(db, "floor@test.io", (Role.SHOPPE_FLOOR, None, None))


# --------------------------------------------------------------------- past
def _seed_history(db):
    p1 = mk_product(db, "CA0000000011", "Copper Jug", odoo_id=401)
    p2 = mk_product(db, "IN0000000012", "Rose Incense", odoo_id=402)
    d10, d3 = TODAY - timedelta(days=10), TODAY - timedelta(days=3)
    db.add_all(
        [
            StockSnapshotDay(snapshot_date=d10, rows=2),
            StockSnapshotDay(snapshot_date=d3, rows=2),
            StockSnapshot(snapshot_date=d10, product_id=p1.id, location_key="bwhse", qty=100),
            StockSnapshot(snapshot_date=d10, product_id=p1.id, location_key="floor", qty=12),
            StockSnapshot(snapshot_date=d10, product_id=p2.id, location_key="floor", qty=7),
            StockSnapshot(snapshot_date=d3, product_id=p1.id, location_key="bwhse", qty=80),
            StockSnapshot(snapshot_date=d3, product_id=p1.id, location_key="floor", qty=9),
            # p2 has NO rows on d3: covered day ⇒ genuinely zero, not unknown
        ]
    )
    db.commit()
    return p1, p2, d10, d3


def test_past_exact_day_matches_fixture(db):
    p1, p2, d10, d3 = _seed_history(db)
    settings = get_settings()
    v = view(db, settings, d3)
    assert v.mode == "past"
    assert v.effective_date == d3
    assert v.confidence["level"] == "high"
    by_sku = {i.sku: i for i in v.items}
    assert by_sku["CA0000000011"].bwhse_qty == 80
    assert by_sku["CA0000000011"].floor_qty == 9
    assert by_sku["CA0000000011"].total_qty == 89
    assert "IN0000000012" not in by_sku  # zero that day (covered ⇒ absent = 0)

    exact_older = view(db, settings, d10)
    assert {i.sku: i.total_qty for i in exact_older.items} == {
        "CA0000000011": 112,
        "IN0000000012": 7,
    }


def test_past_nearest_day_and_no_history(db):
    p1, p2, d10, d3 = _seed_history(db)
    settings = get_settings()
    # between the two snapshot days → nearest earlier day, gap disclosed
    v = view(db, settings, d3 - timedelta(days=2))
    assert v.effective_date == d10
    assert v.confidence["level"] == "medium"
    assert v.confidence["gap_days"] == 5
    # before history began → empty + honest note
    v = view(db, settings, d10 - timedelta(days=30))
    assert v.confidence["level"] == "none"
    assert v.items == []
    assert d10.isoformat() in v.confidence["note"]


def test_today_mode_reads_live_snapshot(db):
    p1, *_ = _seed_history(db)
    db.add(StockLevel(product_id=p1.id, location_key="bwhse", qty=77))
    db.commit()
    v = view(db, get_settings(), TODAY)
    assert v.mode == "today"
    assert v.confidence["level"] == "high"
    assert {i.sku: i.total_qty for i in v.items} == {"CA0000000011": 77}


# ------------------------------------------------------------------- future
def _seed_future(db):
    """Products shaped like import candidates so the ordering engine sees the
    exact same inputs the time machine does."""
    specs = [
        # (sku, odoo_id, monthly velocity, on_hand, incoming (qty, months_out))
        ("CA0023000021", 501, 40.0, 120.0, [(48, 2)]),
        ("RU0000000022", 502, 10.0, 5.0, [(24, 1), (24, 4)]),
        ("IN0000000023", 503, 25.0, 300.0, []),
    ]
    products = []
    for i, (sku, odoo_id, monthly, on_hand, incoming) in enumerate(specs):
        p = mk_product(db, sku, f"Future Product {i}", odoo_id=odoo_id)
        products.append(p)
        db.add(StockLevel(product_id=p.id, location_key="bwhse", qty=on_hand))
        # 12 months of flat history (current month excluded by the series builder)
        for back in range(1, 13):
            y, m = _month_shift(TODAY, -back)
            db.add(
                SalesMonthly(product_id=p.id, year=y, month=m, channel="shoppe", units=monthly)
            )
        for j, (qty, months_out) in enumerate(incoming):
            y, m = _month_shift(TODAY, months_out - 1)
            db.add(
                IncomingMove(
                    odoo_move_id=600 + i * 10 + j,
                    product_id=p.id,
                    qty=qty,
                    expected_date=date(y, m, 15),
                    state="assigned",
                )
            )
    db.commit()
    return products


def test_future_view_matches_engine_projection(db):
    """THE acceptance check: the time machine's future numbers are the
    ordering engine's projection, month for month."""
    _seed_future(db)
    settings = get_settings()
    rules = load_rules(db)
    bundle = build_snapshot_bundle(db, rules)
    assert len(bundle.snapshots) == 3

    for months_out in range(1, rules.horizon + 1):
        y, m = _month_shift(TODAY, months_out)
        target = date(y, m, 15)
        v = view(db, settings, target)
        assert v.mode == "future"
        assert v.confidence["month_index"] == months_out
        got = {i.sku: i for i in v.items}
        for snap in bundle.snapshots:
            assert snap.avg_monthly_sales > 0
            engine_units = (
                suggest_one(snap, rules).projected_moh[months_out - 1] * snap.avg_monthly_sales
            )
            sku = snap.product.global_sku
            if engine_units <= 0 and sku not in got:
                continue  # projected to zero with nothing incoming — hidden row
            assert abs(got[sku].total_qty - engine_units) < 0.05, (
                f"{sku} month {months_out}: time machine {got[sku].total_qty} "
                f"vs engine {engine_units}"
            )


def test_future_includes_incoming_and_confidence(db):
    _seed_future(db)
    v = view(db, get_settings(), date(*_month_shift(TODAY, 2), 15))
    by_sku = {i.sku: i for i in v.items}
    # the 48-unit receipt lands in month 2 → included by then
    assert by_sku["CA0023000021"].incoming_included == 48
    assert by_sku["CA0023000021"].forecast_method in (METHOD_FLAT, METHOD_TREND, METHOD_SEASONAL)
    assert v.confidence["level"] == "medium"
    assert "incoming" in v.confidence["note"]


def test_bounds_open_a_past_window_without_history(db):
    """The live bug: with no history the slider pinned min_date to today and
    the past was unreachable. The window must open anyway."""
    from app.timemachine.service import bounds

    settings = get_settings()
    b = bounds(db, settings)
    assert b["history_days"] == []
    assert b["min_date"] == (
        TODAY - timedelta(days=settings.timemachine_min_past_days)
    ).isoformat()
    v = view(db, settings, TODAY - timedelta(days=30))
    assert v.confidence["level"] == "none"
    assert "backfill" in v.confidence["note"]


def test_backfill_reconstructs_weekly_history(db, settings_env):
    """Admin queues it, the worker processes one date per pass, reconstructed
    days are labeled and never overwrite live capture."""
    from app.odoo.simulator import OdooSimulator
    from app.sync.runner import run_domain
    from app.timemachine.backfill import backfill_state, process_next, request_backfill

    sim = OdooSimulator(settings_env.fixtures_path)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")  # maps locations + today

    state = request_backfill(db, settings_env, weeks=3)
    assert len(state["pending"]) == 3

    processed = []
    while (d := process_next(db, settings_env, sim)) is not None:
        processed.append(d)
    assert len(processed) == 3
    assert backfill_state(db)["pending"] == []
    assert backfill_state(db)["done"] == 3

    days = {d.snapshot_date: d for d in db.scalars(select(StockSnapshotDay))}
    recon_days = [d for d in days.values() if d.source == "reconstructed"]
    assert len(recon_days) == 3
    assert days[TODAY].source == "sync"  # live capture untouched

    # the simulator serves current quants for any as-of date — the copper
    # bottle's bwhse total (120 root + 30 bin + 25 folded in from SHIP)
    # lands under the right key
    week_ago = TODAY - timedelta(weeks=1)
    skus = {p.id: p.global_sku for p in db.scalars(select(Product))}
    recon = {
        (skus[s.product_id], s.location_key): s.qty
        for s in db.scalars(select(StockSnapshot).where(StockSnapshot.snapshot_date == week_ago))
    }
    assert recon[("CA0023000009", "bwhse")] == 175.0
    # SHIP-only stock (the live sesame-oil shape) reconstructs as bwhse too
    assert recon[("BL0000000021", "bwhse")] == 18.0

    # the past view labels reconstructed days honestly, never "high"
    settings = get_settings()
    v = view(db, settings, week_ago)
    assert v.confidence["source"] == "reconstructed"
    assert v.confidence["level"] == "medium"
    assert "reconstructed" in v.confidence["note"]

    # a re-request skips live-captured days but refreshes reconstructed ones
    state = request_backfill(db, settings_env, weeks=3)
    assert len(state["pending"]) == 3


def test_future_beyond_horizon_is_422_and_roles(client, db):
    _seed_future(db)
    _users(db)
    wh = login(client, "warehouse@test.io")
    y, m = _month_shift(TODAY, 8)
    r = client.get(
        "/api/v1/time-machine", params={"date": date(y, m, 15).isoformat()}, headers=wh
    )
    assert r.status_code == 422
    ok = client.get(
        "/api/v1/time-machine", params={"date": TODAY.isoformat()}, headers=wh
    )
    assert ok.status_code == 200 and ok.json()["mode"] == "today"
    bounds = client.get("/api/v1/time-machine/bounds", headers=wh).json()
    assert bounds["horizon_months"] == 6
    # floor role has no time machine
    floor = login(client, "floor@test.io")
    assert client.get(
        "/api/v1/time-machine", params={"date": TODAY.isoformat()}, headers=floor
    ).status_code == 403


# ----------------------------------------------------------- day sales
def test_day_sales_totals_and_per_item_split(db):
    """Sales/returns ride the requested day: gross = net + returned, NULL
    returned (pre-capture rows) reads as unknown — never as zero."""
    from app.models import SalesDaily

    p1, p2, d10, d3 = _seed_history(db)
    db.add_all(
        [
            # p1 net 8 = 10 sold − 2 returned, split captured; plus 5 online
            SalesDaily(product_id=p1.id, day=d3, channel="shoppe", units=8, returned_units=2),
            SalesDaily(product_id=p1.id, day=d3, channel="online", units=5, returned_units=0),
            # p2 synced before returns capture — split unknown
            SalesDaily(product_id=p2.id, day=d3, channel="shoppe", units=3, returned_units=None),
        ]
    )
    db.commit()
    settings = get_settings()

    v = view(db, settings, d3)
    ds = v.day_sales
    assert ds["available"] is True
    assert ds["total_sold"] == 18.0  # (8+2) + 5 + 3
    assert ds["total_returned"] == 2.0
    assert ds["products_sold"] == 2
    assert "returns split unknown" in ds["note"]

    by_sku = {i.sku: i for i in v.items}
    assert by_sku["CA0000000011"].sold_qty == 15.0
    assert by_sku["CA0000000011"].returned_qty == 2.0
    # p2 had zero stock that day (no row) — its sales still count in totals

    # sales describe the REQUESTED day even when the snapshot shown is older
    day_between = d3 + timedelta(days=1)
    v2 = view(db, settings, day_between)
    assert v2.effective_date == d3
    assert v2.day_sales["available"] is True
    assert v2.day_sales["products_sold"] == 0


def test_day_sales_honest_outside_retention_and_future(db):
    _seed_history(db)
    settings = get_settings()
    old = TODAY - timedelta(days=settings.sales_daily_retention_days + 10)
    v = view(db, settings, old)
    assert v.day_sales["available"] is False
    assert "kept" in v.day_sales["note"]

    y, m = _month_shift(TODAY, 1)
    future = date(y, m, 15)
    vf = view(db, settings, future)
    assert vf.day_sales is None
