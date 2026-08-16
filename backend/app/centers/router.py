"""Zones & centers, row-scoped: admin/warehouse/floor see all; coordinators
see their zones' centers; orderers see their own centers.

The centers map (admin, desktop) reads the same list endpoint — coordinates
ride along on every center from the gazetteer in geo.py — and asks
/centers/{id}/detail for the panel it opens on a click.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles, visible_center_ids
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    Center,
    CenterContact,
    Product,
    Role,
    RoleAssignment,
    User,
    Zone,
    not_blacklisted,
    utcnow,
)
from ..odoo.connection import get_connection
from ..odoo.errors import OdooError
from .geo import coordinates_for
from .sales import CenterSales, comparison_months, sales_by_center

log = logging.getLogger(__name__)

router = APIRouter(tags=["centers"])


class ZoneOut(BaseModel):
    id: int
    name: str
    kind: str
    center_count: int


class ContactOut(BaseModel):
    name: str
    email: str
    phone: str
    role_note: str


class CenterOut(BaseModel):
    id: int
    name: str
    city: str
    state: str
    region: str
    country: str
    zone_id: int | None
    zone_name: str | None
    is_active: bool
    activity_raw: str
    stripe_terminal_name: str
    needs_followup: bool
    followup_reasons: list[str]
    shared_product_group: str | None
    notes: str
    contacts: list[ContactOut]
    # Odoo III/CityCenter/… location (mapped by the stock sync); null means
    # order-list approval for this center can't write live yet
    odoo_location_id: int | None = None
    odoo_location_name: str = ""
    # map position (gazetteer, not stored) — null means we have no honest
    # placement for this center and it stays off the map, on the list
    latitude: float | None = None
    longitude: float | None = None
    # Last COMPLETE month vs the one before — a pop-up's setup against its
    # previous setup. Null = the rollup has never seen this center, which is
    # not the same as a month it sold nothing.
    sales_units: float | None = None
    sales_amount: float | None = None
    sales_prev_units: float | None = None
    sales_month: str = ""  # "2026-07"
    sales_prev_month: str = ""
    # Who is actually involved, for the list. Names only — the detail panel is
    # where contact details belong. Reviewers come from the center's ZONE.
    reviewers: list[str] = []
    requesters: list[str] = []


@router.get("/zones", response_model=list[ZoneOut])
def list_zones(
    db: Session = Depends(get_db), authed: AuthedUser = Depends(get_current_user)
) -> list[ZoneOut]:
    counts: dict[int | None, int] = {}
    for zone_id, n in db.execute(select(Center.zone_id, func.count()).group_by(Center.zone_id)):
        counts[zone_id] = n
    zones = db.scalars(select(Zone).order_by(Zone.name)).all()
    scope = None if authed.sees_everything else authed.scoped_zone_ids
    out = []
    for z in zones:
        if scope is not None and z.id not in scope:
            continue
        out.append(ZoneOut(id=z.id, name=z.name, kind=z.kind, center_count=counts.get(z.id, 0)))
    return out


@router.get("/centers", response_model=list[CenterOut])
def list_centers(
    zone_id: int | None = None,
    include_inactive: bool = True,
    q: str = "",
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> list[CenterOut]:
    query = select(Center).options(selectinload(Center.contacts), selectinload(Center.zone))
    scope = visible_center_ids(db, authed)
    if scope is not None:
        query = query.where(Center.id.in_(scope or {-1}))
    if zone_id is not None:
        query = query.where(Center.zone_id == zone_id)
    if not include_inactive:
        query = query.where(Center.is_active.is_(True))
    if q:
        query = query.where(Center.name.ilike(f"%{q}%"))
    centers = db.scalars(query.order_by(Center.name)).all()
    today = utcnow().date()
    sales = sales_by_center(db, today)
    (ly, lm), (py, pm) = comparison_months(today)
    labels = (f"{ly:04d}-{lm:02d}", f"{py:04d}-{pm:02d}")
    people = _people_index(db)
    return [
        _center_out(
            c,
            sales.get(c.id),
            labels,
            reviewers=sorted(people.by_zone.get(c.zone_id or -1, set())),
            requesters=sorted(people.by_center.get(c.id, set())),
        )
        for c in centers
    ]


@dataclass
class _PeopleIndex:
    by_zone: dict[int, set[str]]
    by_center: dict[int, set[str]]


def _people_index(db: Session) -> _PeopleIndex:
    """Reviewers per zone and requesters per center, in ONE query.

    The list shows who is involved for every row; asking per row would be 60
    round trips to render a table.
    """
    by_zone: dict[int, set[str]] = {}
    by_center: dict[int, set[str]] = {}
    rows = db.execute(
        select(RoleAssignment.role, RoleAssignment.zone_id, RoleAssignment.center_id, User)
        .join(User, User.id == RoleAssignment.user_id)
        .where(
            RoleAssignment.role.in_([Role.ZONE_COORDINATOR.value, Role.CENTER_ORDERER.value]),
            User.is_active.is_(True),
        )
    )
    for role, zone_id, center_id, user in rows:
        label = user.display_name or user.email or f"user {user.id}"
        if role == Role.ZONE_COORDINATOR.value and zone_id is not None:
            by_zone.setdefault(zone_id, set()).add(label)
        elif role == Role.CENTER_ORDERER.value and center_id is not None:
            by_center.setdefault(center_id, set()).add(label)
    return _PeopleIndex(by_zone=by_zone, by_center=by_center)


def _center_out(
    c: Center,
    sales: CenterSales | None,
    months: tuple[str, str],
    reviewers: list[str] | None = None,
    requesters: list[str] | None = None,
) -> CenterOut:
    where = _coords(c)
    return CenterOut(
        id=c.id,
        name=c.name,
        city=c.city,
        state=c.state,
        region=c.region,
        country=c.country,
        zone_id=c.zone_id,
        zone_name=c.zone.name if c.zone else None,
        is_active=c.is_active,
        activity_raw=c.activity_raw,
        stripe_terminal_name=c.stripe_terminal_name,
        needs_followup=c.needs_followup,
        followup_reasons=list(c.followup_reasons or []),
        shared_product_group=c.shared_product_group,
        notes=c.notes,
        contacts=[
            ContactOut(name=ct.name, email=ct.email, phone=ct.phone, role_note=ct.role_note)
            for ct in c.contacts
        ],
        odoo_location_id=c.odoo_location_id,
        odoo_location_name=c.odoo_location_name,
        latitude=where[0] if where else None,
        longitude=where[1] if where else None,
        sales_units=sales.units if sales else None,
        sales_amount=sales.amount if sales else None,
        sales_prev_units=sales.prev_units if sales else None,
        sales_month=months[0],
        sales_prev_month=months[1],
        reviewers=reviewers or [],
        requesters=requesters or [],
    )


def _coords(center: Center) -> tuple[float, float] | None:
    return coordinates_for(center.name, center.state, center.zone.kind if center.zone else "")


# --------------------------------------------------------------- map detail
class PersonOut(BaseModel):
    name: str
    email: str
    phone: str = ""
    note: str = ""
    # a login the app knows about, vs a name copied from the roster sheet
    is_app_user: bool = False


class CenterStockLine(BaseModel):
    sku: str
    barcode: str = ""
    name: str
    qty: float


class CenterDetailOut(BaseModel):
    id: int
    name: str
    zone_name: str | None
    reviewers: list[PersonOut]  # Order Reviewers for this center's review zone
    requesters: list[PersonOut]  # Order Requesters scoped to this center
    contacts: list[PersonOut]  # everyone else on the roster row
    stock: list[CenterStockLine]
    stock_total: float
    # Honest about where the shelf figure came from, or why there isn't one.
    stock_status: str  # "ok" | "unmapped" | "unavailable"
    stock_note: str = ""


def _person(user: User, note: str = "") -> PersonOut:
    return PersonOut(
        name=user.display_name or user.email or f"user {user.id}",
        email=user.email or "",
        phone=user.phone or "",
        note=note,
        is_app_user=True,
    )


def _center_stock(
    db: Session, settings: Settings, center: Center
) -> tuple[list[CenterStockLine], str, str]:
    """What is on this center's shelf in Odoo, right now.

    A live read rather than a synced figure: the stock sync covers the four
    warehouse/floor locations only, and adding 54 more to it for a panel
    someone opens occasionally would be a poor trade. One click, one query.
    """
    if not center.odoo_location_id:
        return (
            [],
            "unmapped",
            ("No Odoo location is mapped to this center yet, so there is no shelf to read."),
        )
    try:
        conn = get_connection(settings, read_only=True)
        quants = conn.search_read(
            "stock.quant",
            [["location_id", "child_of", center.odoo_location_id]],
            ["product_id", "quantity"],
        )
    except OdooError as e:
        log.warning("center stock read failed for %s: %s", center.name, e)
        return [], "unavailable", f"Odoo didn't answer: {e}"

    totals: dict[int, float] = {}
    for q in quants:
        field = q.get("product_id")
        odoo_pid = field[0] if isinstance(field, list) else field
        if not isinstance(odoo_pid, int):
            continue
        totals[odoo_pid] = totals.get(odoo_pid, 0.0) + float(q.get("quantity") or 0)

    products = {
        p.odoo_product_id: p
        for p in db.scalars(
            select(Product).where(
                Product.odoo_product_id.in_(list(totals) or [0]), not_blacklisted()
            )
        )
    }
    lines = [
        CenterStockLine(sku=p.global_sku, barcode=p.barcode or "", name=p.name, qty=round(qty, 2))
        for odoo_pid, qty in totals.items()
        if (p := products.get(odoo_pid)) is not None and qty
    ]
    lines.sort(key=lambda line: (-line.qty, line.name))
    return lines, "ok", ""


@router.get("/centers/{center_id}/detail", response_model=CenterDetailOut)
def center_detail(
    center_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(get_current_user),
) -> CenterDetailOut:
    center = db.get(Center, center_id)
    if center is None:
        raise HTTPException(404, "Center not found.")
    scope = visible_center_ids(db, authed)
    if scope is not None and center_id not in scope:
        raise HTTPException(404, "Center not found.")

    reviewers = [
        _person(r.user, "Order Reviewer")
        for r in db.scalars(
            select(RoleAssignment)
            .options(selectinload(RoleAssignment.user))
            .where(
                RoleAssignment.role == Role.ZONE_COORDINATOR.value,
                RoleAssignment.zone_id == center.zone_id,
            )
        )
        if center.zone_id is not None and r.user.is_active
    ]
    requesters = [
        _person(r.user, "Order Requester")
        for r in db.scalars(
            select(RoleAssignment)
            .options(selectinload(RoleAssignment.user))
            .where(
                RoleAssignment.role == Role.CENTER_ORDERER.value,
                RoleAssignment.center_id == center_id,
            )
        )
        if r.user.is_active
    ]
    # Roster people who aren't app logins still matter: for most centers they
    # are the only phone number anyone has.
    known = {p.email.lower() for p in reviewers + requesters if p.email}
    contacts = [
        PersonOut(name=ct.name, email=ct.email, phone=ct.phone, note=ct.role_note)
        for ct in center.contacts
        if (ct.email or "").lower() not in known
    ]

    stock, status, note = _center_stock(db, settings, center)
    return CenterDetailOut(
        id=center.id,
        name=center.name,
        zone_name=center.zone.name if center.zone else None,
        reviewers=reviewers,
        requesters=requesters,
        contacts=contacts,
        stock=stock,
        stock_total=round(sum(line.qty for line in stock), 2),
        stock_status=status,
        stock_note=note,
    )


# ------------------------------------------------------------ admin editing
class ContactIn(BaseModel):
    name: str = Field("", max_length=160)
    email: str = Field("", max_length=255)
    phone: str = Field("", max_length=40)
    role_note: str = Field("", max_length=160)


class CenterPatchIn(BaseModel):
    """Every field optional: null means "leave it", "" means "clear it". Same
    idiom as the user editor, so the two forms behave the same way."""

    name: str | None = Field(None, min_length=1, max_length=160)
    city: str | None = Field(None, max_length=120)
    state: str | None = Field(None, max_length=80)
    region: str | None = Field(None, max_length=80)
    country: str | None = Field(None, max_length=2)
    zone_id: int | None = None
    clear_zone: bool = False  # null zone_id can't say "unassign" on its own
    is_active: bool | None = None
    notes: str | None = None
    stripe_terminal_name: str | None = Field(None, max_length=120)
    stripe_terminal_serial: str | None = Field(None, max_length=120)
    # A full replacement when present, like role sets: the editor shows the
    # whole roster, so sending part of it would silently drop the rest.
    contacts: list[ContactIn] | None = None


@router.patch(
    "/centers/{center_id}",
    response_model=CenterOut,
    dependencies=[Depends(require_roles(Role.ADMIN))],
)
def update_center(
    center_id: int,
    body: CenterPatchIn,
    db: Session = Depends(get_db),
) -> CenterOut:
    """Edit a center and its roster in place.

    The spreadsheet used to be the source of truth and this app a mirror of
    it. It is the other way round now: the roster lives here, an import is
    something an admin chooses to run, and this is how a name, a zone or a
    phone number actually gets fixed.
    """
    center = db.get(Center, center_id)
    if center is None:
        raise HTTPException(404, "Center not found.")

    if body.name is not None:
        clash = db.scalar(
            select(Center).where(Center.name == body.name, Center.id != center_id)
        )
        if clash is not None:
            raise HTTPException(409, f"Another center is already called '{body.name}'.")
        center.name = body.name
    for field in ("city", "state", "region", "notes",
                  "stripe_terminal_name", "stripe_terminal_serial"):
        value = getattr(body, field)
        if value is not None:
            setattr(center, field, value)
    if body.country is not None:
        center.country = body.country.upper()
    if body.is_active is not None:
        center.is_active = body.is_active
        # the roster's free-text answer is what made this ambiguous in the
        # first place; a deliberate click settles it
        center.activity_raw = "Yes" if body.is_active else "No"
    if body.clear_zone:
        center.zone_id = None
    elif body.zone_id is not None:
        if db.get(Zone, body.zone_id) is None:
            raise HTTPException(422, "That review zone doesn't exist.")
        center.zone_id = body.zone_id

    if body.contacts is not None:
        for existing in list(center.contacts):
            db.delete(existing)
        db.flush()
        for row in body.contacts:
            if not (row.name or row.email or row.phone):
                continue  # an empty row in the editor is not a person
            db.add(
                CenterContact(
                    center_id=center.id,
                    name=row.name.strip(),
                    email=(row.email or "").strip().lower(),
                    phone=(row.phone or "").strip(),
                    role_note=row.role_note.strip(),
                )
            )
    db.commit()
    db.refresh(center)
    today = utcnow().date()
    sales = sales_by_center(db, today).get(center.id)
    (ly, lm), (py, pm) = comparison_months(today)
    return _center_out(center, sales, (f"{ly:04d}-{lm:02d}", f"{py:04d}-{pm:02d}"))
