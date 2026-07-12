"""Auth logic: identifier normalization, one-time codes, session tokens.

Two modes, one session shape:
  dev      — the backend issues 6-digit codes itself and returns them in the
             API response (local demo only; no delivery infrastructure).
  supabase — Supabase Auth delivers email/SMS OTP on the frontend; the backend
             verifies the resulting Supabase JWT and exchanges it for an app
             session token. Authorization (roles, scoping) always lives here.

Sessions are long-lived app JWTs (default 30 days) so volunteers on trusted
devices aren't re-coding daily.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import LoginCode, User, utcnow


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ------------------------------------------------------------- identifiers
def normalize_email(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    # tolerate the sheet's stray spaces ("name@bellsouth. net")
    v = v.replace(" ", "")
    if not v or "@" not in v:
        return None
    return v


def normalize_phone(value: str | None) -> str | None:
    """Normalize to +<digits>. 10-digit numbers are assumed North American."""
    raw = (value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if raw.startswith("+") and len(digits) >= 8:
        return f"+{digits}"
    if len(digits) >= 8:  # already-international without the +
        return f"+{digits}"
    return None


def parse_identifier(identifier: str) -> tuple[str | None, str | None]:
    """Return (email, phone) — exactly one set — from a free-form login field."""
    if "@" in identifier:
        return normalize_email(identifier), None
    return None, normalize_phone(identifier)


def find_user_by_identifier(db: Session, identifier: str) -> User | None:
    email, phone = parse_identifier(identifier)
    if email:
        return db.scalar(select(User).where(func.lower(User.email) == email))
    if phone:
        return db.scalar(select(User).where(User.phone == phone))
    return None


# ------------------------------------------------------------- one-time codes
def _hash_code(code: str, secret: str) -> str:
    return hashlib.sha256(f"{code}:{secret}".encode()).hexdigest()


def issue_code(db: Session, user: User, settings: Settings) -> str:
    """Create a fresh code, invalidating any outstanding ones. Raises if the
    last code is under 60s old (simple anti-hammering)."""
    now = utcnow()
    latest = db.scalar(
        select(LoginCode)
        .where(LoginCode.user_id == user.id, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    )
    # The 60s guard protects real email/SMS budgets. Dev mode sends nothing
    # (the code is shown on screen), so hammering is harmless — and e2e suites
    # legitimately re-log the same demo users within seconds.
    if settings.auth_mode != "dev" and latest is not None and latest.created_at is not None:
        created = latest.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        if (now - created).total_seconds() < 60:
            raise AuthError("A code was just sent — wait a minute before requesting another.", 429)

    # expire previous outstanding codes
    for lc in db.scalars(
        select(LoginCode).where(LoginCode.user_id == user.id, LoginCode.consumed_at.is_(None))
    ):
        lc.expires_at = now

    code = f"{secrets.randbelow(1_000_000):06d}"
    channel = "email" if user.email else "sms"
    db.add(
        LoginCode(
            user_id=user.id,
            code_hash=_hash_code(code, settings.app_jwt_secret),
            channel=channel,
            created_at=now,
            expires_at=now + timedelta(minutes=settings.otp_exp_minutes),
        )
    )
    db.commit()
    return code


MAX_CODE_ATTEMPTS = 5


def verify_code(db: Session, user: User, code: str, settings: Settings) -> bool:
    now = utcnow()
    lc = db.scalar(
        select(LoginCode)
        .where(LoginCode.user_id == user.id, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    )
    if lc is None:
        return False
    expires = lc.expires_at if lc.expires_at.tzinfo else lc.expires_at.replace(tzinfo=now.tzinfo)
    if expires <= now or lc.attempts >= MAX_CODE_ATTEMPTS:
        return False
    lc.attempts += 1
    if lc.code_hash != _hash_code(code.strip(), settings.app_jwt_secret):
        db.commit()
        return False
    lc.consumed_at = now
    user.last_login_at = now
    db.commit()
    return True


# ------------------------------------------------------------- app sessions
def create_session_token(user: User, settings: Settings) -> str:
    now = utcnow()
    return jwt.encode(
        {
            "sub": str(user.id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=settings.session_days)).timestamp()),
            "iss": "shop-ops",
        },
        settings.app_jwt_secret,
        algorithm="HS256",
    )


def decode_session_token(token: str, settings: Settings) -> int:
    try:
        payload = jwt.decode(
            token, settings.app_jwt_secret, algorithms=["HS256"], issuer="shop-ops"
        )
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise AuthError("Invalid or expired session.", 401) from e


# ------------------------------------------------------------- supabase mode
def verify_supabase_token(token: str, settings: Settings) -> dict:
    """Verify a Supabase Auth access token (HS256 with the project JWT secret)
    and return its claims. Used by /auth/exchange in supabase mode."""
    if not settings.supabase_jwt_secret:
        raise AuthError("Supabase auth is not configured on the server.", 500)
    try:
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"Supabase token rejected: {e}", 401) from e


def match_supabase_claims_to_user(db: Session, claims: dict) -> User | None:
    """Map a verified Supabase identity onto an app user by auth_uid, then
    email, then phone. First match by contact info links the auth_uid."""
    uid = claims.get("sub")
    if uid:
        user = db.scalar(select(User).where(User.auth_uid == uid))
        if user:
            return user
    email = normalize_email(claims.get("email"))
    phone = normalize_phone(claims.get("phone"))
    user = None
    if email:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None and phone:
        user = db.scalar(select(User).where(User.phone == phone))
    if user is not None and uid and not user.auth_uid:
        user.auth_uid = uid
        db.commit()
    return user
