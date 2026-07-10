from __future__ import annotations

import os

# Force a hermetic environment BEFORE any app import: fixture-mode Odoo,
# dev auth, writes off. (Real env vars would otherwise leak in from .env.)
os.environ.update(
    {
        "ODOO_BASE_URL": "",
        "ODOO_DB": "",
        "ODOO_LOGIN": "",
        "ODOO_PASSWORD": "",
        "ODOO_WRITES_ENABLED": "false",
        "AUTH_MODE": "dev",
        "APP_JWT_SECRET": "test-secret",
        "SUPABASE_URL": "",
        "SUPABASE_ANON_KEY": "",
        "SUPABASE_JWT_SECRET": "",
        "ENV": "test",
    }
)

import pytest
from app.config import get_settings
from app.db import get_engine, get_sessionmaker, reset_engine_for_tests
from app.models import Base
from fastapi.testclient import TestClient

from .odoo_fixture_data import build_test_fixtures


@pytest.fixture()
def settings_env(tmp_path, monkeypatch):
    """Per-test settings: fresh sqlite file DB + freshly built tiny Odoo fixtures."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    fixtures_dir = tmp_path / "odoo_fixtures"
    expectations = build_test_fixtures(fixtures_dir)
    monkeypatch.setenv("ODOO_FIXTURES_DIR", str(fixtures_dir))
    get_settings.cache_clear()
    reset_engine_for_tests()
    settings = get_settings()
    settings._test_expectations = expectations  # type: ignore[attr-defined]
    yield settings
    get_settings.cache_clear()
    reset_engine_for_tests()


@pytest.fixture()
def live_env(settings_env, monkeypatch):
    """Pretend credentials are configured and the kill switch is on, so the
    writer takes its live path — against the simulator, injected by tests."""
    monkeypatch.setenv("ODOO_BASE_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "testdb")
    monkeypatch.setenv("ODOO_LOGIN", "app@example.test")
    monkeypatch.setenv("ODOO_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("ODOO_WRITES_ENABLED", "true")
    get_settings.cache_clear()
    settings = get_settings()
    settings._test_expectations = settings_env._test_expectations  # type: ignore[attr-defined]
    yield settings


@pytest.fixture()
def db(settings_env):
    Base.metadata.create_all(get_engine())
    session = get_sessionmaker()()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    from app.main import create_app

    return TestClient(create_app())
