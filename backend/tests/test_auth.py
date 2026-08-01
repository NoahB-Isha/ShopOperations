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


def test_unknown_identifier_is_friendly(client, db):
    r = client.post("/api/v1/auth/request-code", json={"identifier": "ghost@test.local"})
    assert r.status_code == 404
    assert "invite" in r.json()["detail"].lower()


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
    r = client.post("/api/v1/auth/request-code", json={"identifier": "gone@test.local"})
    assert r.status_code == 404


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
        verify_supabase_token(hs, settings.model_copy(update={"supabase_jwt_secret": "other"}))

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
