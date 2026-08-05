"""Locks the fail-closed config check.

This is the control that keeps the review's critical finding closed: a
production environment must refuse to start with dev auth, a published or short
session key, or wildcard CORS. The reason that finding reached a deployed
blueprint is that nothing failed when the configuration was wrong.
"""
from __future__ import annotations

import pytest
from app.config import InsecureConfig, Settings

GOOD_SECRET = "s" * 40


def test_prod_refuses_dev_auth():
    with pytest.raises(InsecureConfig, match="AUTH_MODE"):
        Settings(env="prod", auth_mode="dev", app_jwt_secret=GOOD_SECRET)


@pytest.mark.parametrize("secret", ["", "dev-only-change-me", "change-me", "short"])
def test_prod_refuses_weak_secrets(secret):
    with pytest.raises(InsecureConfig, match="APP_JWT_SECRET"):
        Settings(env="prod", auth_mode="supabase", app_jwt_secret=secret)


def test_prod_refuses_wildcard_cors():
    with pytest.raises(InsecureConfig, match="CORS_ORIGINS"):
        Settings(
            env="prod", auth_mode="supabase", app_jwt_secret=GOOD_SECRET, cors_origins="*"
        )


def test_prod_error_names_every_problem_at_once():
    """The operator should not have to fix these one restart at a time."""
    with pytest.raises(InsecureConfig) as exc:
        Settings(env="prod", auth_mode="dev", app_jwt_secret="", cors_origins="*")
    message = str(exc.value)
    assert "AUTH_MODE" in message
    assert "APP_JWT_SECRET" in message
    assert "CORS_ORIGINS" in message


def test_prod_starts_when_configured_properly():
    s = Settings(env="prod", auth_mode="supabase", app_jwt_secret=GOOD_SECRET)
    assert s.is_dev_env is False
    assert s.dev_auth is False


def test_dev_never_keeps_a_published_secret():
    """A LAN-reachable dev stack must not be forgeable against a value anyone
    can read in the repo."""
    s = Settings(env="dev", app_jwt_secret="dev-only-change-me")
    assert s.app_jwt_secret != "dev-only-change-me"
    assert len(s.app_jwt_secret) >= 32


def test_dev_auth_needs_both_env_and_mode():
    """dev_auth — never auth_mode alone — gates anything that leaks a code."""
    assert Settings(env="dev", auth_mode="dev", app_jwt_secret=GOOD_SECRET).dev_auth is True
    assert (
        Settings(env="dev", auth_mode="supabase", app_jwt_secret=GOOD_SECRET).dev_auth is False
    )


def test_oauth_provider_list_defaults_to_google():
    s = Settings(env="dev", app_jwt_secret=GOOD_SECRET)
    assert s.oauth_provider_list == ["google"]
    assert Settings(env="dev", supabase_oauth_providers="").oauth_provider_list == []


# --------------------------------------------------------------- SecretStr
# The credential fields are SecretStr so a traceback can't render them. The
# hazard of that change is that SecretStr("") is TRUTHY, so every
# `if not settings.<key>` guard inverts. mypy cannot catch those — these can.
def test_blank_odoo_password_still_means_fixture_mode():
    """The sharpest flip: a truthy blank password would take the LIVE Odoo path
    with no credential at all."""
    s = Settings(
        env="dev",
        app_jwt_secret=GOOD_SECRET,
        odoo_base_url="https://odoo.example.test",
        odoo_db="db",
        odoo_login="user",
        odoo_password="",
    )
    assert s.odoo_configured is False
    assert s.odoo_mode == "fixture"
    assert (
        Settings(
            env="dev",
            app_jwt_secret=GOOD_SECRET,
            odoo_base_url="https://odoo.example.test",
            odoo_db="db",
            odoo_login="user",
            odoo_password="real",
        ).odoo_mode
        == "live"
    )


def test_blank_bot_key_disables_the_bot_surface(client, db):
    """conftest leaves SKUBOT_API_KEY unset; blank must fail closed with 503."""
    assert client.get("/api/v1/bot/health").status_code == 503
    assert client.get("/api/v1/bot/health", headers={"X-API-Key": "guess"}).status_code == 503


def test_secrets_do_not_render_in_a_settings_repr():
    """The point of the change: a traceback with `settings` in frame locals must
    not print credentials."""
    s = Settings(
        env="dev", app_jwt_secret=GOOD_SECRET, odoo_password="hunter2", smtp_password="hunter3"
    )
    blob = f"{s!r} {s} {s.model_dump()}"
    assert "hunter2" not in blob
    assert "hunter3" not in blob
    # …while the real value is still reachable where it is actually needed
    assert s.odoo_password.get_secret_value() == "hunter2"
