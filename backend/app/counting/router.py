"""Inventory counting: count it, submit it, review it, recount it, apply it.

Two audiences, one module. Counters (Floor Team, Warehouse Team, Inventory
Flow Manager) create submissions; reviewers (Inventory Flow Manager, anyone
with the Inventory Wrangler add-on) work the queue.

Rules that live here rather than in the client, because every client must obey
them:

  * a product appears ONCE per submission — adding it again merges quantities;
  * the Odoo quantity stored with a count is READ BY THE SERVER at submit
    time, never taken from the browser: it is the evidence the reviewer judges
    against;
  * a reason is mandatory on reject and recount (flow.check_reason);
  * an approval applies the count to Odoo as a DRAFT adjustment, and nothing
    else ever writes stock;
  * recounts outrank first counts in the queue (flow.queue_rank).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    CountEventKind,
    CountItemStatus,
    CountStatus,
    InventoryCount,
    InventoryCountEntry,
    InventoryCountItem,
    Product,
    Role,
    RoleAssignment,
    User,
    utcnow,
)
from . import flow, locations
from .service import apply_to_odoo, event

router = APIRouter(prefix="/counts", tags=["counting"])

COUNTERS = locations.COUNTER_ROLES
REVIEWERS = locations.REVIEWER_ROLES


# ------------------------------------------------------------------ schemas
class LocationOut(BaseModel):
    key: str
    label: str
    odoo_id: int | None
    note: str


class LocationsOut(BaseModel):
    locations: list[LocationOut]
    default: str
    can_review: bool


class StockAtIn(BaseModel):
    location_key: str
    product_ids: list[int] = Field(min_length=1, max_length=500)


class StockAtOut(BaseModel):
    location_key: str
    source: str  # live | snapshot
    quantities: dict[int, float]


class CountLineIn(BaseModel):
    product_id: int
    counted_qty: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)


class CreateCountIn(BaseModel):
    location_key: str
    note: str = ""
    items: list[CountLineIn] = Field(min_length=1, max_length=500)


class EntryOut(BaseModel):
    attempt: int
    counted_qty: float
    odoo_qty: float
    odoo_qty_source: str
    delta: float
    counted_by: str
    reason: str
    created_at: datetime


class ItemEventOut(BaseModel):
    kind: str
    note: str
    actor: str
    created_at: datetime


class ItemOut(BaseModel):
    id: int
    count_id: int
    product_id: int
    sku: str
    barcode: str
    name: str
    status: str
    location_key: str
    counted_by: str
    recount_assignee: str
    recount_assignee_id: int | None
    reviewed_by: str
    reviewed_at: datetime | None
    attempts: int
    # the count that stands right now (the latest entry)
    counted_qty: float | None
    odoo_qty: float | None
    delta: float | None
    applied_qty: float | None
    picking_status: str
    picking_name: str
    picking_url: str
    picking_error: str
    entries: list[EntryOut]
    events: list[ItemEventOut]
    submitted_at: datetime


class CountOut(BaseModel):
    id: int
    display_name: str
    location_key: str
    location_label: str
    status: str
    counted_by: str
    note: str
    submitted_at: datetime
    items: list[ItemOut]
    events: list[ItemEventOut]


class CountSummaryOut(BaseModel):
    id: int
    display_name: str
    location_key: str
    location_label: str
    status: str
    counted_by: str
    submitted_at: datetime
    item_count: int
    pending_items: int
    recount_items: int


class AssigneeOut(BaseModel):
    id: int
    name: str
    roles: list[str]


class ReviewIn(BaseModel):
    note: str = ""


class RecountRequestIn(BaseModel):
    note: str = ""
    assignee_id: int | None = None


class RecountIn(BaseModel):
    counted_qty: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)


# ------------------------------------------------------------------ helpers
def _names(db: Session, ids: set[int | None]) -> dict[int, str]:
    real = {i for i in ids if i}
    if not real:
        return {}
    return {
        u.id: (u.display_name or u.email or f"user {u.id}")
        for u in db.scalars(select(User).where(User.id.in_(real)))
    }


def _label(key: str) -> str:
    return locations.LOCATION_LABELS.get(key, key)


def _load(db: Session, count_id: int) -> InventoryCount:
    count = db.scalar(
        select(InventoryCount)
        .options(
            selectinload(InventoryCount.items).selectinload(InventoryCountItem.product),
            selectinload(InventoryCount.items).selectinload(InventoryCountItem.entries),
            selectinload(InventoryCount.items).selectinload(InventoryCountItem.events),
            selectinload(InventoryCount.events),
        )
        .where(InventoryCount.id == count_id)
        .execution_options(populate_existing=True)
    )
    if count is None:
        raise HTTPException(404, "Count not found.")
    return count


def _item_out(db: Session, item: InventoryCountItem, names: dict[int, str]) -> ItemOut:
    latest = item.latest
    return ItemOut(
        id=item.id,
        count_id=item.count_id,
        product_id=item.product_id,
        sku=item.product.global_sku,
        barcode=item.product.barcode or "",
        name=item.product.name,
        status=item.status,
        location_key=item.count.location_key,
        counted_by=names.get(item.entries[0].counted_by_id or 0, "unknown")
        if item.entries
        else "unknown",
        recount_assignee=names.get(item.recount_assignee_id or 0, ""),
        recount_assignee_id=item.recount_assignee_id,
        reviewed_by=names.get(item.reviewed_by_id or 0, ""),
        reviewed_at=item.reviewed_at,
        attempts=len(item.entries),
        counted_qty=float(latest.counted_qty) if latest else None,
        odoo_qty=float(latest.odoo_qty) if latest else None,
        delta=latest.delta if latest else None,
        applied_qty=item.applied_qty,
        picking_status=item.picking_status,
        picking_name=item.odoo_picking_name,
        picking_url=item.odoo_picking_url,
        picking_error=item.picking_error,
        entries=[
            EntryOut(
                attempt=e.attempt,
                counted_qty=float(e.counted_qty),
                odoo_qty=float(e.odoo_qty),
                odoo_qty_source=e.odoo_qty_source,
                delta=e.delta,
                counted_by=names.get(e.counted_by_id or 0, "unknown"),
                reason=e.reason,
                created_at=e.created_at,
            )
            for e in item.entries
        ],
        events=[
            ItemEventOut(
                kind=ev.kind,
                note=ev.note,
                actor=names.get(ev.actor_user_id or 0, "system"),
                created_at=ev.created_at,
            )
            for ev in item.events
        ],
        submitted_at=item.count.submitted_at,
    )


def _count_out(db: Session, count: InventoryCount) -> CountOut:
    ids: set[int | None] = {count.counted_by_id}
    for item in count.items:
        ids |= {item.recount_assignee_id, item.reviewed_by_id}
        ids |= {e.counted_by_id for e in item.entries}
        ids |= {ev.actor_user_id for ev in item.events}
    ids |= {ev.actor_user_id for ev in count.events}
    names = _names(db, ids)
    return CountOut(
        id=count.id,
        display_name=count.display_name,
        location_key=count.location_key,
        location_label=_label(count.location_key),
        status=count.status,
        counted_by=names.get(count.counted_by_id or 0, "unknown"),
        note=count.note,
        submitted_at=count.submitted_at,
        items=[_item_out(db, i, names) for i in count.items],
        events=[
            ItemEventOut(
                kind=ev.kind,
                note=ev.note,
                actor=names.get(ev.actor_user_id or 0, "system"),
                created_at=ev.created_at,
            )
            for ev in count.events
            if ev.item_id is None
        ],
    )


def _resolve_location(db: Session, settings: Settings, key: str) -> locations.CountLocation:
    for loc in locations.countable_locations(db, settings):
        if loc.key == key:
            return loc
    raise HTTPException(422, f"'{key}' isn't a countable location.")


def _refresh_status(count: InventoryCount) -> None:
    count.status = flow.roll_up([i.status for i in count.items])


def _may_review(authed: AuthedUser) -> bool:
    return authed.has_role(*REVIEWERS)


# ---------------------------------------------------------------- endpoints
@router.get("/locations", response_model=LocationsOut)
def list_locations(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*COUNTERS, *REVIEWERS)),
) -> LocationsOut:
    """Where this person can count, and where they start."""
    locs = locations.countable_locations(db, settings)
    return LocationsOut(
        locations=[
            LocationOut(key=x.key, label=x.label, odoo_id=x.odoo_id, note=x.note) for x in locs
        ],
        default=locations.default_location(authed.role_names),
        can_review=_may_review(authed),
    )


@router.post("/stock-at", response_model=StockAtOut)
def stock_at(
    body: StockAtIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(*COUNTERS)),
) -> StockAtOut:
    """What Odoo says is at this location for these products, right now — the
    number the counter compares the shelf against."""
    loc = _resolve_location(db, settings, body.location_key)
    qtys, source = locations.quantities_at(db, settings, loc, body.product_ids)
    return StockAtOut(location_key=loc.key, source=source, quantities=qtys)


@router.get("/assignees", response_model=list[AssigneeOut])
def list_assignees(
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> list[AssigneeOut]:
    """Everyone who may perform a count — the recount assignment dropdown."""
    wanted = {r.value for r in COUNTERS}
    rows = db.execute(
        select(User, RoleAssignment.role)
        .join(RoleAssignment, RoleAssignment.user_id == User.id)
        .where(User.is_active.is_(True), RoleAssignment.role.in_(wanted))
        .order_by(User.display_name)
    ).all()
    by_user: dict[int, AssigneeOut] = {}
    for user, role in rows:
        entry = by_user.setdefault(
            user.id,
            AssigneeOut(id=user.id, name=user.display_name or user.email or f"user {user.id}", roles=[]),
        )
        if role not in entry.roles:
            entry.roles.append(role)
    return list(by_user.values())


@router.post("", response_model=CountOut, status_code=201)
def submit_count(
    body: CreateCountIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*COUNTERS)),
) -> CountOut:
    """Submit for review — the counting page's one final action.

    The Odoo quantity is read HERE, by the server, and frozen onto each entry:
    the reviewer has to know what the system claimed when the shelf was
    counted, and a browser is not a trustworthy source for that."""
    loc = _resolve_location(db, settings, body.location_key)

    # a product appears once per submission; a repeat means "change the qty"
    merged: dict[int, float] = {}
    for line in body.items:
        merged[line.product_id] = float(line.counted_qty)

    products = {
        p.id: p for p in db.scalars(select(Product).where(Product.id.in_(merged.keys())))
    }
    for pid in merged:
        p = products.get(pid)
        if p is None or not p.is_active:
            raise HTTPException(422, f"Product {pid} not found or inactive.")
        if not p.is_stock_tracked or not p.odoo_product_id:
            raise HTTPException(
                422, f"'{p.name}' isn't tracked in Odoo — there's no quantity to count."
            )

    odoo_qtys, source = locations.quantities_at(db, settings, loc, list(merged.keys()))

    count = InventoryCount(
        location_key=loc.key,
        counted_by_id=authed.id,
        note=body.note.strip(),
        status=CountStatus.PENDING.value,
        submitted_at=utcnow(),
    )
    db.add(count)
    db.flush()
    for pid, qty in merged.items():
        item = InventoryCountItem(count_id=count.id, product_id=pid)
        db.add(item)
        db.flush()
        db.add(
            InventoryCountEntry(
                item_id=item.id,
                attempt=1,
                counted_qty=qty,
                odoo_qty=float(odoo_qtys.get(pid, 0.0)),
                odoo_qty_source=source,
                counted_by_id=authed.id,
            )
        )
    event(
        db, None, count.id, CountEventKind.SUBMITTED,
        f"{len(merged)} item(s) counted at {_label(loc.key)}"
        + (" (quantities from the last stock sync — Odoo wasn't answering)"
           if source == "snapshot" else ""),
        authed.id,
    )
    db.commit()
    return _count_out(db, _load(db, count.id))


@router.get("", response_model=list[CountSummaryOut])
def list_counts(
    mine: bool = False,
    open_only: bool = False,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*COUNTERS, *REVIEWERS)),
) -> list[CountSummaryOut]:
    """Submissions, newest first. `mine` is the counter's own history; without
    it a reviewer sees everything (a counter sees their own either way)."""
    q = (
        select(InventoryCount)
        .options(selectinload(InventoryCount.items))
        .order_by(InventoryCount.id.desc())
        .execution_options(populate_existing=True)
    )
    if mine or not _may_review(authed):
        q = q.where(InventoryCount.counted_by_id == authed.id)
    if open_only:
        q = q.where(
            InventoryCount.status.in_(
                [CountStatus.PENDING.value, CountStatus.PARTIAL.value, CountStatus.RECOUNT.value]
            )
        )
    counts = db.scalars(q).all()
    names = _names(db, {c.counted_by_id for c in counts})
    return [
        CountSummaryOut(
            id=c.id,
            display_name=c.display_name,
            location_key=c.location_key,
            location_label=_label(c.location_key),
            status=c.status,
            counted_by=names.get(c.counted_by_id or 0, "unknown"),
            submitted_at=c.submitted_at,
            item_count=len(c.items),
            pending_items=sum(1 for i in c.items if i.status == CountItemStatus.PENDING.value),
            recount_items=sum(1 for i in c.items if i.status == CountItemStatus.RECOUNT.value),
        )
        for c in counts
    ]


# NOTE: declared before /{count_id} — the int route would swallow these.
@router.get("/queue", response_model=list[ItemOut])
def review_queue(
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> list[ItemOut]:
    """Everything needing a reviewer's attention, recounts first.

    Ranking lives in flow.queue_rank: a submitted recount is somebody's second
    trip to the same shelf, so it outranks a first count."""
    items = db.scalars(
        select(InventoryCountItem)
        .options(
            selectinload(InventoryCountItem.product),
            selectinload(InventoryCountItem.entries),
            selectinload(InventoryCountItem.events),
            selectinload(InventoryCountItem.count),
        )
        .where(InventoryCountItem.status.in_(flow.OPEN_ITEM_STATUSES))
        .execution_options(populate_existing=True)
    ).all()
    ids: set[int | None] = set()
    for item in items:
        ids |= {item.recount_assignee_id, item.reviewed_by_id}
        ids |= {e.counted_by_id for e in item.entries}
        ids |= {ev.actor_user_id for ev in item.events}
    names = _names(db, ids)
    items = sorted(items, key=lambda i: (*flow.queue_rank(i.status, len(i.entries)), i.id))
    return [_item_out(db, i, names) for i in items]


@router.get("/my-recounts", response_model=list[ItemOut])
def my_recounts(
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*COUNTERS)),
) -> list[ItemOut]:
    """Recounts assigned to me — the counter's to-do list."""
    items = db.scalars(
        select(InventoryCountItem)
        .options(
            selectinload(InventoryCountItem.product),
            selectinload(InventoryCountItem.entries),
            selectinload(InventoryCountItem.events),
            selectinload(InventoryCountItem.count),
        )
        .where(
            InventoryCountItem.status == CountItemStatus.RECOUNT.value,
            InventoryCountItem.recount_assignee_id == authed.id,
        )
        .order_by(InventoryCountItem.id)
        .execution_options(populate_existing=True)
    ).all()
    ids: set[int | None] = set()
    for item in items:
        ids |= {e.counted_by_id for e in item.entries} | {ev.actor_user_id for ev in item.events}
    return [_item_out(db, i, _names(db, ids)) for i in items]


