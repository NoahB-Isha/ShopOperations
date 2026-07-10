from __future__ import annotations

from app.models import OdooWriteAudit
from app.odoo.canary import run_canary_create_internal_transfer
from app.odoo.simulator import OdooSimulator
from app.sync.runner import run_domain
from sqlalchemy import select


def _prepare(db, settings):
    sim = OdooSimulator(settings.fixtures_path, read_only=False)
    run_domain(db, settings, "products", conn=sim, trigger="manual")
    run_domain(db, settings, "stock", conn=sim, trigger="manual")
    return sim


def test_canary_dry_run_works_without_credentials(db, settings_env):
    _prepare(db, settings_env)
    result = run_canary_create_internal_transfer(db, settings_env, None, dry_run=True)
    assert result["ok"], result["steps"]
    assert result["mode"] == "fixture"
    assert result["reference"].startswith("APP-TEST-")
    assert result["payload"]["origin"].startswith("APP-TEST-")


def test_canary_blocked_when_kill_switch_off_live(db, live_env, monkeypatch):
    monkeypatch.setenv("ODOO_WRITES_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    sim = _prepare(db, settings)
    result = run_canary_create_internal_transfer(db, settings, None, dry_run=False, conn=sim)
    assert not result["ok"]
    assert "kill switch" in result["steps"][0]["detail"].lower()


def test_full_canary_create_verify_unlink(db, live_env):
    """The full protocol against the simulator: create APP-TEST- draft with the
    feature flag OFF, read back draft state, verify deep link, unlink."""
    sim = _prepare(db, live_env)
    result = run_canary_create_internal_transfer(db, live_env, None, dry_run=False, conn=sim)
    assert result["ok"], result["steps"]
    names = [s["name"] for s in result["steps"]]
    assert names == ["preconditions", "pick product", "create draft", "read back", "deep link", "unlink"]
    assert all(s["ok"] for s in result["steps"])
    assert "stock.picking" in result["deep_link"]

    # nothing left behind
    assert sim.search_count("stock.picking", []) == 0
    # both writes audited, none dry
    audits = db.scalars(select(OdooWriteAudit).order_by(OdooWriteAudit.id)).all()
    ops = [(a.operation, a.dry_run, a.success) for a in audits]
    assert ("create_internal_transfer", False, True) in ops
    assert ("unlink_app_record", False, True) in ops
    assert all(a.reference.startswith("APP-TEST-") for a in audits)
