"""Inventory time machine API. Office + warehouse eyes."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth.deps import require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Role
from . import service

router = APIRouter(
    prefix="/time-machine",
    tags=["time-machine"],
    dependencies=[Depends(require_roles(Role.WAREHOUSE))],
)


@router.get("/bounds")
def get_bounds(
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    return service.bounds(db, settings)


@router.get("")
def get_view(
    date_: date = Query(alias="date"),
    category: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return service.view(db, settings, date_, category=category, q=q).as_dict()
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
