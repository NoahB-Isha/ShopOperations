from __future__ import annotations

from datetime import timedelta

from app.auth.service import normalize_email, normalize_phone
from app.models import LoginCode, Role, utcnow
from sqlalchemy import select

from .util import login, mk_user


def test_normalizers():
    assert normalize_email("  Tiba@BellSouth. net ") == "tiba@bellsouth.net"
    assert normalize_email("nope") is None
    assert normalize_phone("(512) 937-8219") == "+15129378219"
    assert normalize_phone("1-512-937-8219") == "+15129378219"
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("") is None


def test_dev_login_flow_email(client, db):
    mk_user(db, "admin@test.local", (Role.ADMIN, None, None))
    headers = login(client, "admin@test.local")
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "admin@test.local"
    assert body["roles"][0]["role"] == "admin"


def test_dev_login_flow_phone(client, db):
    user = mk_user(db, "vol@test.local", (Role.CENTER_ORDERER, None, None))
    user.phone = "+15551234567"
    db.commit()
    r = client.post("/api/v1/auth/request-code", json={"identifier": "555-123-4567"})
    assert r.status_code == 200
    assert r.json()["channel"] == "email"  # email preferred when both on file
    code = r.json()["dev_code"]
    r = client.post("/api/v1/auth/verify", json={"identifier": "(555) 123-4567", "code": code})
    assert r.status_code == 200


def test_unknown_identifier_is_indistinguishable_from_a_real_one(client, db):
    """No account-existence oracle: an unknown identifier gets the same 200 and
    the same body shape as a real one, just without a code to use."""
    mk_user(db, "real@test.local", (Role.ADMIN, None, None))
    real = client.post("/api/v1/auth/request-code", json={"identifier": "real@test.local"})
    ghost = client.post("/api/v1/auth/request-code", json={"identifier": "ghost@test.local"})
    assert real.status_code == ghost.status_code == 200
    assert real.json().keys() == ghost.json().keys()
    assert ghost.json() == {"sent": True, "channel": "email", "dev_code": None}
    # and there is nothing to verify with
    bad = client.post(
        "/api/v1/auth/verify", json={"identifier": "ghost@test.local", "code": "000000"}
    )
    assert bad.status_code == 401


def test_wrong_code_and_attempt_limit(client, db):
    mk_user(db, "x@test.local", (Role.ADMIN, None, None))
    r = client.post("/api/v1/auth/request-code", json={"identifier": "x@test.local"})
    code = r.json()["dev_code"]
    for _ in range(5):
        bad = client.post("/api/v1/auth/verify", json={"identifier": "x@test.local", "code": "000000"})
        assert bad.status_code == 401
    # correct code no longer works: attempts exhausted
    r = client.post("/api/v1/auth/verify", json={"identifier": "x@test.local", "code": code})
    assert r.status_code == 401


def test_expired_code_rejected(client, db):
    mk_user(db, "slow@test.local", (Role.ADMIN, None, None))
    r = client.post("/api/v1/auth/request-code", json={"identifier": "slow@test.local"})
    code = r.json()["dev_code"]
    lc = db.scalar(select(LoginCode).order_by(LoginCode.id.desc()))
    lc.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = client.post("/api/v1/auth/verify", json={"identifier": "slow@test.local", "code": code})
    assert r.status_code == 401


def test_request_throttle_skipped_in_dev_but_kept_for_delivery_modes(client, db):
    # dev mode delivers nothing (the code renders on screen), so rapid
    # re-requests are fine — e2e suites re-log demo users within seconds
    mk_user(db, "eager@test.local", (Role.ADMIN, None, None))
    assert client.post("/api/v1/auth/request-code", json={"identifier": "eager@test.local"}).status_code == 200
    r = client.post("/api/v1/auth/request-code", json={"identifier": "eager@test.local"})
    assert r.status_code == 200

    # …but any mode that actually sends email/SMS keeps the 60s guard
    import pytest
    from app.auth.service import AuthError, issue_code
    from app.config import get_settings
    from app.models import User
    from sqlalchemy import select

    user = db.scalar(select(User).where(User.email == "eager@test.local"))
    settings = get_settings().model_copy(update={"auth_mode": "supabase"})
    with pytest.raises(AuthError) as exc:
        issue_code(db, user, settings)
    assert exc.value.status_code == 429


def test_inactive_user_cannot_login(client, db):
    user = mk_user(db, "gone@test.local", (Role.ADMIN, None, None))
    user.is_active = False
    db.commit()
    # Same uniform response as any other identifier — but no code is issued, so
    # there is no way through.
    r = client.post("/api/v1/auth/request-code", json={"identifier": "gone@test.local"})
    assert r.status_code == 200
    assert r.json()["dev_code"] is None
    bad = client.post(
        "/api/v1/auth/verify", json={"identifier": "gone@test.local", "code": "000000"}
    )
    assert bad.status_code == 401


