"""Ordering business rules — every threshold the India-import engine uses.

The defaults below are the values the spec-of-record workbook
(`docs/reference/USA INV CHK.xlsx`) actually uses today; provenance is noted
inline so a buyer can audit any number against the workbook. A deployment
overrides any of them WITHOUT code changes through the admin Settings page
(`ordering_rules` JSON blob) — `merged()` applies such an override dict.

Category names: Odoo categories are paths ("Isha Life USA / Body Care"); the
workbook uses short names ("BODY CARE"). `normalize_category` maps both onto
the same key (uppercased last path segment) so one rules table serves both.

Tags drive the special cases (`product_tags` rows, admin-managed in the
catalog): gold / silver / air_only ship air only (workbook AIR sheet);
camphor / toothpaste are bulk-cycle items ordered ~yearly; bloom / expires are
expiry-sensitive. Clothing never reaches the engine at all (project brief —
excluded upstream by `not_clothing()`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# Lead-time horizon (months). Goods ordered now land ~4 (air) to ~6 (sea)
# months later; the SEA sheet projects stock 6 months forward.
SEA_LEAD_MONTHS = 6
AIR_LEAD_MONTHS = 4
PROJECTION_HORIZON = 6

# Near-term floor for the AIR decision.
# Source: SEA sheet col U -> =IF(N2<3, 3-N2, 0)  (N2 = OH at month 4)
AIR_NEARTERM_FLOOR_MOH = 3.0

DEFAULT_TARGET_MOH = 8.0  # SEA "MTHS REQ" modal value
DEFAULT_CASE_SIZE = 1

# Air-only items (workbook AIR sheet: BHOOMI / GOLD / SILVER): top up to a
# minimum months-on-hand, everything ships air, no sea leg. The sheet uses
# 5-6 per row; 6 is the modal value.
AIR_ONLY_MIN_MOH = 6.0

# Bulk-cycle items (toothpaste, camphor): ordered in bulk roughly yearly, so
# the refill target is a year of cover rather than the category default.
BULK_CYCLE_TARGET_MOH = 12.0

# Expiry-sensitive items (Bloom cosmetics, anything tagged `expires`): never
# stack more months of stock than can sell before expiry risk kicks in.
# EXP INV keeps a 1.5-month "MIN MONS FOR SALE" buffer; capping the refill
# target is the projection-side counterpart.
EXPIRY_MAX_TARGET_MOH = 6.0

# Domestic vendors (workbook DOMESTIC sheet): no sea/air — order one MOQ when
# months-on-hand drops below the trigger. Source: col I "=IF(F2<4,...)".
DOMESTIC_MOQ_TRIGGER_MOH = 4.0

# Per-category target months-on-hand (SEA "MTHS REQ" grouped by CATEGORY,
# modal value per category; per-SKU overrides live on the product).
CATEGORY_TARGET_MOH: dict[str, float] = {
    "ACCESSORY": 6.0,
    "BODY CARE": 8.0,
    "BOOK": 8.0,
    "CONX": 8.0,
    "COPPER": 8.0,
    "CRAFT": 8.0,
    "INCENSE": 8.0,
    "INVENTORY": 8.0,
    "JEWELRY": 8.0,
    "NATURAL FOOD": 8.0,
    "PHOTO": 8.0,
    "TEMPLE": 8.0,
    "YOGA STORE": 8.0,
    "A & A": 8.0,
}

# Per-category default case sizes (BLOOM ships in cases of 32 — BLOOM col M).
CATEGORY_CASE_SIZE: dict[str, int] = {
    "BLOOM": 32,
}

# Tag names (mirrors models.catalog.TagName values) grouped by rule.
AIR_ONLY_TAGS = ("air_only", "gold", "silver")
BULK_CYCLE_TAGS = ("camphor", "toothpaste")
EXPIRY_TAGS = ("bloom", "expires")


def coverage_overrides(months: float, base: OrderingRules | None = None) -> dict[str, Any]:
    """Build the override dict that orders `months` of cover for everything.

    This exists because "set the target to a year" is NOT one number. Every
    category carries its own entry in CATEGORY_TARGET_MOH, and
    `target_moh_for` reads the category map BEFORE `default_target_moh` — so
    setting only the default changes almost nothing, silently (the categories
    stay where they were). A buyer editing JSON by hand would have to
    enumerate all of them and would eventually miss one.

    What it deliberately does NOT touch:

      * `expiry_max_target_moh` — Bloom and anything tagged `expires` stay
        capped, because a year of face wash expires before it sells. The
        engine applies that cap after the target, so those items keep their
        shorter cover automatically.
      * `air_only_min_moh` — Bhoomi/Gold/Silver ship by AIR. A year of gold on
        a plane is a cash decision, not a coverage decision.
      * `bulk_cycle_target_moh` — camphor and toothpaste are already ordered a
        year at a time, and the engine takes max(target, bulk) anyway.
      * `horizon` / lead times — those say WHEN a container lands (month 6),
        not how much cover to buy on arrival. Raising them changes no
        quantity; the sea leg is always measured at the lead-time month.
    """
    rules = base or OrderingRules()
    target = float(months)
    return {
        "default_target_moh": target,
        # every known category, so nothing is left behind at the old figure
        "category_target_moh": dict.fromkeys(rules.category_target_moh, target),
    }


def coverage_of(rules: OrderingRules) -> float | None:
    """The single "months of cover" figure, when every category agrees on one.
    None means the targets have been tuned per category and no single number
    describes them — the UI then shows the table rather than lying with one
    number."""
    values = {float(v) for v in rules.category_target_moh.values()}
    values.add(float(rules.default_target_moh))
    return values.pop() if len(values) == 1 else None


def normalize_category(category: str | None) -> str:
    """'Isha Life USA / Body Care' and 'BODY CARE' -> 'BODY CARE'."""
    if not category:
        return ""
    leaf = category.split("/")[-1]
    return " ".join(leaf.upper().split())


@dataclass(frozen=True)
class ForecastRules:
    """Tunables for the demand forecaster (pure — passed in, never imported)."""

    min_months_for_seasonal: int = 24  # need ~2 yrs to trust seasonality
    min_months_for_trend: int = 12
    low_confidence_months: int = 6  # below this -> flat baseline
    divergence_flag_pct: float = 0.30  # forecast vs baseline gap to flag
    analogy_graduation_months: int = 6  # real history that retires an analogy
    # Seasonal indices are shrunk toward 1.0 by k_obs / (k_obs + this). At 24
    # months of history each index rests on TWO observations, so 2.0 moves them
    # half way to the raw figure — deliberately timid, because an over-confident
    # index compounds across a year-long order (finding 06).
    seasonal_shrink_k: float = 2.0


@dataclass(frozen=True)
class OrderingRules:
    """Everything the pure suggestion engine needs. No I/O lives here."""

    sea_lead_months: int = SEA_LEAD_MONTHS
    air_lead_months: int = AIR_LEAD_MONTHS
    horizon: int = PROJECTION_HORIZON
    air_nearterm_floor_moh: float = AIR_NEARTERM_FLOOR_MOH
    default_target_moh: float = DEFAULT_TARGET_MOH
    default_case_size: int = DEFAULT_CASE_SIZE
    air_only_min_moh: float = AIR_ONLY_MIN_MOH
    # The AIR sheet ignores in-transit stock; counting it prevents re-ordering
    # goods already on a plane, so it defaults ON (delta from the workbook,
    # identical when nothing is in transit).
    air_only_count_incoming: bool = True
    bulk_cycle_target_moh: float = BULK_CYCLE_TARGET_MOH
    expiry_max_target_moh: float = EXPIRY_MAX_TARGET_MOH
    domestic_moq_trigger_moh: float = DOMESTIC_MOQ_TRIGGER_MOH
    # Safety stock (finding 02). Cover is a flat months figure, so an item
    # selling 100+-5 a month gets the same cover as one selling 100+-80. This
    # adds z * sd * sqrt(lead + cover) months on top of the target, per SKU.
    # DEFAULT 0 = off, which is the workbook's own behaviour and what keeps
    # the parity test meaningful; a deployment turns it on.
    #   1.28 ~ 90% service level, 1.65 ~ 95%, 2.05 ~ 98%
    safety_z: float = 0.0
    # and a ceiling, so a wildly erratic seller can't ask for a decade
    safety_max_moh: float = 6.0
    category_target_moh: dict[str, float] = field(
        default_factory=lambda: dict(CATEGORY_TARGET_MOH)
    )
    category_case_size: dict[str, int] = field(default_factory=lambda: dict(CATEGORY_CASE_SIZE))
    forecast: ForecastRules = field(default_factory=ForecastRules)

    def target_moh_for(self, category: str | None, override: float | None = None) -> float:
        if override is not None:
            return float(override)
        key = normalize_category(category)
        return self.category_target_moh.get(key, self.default_target_moh)

    def case_size_for(self, category: str | None, override: int | None = None) -> int:
        if override:
            return int(override)
        key = normalize_category(category)
        return self.category_case_size.get(key, self.default_case_size)

    def merged(self, overrides: dict[str, Any] | None) -> OrderingRules:
        """Apply an admin override dict (the `ordering_rules` setting). Unknown
        keys are ignored rather than fatal — a typo in settings must never
        take the review screen down."""
        if not overrides:
            return self
        rules = self
        fc = self.forecast
        fc_fields = {f: getattr(fc, f) for f in ForecastRules.__dataclass_fields__}
        for key, value in overrides.items():
            if key == "category_target_moh" and isinstance(value, dict):
                merged = dict(rules.category_target_moh)
                merged.update({normalize_category(k): float(v) for k, v in value.items()})
                rules = replace(rules, category_target_moh=merged)
            elif key == "category_case_size" and isinstance(value, dict):
                merged_c = dict(rules.category_case_size)
                merged_c.update({normalize_category(k): int(v) for k, v in value.items()})
                rules = replace(rules, category_case_size=merged_c)
            elif key == "forecast" and isinstance(value, dict):
                fc_fields.update({k: v for k, v in value.items() if k in fc_fields})
            elif key in OrderingRules.__dataclass_fields__ and key != "forecast":
                current = getattr(rules, key)
                try:
                    rules = replace(rules, **{key: type(current)(value)})
                except (TypeError, ValueError):
                    continue
        return replace(rules, forecast=ForecastRules(**fc_fields))
