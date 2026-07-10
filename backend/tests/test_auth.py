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


def test_request_throttle(client, db):
    mk_user(db, "eager@test.local", (Role.ADMIN, None, None))
    assert client.post("/api/v1/auth/request-code", json={"identifier": "eager@test.local"}).status_code == 200
    r = client.post("/api/v1/auth/request-code", json={"identifier": "eager@test.local"})
    assert r.status_code == 429


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
