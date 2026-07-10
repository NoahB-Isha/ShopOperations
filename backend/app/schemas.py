"""Shared API schemas (kept small; feature routers own their own shapes)."""
from __future__ import annotations

from pydantic import BaseModel

from .models import User


class RoleOut(BaseModel):
    role: str
    zone_id: int | None = None
    zone_name: str | None = None
    center_id: int | None = None
    center_name: str | None = None


class UserOut(BaseModel):
    id: int
    email: str | None
    phone: str | None
    display_name: str
    is_active: bool
    roles: list[RoleOut]


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=[
            RoleOut(
                role=a.role,
                zone_id=a.zone_id,
                zone_name=a.zone.name if a.zone else None,
                center_id=a.center_id,
                center_name=a.center.name if a.center else None,
            )
            for a in user.roles
        ],
    )
