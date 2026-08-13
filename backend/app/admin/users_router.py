"""Admin: invite users, assign roles, deactivate. Admin-only throughout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..auth.service import normalize_email, normalize_phone
from ..db import get_db
from ..models import Center, Role, RoleAssignment, User, Zone
from ..schemas import UserOut, user_out

router = APIRouter(
    prefix="/admin/users",
    tags=["admin"],
    dependencies=[Depends(require_roles(Role.ADMIN))],
)


class RoleIn(BaseModel):
    role: Role
    zone_id: int | None = None
    center_id: int | None = None


class UserCreateIn(BaseModel):
    email: str | None = None
    phone: str | None = None
    display_name: str = ""
    roles: list[RoleIn] = []


class UserUpdateIn(BaseModel):
    display_name: str | None = None
    email: str | None = None
    phone: str | None = None
    is_active: bool | None = None
    roles: list[RoleIn] | None = None  # full replacement when provided


def _validate_contact(db: Session, email: str | None, phone: str | None, exclude_id: int | None):
    if not email and not phone:
        raise HTTPException(422, "A user needs an email or a phone number to receive codes.")
    if email:
        q = select(User).where(func.lower(User.email) == email)
        if exclude_id:
            q = q.where(User.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(409, f"A user with email {email} already exists.")
    if phone:
        q = select(User).where(User.phone == phone)
        if exclude_id:
            q = q.where(User.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(409, f"A user with phone {phone} already exists.")


def _validate_role_scopes(db: Session, roles: list[RoleIn]) -> None:
    for r in roles:
        if r.zone_id and db.get(Zone, r.zone_id) is None:
            raise HTTPException(422, f"Zone {r.zone_id} doesn't exist.")
        if r.center_id and db.get(Center, r.center_id) is None:
            raise HTTPException(422, f"Center {r.center_id} doesn't exist.")
        if r.role is Role.ZONE_COORDINATOR and not r.zone_id:
            raise HTTPException(422, f"{r.role.value} needs a review zone.")
        if r.role is Role.CENTER_ORDERER and not r.center_id:
            raise HTTPException(422, f"{r.role.value} needs a center.")


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)) -> list[UserOut]:
    users = db.scalars(
        select(User).options(selectinload(User.roles)).order_by(User.display_name, User.id)
    ).all()
    return [user_out(u) for u in users]


@router.post("", response_model=UserOut, status_code=201)
def invite_user(
    body: UserCreateIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> UserOut:
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    _validate_contact(db, email, phone, exclude_id=None)
    _validate_role_scopes(db, body.roles)
    user = User(
        email=email,
        phone=phone,
        display_name=body.display_name.strip(),
        invited_by_id=authed.id,
    )
    db.add(user)
    db.flush()
    for r in body.roles:
        db.add(
            RoleAssignment(
                user_id=user.id, role=r.role.value, zone_id=r.zone_id, center_id=r.center_id
            )
        )
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdateIn, db: Session = Depends(get_db)) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    email = normalize_email(body.email) if body.email is not None else user.email
    phone = normalize_phone(body.phone) if body.phone is not None else user.phone
    _validate_contact(db, email, phone, exclude_id=user.id)
    user.email, user.phone = email, phone
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            user.token_epoch = int(user.token_epoch or 0) + 1
    if body.roles is not None:
        _validate_role_scopes(db, body.roles)
        # A role change must not leave old sessions running the old permissions.
        user.token_epoch = int(user.token_epoch or 0) + 1
        for a in list(user.roles):
            db.delete(a)
        db.flush()
        for r in body.roles:
            db.add(
                RoleAssignment(
                    user_id=user.id, role=r.role.value, zone_id=r.zone_id, center_id=r.center_id
                )
            )
    db.commit()
    db.refresh(user)
    return user_out(user)
