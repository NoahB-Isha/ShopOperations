"""Product catalog API: search/browse for everyone, tag & item management for admins."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..auth.deps import AuthedUser, get_current_user, require_roles
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Product, ProductSource, ProductTag, Role, StockLevel, TagName
from ..odoo.urls import odoo_record_url

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
    is_stock_tracked: bool
    is_active: bool
    case_size: int
    dept_orderable: bool
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
        is_stock_tracked=p.is_stock_tracked,
        is_active=p.is_active,
        case_size=p.case_size,
        dept_orderable=p.dept_orderable,
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
    search: str = "",
    category: str = "",
    tag: str = "",
    source: str = "",
    include_inactive: bool = False,
    dept_orderable: bool | None = None,
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
    if search:
        needle = f"%{search.strip()}%"
        q = q.where(
            or_(
                Product.name.ilike(needle),
                Product.global_sku.ilike(needle),
                Product.us_sku.ilike(needle),
                Product.barcode.ilike(needle),
                Product.category.ilike(needle),
            )
        )
    if category:
        q = q.where(Product.category == category)
    if source:
        q = q.where(Product.source == source)
    if dept_orderable is not None:
        q = q.where(Product.dept_orderable.is_(dept_orderable))
    if tag:
        q = q.join(ProductTag, ProductTag.product_id == Product.id).where(ProductTag.tag == tag)

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


@router.get("/facets", response_model=FacetsOut)
def facets(db: Session = Depends(get_db), _: AuthedUser = Depends(get_current_user)) -> FacetsOut:
    categories = [
        c for (c,) in db.execute(
            select(Product.category).where(Product.category != "").distinct().order_by(Product.category)
        )
    ]
    total = db.scalar(select(func.count()).where(Product.is_active.is_(True))) or 0
    return FacetsOut(categories=categories, tags=[t.value for t in TagName], total_active=total)


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
