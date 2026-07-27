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
    # Odoo 17+ barcode app deep link (Enterprise stock_barcode); adjust if the
    # instance routes differently.
    odoo_barcode_url_template: str = "{base}/odoo/barcode/{id}"
    odoo_fixtures_dir: str = "backend/data/demo_fixtures"
    # seconds between polls of a count picking's state (per request)
    odoo_count_poll_seconds: int = 10
    # operation types for floor OOS data cleanup, matched by name (ilike; %
    # wildcards allowed — the live names are "USA-III: Inventory Adj Reduction"
    # and "USA-III: Inventory Adj  Adding Qty", note the double space)
    odoo_reduction_picking_type: str = "Inventory Adj Reduction"
    odoo_addition_picking_type: str = "Inventory Adj%Adding Qty"

    # --- sync cadence (minutes) ---
    sync_products_minutes: int = 720
    sync_stock_minutes: int = 240
    sync_sales_minutes: int = 60
    sync_incoming_minutes: int = 240
    # staging-bound pickings made directly in Odoo → coming-soon; short
    # cadence because transfers move within the working day (one tiny
    # search per run — still a polite client)
    sync_transfers_minutes: int = 10
    sync_stale_factor: float = 2.0
    sales_backfill_months: int = 24
    sales_daily_retention_days: int = 60  # sales_daily keeps only this window

    # --- restock lists (ILscripts port; see app/restock/engine.py) ---
    restock_floor_threshold: float = 4  # POS units accumulated before an item is flagged
    restock_low_cover_days: float = 7  # floor cover below this → on the back-stock list
    restock_target_cover_days: float = 14  # back-stock suggestion refills to this cover
    restock_avg_window_days: int = 28  # trailing window for avg daily POS units

    # --- center ordering (phase 3) ---
    # stock at/below this (but above zero) shows the "low — verify" caveat
    catalog_low_stock_threshold: float = 4
    # seconds between polls of an approved order's picking state (shipped detection)
    order_shipped_poll_seconds: int = 30
    # reasonability rules: history window + spike multiplier
    reasonability_history_days: int = 120
    reasonability_spike_factor: float = 3.0
    reasonability_huge_order_units: float = 500  # absolute order-size sanity cap

    # --- LLM (reasonability polish; advisory only, rules run without it) ---
    anthropic_api_key: str = ""
    reasonability_llm_model: str = "claude-opus-4-8"
    reasonability_llm_timeout_seconds: float = 10.0

    # --- ordering (phase 4: India imports, vendor orders, order mailbox) ---
    # Recipients live in the admin-editable `ordering_email` AppSetting row —
    # only transport-level config sits here.
    # Order-reply ingestion mailbox (READ-ONLY IMAP, scoped to order threads).
    imap_host: str = ""  # blank = reply ingestion not configured
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    ordering_mailbox_poll_seconds: int = 300
    # LLM reply parsing (proposals only, human-confirmed; a deterministic
    # heuristic parser runs when no key is configured)
    ordering_parser_llm_model: str = "claude-opus-4-8"
    ordering_parser_llm_timeout_seconds: float = 30.0

    # --- phase 5: reporting, time machine, availability ---
    stock_snapshot_retention_days: int = 730  # daily on-hand history for the time machine
    timemachine_max_gap_days: int = 7  # past view falls back to the nearest snapshot this far
    # the slider opens this far back even before history exists (uncovered
    # dates get the honest empty state + a pointer at the backfill)
    timemachine_min_past_days: int = 90
    timemachine_backfill_weeks: int = 26  # weekly reconstructed points, ~6 months
    availability_digest_hour_utc: int = 12  # earliest UTC hour a day's digest goes out
    skubot_api_key: str = ""  # X-API-Key for the bot endpoints; blank = disabled
    # LLM narrative/Q&A on the dashboard (generated content is always labeled;
    # a deterministic summary is served when no key is configured)
    reports_llm_model: str = "claude-opus-4-8"
    reports_llm_timeout_seconds: float = 25.0

    # --- notifications (WhatsApp primary, email fallback) ---
    notify_enabled: bool = True  # kill switch; False simulates every send app-wide
    notify_max_attempts: int = 5
    whatsapp_bridge_url: str = ""  # skubot's bridge HTTP endpoint; blank = not configured
    whatsapp_bridge_token: str = ""
    whatsapp_bridge_timeout_seconds: float = 10.0
    smtp_host: str = ""  # blank = email not configured
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "Isha Life Shop Ops <orders@ishalife.test>"
    smtp_starttls: bool = True
    # where notification links point (the public app URL once tunneled)
    app_public_url: str = "http://localhost:5173"

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
            "transfers": self.sync_transfers_minutes,
        }[domain]


@lru_cache
def get_settings() -> Settings:
    return Settings()