@router.get("/{count_id}", response_model=CountOut)
def get_count(
    count_id: int,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*COUNTERS, *REVIEWERS)),
) -> CountOut:
    count = _load(db, count_id)
    if not _may_review(authed) and count.counted_by_id != authed.id:
        raise HTTPException(403, "That count belongs to someone else.")
    return _count_out(db, count)


def _get_item(db: Session, item_id: int) -> InventoryCountItem:
    item = db.scalar(
        select(InventoryCountItem)
        .options(
            selectinload(InventoryCountItem.product),
            selectinload(InventoryCountItem.entries),
            selectinload(InventoryCountItem.count).selectinload(InventoryCount.items),
        )
        .where(InventoryCountItem.id == item_id)
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise HTTPException(404, "Count item not found.")
    return item


@router.post("/items/{item_id}/approve", response_model=CountOut)
def approve_item(
    item_id: int,
    body: ReviewIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> CountOut:
    """Approve one item: the counted quantity becomes Odoo's, via a draft."""
    item = _get_item(db, item_id)
    if not flow.can_review(item.status):
        raise HTTPException(409, f"That item is already {item.status}.")
    loc = _resolve_location(db, settings, item.count.location_key)
    item.status = CountItemStatus.APPROVED.value
    item.reviewed_by_id = authed.id
    item.reviewed_at = utcnow()
    item.recount_assignee_id = None
    note = apply_to_odoo(db, settings, item, loc, authed.id)
    event(db, item, item.count_id, CountEventKind.APPROVED, body.note.strip() or "approved", authed.id)
    event(db, item, item.count_id, CountEventKind.ODOO, note, authed.id)
    _refresh_status(item.count)
    db.commit()
    return _count_out(db, _load(db, item.count_id))


@router.post("/items/{item_id}/reject", response_model=CountOut)
def reject_item(
    item_id: int,
    body: ReviewIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> CountOut:
    """Throw the count out. Odoo is not touched; the reason is kept forever."""
    item = _get_item(db, item_id)
    if not flow.can_review(item.status):
        raise HTTPException(409, f"That item is already {item.status}.")
    try:
        flow.check_reason("reject", body.note)
    except flow.CountError as e:
        raise HTTPException(422, str(e)) from e
    item.status = CountItemStatus.REJECTED.value
    item.reviewed_by_id = authed.id
    item.reviewed_at = utcnow()
    item.recount_assignee_id = None
    event(db, item, item.count_id, CountEventKind.REJECTED, body.note.strip(), authed.id)
    _refresh_status(item.count)
    db.commit()
    return _count_out(db, _load(db, item.count_id))


@router.post("/items/{item_id}/request-recount", response_model=CountOut)
def request_recount(
    item_id: int,
    body: RecountRequestIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> CountOut:
    """Ask for another physical count, optionally by a specific person."""
    item = _get_item(db, item_id)
    if not flow.can_review(item.status):
        raise HTTPException(409, f"That item is already {item.status}.")
    try:
        flow.check_reason("recount", body.note)
    except flow.CountError as e:
        raise HTTPException(422, str(e)) from e
    assignee_id = _valid_assignee(db, body.assignee_id)
    item.status = CountItemStatus.RECOUNT.value
    item.recount_assignee_id = assignee_id
    item.reviewed_by_id = authed.id
    item.reviewed_at = utcnow()
    who = _names(db, {assignee_id}).get(assignee_id or 0, "anyone who counts")
    event(
        db, item, item.count_id, CountEventKind.RECOUNT_REQUESTED,
        f"{body.note.strip()} — assigned to {who}", authed.id,
    )
    _refresh_status(item.count)
    db.commit()
    return _count_out(db, _load(db, item.count_id))


def _valid_assignee(db: Session, assignee_id: int | None) -> int | None:
    """A recount can only be assigned to someone who may count."""
    if assignee_id is None:
        return None
    user = db.get(User, assignee_id)
    if user is None or not user.is_active:
        raise HTTPException(422, "That user doesn't exist or is inactive.")
    roles = {r.role for r in db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == user.id))}
    if not roles & {r.value for r in COUNTERS}:
        raise HTTPException(
            422, f"{user.display_name or user.email} isn't allowed to perform counts."
        )
    return assignee_id


@router.post("/items/{item_id}/recount", response_model=CountOut)
def submit_recount(
    item_id: int,
    body: RecountIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*COUNTERS)),
) -> CountOut:
    """Perform the recount. Appends an entry — the original is never touched —
    and puts the item back in the queue, where recounts rank higher."""
    item = _get_item(db, item_id)
    if item.status != CountItemStatus.RECOUNT.value:
        raise HTTPException(409, "That item isn't waiting for a recount.")
    if (
        item.recount_assignee_id
        and item.recount_assignee_id != authed.id
        and not authed.has_role(Role.ADMIN, Role.SHOPPE_FLOOR)
    ):
        raise HTTPException(403, "That recount is assigned to someone else.")
    loc = _resolve_location(db, settings, item.count.location_key)
    qtys, source = locations.quantities_at(db, settings, loc, [item.product_id])
    reason = ""
    for ev in reversed(item.events):
        if ev.kind == CountEventKind.RECOUNT_REQUESTED.value:
            reason = ev.note
            break
    db.add(
        InventoryCountEntry(
            item_id=item.id,
            attempt=len(item.entries) + 1,
            counted_qty=float(body.counted_qty),
            odoo_qty=float(qtys.get(item.product_id, 0.0)),
            odoo_qty_source=source,
            counted_by_id=authed.id,
            reason=reason,
        )
    )
    item.status = CountItemStatus.PENDING.value  # back to the queue
    item.recount_assignee_id = None
    event(
        db, item, item.count_id, CountEventKind.RECOUNTED,
        f"recounted {body.counted_qty:g} (Odoo showed {qtys.get(item.product_id, 0.0):g})",
        authed.id,
    )
    _refresh_status(item.count)
    db.commit()
    return _count_out(db, _load(db, item.count_id))


