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
import logging
import re
import secrets
from datetime import timedelta
from functools import lru_cache

import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import LoginCode, User, utcnow

log = logging.getLogger(__name__)


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
            # epoch at mint time; bumping users.token_epoch retires the token
            "ep": int(user.token_epoch or 0),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=settings.session_days)).timestamp()),
            "iss": "shop-ops",
        },
        settings.app_jwt_secret,
        algorithm="HS256",
    )


def decode_session_token(token: str, settings: Settings) -> tuple[int, int]:
    """(user id, token epoch). Tokens minted before the epoch existed read as 0,
    which matches the column's server_default."""
    try:
        payload = jwt.decode(
            token, settings.app_jwt_secret, algorithms=["HS256"], issuer="shop-ops"
        )
        return int(payload["sub"]), int(payload.get("ep") or 0)
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise AuthError("Invalid or expired session.", 401) from e


def revoke_sessions(db: Session, user: User) -> None:
    """Invalidate every outstanding session for this user, right now."""
    user.token_epoch = int(user.token_epoch or 0) + 1
    db.commit()


# ------------------------------------------------------------- supabase mode
@lru_cache(maxsize=4)
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    """Cached JWKS fetcher for projects on Supabase's asymmetric signing keys
    (the public keys live at a well-known URL; PyJWKClient caches them)."""
    return jwt.PyJWKClient(
        f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
        timeout=10,
    )


def verify_supabase_token(token: str, settings: Settings) -> dict:
    """Verify a Supabase Auth access token and return its claims. Handles both
    signing schemes: legacy projects sign HS256 with the shared JWT secret;
    newer projects sign ES256/RS256 with asymmetric keys we verify against the
    project's public JWKS endpoint. Used by /auth/exchange in supabase mode."""
    try:
        alg = str(jwt.get_unverified_header(token).get("alg", ""))
    except jwt.PyJWTError as e:
        raise AuthError(f"Supabase token rejected: {e}", 401) from e
    try:
        if alg == "HS256":
            if not settings.supabase_jwt_secret.get_secret_value():
                raise AuthError("Supabase auth is not configured on the server.", 500)
            return jwt.decode(
                token,
                settings.supabase_jwt_secret.get_secret_value(),
                algorithms=["HS256"],
                audience="authenticated",
            )
        if alg in ("ES256", "RS256"):
            if not settings.supabase_url:
                raise AuthError("Supabase auth is not configured on the server.", 500)
            key = _jwks_client(settings.supabase_url).get_signing_key_from_jwt(token).key
            return jwt.decode(token, key, algorithms=[alg], audience="authenticated")
        raise AuthError(f"Supabase token rejected: unsupported alg {alg!r}.", 401)
    except AuthError:
        raise
    except (jwt.PyJWTError, OSError, ValueError) as e:
        # PyJWKClientError (bad/unreachable JWKS) subclasses PyJWTError;
        # OSError covers network failures fetching the keys
        raise AuthError(f"Supabase token rejected: {e}", 401) from e


def _claim_is_verified(claims: dict, field: str) -> bool:
    """True only when the token positively asserts the identifier is verified.

    Sources are checked most-trustworthy first and the first one carrying the
    field wins: top-level and `app_metadata` are set by Supabase/the identity
    provider, while `user_metadata` is client-writable at signup — so a client
    can supply a value there but can never override a real one. Anything missing
    or unparseable counts as UNVERIFIED (fail closed). Google OAuth populates
    this; email/password signups without confirmation do not."""
    for source in (claims, claims.get("app_metadata") or {}, claims.get("user_metadata") or {}):
        if not isinstance(source, dict):
            continue
        value = source.get(field)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == "true"
    return False


def match_supabase_claims_to_user(db: Session, claims: dict) -> User | None:
    """Map a Supabase identity onto an app user by auth_uid, then email, then
    phone. First match by contact info links the auth_uid permanently.

    Because that link hands over the app account's roles, it is only ever made
    on an identifier Supabase says it VERIFIED. Without that check, anyone able
    to sign up on the Supabase project could claim an admin's address and keep
    it — so the project must also offer only trusted providers (Google OAuth)
    and require confirmation on any other flow."""
    uid = claims.get("sub")
    if uid:
        user = db.scalar(select(User).where(User.auth_uid == uid))
        if user:
            return user  # already linked, and the link was verified when made
    email = normalize_email(claims.get("email"))
    phone = normalize_phone(claims.get("phone"))
    email_ok = bool(email) and _claim_is_verified(claims, "email_verified")
    phone_ok = bool(phone) and _claim_is_verified(claims, "phone_verified")
    user = None
    if email_ok:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None and phone_ok:
        user = db.scalar(select(User).where(User.phone == phone))
    if user is None:
        if (email and not email_ok) or (phone and not phone_ok):
            # An unverified identifier that would otherwise match an app user is
            # a takeover attempt, not a misconfiguration to paper over.
            log.warning(
                "Refused to link Supabase uid %r on an unverified identifier.", uid or "?"
            )
            raise AuthError(
                "Your sign-in provider hasn't confirmed that email or phone number, so it "
                "can't be matched to an app account. Sign in with Google, or ask an admin.",
                403,
            )
        return None
    if uid and not user.auth_uid:
        user.auth_uid = uid
        db.commit()
    return user
