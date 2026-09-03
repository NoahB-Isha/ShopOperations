"""Shared API schemas (kept small; feature routers own their own shapes)."""
from __future__ import annotations

from pydantic import BaseModel

from .models import User


class RoleOut(BaseModel):
    role: str
    zone_id: int | None = None
    zone_name: str | None = None
    # field | departments — the app's "center" vs "department" wording keys off
    # this, now that a departments reviewer is just an Order Reviewer whose
    # review zone happens to be III Departments
    zone_kind: str | None = None
    center_id: int | None = None
    center_name: str | None = None


class UserOut(BaseModel):
    id: int
    email: str | None
    phone: str | None
    display_name: str
    # the picked profile art (avatars.tsx owns the icon ids) — empty until chosen
    avatar_icon: str = ""
    avatar_color: str = ""
    # true until the person has been through the first-login setup (save OR skip)
    needs_profile_setup: bool = False
    is_active: bool
    roles: list[RoleOut]


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        phone=user.phone,
        display_name=user.display_name,
        avatar_icon=user.avatar_icon or "",
        avatar_color=user.avatar_color or "",
        needs_profile_setup=user.profile_setup_at is None,
        is_active=user.is_active,
        roles=[
            RoleOut(
                role=a.role,
                zone_id=a.zone_id,
                zone_name=a.zone.name if a.zone else None,
                zone_kind=a.zone.kind if a.zone else None,
                center_id=a.center_id,
                center_name=a.center.name if a.center else None,
            )
            for a in user.roles
        ],
    )
