"""Request-scoped auth dependencies: current user, role guards, row scoping."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    CENTER_SCOPED_ROLES,
    SEE_EVERYTHING_ROLES,
    ZONE_SCOPED_ROLES,
    Center,
    Role,
    User,
)
from .service import AuthError, decode_session_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthedUser:
    user: User

    @property
    def id(self) -> int:
        return self.user.id

    @property
    def role_names(self) -> set[str]:
        return {a.role for a in self.user.roles}

    def has_role(self, *roles: Role) -> bool:
        return bool(self.role_names & {r.value for r in roles})

    @property
    def sees_everything(self) -> bool:
        return bool(self.role_names & {r.value for r in SEE_EVERYTHING_ROLES})

    @property
    def scoped_zone_ids(self) -> set[int]:
        return {
            a.zone_id
            for a in self.user.roles
            if a.zone_id and Role(a.role) in ZONE_SCOPED_ROLES
        }

    @property
    def scoped_center_ids(self) -> set[int]:
        return {
            a.center_id
            for a in self.user.roles
            if a.center_id and Role(a.role) in CENTER_SCOPED_ROLES
        }


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthedUser:
    if creds is None:
        raise HTTPException(401, "Not authenticated.")
    try:
        user_id, epoch = decode_session_token(creds.credentials, settings)
    except AuthError as e:
        raise HTTPException(e.status_code, str(e)) from e
    user = db.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    if user is None or not user.is_active:
        raise HTTPException(401, "Account not found or deactivated.")
    if epoch != int(user.token_epoch or 0):
        raise HTTPException(401, "This session was signed out. Sign in again.")
    return AuthedUser(user=user)


def require_roles(*roles: Role):
    """Router guard: the user must hold at least one of the given roles.
    Admin always passes."""

    def guard(authed: AuthedUser = Depends(get_current_user)) -> AuthedUser:
        if authed.has_role(Role.ADMIN) or authed.has_role(*roles):
            return authed
        raise HTTPException(403, "You don't have access to this.")

    return guard


def visible_center_ids(db: Session, authed: AuthedUser) -> set[int] | None:
    """Row-level scope for centers: None means unrestricted (admin/warehouse/
    floor); otherwise the centers of the user's zones plus directly-assigned
    centers."""
    if authed.sees_everything:
        return None
    ids: set[int] = set(authed.scoped_center_ids)
    zone_ids = authed.scoped_zone_ids
    if zone_ids:
        rows = db.scalars(select(Center.id).where(Center.zone_id.in_(zone_ids)))
        ids.update(rows)
    return ids
