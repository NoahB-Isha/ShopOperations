from __future__ import annotations

from app.models import IncomingMove, Product, SalesDaily, SalesMonthly, StockLevel, SyncState
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
    run = run_domain(db, settings_env, "sales", conn=sim, trigger="manual")
    assert run.status == "success", run.error

    skus = {p.id: p.global_sku for p in db.scalars(select(Product))}
    got = {
        (skus[r.product_id], r.year, r.month, r.channel): r.units
        for r in db.scalars(select(SalesMonthly))
    }
    assert got == settings_env._test_expectations["expected_sales"]

    daily = {
        (skus[r.product_id], r.day.isoformat(), r.channel): r.units
        for r in db.scalars(select(SalesDaily))
    }
    assert daily == settings_env._test_expectations["expected_sales_daily"]

    state = db.get(SyncState, "sales")
    assert state.extra.get("backfill_done_at")
    assert "backfill" in state.extra.get("last_window", "")

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
    assert [r.domain for r in runs] == ["products", "stock", "sales", "incoming"]
    assert all(r.status == "success" for r in runs), [r.error for r in runs]
    assert all(r.source == "fixture" for r in runs)
