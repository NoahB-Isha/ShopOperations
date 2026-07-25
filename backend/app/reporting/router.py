"""Sales dashboard API — admin/office only (revenue lives here)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.deps import require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Role
from .narrative import answer_question, narrative
from .queries import PERIOD_KEYS, SCOPE_KEYS, breakdown, resolve_period, sales_overview

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(require_roles(Role.ADMIN))],
)


class QaIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    period: str = "3m"


def _period(period: str):
    # unknown keys quietly resolve to the default — the UI only sends known ones
    return resolve_period(period if period in PERIOD_KEYS else "3m")


def _scope(scope: str) -> str:
    return scope if scope in SCOPE_KEYS else "all"


@router.get("/sales")
def get_sales_overview(
    period: str = Query("3m"),
    scope: str = Query("all"),
    db: Session = Depends(get_db),
) -> dict:
    return sales_overview(db, _period(period), scope=_scope(scope))


@router.get("/breakdown")
def get_breakdown(
    period: str = Query("3m"),
    dim: str = Query("category", pattern="^(category|product|channel|center)$"),
    scope: str = Query("all"),
    limit: int = Query(100, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    p = _period(period)
    return {
        "period": {"key": p.key, "label": p.label},
        "dim": dim,
        "scope": _scope(scope),
        "rows": breakdown(db, p, dim=dim, limit=limit, offset=offset, scope=_scope(scope)),
    }


@router.get("/narrative")
def get_narrative(
    period: str = Query("3m"),
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return narrative(db, settings, _period(period), force=refresh)


@router.post("/qa")
def post_question(
    body: QaIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return answer_question(db, settings, _period(body.period), body.question)
