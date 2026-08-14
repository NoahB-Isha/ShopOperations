"""Product catalog API: search/browse for everyone, tag & item management for admins."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import (
    Product,
    ProductSource,
    ProductTag,
    Role,
    SalesDaily,
    SalesMonthly,
    StockLevel,
    StockSnapshot,
    StockSnapshotDay,
    TagName,
    utcnow,
)
from ..odoo.urls import odoo_record_url
from ..ratelimit import rate_limit
from .search import product_search_clause

router = APIRouter(prefix="/products", tags=["catalog"])


class TagOut(BaseModel):
    tag: str
    expires_on: date | None = None


class ProductOut(BaseModel):
    id: int
    global_sku: str
    us_sku: str
    odoo_internal_ref: str
    barcode: str
    name: str
    category: str
    cost: float
    retail_price: float
    source: str
    sourcing: str
    is_stock_tracked: bool
    is_active: bool
    case_size: int
    dept_orderable: bool
    restock_exclude: bool
    blacklisted: bool
    available_in_pos: bool = True
    tags: list[TagOut]
    stock: dict[str, float]
    odoo_url: str | None = None


class ProductListOut(BaseModel):
    items: list[ProductOut]
    total: int
    page: int
    page_size: int


def _stock_for(db: Session, product_id: int) -> dict[str, float]:
    rows = db.execute(
        select(StockLevel.location_key, StockLevel.qty).where(StockLevel.product_id == product_id)
    )
    return {key: float(qty) for key, qty in rows}


def _product_out(p: Product, stock: dict[str, float], settings: Settings) -> ProductOut:
    return ProductOut(
        id=p.id,
        global_sku=p.global_sku,
        us_sku=p.us_sku,
        odoo_internal_ref=p.odoo_internal_ref,
        barcode=p.barcode,
        name=p.name,
        category=p.category,
        cost=float(p.cost or 0),
        retail_price=float(p.retail_price or 0),
        source=p.source,
        sourcing=p.sourcing or "",
        is_stock_tracked=p.is_stock_tracked,
        is_active=p.is_active,
        case_size=p.case_size,
        dept_orderable=p.dept_orderable,
        restock_exclude=p.restock_exclude,
        blacklisted=p.blacklisted,
        available_in_pos=bool(p.available_in_pos),
        tags=[TagOut(tag=t.tag, expires_on=t.expires_on) for t in p.tags],
        stock=stock,
        odoo_url=odoo_record_url(settings, "product.product", p.odoo_product_id)
        if p.odoo_product_id
        else None,
    )


SORTS = {
    "name": Product.name,
    "sku": Product.global_sku,
    "category": Product.category,
    "price": Product.retail_price,
    "cost": Product.cost,
}


@router.get("", response_model=ProductListOut)
def list_products(
    # search is tokenized into one ILIKE per token per field (5 fields), so a
    # 100KB query would compile to ~250k predicates — the length cap is the
    # cheap half of that defence
    search: str = Query("", max_length=200),
    category: str = Query("", max_length=100),
    tag: str = Query("", max_length=100),
    source: str = Query("", max_length=100),
    include_inactive: bool = False,
    dept_orderable: bool | None = None,
    blacklisted: bool | None = None,
    # "Hide old SKUs" — the register is the honest test of what the shop still
    # sells. On by default; the retired ~24% only appear when asked for.
    in_pos_only: bool = True,
    # "Hide OOS" — only items with stock SOMEWHERE (warehouse, floor, staging,
    # staging2). Off by default: the catalog is the whole book, not a shelf.
    in_stock_only: bool = False,
    price_min: float | None = Query(None, ge=0, le=1_000_000),
    price_max: float | None = Query(None, ge=0, le=1_000_000),
    barcode_prefix: str = Query("", max_length=8),
    # "sold at least N units in the last D days" — two params so the window is
    # the operator's choice rather than something hardcoded
    sold_days: int | None = Query(None, ge=1, le=730),
    sold_min: float = Query(1, ge=0, le=1_000_000),
    sort: str = "name",
    dir: str = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(get_current_user),
) -> ProductListOut:
    q = select(Product).options(selectinload(Product.tags))
    if not include_inactive:
        q = q.where(Product.is_active.is_(True))
    # default: blacklisted items are invisible; blacklisted=true is the
    # Settings-page manager view of what's currently hidden
    q = q.where(Product.blacklisted.is_(True if blacklisted else False))
    if search:
        clause = product_search_clause(
            search,
            (
                Product.name,
                Product.global_sku,
                Product.us_sku,
                Product.barcode,
                Product.category,
            ),
        )
        if clause is not None:
            q = q.where(clause)
    if category:
        q = q.where(Product.category == category)
    if source:
        q = q.where(Product.source == source)
    if dept_orderable is not None:
        q = q.where(Product.dept_orderable.is_(dept_orderable))
    if tag:
        q = q.join(ProductTag, ProductTag.product_id == Product.id).where(ProductTag.tag == tag)
    if in_pos_only:
        q = q.where(Product.available_in_pos.is_(True))
    if in_stock_only:
        # any location with a positive quantity counts; Odoo vacuums zero
        # quants, so a product with no StockLevel rows at all is simply out
        with_stock = (
            select(StockLevel.product_id)
            .group_by(StockLevel.product_id)
            .having(func.coalesce(func.sum(StockLevel.qty), 0) > 0)
        )
        q = q.where(Product.id.in_(with_stock))
    if price_min is not None:
        q = q.where(Product.retail_price >= price_min)
    if price_max is not None:
        q = q.where(Product.retail_price <= price_max)
    if barcode_prefix:
        # startswith() escapes the pattern, so a stray % can't widen the match
        q = q.where(Product.barcode.startswith(barcode_prefix.strip().upper()))
    if sold_days is not None:
        since = utcnow().date() - timedelta(days=sold_days)
        sold = (
            select(SalesDaily.product_id)
            .where(SalesDaily.day >= since)
            .group_by(SalesDaily.product_id)
            .having(func.coalesce(func.sum(SalesDaily.units), 0) >= sold_min)
        )
        q = q.where(Product.id.in_(sold))

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    sort_col = SORTS.get(sort, Product.name)
    q = q.order_by(sort_col.desc() if dir == "desc" else sort_col.asc(), Product.id)
    items = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()

    stock_map: dict[int, dict[str, float]] = {}
    if items:
        rows = db.execute(
            select(StockLevel.product_id, StockLevel.location_key, StockLevel.qty).where(
                StockLevel.product_id.in_([p.id for p in items])
            )
        )
        for pid, key, qty in rows:
            stock_map.setdefault(pid, {})[key] = qty

    return ProductListOut(
        items=[_product_out(p, stock_map.get(p.id, {}), settings) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


class FacetsOut(BaseModel):
    categories: list[str]
    tags: list[str]
    total_active: int
    barcode_prefixes: list[str] = []


@router.get("/facets", response_model=FacetsOut)
def facets(db: Session = Depends(get_db), _: AuthedUser = Depends(get_current_user)) -> FacetsOut:
    categories = [
        c for (c,) in db.execute(
            select(Product.category).where(Product.category != "").distinct().order_by(Product.category)
        )
    ]
    total = db.scalar(select(func.count()).where(Product.is_active.is_(True))) or 0
    # Barcode prefixes are the shop's own product families (CX, IN, JW…).
    # Derived from the catalog rather than hardcoded so the list can't go stale,
    # and so families nobody thought to list still surface (CA is the biggest,
    # ~227). Counted in Python, not with a SQL regex: `~` is Postgres-only and
    # the suite runs on SQLite, so the two engines would disagree. Anything
    # under 5 products is dropped — the tail is ~35 one-offs that would bury
    # the families people actually filter by.
    counts: dict[str, int] = {}
    for (barcode,) in db.execute(
        select(Product.barcode).where(
            Product.is_active.is_(True),
            Product.blacklisted.is_(False),
            Product.barcode != "",
        )
    ):
        head = (barcode or "")[:2]
        if head.isalpha():
            counts[head.upper()] = counts.get(head.upper(), 0) + 1
    return FacetsOut(
        categories=categories,
        tags=[t.value for t in TagName],
        total_active=total,
        barcode_prefixes=[
            pfx for pfx, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 5
        ],
    )


# ---------------------------------------------------------- blacklist sweep
# Items that were never stocked AND never sold (dead entries — one-off
# consecrated pieces, retired listings) plus the stale "-USA" duplicate
# products. The never-SOLD half matters: snapshot history is weekly and only
# ~6 months deep, so fast-moving items (sold out between snapshots) and
# digital/menu items legitimately trade without ever showing stock — an item
# with sales is not junk. Sweeps are previewed (apply=false) and re-runnable
# as new junk syncs in. Declared BEFORE /{product_id} — route order matters.

# never sweep these, whatever the rules say (IL-Service is a real service
# item the floor uses even though it has no stock history)
SWEEP_EXCEPTIONS = ("IL-Service", "IL Service")


class SweepIn(BaseModel):
    apply: bool = False


class SweepOut(BaseModel):
    no_stock_history: int
    usa_items: int
    total: int
    applied: bool
    sample: list[str]  # first names, so the preview shows what it means


def _sweep_candidates(db: Session) -> tuple[set[int], set[int], list[Product]]:
    """(no-history ids, usa ids, union of products) for the sweep."""
    not_exception = ~or_(
        Product.global_sku.in_(SWEEP_EXCEPTIONS), Product.name.in_(SWEEP_EXCEPTIONS)
    )
    ever_snapshotted = exists(
        select(StockSnapshot.id).where(
            StockSnapshot.product_id == Product.id, StockSnapshot.qty > 0
        )
    )
    stocked_now = exists(
        select(StockLevel.id).where(
            StockLevel.product_id == Product.id, StockLevel.qty > 0
        )
    )
    ever_sold = exists(
        select(SalesMonthly.id).where(SalesMonthly.product_id == Product.id)
    )
    no_history = {
        pid
        for (pid,) in db.execute(
            select(Product.id).where(
                Product.is_active.is_(True),
                Product.blacklisted.is_(False),
                Product.source == ProductSource.ODOO.value,
                ~ever_snapshotted,
                ~stocked_now,
                ~ever_sold,
                not_exception,
            )
        )
    }
    # "-USA" duplicates: ilike prefilter, then a case-sensitive check in
    # Python so SQLite (tests) and Postgres agree and "USA" never matches a
    # lowercase word
    usa_rows = db.scalars(
        select(Product).where(
            Product.is_active.is_(True),
            Product.blacklisted.is_(False),
            or_(
                Product.name.ilike("%usa%"),
                Product.global_sku.ilike("%-usa%"),
                Product.us_sku.ilike("%-usa%"),
                Product.odoo_internal_ref.ilike("%-usa%"),
            ),
            not_exception,
        )
    ).all()
    usa = {
        p.id
        for p in usa_rows
        if "USA" in (p.name or "")
        or "-USA" in (p.global_sku or "")
        or "-USA" in (p.us_sku or "")
        or "-USA" in (p.odoo_internal_ref or "")
    }
    union_ids = no_history | usa
    products = (
        db.scalars(
            select(Product).where(Product.id.in_(union_ids or {-1})).order_by(Product.name)
        ).all()
        if union_ids
        else []
    )
    return no_history, usa, list(products)


@router.post(
    "/blacklist/sweep",
    response_model=SweepOut,
    # a full-catalog scan with four ilike prefilters; previews are re-runnable
    dependencies=[Depends(rate_limit("catalog:sweep", limit=10, per_seconds=300))],
)
def blacklist_sweep(
    body: SweepIn,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> SweepOut:
    no_history, usa, products = _sweep_candidates(db)
    if body.apply and products:
        db.execute(
            update(Product)
            .where(Product.id.in_([p.id for p in products]))
            .values(blacklisted=True)
        )
        db.commit()
    return SweepOut(
        no_stock_history=len(no_history),
        usa_items=len(usa),
        total=len(products),
        applied=bool(body.apply and products),
        sample=[p.name or p.global_sku for p in products[:15]],
    )


def barcode_candidates(code: str) -> list[str]:
    """The forms one scanned symbol can legitimately take in the catalog.

    A UPC-A label (12 digits) is read as EAN-13 by most scanners, which pads a
    leading zero — so `012345678905` and `0012345678905` are the same physical
    barcode, and Odoo may hold either. EAN-8 stays as it is. Also tried as a
    SKU, because plenty of shelf labels here carry the internal reference in
    Code 128 rather than a retail symbol.
    """
    raw = code.strip().upper()
    forms = [raw]
    if raw.isdigit():
        stripped = raw.lstrip("0") or "0"
        for candidate in (stripped, stripped.zfill(12), stripped.zfill(13), stripped.zfill(14)):
            if candidate not in forms:
                forms.append(candidate)
    return forms


@router.get("/by-barcode/{code}", response_model=ProductOut)
def get_product_by_barcode(
    code: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(get_current_user),
) -> ProductOut:
    """Exact lookup for the phone scanner: one symbol in, one product out.

    Deliberately NOT the tokenized catalog search — a scan is an identity
    claim, so a partial or ambiguous match is a miss, not a guess. Blacklisted
    and inactive products are returned: someone holding the item wants to know
    what it is, and "hidden from the catalog" is part of that answer.
    """
    forms = barcode_candidates(code)
    if not forms or not forms[0]:
        raise HTTPException(404, "No product with that barcode.")
    q = select(Product).options(selectinload(Product.tags))
    p = db.scalar(q.where(Product.barcode.in_(forms)).order_by(Product.is_active.desc()))
    if p is None:
        p = db.scalar(
            q.where(
                or_(
                    Product.global_sku.in_(forms),
                    Product.us_sku.in_(forms),
                    Product.odoo_internal_ref.in_(forms),
                )
            ).order_by(Product.is_active.desc())
        )
    if p is None:
        raise HTTPException(404, "No product with that barcode.")
    return _product_out(p, _stock_for(db, p.id), settings)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(get_current_user),
) -> ProductOut:
    p = db.scalar(
        select(Product).options(selectinload(Product.tags)).where(Product.id == product_id)
    )
    if p is None:
        raise HTTPException(404, "Product not found.")
    return _product_out(p, _stock_for(db, p.id), settings)


# ---------------------------------------------------------- stock history
# The drawer's availability-over-time graph. Same honesty rules as the time
# machine's past view: a covered day (a StockSnapshotDay row) with no snapshot
# rows for this product is a GENUINE zero — that is the out-of-stock signal,
# so zero points are emitted, never skipped. Uncovered days are simply absent.

HISTORY_LOCATION_KEYS = ("bwhse", "floor", "staging", "staging2")


class StockHistoryPoint(BaseModel):
    day: date
    total: float
    bwhse: float
    floor: float
    staging: float
    staging2: float
    source: str  # sync | reconstructed | live


class StockHistoryOut(BaseModel):
    points: list[StockHistoryPoint]  # oldest → newest; last point is live
    first_covered: date | None  # earliest history day ON RECORD (any product)
    covered_days: int  # history points in the window (excludes the live point)
    reconstructed_days: int  # of those, backfilled from Odoo's move ledger


def _history_point(day: date, buckets: dict[str, float], source: str) -> StockHistoryPoint:
    vals = {k: float(buckets.get(k, 0) or 0) for k in HISTORY_LOCATION_KEYS}
    return StockHistoryPoint(day=day, total=sum(vals.values()), source=source, **vals)


@router.get("/{product_id}/stock-history", response_model=StockHistoryOut)
def stock_history(
    product_id: int,
    days: int = Query(180, ge=14, le=730),
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(get_current_user),
) -> StockHistoryOut:
    p = db.get(Product, product_id)
    if p is None:
        raise HTTPException(404, "Product not found.")
    first_covered = db.scalar(select(func.min(StockSnapshotDay.snapshot_date)))
    if not (p.is_stock_tracked and p.source == ProductSource.ODOO.value):
        # manual/untracked items have no counts anywhere — an empty series,
        # not an error, so the UI can say so plainly
        return StockHistoryOut(
            points=[], first_covered=first_covered, covered_days=0, reconstructed_days=0
        )

    today = utcnow().date()
    start = today - timedelta(days=days)
    day_rows = (
        db.execute(
            select(StockSnapshotDay)
            .where(StockSnapshotDay.snapshot_date >= start)
            .order_by(StockSnapshotDay.snapshot_date)
        )
        .scalars()
        .all()
    )
    by_day: dict[date, dict[str, float]] = {}
    for d, key, qty in db.execute(
        select(StockSnapshot.snapshot_date, StockSnapshot.location_key, StockSnapshot.qty).where(
            StockSnapshot.product_id == product_id,
            StockSnapshot.snapshot_date >= start,
        )
    ):
        by_day.setdefault(d, {})[key] = float(qty or 0)

    points: list[StockHistoryPoint] = []
    reconstructed = 0
    for row in day_rows:
        if row.snapshot_date >= today:
            continue  # the live point below is fresher than today's snapshot
        if row.source == "reconstructed":
            reconstructed += 1
        points.append(_history_point(row.snapshot_date, by_day.get(row.snapshot_date, {}), row.source))
    points.append(_history_point(today, _stock_for(db, product_id), "live"))
    return StockHistoryOut(
        points=points,
        first_covered=first_covered,
        covered_days=len(points) - 1,
        reconstructed_days=reconstructed,
    )


class TagsIn(BaseModel):
    tags: list[TagOut]


@router.put("/{product_id}/tags", response_model=ProductOut)
def set_tags(
    product_id: int,
    body: TagsIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> ProductOut:
    p = db.scalar(
        select(Product).options(selectinload(Product.tags)).where(Product.id == product_id)
    )
    if p is None:
        raise HTTPException(404, "Product not found.")
    valid = {t.value for t in TagName}
    seen: set[str] = set()
    for t in body.tags:
        if t.tag not in valid:
            raise HTTPException(422, f"Unknown tag '{t.tag}'. Valid: {sorted(valid)}")
        if t.tag in seen:
            raise HTTPException(422, f"Tag '{t.tag}' given twice.")
        seen.add(t.tag)
        if t.tag == TagName.EXPIRES.value and t.expires_on is None:
            raise HTTPException(422, "The 'expires' tag needs a date.")
        if t.tag != TagName.EXPIRES.value and t.expires_on is not None:
            raise HTTPException(422, f"Only 'expires' carries a date (got one on '{t.tag}').")
    if TagName.AIR_ONLY.value in seen and TagName.SEA_ONLY.value in seen:
        raise HTTPException(422, "A product can't be both air-only and sea-only.")

    for existing in list(p.tags):
        db.delete(existing)
    db.flush()
    for t in body.tags:
        db.add(ProductTag(product_id=p.id, tag=t.tag, expires_on=t.expires_on))
    db.commit()
    db.refresh(p)
    return _product_out(p, _stock_for(db, p.id), settings)


class ProductPatchIn(BaseModel):
    case_size: int | None = Field(None, ge=1, le=10000)
    dept_orderable: bool | None = None
    restock_exclude: bool | None = None
    blacklisted: bool | None = None
    # manual items only:
    name: str | None = None
    category: str | None = None
    retail_price: float | None = Field(None, ge=0)
    is_active: bool | None = None


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    body: ProductPatchIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> ProductOut:
    p = db.scalar(
        select(Product).options(selectinload(Product.tags)).where(Product.id == product_id)
    )
    if p is None:
        raise HTTPException(404, "Product not found.")
    if body.case_size is not None:
        p.case_size = body.case_size
    if body.dept_orderable is not None:
        p.dept_orderable = body.dept_orderable
    if body.restock_exclude is not None:
        p.restock_exclude = body.restock_exclude
    if body.blacklisted is not None:
        p.blacklisted = body.blacklisted
    synced_fields = {"name": body.name, "category": body.category,
                     "retail_price": body.retail_price, "is_active": body.is_active}
    touched_synced = {k: v for k, v in synced_fields.items() if v is not None}
    if touched_synced:
        if p.source != ProductSource.MANUAL.value:
            raise HTTPException(
                422,
                f"{', '.join(touched_synced)} come from Odoo for synced products — change them there.",
            )
        for k, v in touched_synced.items():
            setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _product_out(p, _stock_for(db, p.id), settings)


class ManualProductIn(BaseModel):
    name: str = Field(min_length=2)
    global_sku: str = ""
    category: str = "Department Supplies"
    retail_price: float = Field(0, ge=0)
    dept_orderable: bool = True


@router.post("", response_model=ProductOut, status_code=201)
def create_manual_product(
    body: ManualProductIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> ProductOut:
    """Non-Odoo items (water, cookies): dept-orderable, no stock tracking."""
    sku = body.global_sku.strip().upper()
    if not sku:
        n = (db.scalar(select(func.count()).where(Product.source == "manual")) or 0) + 1
        sku = f"MAN-{n:04d}"
    if db.scalar(select(Product).where(Product.global_sku == sku)):
        raise HTTPException(409, f"SKU {sku} already exists.")
    p = Product(
        global_sku=sku,
        us_sku=sku,
        name=body.name.strip(),
        category=body.category.strip(),
        retail_price=body.retail_price,
        source=ProductSource.MANUAL.value,
        is_stock_tracked=False,
        dept_orderable=body.dept_orderable,
        is_active=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _product_out(p, {}, settings)
