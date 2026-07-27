"""Availability API — org-wide OOS + Coming Soon lists over the stock
snapshot. Powers the Out-of-stock page's Everywhere/Warehouse scopes, the
warehouse Incoming page, and the skubot bot API (via the same service).
Viewers: warehouse, floor (incl. rotating), admin (office)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth.deps import require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Role
from .service import (
    OOS_SCOPES,
    coming_soon_items,
    oos_items,
    snapshot_freshness,
)

router = APIRouter(
    prefix="/availability",
    tags=["availability"],
    dependencies=[
        Depends(require_roles(Role.WAREHOUSE, Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING))
    ],
)


# ------------------------------------------------------------------ schemas
class AvailabilityItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str
    name: str
    category: str
    bwhse_qty: float
    floor_qty: float
    staging_qty: float
    total_qty: float
    incoming_qty: float
    incoming_expected: str | None
    incoming_label: str
    last_in_stock_on: str | None
    low_count_caveat: bool


class ListMetaOut(BaseModel):
    freshness: dict[str, str | None]


# -------------------------------------------------------------------- lists
@router.get("/oos", response_model=list[AvailabilityItemOut])
def list_oos(
    scope: str = Query("org"),
    q: str | None = None,
    include_never_stocked: bool = False,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if scope not in OOS_SCOPES:
        raise HTTPException(422, f"scope must be one of {', '.join(OOS_SCOPES)}")
    return [
        i.as_dict()
        for i in oos_items(
            db, settings, scope=scope, q=q, include_never_stocked=include_never_stocked
        )
    ]


@router.get("/coming-soon", response_model=list[AvailabilityItemOut])
def list_coming_soon(
    q: str | None = None,
    within_days: int | None = Query(None, ge=1, le=365),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    return [
        i.as_dict()
        for i in coming_soon_items(db, settings, q=q, within_days=within_days)
    ]


@router.get("/meta", response_model=ListMetaOut)
def list_meta(db: Session = Depends(get_db)):
    return {"freshness": snapshot_freshness(db)}