def test_bad_token_rejected(client, db):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_verify_supabase_token_both_signing_schemes(monkeypatch):
    """Legacy projects sign HS256 with the shared secret; new projects sign
    ES256 with asymmetric keys served via JWKS. Both must verify; anything
    else is rejected without a key lookup."""
    import jwt as pyjwt
    import pytest
    from app.auth import service
    from app.auth.service import AuthError, verify_supabase_token
    from app.config import Settings
    from cryptography.hazmat.primitives.asymmetric import ec

    # model_copy skips validation, so an override has to be a SecretStr already
    from pydantic import SecretStr

    settings = Settings(
        auth_mode="supabase",
        supabase_url="https://proj.supabase.co",
        supabase_jwt_secret="legacy-secret",
    )
    claims = {"sub": "uid-1", "email": "n@x.org", "aud": "authenticated"}

    # HS256 — the legacy shared-secret path
    hs = pyjwt.encode(claims, "legacy-secret", algorithm="HS256")
    assert verify_supabase_token(hs, settings)["sub"] == "uid-1"
    with pytest.raises(AuthError):  # wrong secret
        verify_supabase_token(
            hs, settings.model_copy(update={"supabase_jwt_secret": SecretStr("other")})
        )

    # ES256 — the asymmetric path, JWKS stubbed to return our public key
    priv = ec.generate_private_key(ec.SECP256R1())
    es = pyjwt.encode(claims, priv, algorithm="ES256", headers={"kid": "k1"})

    class _StubKey:
        key = priv.public_key()

    class _StubClient:
        def get_signing_key_from_jwt(self, token):
            return _StubKey()

    monkeypatch.setattr(service, "_jwks_client", lambda url: _StubClient())
    assert verify_supabase_token(es, settings)["email"] == "n@x.org"

    # audience is enforced on the asymmetric path too
    bad_aud = pyjwt.encode({**claims, "aud": "elsewhere"}, priv, algorithm="ES256")
    with pytest.raises(AuthError):
        verify_supabase_token(bad_aud, settings)

    # unsupported algorithms never reach a key lookup
    none_tok = pyjwt.encode(claims, None, algorithm="none")
    with pytest.raises(AuthError) as exc:
        verify_supabase_token(none_tok, settings)
    assert "unsupported alg" in str(exc.value)


def test_profile_setup_is_self_service_and_shows_once(client, db):
    """PATCH /auth/me: the person's own name + avatar, and ANY call (even the
    'maybe later' empty one) stamps profile_setup_at so the first-login setup
    appears exactly once. Roles and identifiers stay admin-managed."""
    mk_user(db, "floor@test.local", (Role.SHOPPE_FLOOR, None, None))
    headers = login(client, "floor@test.local")

    # fresh user: the setup is owed
    assert client.get("/api/v1/auth/me", headers=headers).json()["needs_profile_setup"] is True

    r = client.patch(
        "/api/v1/auth/me",
        json={"display_name": "  Anandi  ", "avatar_icon": "lotus", "avatar_color": "#a91226"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["display_name"] == "Anandi"  # stripped
    assert body["avatar_icon"] == "lotus" and body["avatar_color"] == "#a91226"
    assert body["needs_profile_setup"] is False

    # bounds: empty name refused, icon charset and color shape enforced
    assert client.patch(
        "/api/v1/auth/me", json={"display_name": "   "}, headers=headers
    ).status_code == 422
    assert client.patch(
        "/api/v1/auth/me", json={"avatar_icon": "Nope Icon!"}, headers=headers
    ).status_code == 422
    assert client.patch(
        "/api/v1/auth/me", json={"avatar_color": "red"}, headers=headers
    ).status_code == 422

    # a failed validation changed nothing
    body = client.get("/api/v1/auth/me", headers=headers).json()
    assert body["display_name"] == "Anandi" and body["avatar_icon"] == "lotus"


def test_profile_skip_still_counts_as_seen(client, db):
    mk_user(db, "shy@test.local", (Role.SHOPPE_FLOOR, None, None))
    headers = login(client, "shy@test.local")
    r = client.patch("/api/v1/auth/me", json={}, headers=headers)  # "maybe later"
    assert r.status_code == 200
    assert r.json()["needs_profile_setup"] is False
    assert r.json()["avatar_icon"] == ""  # nothing was chosen, nothing invented
