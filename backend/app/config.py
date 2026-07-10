"""App configuration. Everything comes from environment variables (or `.env`
at the repo root); credentials are held in memory only and never logged.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root anchor: this file lives at <root>/backend/app/config.py, and the
# Docker images preserve the same relative layout under /app.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "postgresql+psycopg://ilops:ilops@localhost:5432/ilops"

    # --- auth ---
    auth_mode: str = "dev"  # dev | supabase
    app_jwt_secret: str = "dev-only-change-me"
    session_days: int = 30
    otp_exp_minutes: int = 10
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""

    # --- Odoo connection (all four blank => fixture mode) ---
    odoo_base_url: str = ""
    odoo_db: str = ""
    odoo_login: str = ""
    odoo_password: str = ""

    odoo_writes_enabled: bool = False  # global kill switch; False forces dry-run app-wide
    odoo_page_size: int = 1000
    odoo_throttle_seconds: float = 0.25
    odoo_timeout_seconds: float = 120
    odoo_record_url_template: str = "{base}/web#id={id}&model={model}&view_type=form"
    odoo_fixtures_dir: str = "backend/data/demo_fixtures"

    # --- sync cadence (minutes) ---
    sync_products_minutes: int = 720
    sync_stock_minutes: int = 240
    sync_sales_minutes: int = 60
    sync_incoming_minutes: int = 240
    sync_stale_factor: float = 2.0
    sales_backfill_months: int = 24

    # --- seeds ---
    seed_coordinator_xlsx: str = "docs/reference/IL City Coordinators.xlsx"

    # --- web ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def odoo_configured(self) -> bool:
        return all([self.odoo_base_url, self.odoo_db, self.odoo_login, self.odoo_password])

    @property
    def odoo_mode(self) -> str:
        """'live' when credentials are present, else 'fixture' (simulator-backed)."""
        return "live" if self.odoo_configured else "fixture"

    @property
    def fixtures_path(self) -> Path:
        p = Path(self.odoo_fixtures_dir)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def coordinator_xlsx_path(self) -> Path:
        p = Path(self.seed_coordinator_xlsx)
        return p if p.is_absolute() else REPO_ROOT / p

    def sync_interval_minutes(self, domain: str) -> int:
        return {
            "products": self.sync_products_minutes,
            "stock": self.sync_stock_minutes,
            "sales": self.sync_sales_minutes,
            "incoming": self.sync_incoming_minutes,
        }[domain]


@lru_cache
def get_settings() -> Settings:
    return Settings()
