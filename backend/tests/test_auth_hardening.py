"""Session revocation, verified-claim identity linking, and rate limiting.

These cover the auth findings from the security review: a stolen token must be
revocable, an UNVERIFIED Supabase claim must never link to an existing account,
and the unauthenticated auth endpoints must have a ceiling.
"""
from __future__ import annotations

import pytest
from app.auth.service import (
    AuthError,
    _claim_is_verified,
    match_supabase_claims_to_user,
)
from app.config import get_settings
from app.models import Role
from app.ratelimit import enforce, enforce_login_limits, reset_for_tests
from fastapi import HTTPException

from .util import login, mk_user


# --------------------------------------------------------------- revocation
def test_bumping_token_epoch_kills_existing_sessions(client, db):
    user = mk_user(db, "boss@test.local", (Role.ADMIN, None, None))
    headers = login(client, user.email)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    user.token_epoch = int(user.token_epoch or 0) + 1
    db.commit()

    r = client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 401
    assert "signed out" in r.json()["detail"].lower()


def test_logout_everywhere_retires_the_calling_session(client, db):
    user = mk_user(db, "phone-lost@test.local", (Role.ADMIN, None, None))
    headers = login(client, user.email)
    assert client.post("/api/v1/auth/logout-everywhere", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    # and a fresh sign-in still works
    assert client.get("/api/v1/auth/me", headers=login(client, user.email)).status_code == 200


def test_role_change_retires_old_sessions(client, db):
    """An old session must not keep running the old permissions."""
    admin = mk_user(db, "admin@test.local", (Role.ADMIN, None, None))
    victim = mk_user(db, "wh@test.local", (Role.WAREHOUSE, None, None))
    victim_headers = login(client, victim.email)
    assert client.get("/api/v1/auth/me", headers=victim_headers).status_code == 200

    r = client.patch(
        f"/api/v1/admin/users/{victim.id}",
        json={"roles": [{"role": Role.SHOPPE_FLOOR.value, "zone_id": None, "center_id": None}]},
        headers=login(client, admin.email),
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me", headers=victim_headers).status_code == 401


def test_deactivating_a_user_retires_their_sessions(client, db):
    admin = mk_user(db, "admin2@test.local", (Role.ADMIN, None, None))
    victim = mk_user(db, "bye@test.local", (Role.WAREHOUSE, None, None))
    victim_headers = login(client, victim.email)
    r = client.patch(
        f"/api/v1/admin/users/{victim.id}",
        json={"is_active": False},
        headers=login(client, admin.email),
    )
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me", headers=victim_headers).status_code == 401


# ------------------------------------------------- verified-claim linking
@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"email_verified": True}, True),
        ({"email_verified": "true"}, True),
        ({"email_verified": False}, False),
        ({}, False),  # missing => unverified, fail closed
        ({"email_verified": "yes"}, False),  # unparseable => unverified
        ({"app_metadata": {"email_verified": True}}, True),
        ({"user_metadata": {"email_verified": True}}, True),
        # top-level (Supabase-controlled) wins over client-writable user_metadata
        ({"email_verified": False, "user_metadata": {"email_verified": True}}, False),
    ],
)
def test_claim_is_verified_precedence(claims, expected):
    assert _claim_is_verified(claims, "email_verified") is expected


def test_unverified_email_cannot_claim_an_existing_account(db):
    """The account-takeover path: sign up on the Supabase project with someone
    else's address, exchange the token, inherit their roles. Must be refused."""
    mk_user(db, "target@test.local", (Role.ADMIN, None, None))
    with pytest.raises(AuthError) as exc:
        match_supabase_claims_to_user(
            db, {"sub": "attacker-uid", "email": "target@test.local"}
        )
    assert exc.value.status_code == 403


def test_verified_email_links_and_is_remembered(db):
    """Google OAuth supplies a verified email, so the link is allowed — and the
    auth_uid is stored so later sign-ins skip the identifier match entirely."""
    user = mk_user(db, "real@test.local", (Role.ADMIN, None, None))
    claims = {"sub": "google-uid-1", "email": "real@test.local", "email_verified": True}
    assert match_supabase_claims_to_user(db, claims) is user
    assert user.auth_uid == "google-uid-1"
    # second visit matches on auth_uid alone
    assert match_supabase_claims_to_user(db, {"sub": "google-uid-1"}) is user


def test_unknown_verified_email_is_simply_not_found(db):
    """A verified identity with no app account is a 'no account' case, not a
    takeover attempt — it must return None rather than raise."""
    assert (
        match_supabase_claims_to_user(
            db, {"sub": "u", "email": "nobody@test.local", "email_verified": True}
        )
        is None
    )


# ------------------------------------------------------------ rate limiting
def test_authenticated_rate_limit_returns_429(client, db, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    reset_for_tests()
    try:
        headers = login(client, mk_user(db, "sweeper@test.local", (Role.ADMIN, None, None)).email)
        codes = [
            client.post(
                "/api/v1/products/blacklist/sweep", json={"apply": False}, headers=headers
            ).status_code
            for _ in range(11)
        ]
        assert codes[-1] == 429
        assert codes.count(429) == 1
    finally:
        get_settings.cache_clear()
        reset_for_tests()


def test_login_limits_apply_when_codes_are_actually_delivered():
    """dev_auth is exempt (nothing is delivered, loopback only); a real
    delivery-mode server is not."""
    reset_for_tests()
    settings = get_settings().model_copy(
        update={"auth_mode": "supabase", "env": "prod", "rate_limit_enabled": True}
    )
    assert settings.dev_auth is False

    class _Req:  # only .client.host is read
        client = type("C", (), {"host": "203.0.113.9"})()

    for _ in range(5):
        enforce_login_limits(settings, _Req(), "boss@test.local")
    with pytest.raises(HTTPException) as exc:
        enforce_login_limits(settings, _Req(), "boss@test.local")
    assert exc.value.status_code == 429
    assert exc.value.headers is not None and "Retry-After" in exc.value.headers
    reset_for_tests()


def test_dev_auth_is_exempt_from_login_limits():
    reset_for_tests()
    settings = get_settings()
    assert settings.dev_auth is True

    class _Req:
        client = type("C", (), {"host": "127.0.0.1"})()

    for _ in range(50):  # the e2e suite re-logs demo users in tight loops
        enforce_login_limits(settings, _Req(), "demo@test.local")


def test_enforce_is_a_no_op_when_disabled():
    reset_for_tests()
    settings = get_settings()  # RATE_LIMIT_ENABLED=false in conftest
    for _ in range(100):
        enforce(settings, "bucket", "subject", limit=1, per_seconds=60)
