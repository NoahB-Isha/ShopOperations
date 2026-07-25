"""Restock API: the floor + back-stock checklists, phone-first.

Reads fold the accumulator lazily (a no-op unless a day has rolled over
since the last fold), so the lists refresh with every sales sync without a
scheduler hook. Check-off state is per-line for the floor list and per-day
for the back list — both read fresh each morning.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import AuthedUser, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    Product,
    RestockCheckoff,
    RestockFoldState,
    RestockLine,
    Role,
    StockLevel,
    SyncState,
    User,
    utcnow,
)
from .engine import BACK_LIST, FLOOR_LIST, back_list, floor_list, fold_floor_restock, reset_floor

router = APIRouter(
    prefix="/restock",
    tags=["restock"],
    dependencies=[Depends(require_roles(Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE))],
)


class FloorItemOut(BaseModel):
    line_id: int
    product_id: int
    sku: str
    name: str
    category: str
    qty: float
    flagged_on: date
    floor_qty: float
    bwhse_qty: float
    checked: bool


class BackItemOut(BaseModel):
    product_id: int
    sku: str
    name: str
    category: str
    floor_qty: float
    bwhse_qty: float
    avg_daily: float
    days_of_cover: float | None
    suggested_qty: float
    checked: bool


class RestockMetaOut(BaseModel):
    today: date
    folded_through: date | None
    sales_synced_at: datetime | None
    floor_threshold: float
    low_cover_days: float
    target_cover_days: float
    avg_window_days: int
    # last "floor fully stocked" reset — lets an empty list explain itself
    last_reset_at: datetime | None = None
    last_reset_by: str = ""


class RestockOut(BaseModel):
    floor: list[FloorItemOut]
    back: list[BackItemOut]
    meta: RestockMetaOut


def _product_map(db: Session, ids: set[int]) -> dict[int, Product]:
    if not ids:
        return {}
    return {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(ids)))}


def _stock_map(db: Session, ids: set[int]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    if not ids:
        return out
    for pid, key, qty in db.execute(
        select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
            StockLevel.product_id.in_(ids)
        )
    ):
        out.setdefault(pid, {})[key] = float(qty)
    return out


@router.get("", response_model=RestockOut)
def get_restock(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RestockOut:
    today = utcnow().date()
    fold_floor_restock(db, settings, today)

    floor_items = floor_list(db, today)
    back_items = back_list(db, settings, today)

    pids = {i.product_id for i in floor_items} | {i.product_id for i in back_items}
    products = _product_map(db, pids)
    stock = _stock_map(db, {i.product_id for i in floor_items})

    floor_out = []
    for item in floor_items:
        p = products.get(item.product_id)
        if p is None:
            continue
        s = stock.get(item.product_id, {})
        floor_out.append(
            FloorItemOut(
                line_id=item.line_id,
                product_id=item.product_id,
                sku=p.global_sku,
                name=p.name,
                category=p.category,
                qty=item.qty,
                flagged_on=item.flagged_on,
                floor_qty=s.get("floor", 0.0),
                bwhse_qty=s.get("bwhse", 0.0),
                checked=item.checked,
            )
        )

    back_out = []
    for back_item in back_items:
        p = products.get(back_item.product_id)
        if p is None:
            continue
        back_out.append(
            BackItemOut(
                product_id=back_item.product_id,
                sku=p.global_sku,
                name=p.name,
                category=p.category,
                floor_qty=back_item.floor_qty,
                bwhse_qty=back_item.bwhse_qty,
                avg_daily=back_item.avg_daily,
                days_of_cover=back_item.days_of_cover,
                suggested_qty=back_item.suggested_qty,
                checked=back_item.checked,
            )
        )

    sales_state = db.get(SyncState, "sales")
    fold_state = db.get(RestockFoldState, 1)
    return RestockOut(
        floor=floor_out,
        back=back_out,
        meta=_meta(db, settings, today, fold_state, sales_state),
    )


def _meta(
    db: Session,
    settings: Settings,
    today: date,
    fold_state: RestockFoldState | None,
    sales_state: SyncState | None,
) -> RestockMetaOut:
    reset_by = ""
    if fold_state and fold_state.last_reset_by_id:
        u = db.get(User, fold_state.last_reset_by_id)
        if u:
            reset_by = u.display_name or u.email or f"user {u.id}"
    return RestockMetaOut(
        today=today,
        folded_through=fold_state.folded_through if fold_state else None,
        sales_synced_at=sales_state.last_success_at if sales_state else None,
        floor_threshold=float(settings.restock_floor_threshold),
        low_cover_days=float(settings.restock_low_cover_days),
        target_cover_days=float(settings.restock_target_cover_days),
        avg_window_days=int(settings.restock_avg_window_days),
        last_reset_at=fold_state.last_reset_at if fold_state else None,
        last_reset_by=reset_by,
    )


class ResetOut(BaseModel):
    lines_cleared: int
    accumulators_zeroed: int
    meta: RestockMetaOut


@router.post("/floor/reset", response_model=ResetOut)
def reset_floor_list(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE)),
) -> ResetOut:
    """'The floor is fully stocked': wipe the checklist, zero the counters,
    and give today amnesty — counting resumes with tomorrow's sales. For the
    morning after a full physical restock."""
    today = utcnow().date()
    result = reset_floor(db, today, actor_user_id=authed.id)
    return ResetOut(
        lines_cleared=result["lines_cleared"],
        accumulators_zeroed=result["accumulators_zeroed"],
        meta=_meta(db, settings, today, db.get(RestockFoldState, 1), db.get(SyncState, "sales")),
    )


class CheckIn(BaseModel):
    checked: bool


@router.post("/floor/{line_id}/check", response_model=FloorItemOut)
def check_floor_line(
    line_id: int,
    body: CheckIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE)),
) -> FloorItemOut:
    line = db.get(RestockLine, line_id)
    if line is None or line.list_type != FLOOR_LIST:
        raise HTTPException(404, "Restock line not found.")
    line.checked_off_at = utcnow() if body.checked else None
    line.checked_off_by_id = authed.id if body.checked else None
    db.commit()

    p = db.get(Product, line.product_id)
    s = _stock_map(db, {line.product_id}).get(line.product_id, {})
    return FloorItemOut(
        line_id=line.id,
        product_id=line.product_id,
        sku=p.global_sku if p else "",
        name=p.name if p else "",
        category=p.category if p else "",
        qty=line.qty,
        flagged_on=line.flagged_on,
        floor_qty=s.get("floor", 0.0),
        bwhse_qty=s.get("bwhse", 0.0),
        checked=line.checked_off_at is not None,
    )


class BackCheckIn(BaseModel):
    checked: bool
    list_type: Literal["back"] = "back"


@router.post("/back/{product_id}/check")
def check_back_item(
    product_id: int,
    body: BackCheckIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE)),
) -> dict:
    if db.get(Product, product_id) is None:
        raise HTTPException(404, "Product not found.")
    today = utcnow().date()
    existing = db.scalar(
        select(RestockCheckoff).where(
            RestockCheckoff.day == today,
            RestockCheckoff.list_type == BACK_LIST,
            RestockCheckoff.product_id == product_id,
        )
    )
    if body.checked and existing is None:
        db.add(
            RestockCheckoff(
                day=today, list_type=BACK_LIST, product_id=product_id, checked_by_id=authed.id
            )
        )
    elif not body.checked and existing is not None:
        db.delete(existing)
    db.commit()
    return {"product_id": product_id, "checked": body.checked, "day": today.isoformat()}
