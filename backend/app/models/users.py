from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JSONVariant, TimestampMixin


class Role(str, enum.Enum):
    """The six user types. The stored keys are historical and deliberately
    unchanged — renaming them would rewrite every permission check, every
    test and every seeded row for no user-visible gain. What people SEE lives
    in the frontend's ROLE_LABELS (2026-08-13):

        admin            → Admin
        warehouse        → Warehouse Team
        shoppe_floor     → Inventory Flow Manager
        floor_rotating   → Floor Team
        zone_coordinator → Order Reviewer
        center_orderer   → Order Requester

    The separate dept_liaison / dept_orderer roles were FOLDED IN on
    2026-08-13: a departments reviewer is simply an Order Reviewer whose
    review zone is "III Departments", and a departments requester is an Order
    Requester whose center is a department. Everything that behaves
    differently for departments already keys off the ZONE's kind, never off
    the role (see center_orders/catalog.py and service.py), so the merge cost
    nothing behaviourally.
    """

    ADMIN = "admin"
    WAREHOUSE = "warehouse"
    SHOPPE_FLOOR = "shoppe_floor"
    # Floor Team: the Inventory Flow Manager's views minus the ability to
    # create (or edit the lines of) transfer requests.
    FLOOR_ROTATING = "floor_rotating"
    ZONE_COORDINATOR = "zone_coordinator"
    CENTER_ORDERER = "center_orderer"


# Roles whose row-scope is a review zone / a center.
ZONE_SCOPED_ROLES = {Role.ZONE_COORDINATOR}
CENTER_SCOPED_ROLES = {Role.CENTER_ORDERER}
SEE_EVERYTHING_ROLES = {Role.ADMIN, Role.WAREHOUSE, Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING}


class ZoneKind(str, enum.Enum):
    FIELD = "field"  # city-center zones (Lili, Mik, Ravi, Vivek, Canada)
    DEPARTMENTS = "departments"  # III Departments pseudo-zone


class Zone(Base, TimestampMixin):
    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default=ZoneKind.FIELD.value)
    # As found in the coordinator sheet ("1.0".."4.0"); informational only.
    sheet_code: Mapped[str | None] = mapped_column(String(20))

    centers: Mapped[list[Center]] = relationship(back_populates="zone")


class Center(Base, TimestampMixin):
    """A city-center pop-up shop — or, in the III Departments zone, a campus
    department that orders from the Shoppe."""

    __tablename__ = "centers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"))
    name: Mapped[str] = mapped_column(String(160), unique=True)
    city: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(80), default="")
    region: Mapped[str] = mapped_column(String(80), default="")
    country: Mapped[str] = mapped_column(String(2), default="US")  # US | CA

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Raw activity answer from the sheet ("Yes"/"No"/"?"/"Temporary"/"NA") so
    # ambiguity stays visible instead of being silently coerced.
    activity_raw: Mapped[str] = mapped_column(String(40), default="")

    address: Mapped[str] = mapped_column(Text, default="")
    stripe_terminal_name: Mapped[str] = mapped_column(String(120), default="")
    stripe_terminal_serial: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    # Centers that sell from one shared product set (Austin/San Antonio,
    # Mountain View/San Ramon) carry the same group label.
    shared_product_group: Mapped[str | None] = mapped_column(String(80))

    # Odoo internal location (III/CityCenter/<City>), rediscovered by every
    # stock sync via name match. Null = unmapped — order-list approval for
    # this center can only dry-run until an admin fixes the name mismatch.
    odoo_location_id: Mapped[int | None] = mapped_column(Integer)
    odoo_location_name: Mapped[str] = mapped_column(String(255), default="")

    needs_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_reasons: Mapped[list] = mapped_column(JSONVariant, default=list)

    zone: Mapped[Zone | None] = relationship(back_populates="centers")
    contacts: Mapped[list[CenterContact]] = relationship(
        back_populates="center", cascade="all, delete-orphan"
    )


class CenterContact(Base, TimestampMixin):
    """A person attached to a center in the coordinator sheet. Kept separate
    from app users: contacts are roster data; users are login identities."""

    __tablename__ = "center_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    name: Mapped[str] = mapped_column(String(160), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    role_note: Mapped[str] = mapped_column(String(160), default="")  # e.g. "Shoppe", "IL Satsang"
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    center: Mapped[Center] = relationship(back_populates="contacts")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL", name="email_or_phone_present"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)  # stored lowercased
    phone: Mapped[str | None] = mapped_column(String(40), unique=True)  # normalized +1…
    display_name: Mapped[str] = mapped_column(String(160), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_uid: Mapped[str | None] = mapped_column(String(64), unique=True)  # Supabase user id
    invited_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Bumped to invalidate every existing session for this user (logout
    # everywhere, role change, suspected compromise). Session tokens carry the
    # epoch they were minted at; a mismatch is a 401.
    token_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    roles: Mapped[list[RoleAssignment]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="RoleAssignment.user_id"
    )


class RoleAssignment(Base, TimestampMixin):
    """A role a user holds, optionally scoped to a zone or a center.
    A user may hold several (e.g. coordinator of zone 3 AND orderer at one center)."""

    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role", "zone_id", "center_id", name="uq_role_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(30))
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("zones.id"))
    center_id: Mapped[int | None] = mapped_column(ForeignKey("centers.id"))

    user: Mapped[User] = relationship(back_populates="roles", foreign_keys=[user_id])
    zone: Mapped[Zone | None] = relationship()
    center: Mapped[Center | None] = relationship()


class LoginCode(Base):
    """A one-time login code (dev-mode auth). Supabase mode delegates OTP to
    Supabase Auth; this table is unused there."""

    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    code_hash: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(10))  # email | sms
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
