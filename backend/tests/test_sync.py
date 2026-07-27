from __future__ import annotations

from datetime import timedelta

from app.models import (
    Center,
    CustomerFirstSeen,
    IncomingMove,
    Product,
    SalesCenterMonthly,
    SalesDaily,
    SalesMonthly,
    SalesOrdersMonthly,
    StockLevel,
    StockSnapshot,
    StockSnapshotDay,
    SyncState,
    utcnow,
)
from app.odoo.simulator import OdooSimulator
from app.sync.runner import run_all, run_domain
from sqlalchemy import select


def _sim(settings):
    return OdooSimulator(settings.fixtures_path)


def test_product_sync_upserts_and_dedupes(db, settings_env):
    run = run_domain(db, settings_env, "products", conn=_sim(settings_env), trigger="manual")
    assert run.status == "success", run.error
    products = db.scalars(select(Product)).all()
    exp = settings_env._test_expectations
    assert len(products) == exp["product_count"]
    by_sku = {p.global_sku: p for p in products}
    assert by_sku["CA0023000009"].name == "Copper Water Bottle — 950ml"  # first variant won
    assert by_sku["ODOO-207"].name == "Mystery Item (no code)"  # blank code got sentinel key
    assert by_sku["OC0000000042"].category == "Oral Care"
    # Domestic/India product tags land in `sourcing`; other tags are noise
    for sku, p in by_sku.items():
        assert p.sourcing == exp["expected_sourcing"].get(sku, ""), sku


