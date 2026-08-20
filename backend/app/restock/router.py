"""Restock API: the floor + back-stock checklists, phone-first.

Reads fold the accumulator lazily (a no-op unless a day has rolled over
since the last fold), so the lists refresh with every sales sync without a
scheduler hook. Check-off state is per-line for the floor list and per-day
for the back list — both read fresh each morning.

The GET is also this page's own refresh: it claims a throttled stock/sales
sync and runs it AFTER the response, so numbers stay current on a deployment
whose background worker isn't running (the hosted stack's is switched off, and
nothing had synced for two days when this was written). The phone polls every
few seconds, so the next tick shows what the refresh brought in.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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
    SuggestionSnooze,
    SyncState,
    User,
    utcnow,
)
from ..ordering.service import get_app_setting, set_app_setting
from ..sync.runner import claim_stale_refresh, refresh_domains_in_background
from . import grouping
from .engine import (
    BACK_LIST,
    FLOOR_LIST,
    back_list,
    expire_stale_lines,
    floor_list,
    fold_floor_restock,
    reset_floor,
    snooze_floor_line,
)

router = APIRouter(
    prefix="/restock",
    tags=["restock"],
    dependencies=[Depends(require_roles(Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE))],
)


class FloorItemOut(BaseModel):
    line_id: int
    product_id: int
    sku: str
    barcode: str = ""
    name: str
    category: str
    qty: float
    flagged_on: date
    # floor_qty is the number the aisle reads; bwhse_qty isn't shown on the row
    # (this list is "carry it from the back to the shelf") but rides along so a
    # "request more" swipe can put an honest warehouse figure on the transfer
    # draft it builds.
    floor_qty: float
    bwhse_qty: float = 0.0
    checked: bool
    snoozed: bool = False
    # Grouping + best-seller rank (see restock/grouping.py). `group` is the
    # aisle label; the two popularity numbers are what the sort used, exposed
    # so the UI can show "why is this first" without recomputing anything.
    group: str = ""
    popularity: float = 0.0
    group_popularity: float = 0.0


class BackItemOut(BaseModel):
    product_id: int
    sku: str
    barcode: str = ""
    name: str
    category: str
    floor_qty: float
    bwhse_qty: float
    avg_daily: float
    days_of_cover: float | None
    suggested_qty: float
    checked: bool
    group: str = ""
    popularity: float = 0.0
    group_popularity: float = 0.0


class RestockMetaOut(BaseModel):
    today: date
    folded_through: date | None
    sales_synced_at: datetime | None
    # the stock sync behind the floor/warehouse numbers on each row
    stock_synced_at: datetime | None = None
    # how long an unchecked line stays on the list (0 = forever)
    line_max_age_days: int = 7
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


def _claim_refresh(db: Session, settings: Settings) -> list[str]:
    """Which syncs this read gets to refresh. Stock first — those are the
    numbers the aisle reads off the row — and sales on a slower clock, since
    they only change the list when a day rolls over.

    Fixture mode refreshes nothing: there is no Odoo to be behind, and a sync
    from the simulator would overwrite seeded demo/test data with fixtures.
    """
    if not settings.odoo_configured:
        return []
    domains = []
    if claim_stale_refresh(db, "stock", settings.restock_refresh_stock_seconds):
        domains.append("stock")
    if claim_stale_refresh(db, "sales", settings.restock_refresh_sales_seconds):
        domains.append("sales")
    return domains


@router.get("", response_model=RestockOut)
def get_restock(
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RestockOut:
    today = utcnow().date()
    fold_floor_restock(db, settings, today)
    expire_stale_lines(db, settings, today)
    stale = _claim_refresh(db, settings)
    if stale:
        background.add_task(refresh_domains_in_background, settings, stale)

    floor_items = floor_list(db, today)
    back_items = back_list(db, settings, today)

    pids = {i.product_id for i in floor_items} | {i.product_id for i in back_items}
    products = _product_map(db, pids)
    stock = _stock_map(db, {i.product_id for i in floor_items})
    # One pass for both lists: which aisle each item belongs to, and how well
    # it sells on THIS floor. The admin's prefix overrides live in app_settings.
    grouped = grouping.assign(
        products,
        grouping.popularity(db, pids),
        get_app_setting(db, grouping.SETTING_KEY),
    )
    blank = grouping.Grouped(group=grouping.FALLBACK_GROUP, popularity=0.0, group_popularity=0.0)

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
                barcode=p.barcode or "",
                name=p.name,
                category=p.category,
                qty=item.qty,
                flagged_on=item.flagged_on,
                floor_qty=s.get("floor", 0.0),
                bwhse_qty=s.get("bwhse", 0.0),
                checked=item.checked,
                group=grouped.get(item.product_id, blank).group,
                popularity=grouped.get(item.product_id, blank).popularity,
                group_popularity=grouped.get(item.product_id, blank).group_popularity,
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
                barcode=p.barcode or "",
                name=p.name,
                category=p.category,
                floor_qty=back_item.floor_qty,
                bwhse_qty=back_item.bwhse_qty,
                avg_daily=back_item.avg_daily,
                days_of_cover=back_item.days_of_cover,
                suggested_qty=back_item.suggested_qty,
                checked=back_item.checked,
                group=grouped.get(back_item.product_id, blank).group,
                popularity=grouped.get(back_item.product_id, blank).popularity,
                group_popularity=grouped.get(back_item.product_id, blank).group_popularity,
            )
        )

    # Best-selling groups first, best sellers within them (grouping.sort_key).
    # The BACK list is deliberately NOT re-sorted: its worst-cover-first order
    # is what the Suggested items page and the transfer form's strip rely on.
    floor_out.sort(key=lambda r: grouping.sort_key(grouped.get(r.product_id, blank), r.name))

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
    stock_state = db.get(SyncState, "stock")
    return RestockMetaOut(
        today=today,
        folded_through=fold_state.folded_through if fold_state else None,
        sales_synced_at=sales_state.last_success_at if sales_state else None,
        stock_synced_at=stock_state.last_success_at if stock_state else None,
        line_max_age_days=int(settings.restock_line_max_age_days),
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


class GroupsOut(BaseModel):
    """What names the aisles, and what an admin has changed."""

    defaults: dict[str, str]
    overrides: dict[str, str]
    effective: dict[str, str]
    never_group: list[str]
    popularity_days: int


class GroupsIn(BaseModel):
    # prefix -> label. A blank label stops grouping by that prefix.
    overrides: dict[str, str]


@router.get("/groups", response_model=GroupsOut)
def get_groups(
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> GroupsOut:
    """The prefix→aisle table. Editable because the shop will coin a prefix
    long before anyone ships a release."""
    overrides = get_app_setting(db, grouping.SETTING_KEY)
    return GroupsOut(
        defaults=grouping.PREFIX_GROUPS,
        overrides={k: str(v) for k, v in overrides.items() if isinstance(k, str)},
        effective=grouping.merged_groups(overrides),
        never_group=sorted(grouping.NEVER_GROUP),
        popularity_days=grouping.POPULARITY_DAYS,
    )


@router.put("/groups", response_model=GroupsOut)
def put_groups(
    body: GroupsIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> GroupsOut:
    """Replace the overrides. Prefixes in NEVER_GROUP are dropped — CA names a
    shipping origin, not a type, and mapping it would put unrelated items in
    one aisle."""
    clean = {
        k.strip().upper(): v.strip()
        for k, v in body.overrides.items()
        if isinstance(k, str) and isinstance(v, str) and k.strip()
    }
    refused = sorted(set(clean) & grouping.NEVER_GROUP)
    for prefix in refused:
        clean.pop(prefix, None)
    if refused:
        raise HTTPException(
            422,
            f"{', '.join(refused)} can't name a group: a two-letter prefix followed by ten "
            "digits is an India import reference, so it says where an item shipped from, not "
            "what it is.",
        )
    user = db.get(User, authed.id)
    set_app_setting(db, grouping.SETTING_KEY, clean, actor=user)
    db.commit()
    return get_groups(db, authed)


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
        barcode=(p.barcode or "") if p else "",
        name=p.name if p else "",
        category=p.category if p else "",
        qty=line.qty,
        flagged_on=line.flagged_on,
        floor_qty=s.get("floor", 0.0),
        checked=line.checked_off_at is not None,
        snoozed=line.snoozed_until is not None and line.snoozed_until > utcnow().date(),
    )


class SnoozeIn(BaseModel):
    snoozed: bool = True


@router.post("/floor/{line_id}/snooze", response_model=FloorItemOut)
def snooze_floor_line_endpoint(
    line_id: int,
    body: SnoozeIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR, Role.FLOOR_ROTATING, Role.WAREHOUSE)),
) -> FloorItemOut:
    """"Not today" — swipe a line away and it returns tomorrow, qty intact.
    Same roles as check-off: whoever can work the list can defer an item."""
    line = db.get(RestockLine, line_id)
    if line is None or line.list_type != FLOOR_LIST:
        raise HTTPException(404, "Restock line not found.")
    if line.checked_off_at is not None:
        raise HTTPException(409, "That line is already checked off.")
    today = utcnow().date()
    snooze_floor_line(db, line, today, snoozed=body.snoozed)

    p = db.get(Product, line.product_id)
    s = _stock_map(db, {line.product_id}).get(line.product_id, {})
    return FloorItemOut(
        line_id=line.id,
        product_id=line.product_id,
        sku=p.global_sku if p else "",
        barcode=(p.barcode or "") if p else "",
        name=p.name if p else "",
        category=p.category if p else "",
        qty=line.qty,
        flagged_on=line.flagged_on,
        floor_qty=s.get("floor", 0.0),
        checked=False,
        snoozed=line.snoozed_until is not None and line.snoozed_until > today,
    )


class BackCheckIn(BaseModel):
    checked: bool
    list_type: Literal["back"] = "back"


class SnoozeSuggestionIn(BaseModel):
    days: int = 7


@router.post("/back/{product_id}/snooze")
def snooze_suggestion(
    product_id: int,
    body: SnoozeSuggestionIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.SHOPPE_FLOOR)),
) -> dict:
    """"Not this week." A computed suggestion can't be settled for good — the
    numbers will keep saying the same thing — so it parks for a week and
    comes back on its own. (A Floor Team ask, judged by a person, is
    dismissed permanently instead; that lives in floor_requests.)"""
    if db.get(Product, product_id) is None:
        raise HTTPException(404, "Product not found.")
    days = max(1, min(int(body.days), 90))
    until = utcnow().date() + timedelta(days=days)
    row = db.scalar(select(SuggestionSnooze).where(SuggestionSnooze.product_id == product_id))
    if row is None:
        row = SuggestionSnooze(product_id=product_id)
        db.add(row)
    row.snoozed_until = until
    row.snoozed_by_id = authed.id
    db.commit()
    return {"product_id": product_id, "snoozed_until": until.isoformat()}


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
