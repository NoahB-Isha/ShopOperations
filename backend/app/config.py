"""App configuration. Everything comes from environment variables (or `.env`
at the repo root); credentials are held in memory only and never logged.
"""
from __future__ import annotations

import logging
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root anchor: this file lives at <root>/backend/app/config.py, and the
# Docker images preserve the same relative layout under /app.
REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger(__name__)

# The only ENV values allowed to run the insecure conveniences (dev-mode auth,
# login codes in API responses, the interactive API docs). Everything else is
# production and must be configured securely or the process refuses to start.
DEV_ENVS = frozenset({"dev", "test", "local"})

# Session keys that have ever been committed to this repo or its examples.
# Treated as public, because they are.
PUBLIC_JWT_SECRETS = frozenset({"dev-only-change-me", "change-me", "changeme", "secret"})
MIN_JWT_SECRET_CHARS = 32


class InsecureConfig(RuntimeError):
    """Raised at startup when a production configuration is unsafe. Deliberately
    not a ValueError: pydantic rewraps ValueError into a ValidationError, and
    this message has to reach the operator verbatim."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "postgresql+psycopg://ilops:ilops@localhost:5432/ilops"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_scheme(cls, v: str) -> str:
        """Providers hand out plain `postgres(ql)://` URLs (Supabase, Render,
        Heroku); SQLAlchemy would route those to the legacy psycopg2 driver,
        which isn't installed. Rewrite to the psycopg v3 dialect so pasted
        connection strings just work."""
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    # --- auth ---
    auth_mode: str = "dev"  # dev | supabase — "dev" is refused outside DEV_ENVS
    # No default on purpose: a committed default session key is a public key.
    # Blank is filled with a random per-process value in dev (see the validator).
    app_jwt_secret: str = ""
    session_days: int = 30
    otp_exp_minutes: int = 10
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: SecretStr = SecretStr("")
    # Sign-in options offered in supabase mode. Google OAuth is the primary path:
    # Google verifies the email, which is what lets the app safely link a
    # Supabase identity to an existing account (see match_supabase_claims_to_user).
    supabase_oauth_providers: str = "google"  # comma-separated; "" = none
    # The email/SMS one-time-code form. Off by default: every extra provider is
    # another way to obtain a token bearing someone else's address.
    supabase_otp_enabled: bool = False

    # --- Odoo connection (all four blank => fixture mode) ---
    odoo_base_url: str = ""
    odoo_db: str = ""
    odoo_login: str = ""
    odoo_password: SecretStr = SecretStr("")

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
    # An unchecked floor line ages out after this many days: the list should be
    # what's actionable now, not a three-week backlog reprinted every morning.
    restock_line_max_age_days: int = 7
    # On-read refresh budget for /restock when no worker is running. Stock
    # moves through the day (the aisle reads those numbers); sales only change
    # the list at a day boundary, so it can afford to be lazier.
    restock_refresh_stock_seconds: int = 300
    # Sales is the HEAVY pull (CLAUDE.md: never poll it). It only changes this
    # list at a day boundary, so half-hourly while someone has the page open is
    # already more attentive than the worker's hourly cadence.
    restock_refresh_sales_seconds: int = 1800

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
    anthropic_api_key: SecretStr = SecretStr("")
    reasonability_llm_model: str = "claude-opus-4-8"
    reasonability_llm_timeout_seconds: float = 10.0

    # --- ordering (phase 4: India imports, vendor orders, order mailbox) ---
    # Recipients live in the admin-editable `ordering_email` AppSetting row —
    # only transport-level config sits here.
    # Order-reply ingestion mailbox (READ-ONLY IMAP, scoped to order threads).
    imap_host: str = ""  # blank = reply ingestion not configured
    imap_port: int = 993
    imap_username: str = ""
    imap_password: SecretStr = SecretStr("")
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
    skubot_api_key: SecretStr = SecretStr("")  # X-API-Key for bot endpoints; blank = off
    # LLM narrative/Q&A on the dashboard (generated content is always labeled;
    # a deterministic summary is served when no key is configured)
    reports_llm_model: str = "claude-opus-4-8"
    reports_llm_timeout_seconds: float = 25.0

    # --- notifications (WhatsApp primary, email fallback) ---
    notify_enabled: bool = True  # kill switch; False simulates every send app-wide
    notify_max_attempts: int = 5
    whatsapp_bridge_url: str = ""  # skubot's bridge HTTP endpoint; blank = not configured
    whatsapp_bridge_token: SecretStr = SecretStr("")
    whatsapp_bridge_timeout_seconds: float = 10.0
    smtp_host: str = ""  # blank = email not configured
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "Isha Life Shop Ops <orders@ishalife.test>"
    smtp_starttls: bool = True
    # where notification links point (the public app URL once tunneled)
    app_public_url: str = "http://localhost:5173"

    # --- seeds ---
    # Volunteer PII (names, emails, phones, addresses, Stripe terminal serials),
    # so the workbook is in neither the repo nor the image: drop it in ./private/
    # (gitignored, mounted read-only) or give an absolute host path. A missing
    # file degrades gracefully — the seed skips the roster, the import 404s.
    seed_coordinator_xlsx: str = "private/IL City Coordinators.xlsx"

    # --- web ---
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # In-process rate limiting (app/ratelimit.py). Off only for tests.
    rate_limit_enabled: bool = True

    @model_validator(mode="after")
    def _refuse_insecure_production(self) -> Settings:
        """Fail closed. Production may not run dev auth, a published or short
        session key, or wildcard CORS. Dev and test keep working with zero
        configuration — but never with a key that has been published."""
        if not self.is_dev_env:
            problems: list[str] = []
            if self.auth_mode != "supabase":
                problems.append(
                    f"AUTH_MODE={self.auth_mode!r}: dev auth returns login codes in the API "
                    "response, so anyone who can reach this server can sign in as anyone. "
                    "Set AUTH_MODE=supabase (and SUPABASE_URL / SUPABASE_ANON_KEY / "
                    "SUPABASE_JWT_SECRET)."
                )
            if not self.app_jwt_secret or self.app_jwt_secret in PUBLIC_JWT_SECRETS:
                problems.append(
                    "APP_JWT_SECRET is empty or still a published example value: session "
                    "tokens can be forged offline. Generate one with "
                    '`python -c "import secrets; print(secrets.token_urlsafe(48))"`.'
                )
            elif len(self.app_jwt_secret) < MIN_JWT_SECRET_CHARS:
                problems.append(
                    f"APP_JWT_SECRET is {len(self.app_jwt_secret)} characters; use at least "
                    f"{MIN_JWT_SECRET_CHARS}."
                )
            if "*" in self.cors_origin_list:
                problems.append(
                    "CORS_ORIGINS contains '*': list the frontend origins explicitly."
                )
            if problems:
                raise InsecureConfig(
                    f"Refusing to start with ENV={self.env!r} —\n  - " + "\n  - ".join(problems)
                )
            return self
        # Dev/test: a blank or published key becomes a random per-process one, so
        # a stack someone can reach on the LAN can't be forged against a value
        # they read in the repo. Sessions and outstanding login codes end on
        # restart; dev codes show on screen, so that costs one click.
        if not self.app_jwt_secret or self.app_jwt_secret in PUBLIC_JWT_SECRETS:
            self.app_jwt_secret = secrets.token_urlsafe(48)
            log.warning(
                "APP_JWT_SECRET is unset or still the example value — generated an ephemeral "
                "one for this process. Sessions end on restart. Set a real value in .env."
            )
        return self

    @property
    def is_dev_env(self) -> bool:
        return self.env.strip().lower() in DEV_ENVS

    @property
    def dev_auth(self) -> bool:
        """True only when BOTH the environment and the auth mode are dev. This —
        never `auth_mode` alone — gates anything that leaks a login code."""
        return self.is_dev_env and self.auth_mode == "dev"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def oauth_provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.supabase_oauth_providers.split(",") if p.strip()]

    @property
    def odoo_configured(self) -> bool:
        return all(
            [
                self.odoo_base_url,
                self.odoo_db,
                self.odoo_login,
                # .get_secret_value(): SecretStr("") is truthy, so a bare
                # truthiness test here would call a blank password configured.
                self.odoo_password.get_secret_value(),
            ]
        )

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
