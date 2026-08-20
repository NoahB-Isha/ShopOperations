"""Restock list grouping and popularity — pure, table-driven, admin-editable.

Two asks from Noah (2026-08-18), both about making the morning list read the
way the floor walks the shop:

  * **group by type.** The Odoo category is too coarse to walk by — "Home"
    holds incense, candle holders and bath towels. The BARCODE prefix is the
    finer signal the shop already uses: IN → Incense (61 items, every one an
    Incense-Stick-*), PH → Photos, BK → Books. So the prefix names the group.

  * **CA is not a type.** Verified on live data before writing this: the
    two-letter prefix is also the shape of an India import reference
    (`CA0023000009`), so a CA code says where a thing shipped from, not what
    it is. Those items fall back to their Odoo category instead of inventing
    a "CA" aisle. Same for anything else unmapped — a wrong group is worse
    than an honest "Other".

  * **best sellers first**, inside each group AND between groups, so the
    biggest sellers are the first thing picked up.

`PREFIX_GROUPS` is only the default. `restock_groups` in app_settings overrides
it ({"IN": "Incense"}), because the shop will coin a prefix long before anyone
ships a release — a blank label means "stop grouping by this prefix".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SHOPPE_CHANNELS, Product, SalesDaily
from ..models.base import utcnow

# Derived from live data 2026-08-18 (counts are visible active products) and
# named the way the floor would say it, not the way Odoo does.
PREFIX_GROUPS: dict[str, str] = {
    "IN": "Incense",
    "BC": "Body Care",
    "HI": "Health & Immunity",
    "RJ": "Health & Immunity",
    "FD": "Food & Honey",
    "NF": "Food & Honey",
    "PH": "Photos",
    "BK": "Books",
    "DV": "DVDs",
    "MU": "Music",
    "ME": "Media",
    "YA": "Yoga",
    "YS": "Yoga",
    "JW": "Jewellery",
    "MC": "Lamps & Metalware",
    "TC": "Temple & Consecrated",
    "WC": "Temple & Consecrated",
    "PG": "Devi Offerings",
    "GFI": "Gift Sets",
    "BG": "Bags",
    "CU": "Clothing",
    "CW": "Clothing",
    "CM": "Clothing",
    "UC": "Clothing",
    "SAR": "Sarees",
    # DELIBERATELY UNMAPPED, for the same reason as CA: verified against live
    # data, these prefixes span unrelated categories, so naming an aisle after
    # one would put a bottle of sesame oil next to a face mask —
    #   EX  Home 88 / Clothing 27 / Snacks 13
    #   CX  Temple 103 / Clothing 58 / Rudraksh 27
    #   WC  Clothing 8 / Home 6
    #   ME  Digital 17 / Media 11
    # They fall back to their Odoo category, which is at least true. And CA is
    # absent because a two-letter prefix plus ten digits is an India import
    # reference — see the module docstring. Don't add it.
}

# Prefixes that must never name a group even if someone maps them: the India
# import reference shape (two letters + ten digits) makes the prefix a shipping
# fact, not a type.
NEVER_GROUP = {"CA"}

SETTING_KEY = "restock_groups"  # admin-editable prefix -> label overrides
FALLBACK_GROUP = "Other"
_PREFIX = re.compile(r"^([A-Z]{2,3})")
_INDIA_REF = re.compile(r"^[A-Z]{2}\d{10}$")

# How much history counts as "popular". Long enough to survive a quiet week,
# short enough that last season's hit doesn't outrank this month's.
POPULARITY_DAYS = 90


@dataclass(frozen=True)
class Grouped:
    group: str
    popularity: float  # units sold in the window, this product
    group_popularity: float  # units sold in the window, the whole group


def merged_groups(overrides: dict | None) -> dict[str, str]:
    """Defaults with the admin's `restock_groups` on top. Invalid entries are
    ignored rather than raising — the same forgiving contract as
    ordering_rules, because this setting is edited by hand."""
    out = dict(PREFIX_GROUPS)
    for key, label in (overrides or {}).items():
        if not isinstance(key, str) or not isinstance(label, str):
            continue
        prefix = key.strip().upper()
        if not prefix or prefix in NEVER_GROUP:
            continue
        if label.strip():
            out[prefix] = label.strip()
        else:
            out.pop(prefix, None)  # blank label = stop grouping this prefix
    return out


def group_for(product: Product, groups: dict[str, str]) -> str:
    """The aisle this product belongs to. Barcode prefix first, then the Odoo
    category, then "Other" — never a bare prefix nobody has named."""
    code = (product.barcode or "").strip().upper()
    if code and not _INDIA_REF.match(code):
        m = _PREFIX.match(code)
        if m and m.group(1) not in NEVER_GROUP:
            label = groups.get(m.group(1))
            if label:
                return label
    # No usable prefix: the Odoo category is a real (if coarse) answer, and
    # its last segment is the readable part of "Isha Life USA / Home".
    category = (product.category or "").split("/")[-1].strip()
    return category or FALLBACK_GROUP


def popularity(db: Session, product_ids: set[int], days: int = POPULARITY_DAYS) -> dict[int, float]:
    """Units sold per product over the window, SHOP sales only.

    SHOPPE_CHANNELS is the same filter the restock accumulator counts on, so
    "popular" here means popular ON THIS FLOOR — a city-center hit doesn't
    reorder the shop's shelves. Returns 0 for anything that hasn't sold."""
    if not product_ids:
        return {}
    since = utcnow().date() - timedelta(days=days)
    rows = db.execute(
        select(SalesDaily.product_id, func.sum(SalesDaily.units))
        .where(
            SalesDaily.product_id.in_(product_ids),
            SalesDaily.channel.in_(SHOPPE_CHANNELS),
            SalesDaily.day >= since,
        )
        .group_by(SalesDaily.product_id)
    )
    return {pid: float(units or 0) for pid, units in rows}


def assign(
    products: dict[int, Product],
    sold: dict[int, float],
    overrides: dict | None = None,
) -> dict[int, Grouped]:
    """Group every product and score it. Group popularity is the SUM of its
    items' sales: a group earns its place at the top by how much the shop
    moves out of it, not by its single best item."""
    groups = merged_groups(overrides)
    labels = {pid: group_for(p, groups) for pid, p in products.items()}
    totals: dict[str, float] = {}
    for pid, label in labels.items():
        totals[label] = totals.get(label, 0.0) + float(sold.get(pid, 0.0))
    return {
        pid: Grouped(
            group=label,
            popularity=float(sold.get(pid, 0.0)),
            group_popularity=totals.get(label, 0.0),
        )
        for pid, label in labels.items()
    }


def sort_key(g: Grouped, name: str) -> tuple:
    """Best-selling groups first, best sellers inside them, then A-Z so the
    order is stable when nothing has sold (a fresh install, or a group of
    genuine zeroes)."""
    return (-g.group_popularity, g.group, -g.popularity, name.lower())
