from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import utcnow
from ..ratelimit import client_key, enforce, enforce_login_limits
from ..schemas import UserOut, user_out
from .deps import AuthedUser, get_current_user
from .service import (
    AuthError,
    create_session_token,
    find_user_by_identifier,
    issue_code,
    match_supabase_claims_to_user,
    revoke_sessions,
    verify_code,
    verify_supabase_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Shown when a dev-only endpoint is called on a server that isn't in dev auth.
_NOT_DEV_AUTH = "This server uses Supabase sign-in — use the buttons on the sign-in page."


class AuthConfigOut(BaseModel):
    mode: str
    supabase_url: str = ""
    supabase_anon_key: str = ""
    # OAuth providers the sign-in page should offer (supabase mode only).
    oauth_providers: list[str] = []
    # Whether to also offer the email/SMS one-time-code form.
    otp_enabled: bool = True


@router.get("/config", response_model=AuthConfigOut)
def auth_config(settings: Settings = Depends(get_settings)) -> AuthConfigOut:
    return AuthConfigOut(
        mode=settings.auth_mode,
        supabase_url=settings.supabase_url,
        supabase_anon_key=settings.supabase_anon_key,
        # Dev auth has no provider buttons — the code form is the only way in.
        oauth_providers=[] if settings.dev_auth else settings.oauth_provider_list,
        otp_enabled=settings.dev_auth or settings.supabase_otp_enabled,
    )


class RequestCodeIn(BaseModel):
    identifier: str  # email address or phone number


class RequestCodeOut(BaseModel):
    sent: bool
    channel: str
    # Dev mode only: the code is returned so the local demo works with no
    # email/SMS delivery. Gated on settings.dev_auth — the ENVIRONMENT must be a
    # development one, not just the auth mode — so a mode mistake alone can never
    # hand a login code to an anonymous caller.
    dev_code: str | None = None


@router.post("/request-code", response_model=RequestCodeOut)
def request_code(
    body: RequestCodeIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RequestCodeOut:
    if not settings.dev_auth:
        raise HTTPException(400, _NOT_DEV_AUTH)
    enforce_login_limits(settings, request, body.identifier)
    # Uniform response: a 404 here told any anonymous caller which emails and
    # phone numbers have accounts. Unknown and inactive identifiers now get the
    # same shape as a real one.
    user = find_user_by_identifier(db, body.identifier)
    if user is None or not user.is_active:
        return RequestCodeOut(
            sent=True, channel="email" if "@" in body.identifier else "sms", dev_code=None
        )
    try:
        code = issue_code(db, user, settings)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e
    return RequestCodeOut(sent=True, channel="email" if user.email else "sms", dev_code=code)


class VerifyIn(BaseModel):
    identifier: str
    code: str


class SessionOut(BaseModel):
    token: str
    user: UserOut


@router.post("/verify", response_model=SessionOut)
def verify(
    body: VerifyIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    if not settings.dev_auth:
        raise HTTPException(400, _NOT_DEV_AUTH)
    enforce_login_limits(settings, request, body.identifier)
    user = find_user_by_identifier(db, body.identifier)
    # One failure for an unknown identifier and for a wrong code — no oracle.
    if user is None or not user.is_active or not verify_code(db, user, body.code, settings):
        raise HTTPException(401, "That code is wrong or expired. Request a fresh one.")
    return SessionOut(token=create_session_token(user, settings), user=user_out(user))


class ExchangeIn(BaseModel):
    supabase_token: str


@router.post("/exchange", response_model=SessionOut)
def exchange(
    body: ExchangeIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    """Supabase mode: swap a verified Supabase session for an app session. This
    is the landing point for Google OAuth as well as email/SMS OTP — the app
    only ever sees the resulting Supabase JWT, never a Google credential."""
    if settings.auth_mode != "supabase":
        raise HTTPException(400, "This server is in dev-auth mode.")
    # Verifying a JWT is cheap, but the JWKS path can reach out to Supabase.
    enforce(settings, "auth:exchange", client_key(request), limit=60, per_seconds=300)
    try:
        claims = verify_supabase_token(body.supabase_token, settings)
        # Raises when an UNVERIFIED identifier would otherwise match an account.
        user = match_supabase_claims_to_user(db, claims)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e
    if user is None or not user.is_active:
        raise HTTPException(
            403, "You're signed in, but no app account matches this email/phone. Ask an admin."
        )
    user.last_login_at = utcnow()
    db.commit()
    return SessionOut(token=create_session_token(user, settings), user=user_out(user))


@router.get("/me", response_model=UserOut)
def me(authed: AuthedUser = Depends(get_current_user)) -> UserOut:
    return user_out(authed.user)


class MeUpdateIn(BaseModel):
    # None = leave alone. display_name must survive a strip() non-empty; the
    # avatar fields accept "" to clear. Icon ids are frontend art names
    # (avatars.tsx owns the set) — the backend only bounds their shape.
    display_name: str | None = Field(default=None, max_length=160)
    avatar_icon: str | None = Field(default=None, max_length=40, pattern=r"^[a-z0-9-]*$")
    avatar_color: str | None = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6})?$")


@router.patch("/me", response_model=UserOut)
def update_me(
    body: MeUpdateIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> UserOut:
    """Self-service profile: the person's own name and avatar, nothing else —
    roles, contact identifiers and activation stay admin-managed. ANY call,
    even an empty one (the "maybe later" button), records that the first-login
    setup has been seen, so it appears exactly once."""
    user = authed.user
    if body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            raise HTTPException(422, "The name can't be empty.")
        user.display_name = name
    if body.avatar_icon is not None:
        user.avatar_icon = body.avatar_icon
    if body.avatar_color is not None:
        user.avatar_color = body.avatar_color
    user.profile_setup_at = user.profile_setup_at or utcnow()
    db.commit()
    return user_out(user)


@router.post("/logout-everywhere", status_code=204)
def logout_everywhere(
    authed: AuthedUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    """Retire every session for the caller, including this one — the answer to
    "I lost my phone". Ordinary sign-out stays client-side."""
    revoke_sessions(db, authed.user)
