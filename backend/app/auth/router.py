from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_db
from ..models import utcnow
from ..schemas import UserOut, user_out
from .deps import AuthedUser, get_current_user
from .service import (
    AuthError,
    create_session_token,
    find_user_by_identifier,
    issue_code,
    match_supabase_claims_to_user,
    verify_code,
    verify_supabase_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthConfigOut(BaseModel):
    mode: str
    supabase_url: str = ""
    supabase_anon_key: str = ""


@router.get("/config", response_model=AuthConfigOut)
def auth_config(settings: Settings = Depends(get_settings)) -> AuthConfigOut:
    return AuthConfigOut(
        mode=settings.auth_mode,
        supabase_url=settings.supabase_url,
        supabase_anon_key=settings.supabase_anon_key,
    )


class RequestCodeIn(BaseModel):
    identifier: str  # email address or phone number


class RequestCodeOut(BaseModel):
    sent: bool
    channel: str
    # Dev mode only: the code is returned so the local demo works with no
    # email/SMS delivery. Never populated in supabase mode.
    dev_code: str | None = None


@router.post("/request-code", response_model=RequestCodeOut)
def request_code(
    body: RequestCodeIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RequestCodeOut:
    if settings.auth_mode != "dev":
        raise HTTPException(400, "This server uses Supabase OTP — sign in via the app.")
    user = find_user_by_identifier(db, body.identifier)
    if user is None or not user.is_active:
        raise HTTPException(
            404, "No account found for that email or phone. Ask an admin for an invite."
        )
    try:
        code = issue_code(db, user, settings)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e
    channel = "email" if user.email else "sms"
    return RequestCodeOut(sent=True, channel=channel, dev_code=code)


class VerifyIn(BaseModel):
    identifier: str
    code: str


class SessionOut(BaseModel):
    token: str
    user: UserOut


@router.post("/verify", response_model=SessionOut)
def verify(
    body: VerifyIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    if settings.auth_mode != "dev":
        raise HTTPException(400, "This server uses Supabase OTP — sign in via the app.")
    user = find_user_by_identifier(db, body.identifier)
    if user is None or not user.is_active:
        raise HTTPException(404, "No account found for that email or phone.")
    if not verify_code(db, user, body.code, settings):
        raise HTTPException(401, "That code is wrong or expired. Request a fresh one.")
    return SessionOut(token=create_session_token(user, settings), user=user_out(user))


class ExchangeIn(BaseModel):
    supabase_token: str


@router.post("/exchange", response_model=SessionOut)
def exchange(
    body: ExchangeIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    """Supabase mode: swap a verified Supabase session for an app session."""
    if settings.auth_mode != "supabase":
        raise HTTPException(400, "This server is in dev-auth mode.")
    try:
        claims = verify_supabase_token(body.supabase_token, settings)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e
    user = match_supabase_claims_to_user(db, claims)
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