# ------------------------------------------------- whole-submission actions
@router.post("/{count_id}/approve", response_model=CountOut)
def approve_all(
    count_id: int,
    body: ReviewIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> CountOut:
    """Approve every item still open. Items already decided are left alone —
    an individual decision always outranks a bulk one."""
    count = _load(db, count_id)
    loc = _resolve_location(db, settings, count.location_key)
    done = 0
    for item in count.items:
        if not flow.can_review(item.status):
            continue
        item.status = CountItemStatus.APPROVED.value
        item.reviewed_by_id = authed.id
        item.reviewed_at = utcnow()
        item.recount_assignee_id = None
        note = apply_to_odoo(db, settings, item, loc, authed.id)
        event(db, item, count.id, CountEventKind.APPROVED, body.note.strip() or "approved with the submission", authed.id)
        event(db, item, count.id, CountEventKind.ODOO, note, authed.id)
        done += 1
    event(db, None, count.id, CountEventKind.APPROVED, f"{done} item(s) approved{(' — ' + body.note.strip()) if body.note.strip() else ''}", authed.id)
    _refresh_status(count)
    db.commit()
    return _count_out(db, _load(db, count_id))


@router.post("/{count_id}/reject", response_model=CountOut)
def reject_all(
    count_id: int,
    body: ReviewIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> CountOut:
    count = _load(db, count_id)
    try:
        flow.check_reason("reject", body.note)
    except flow.CountError as e:
        raise HTTPException(422, str(e)) from e
    done = 0
    for item in count.items:
        if not flow.can_review(item.status):
            continue
        item.status = CountItemStatus.REJECTED.value
        item.reviewed_by_id = authed.id
        item.reviewed_at = utcnow()
        item.recount_assignee_id = None
        event(db, item, count.id, CountEventKind.REJECTED, body.note.strip(), authed.id)
        done += 1
    event(db, None, count.id, CountEventKind.REJECTED, f"{done} item(s) rejected — {body.note.strip()}", authed.id)
    _refresh_status(count)
    db.commit()
    return _count_out(db, _load(db, count_id))


@router.post("/{count_id}/request-recount", response_model=CountOut)
def request_recount_all(
    count_id: int,
    body: RecountRequestIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(*REVIEWERS)),
) -> CountOut:
    count = _load(db, count_id)
    try:
        flow.check_reason("recount", body.note)
    except flow.CountError as e:
        raise HTTPException(422, str(e)) from e
    assignee_id = _valid_assignee(db, body.assignee_id)
    who = _names(db, {assignee_id}).get(assignee_id or 0, "anyone who counts")
    done = 0
    for item in count.items:
        if not flow.can_review(item.status):
            continue
        item.status = CountItemStatus.RECOUNT.value
        item.recount_assignee_id = assignee_id
        item.reviewed_by_id = authed.id
        item.reviewed_at = utcnow()
        event(
            db, item, count.id, CountEventKind.RECOUNT_REQUESTED,
            f"{body.note.strip()} — assigned to {who}", authed.id,
        )
        done += 1
    event(
        db, None, count.id, CountEventKind.RECOUNT_REQUESTED,
        f"{done} item(s) sent for recount by {who} — {body.note.strip()}", authed.id,
    )
    _refresh_status(count)
    db.commit()
    return _count_out(db, _load(db, count_id))
