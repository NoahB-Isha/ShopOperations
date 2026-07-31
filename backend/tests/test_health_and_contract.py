from __future__ import annotations

import os

import pytest
from app.models import SyncState
from app.odoo.contract import check_contract
from app.odoo.simulator import OdooSimulator
from app.sync.runner import run_all
from sqlalchemy import select


def test_health_reports_never_synced_honestly(client, db):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded"
    assert body["odoo_mode"] == "fixture"
    assert body["writes_enabled"] is False
    assert body["sync"]["products"]["stale"] is True
    assert body["sync"]["products"]["last_error"] == "never synced"


def test_health_ok_after_full_sync(client, db, settings_env):
    run_all(db, settings_env, trigger="manual")
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert all(not d["stale"] for d in body["sync"].values())
    assert body["sync"]["sales"]["extra"].get("backfill_done_at")


def test_health_screams_on_auth_failure(client, db, settings_env):
    run_all(db, settings_env, trigger="manual")
    state = db.scalar(select(SyncState).where(SyncState.domain == "stock"))
    state.auth_failed = True
    state.last_error = "OdooAuthError: Access Denied"
    db.commit()
    body = client.get("/api/v1/health").json()
    assert body["status"] == "degraded"
    assert body["odoo_auth_failed"] is True


def test_contract_check_passes_against_fixtures(settings_env):
    sim = OdooSimulator(settings_env.fixtures_path)
    results = check_contract(sim)
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]


@pytest.mark.odoo_live
@pytest.mark.skipif(
    not all(os.environ.get(k) for k in ("ODOO_BASE_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_PASSWORD")),
    reason="live Odoo credentials not configured",
)
def test_contract_check_against_production_readonly():
    """Read-only schema re-validation against the real instance (fields_get +
    search_count only). Run manually when fixture drift is suspected."""
    from app.config import Settings
    from app.odoo.client import OdooClient

    client = OdooClient(Settings(), read_only=True)
    results = check_contract(client)
    assert all(r["ok"] for r in results), [r for r in results if not r["ok"]]


def test_database_url_scheme_normalizes_to_psycopg3(monkeypatch):
    """Pasted provider URLs (plain postgres:// or postgresql://) must resolve
    to the installed psycopg v3 dialect, never legacy psycopg2."""
    from app.config import Settings

    for raw in (
        "postgres://u:p@host:6543/db?sslmode=require",
        "postgresql://u:p@host:6543/db?sslmode=require",
        "postgresql+psycopg://u:p@host:6543/db?sslmode=require",
    ):
        s = Settings(database_url=raw)
        assert s.database_url == "postgresql+psycopg://u:p@host:6543/db?sslmode=require"
