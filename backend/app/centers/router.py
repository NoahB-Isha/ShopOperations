"""Zones & centers, row-scoped: admin/warehouse/floor see all; coordinators
see their zones' centers; orderers see their own centers."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, visible_center_ids
from ..db import get_db
from ..models import Center, Zone

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
    return [
        CenterOut(
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
                ContactOut(
                    name=ct.name, email=ct.email, phone=ct.phone, role_note=ct.role_note
                )
                for ct in c.contacts
            ],
            odoo_location_id=c.odoo_location_id,
            odoo_location_name=c.odoo_location_name,
        )
        for c in centers
    ]
