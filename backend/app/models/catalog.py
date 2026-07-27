from __future__ import annotations

import enum
from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class ProductSource(str, enum.Enum):
    ODOO = "odoo"  # synced from Odoo; sync owns the synced columns
    MANUAL = "manual"  # app-only item (water, cookies) — no Odoo record, no stock tracking


# `Product.sourcing` values — procurement origin declared IN ODOO by tagging
# the product "Domestic" or "India" (product tags, case-insensitive names).
SOURCING_DOMESTIC = "domestic"
SOURCING_INDIA = "india"


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
    # Odoo-declared procurement origin, synced from product tags named exactly
    # "Domestic" or "India" (case-insensitive): '' | 'domestic' | 'india'.
    # Domestic wins if a product somehow carries both. The one behavioral hook
    # is India-order candidacy (`ordering/inputs.import_candidates`): domestic
    # is a hard exclude, india is an include regardless of reference shape.
    sourcing: Mapped[str] = mapped_column(String(10), default="", server_default="")

    # --- app-managed (never touched by sync) ---
    case_size: Mapped[int] = mapped_column(Integer, default=1)
    dept_orderable: Mapped[bool] = mapped_column(Boolean, default=False)
    # Non-retail POS items (campus meals, prasadam…) sell through the same
    # registers but never belong on the Shoppe restock lists.
    restock_exclude: Mapped[bool] = mapped_column(Boolean, default=False)
    # Admin blacklist: hidden from every list, report, and flow app-wide
    # (stale Odoo entries, items irrelevant to shop operations). Managed on
    # the Settings page; query-side twin: `not_blacklisted()`.
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # --- ordering (phase 4; app-managed, workbook-sourced where Odoo lacks) ---
    hsn_code: Mapped[str] = mapped_column(String(20), default="")  # customs code
    unit_weight_g: Mapped[float | None] = mapped_column(Float)
    target_moh_override: Mapped[float | None] = mapped_column(Float)  # per-SKU MTHS REQ
    moq: Mapped[int | None] = mapped_column(Integer)  # domestic vendors
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), index=True)
    # keeps an item off the India review table without deactivating it
    ordering_exclude: Mapped[bool] = mapped_column(Boolean, default=False)

    tags: Mapped[list[ProductTag]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    @property
    def is_clothing(self) -> bool:
        """Clothing is OUT OF SCOPE for ordering flows (project brief) —
        excluded from order lists, center catalogs, and placements. Matched by
        category (live paths look like 'Isha Life USA / Clothing & …').
        Query-side twin: `not_clothing()`."""
        return "clothing" in (self.category or "").lower()


def not_clothing():
    """The SQL predicate matching `Product.is_clothing == False` — use it in
    every query that feeds an ordering flow."""
    return ~Product.category.ilike("%clothing%")


def not_blacklisted():
    """The SQL predicate every user-facing product list, report aggregation,
    and ordering flow must carry — blacklisted items exist only on the admin
    Settings page (where the blacklist is managed) and in raw Odoo."""
    return Product.blacklisted.is_(False)


class ProductTag(Base):
    __tablename__ = "product_tags"
    __table_args__ = (UniqueConstraint("product_id", "tag", name="uq_product_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    tag: Mapped[str] = mapped_column(String(30))
    expires_on: Mapped[date | None] = mapped_column(Date)  # only for tag == 'expires'

    product: Mapped[Product] = relationship(back_populates="tags")