def test_product_sync_reclassifies_when_tags_change(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    p = db.scalar(select(Product).where(Product.global_sku == "OC0000000042"))
    assert p.sourcing == "domestic"
    for r in sim.tables["product.product"]:  # untag it in "Odoo"
        if r["id"] == 205:
            r["all_product_tag_ids"] = []
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    db.refresh(p)
    assert p.sourcing == ""


def test_product_sync_deactivates_disappeared(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    sim.tables["product.product"] = [
        r for r in sim.tables["product.product"] if r["id"] != 204
    ]
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    chips = db.scalar(select(Product).where(Product.global_sku == "US-SN0001"))
    assert chips.is_active is False


def test_product_sync_preserves_app_managed_fields(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    p = db.scalar(select(Product).where(Product.global_sku == "CA0023000009"))
    p.case_size = 12
    p.dept_orderable = True
    db.commit()
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    db.refresh(p)
    assert p.case_size == 12 and p.dept_orderable is True


def test_stock_sync_aggregates_by_location(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run = run_domain(db, settings_env, "stock", conn=sim, trigger="manual")
    assert run.status == "success", run.error

    skus = {p.id: p.global_sku for p in db.scalars(select(Product))}
    got = {
        (skus[s.product_id], s.location_key): s.qty
        for s in db.scalars(select(StockLevel))
    }
    assert got == settings_env._test_expectations["expected_stock"]


def test_stock_sync_captures_daily_history(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")

    today = utcnow().date()
    day = db.get(StockSnapshotDay, today)
    assert day is not None and day.rows > 0

    skus = {p.id: p.global_sku for p in db.scalars(select(Product))}
    history = {
        (skus[s.product_id], s.location_key): s.qty
        for s in db.scalars(select(StockSnapshot).where(StockSnapshot.snapshot_date == today))
    }
    # history mirrors the live snapshot (zero rows aren't stored)
    assert history == {
        k: v for k, v in settings_env._test_expectations["expected_stock"].items() if v > 0
    }

    # same-day re-run replaces rather than duplicates; old days are pruned
    stale = today - timedelta(days=settings_env.stock_snapshot_retention_days + 5)
    db.add(StockSnapshotDay(snapshot_date=stale, rows=1))
    db.add(
        StockSnapshot(
            snapshot_date=stale,
            product_id=next(iter(skus)),
            location_key="bwhse",
            qty=1,
        )
    )
    db.commit()
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")
    days = db.scalars(select(StockSnapshotDay.snapshot_date)).all()
    assert days == [today]
    count_today = len(
        db.scalars(select(StockSnapshot).where(StockSnapshot.snapshot_date == today)).all()
    )
    assert count_today == len(history)


def test_stock_sync_failure_keeps_last_snapshot(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")
    before = db.scalars(select(StockLevel)).all()
    assert before

    # break the world: locations vanish -> sync must fail loudly...
    sim.tables["stock.location"] = []
    run = run_domain(db, settings_env, "stock", conn=sim, trigger="manual")
    assert run.status == "failure"
    assert "locations not found" in run.error

    # ...but the previous snapshot survives untouched (self-healing rule)
    after = db.scalars(select(StockLevel)).all()
    assert len(after) == len(before)
    state = db.get(SyncState, "stock")
    assert state.last_error and state.last_success_at is not None


def test_sales_sync_backfill_then_incremental(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    # channel classification matches pos.config names against centers
    db.add(Center(name="Austin", city="Austin"))
    db.commit()
    run = run_domain(db, settings_env, "sales", conn=sim, trigger="manual")
    assert run.status == "success", run.error

    skus = {p.id: p.global_sku for p in db.scalars(select(Product))}
    got = {
        (skus[r.product_id], r.year, r.month, r.channel): r.units
        for r in db.scalars(select(SalesMonthly))
    }
    assert got == settings_env._test_expectations["expected_sales"]

    amounts = {
        (skus[r.product_id], r.year, r.month, r.channel): r.amount
        for r in db.scalars(select(SalesMonthly))
    }
    for key, amount in settings_env._test_expectations["expected_amounts"].items():
        assert amounts[key] == amount

    centers = {
        (r.config_name, r.year, r.month): (r.units, r.amount)
        for r in db.scalars(select(SalesCenterMonthly))
    }
    assert centers == settings_env._test_expectations["expected_center_sales"]
    austin = db.scalar(select(SalesCenterMonthly))
    assert austin.center_id == db.scalar(select(Center.id).where(Center.name == "Austin"))

    daily = {
        (skus[r.product_id], r.day.isoformat(), r.channel): r.units
        for r in db.scalars(select(SalesDaily))
    }
    assert daily == settings_env._test_expectations["expected_sales_daily"]

    orders = {
        (r.year, r.month, r.channel): (
            r.orders, r.amount, r.orders_with_customer,
            r.distinct_customers, r.new_customers, r.returning_customers,
        )
        for r in db.scalars(select(SalesOrdersMonthly))
    }
    assert orders == settings_env._test_expectations["expected_orders"]
    first_seen = {
        (r.partner_id, r.channel): r.first_order_on
        for r in db.scalars(select(CustomerFirstSeen))
    }
    prev_y, prev_m = settings_env._test_expectations["months"]["previous"]
    assert first_seen[(9001, "shoppe")].month == prev_m  # loyalty anchored to first order
    assert (9003, "online") in first_seen

    state = db.get(SyncState, "sales")
    assert state.extra.get("backfill_done_at")
    assert "backfill" in state.extra.get("last_window", "")
    assert state.extra.get("pos_config_channels") == {
        "III Floor": "shoppe",
        "Austin": "city_center",
        "III-Snack": "campus_other",
    }

    # second run is a small incremental, and stays idempotent
    run = run_domain(db, settings_env, "sales", conn=sim, trigger="manual")
    assert run.status == "success"
    state = db.get(SyncState, "sales")
    assert "incremental" in state.extra.get("last_window", "")
    got2 = {
        (skus[r.product_id], r.year, r.month, r.channel): r.units
        for r in db.scalars(select(SalesMonthly))
    }
    assert got2 == got
    orders2 = {
        (r.year, r.month, r.channel): (
            r.orders, r.amount, r.orders_with_customer,
            r.distinct_customers, r.new_customers, r.returning_customers,
        )
        for r in db.scalars(select(SalesOrdersMonthly))
    }
    # the incremental window replays current+previous month; the returning
    # split must not drift (first_seen memory keeps it stable)
    assert orders2 == orders


def test_detect_house_partners_thresholds():
    """Register house accounts = dominant share AND a real order floor."""
    from app.sync.sales import detect_house_partners

    partner_orders = {
        (110992, "shoppe"): 990,  # the register default — 99% of the channel
        (24711, "shoppe"): 10,  # a real person occasionally attached
        (9003, "online"): 40,  # a loyal human, well under dominance
    }
    channel_orders = {"shoppe": 1000, "online": 2000}
    assert detect_house_partners(partner_orders, channel_orders) == {(110992, "shoppe")}
    # the floor keeps tiny windows honest: 4 of 10 orders is 40% share but
    # 4 orders is no register
    assert (
        detect_house_partners({(7, "shoppe"): 4}, {"shoppe": 10}) == set()
    )
    assert detect_house_partners(
        {(7, "shoppe"): 4}, {"shoppe": 10}, min_orders=2, min_share=0.3
    ) == {(7, "shoppe")}


def test_monthly_house_partners_volume_rule():
    """A register default attached to only SOME of its orders never wins on
    share — but no person places 25+ orders in a month."""
    from app.sync.sales import monthly_house_partners

    bucket = {
        # the LA-POS pattern: ~50 attached orders in a 1,500-order month
        (2026, 6, "campus_other"): {"orders": 1500, "partner_orders": {110994: 50, 7: 3}},
        (2026, 6, "online"): {"orders": 2000, "partner_orders": {9003: 4}},
    }
    assert monthly_house_partners(bucket) == {(110994, "campus_other")}
    assert monthly_house_partners(bucket, min_monthly=4) == {
        (110994, "campus_other"),
        (9003, "online"),
    }


def test_house_partners_excluded_from_customer_metrics(db, settings_env, monkeypatch):
    """With thresholds lowered to fixture scale, dominant partners drop out
    of every customer metric (orders still count), the first-seen memory is
    scrubbed, and the detection is remembered in sync_state."""
    import app.sync.sales as sales_mod

    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    db.add(Center(name="Austin", city="Austin"))
    db.commit()

    # fixture scale: partner 9001 has 2 of 3 shoppe orders (67% — the house
    # account); 9002 and 9003 have one order each (under the floor → people)
    monkeypatch.setattr(sales_mod, "HOUSE_PARTNER_MIN_ORDERS", 2)
    monkeypatch.setattr(sales_mod, "HOUSE_PARTNER_MIN_SHARE", 0.5)
    run = run_domain(db, settings_env, "sales", conn=sim, trigger="manual")
    assert run.status == "success", run.error

    first_seen = {(r.partner_id, r.channel) for r in db.scalars(select(CustomerFirstSeen))}
    assert (9001, "shoppe") not in first_seen  # house account scrubbed
    assert (9002, "campus_other") in first_seen  # under the floor → a person
    assert (9003, "online") in first_seen

    rows = {
        (r.year, r.month, r.channel): r for r in db.scalars(select(SalesOrdersMonthly))
    }
    exp = settings_env._test_expectations["expected_orders"]
    for key, (orders, amount, _wc, _dc, _new, _ret) in exp.items():
        assert rows[key].orders == orders  # orders/revenue never change
        assert rows[key].amount == amount
    shoppe_rows = [r for r in rows.values() if r.channel == "shoppe"]
    assert all(
        r.orders_with_customer == 0 and r.distinct_customers == 0 for r in shoppe_rows
    )

    state = db.get(SyncState, "sales")
    remembered = {tuple(p) for p in state.extra.get("house_partners", [])}
    assert (9001, "shoppe") in remembered

    # back at REAL thresholds an incremental window can't re-detect — the
    # memory keeps the exclusion in force
    monkeypatch.setattr(sales_mod, "HOUSE_PARTNER_MIN_ORDERS", 50)
    monkeypatch.setattr(sales_mod, "HOUSE_PARTNER_MIN_SHARE", 0.30)
    run = run_domain(db, settings_env, "sales", conn=sim, trigger="manual")
    assert run.status == "success", run.error
    first_seen = {(r.partner_id, r.channel) for r in db.scalars(select(CustomerFirstSeen))}
    assert (9001, "shoppe") not in first_seen
    state = db.get(SyncState, "sales")
    assert (9001, "shoppe") in {tuple(p) for p in state.extra.get("house_partners", [])}


def test_incoming_sync_excludes_done_moves(db, settings_env):
    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run = run_domain(db, settings_env, "incoming", conn=sim, trigger="manual")
    assert run.status == "success", run.error
    moves = db.scalars(select(IncomingMove)).all()
    assert len(moves) == settings_env._test_expectations["incoming_count"]
    assert all(m.state in ("assigned", "confirmed", "waiting") for m in moves)


def test_run_all_order_and_states(db, settings_env):
    runs = run_all(db, settings_env, trigger="manual")
    # transfers runs LAST — it needs the locations the stock sync maps
    assert [r.domain for r in runs] == ["products", "stock", "sales", "incoming", "transfers"]
    assert all(r.status == "success" for r in runs), [r.error for r in runs]
    assert all(r.source == "fixture" for r in runs)


def test_transfers_sync_discovers_native_staging_pickings(db, settings_env):
    """Inbound transfer discovery: pending staging-bound pickings (drafts
    included) snapshot their lines; done pickings and app-placed pickings
    never land; re-sync replaces."""
    from app.models import StagingInboundMove, TransferRequest

    sim = _sim(settings_env)
    run_domain(db, settings_env, "products", conn=sim, trigger="manual")
    run_domain(db, settings_env, "stock", conn=sim, trigger="manual")  # maps locations
    run = run_domain(db, settings_env, "transfers", conn=sim, trigger="manual")
    assert run.status == "success", run.error

    by_odoo_pid = {
        p.odoo_product_id: p for p in db.scalars(select(Product)) if p.odoo_product_id
    }
    rows = db.scalars(select(StagingInboundMove)).all()
    got = {(by_odoo_pid[203].id, 24.0), (by_odoo_pid[201].id, 6.0)}
    assert {(r.product_id, r.qty) for r in rows} == got
    assert all(r.picking_name == "WH/INT/NATIVE1" for r in rows)  # done twin excluded
    assert all(r.picking_state == "assigned" for r in rows)
    assert all(r.expected_date is not None for r in rows)

    state = db.get(SyncState, "transfers")
    assert state.extra.get("native_pickings") == 1

    # an app-placed request claims that picking -> it must drop out (no
    # double counting between the board and the discovery snapshot)
    db.add(TransferRequest(status="requested", odoo_picking_id=7001))
    db.commit()
    run = run_domain(db, settings_env, "transfers", conn=sim, trigger="manual")
    assert run.status == "success", run.error
    assert db.scalars(select(StagingInboundMove)).all() == []
    state = db.get(SyncState, "transfers")
    assert state.extra.get("app_pickings_excluded") == 1
