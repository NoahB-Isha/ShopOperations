from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class ProductSource(str, enum.Enum):
    ODOO = "odoo"  # synced from Odoo; sync owns the synced columns
    MANUAL = "manual"  # app-only item (water, cookies) — no Odoo record, no stock tracking


class TagName(str, enum.Enum):
    AIR_ONLY = "air_only"
    SEA_ONLY = "sea_only"
    GOLD = "gold"
    SILVER = "silver"
    BLOOM = "bloom"
    CAMPHOR = "camphor"
    TOOTHPASTE = "toothpaste"
    EXPIRES = "expires"  # carries expires_on


class Product(Base, TimestampMixin):
    """Unified catalog keyed by Global SKU.

    Synced columns (name, category, prices, odoo ids…) are overwritten by the
    Odoo product sync for source='odoo' rows. App-managed columns (case_size,
    dept_orderable, tags) survive every sync.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    global_sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    us_sku: Mapped[str] = mapped_column(String(64), default="", index=True)
    odoo_product_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    odoo_internal_ref: Mapped[str] = mapped_column(String(64), default="")
    barcode: Mapped[str] = mapped_column(String(64), default="")

    name: Mapped[str] = mapped_column(String(255), default="", index=True)
    category: Mapped[str] = mapped_column(String(120), default="", index=True)
    cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    retail_price: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    origin_country: Mapped[str] = mapped_column(String(80), default="")

    source: Mapped[str] = mapped_column(String(10), default=ProductSource.ODOO.value)
    is_stock_tracked: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # --- app-managed (never touched by sync) ---
    case_size: Mapped[int] = mapped_column(Integer, default=1)
    dept_orderable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Non-retail POS items (campus meals, prasadam…) sell through the same
    # registers but never belong on the Shoppe restock lists.
    restock_exclude: Mapped[bool] = mapped_column(Boolean, default=False)

    tags: Mapped[list[ProductTag]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductTag(Base):
    __tablename__ = "product_tags"
    __table_args__ = (UniqueConstraint("product_id", "tag", name="uq_product_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    tag: Mapped[str] = mapped_column(String(30))
    expires_on: Mapped[date | None] = mapped_column(Date)  # only for tag == 'expires'

    product: Mapped[Product] = relationship(back_populates="tags")
