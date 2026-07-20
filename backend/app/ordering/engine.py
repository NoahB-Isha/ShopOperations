"""The India-ordering suggestion engine — a PURE, testable function.

Given a per-SKU snapshot (on-hand, demand forecast, in-transit arrivals,
product attributes) plus `OrderingRules`, it returns sea/air quantities, the
month 1-6 MOH projection, the economics, per-SKU flags, and a human-readable
reason for any air split. No I/O happens in here.

It reproduces the workbook's SEA-sheet math EXACTLY when demand is flat, and
generalises to a per-month demand forecast by projecting in MOH-space with a
per-month demand multiplier. When the forecast equals the flat average every
multiplier is 1.0 and the result is identical to the workbook — so the
workbook stays a provable baseline (see tests/test_workbook_parity.py: all
281 fully-numeric SEA rows reproduce within rounding).

Workbook formulas reproduced (SEA sheet):
    OH_mthN = max(0, OH_mth(N-1) - demand_moh_N + incoming_moh_N)
                                                  col K..P: =IF((prev-1+inc)<0,0,...)
    SEA SHIP (months) = max(0, target - OH_mth6)  col T: =IF(P<J, J-P, 0)
    SEA QTY           = sea_months * monthly_sales    col Q: =T*F
    AIR SHIP (months) = max(0, 3 - OH_mth4)       col U: =IF(N<3, 3-N, 0)
    AIR QTY           = air_months * monthly_sales    col S: =U*F
    round             = CEILING(qty, case)        ORDER LIST cols I/J
Air-only items (AIR sheet, BHOOMI/GOLD/SILVER):
    ORDER = max(0, min_moh - MOH) * monthly_sales col E: =IF(J-I>0,(J-I)*G,0)
Domestic vendors (DOMESTIC sheet): order one MOQ when MOH < 4, no sea/air.
Economics (ORDER LIST): margin = retail - cogs; profit_lost_air = margin * air.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .forecasting import Forecast
from .rules import OrderingRules

# Suggestion.flags values (stable strings the UI filters on)
FLAG_LOW_CONFIDENCE = "low_confidence"
FLAG_DIVERGENCE = "divergence"
FLAG_AIR_ONLY = "air_only"
FLAG_SEA_ONLY = "sea_only"
FLAG_BULK_CYCLE = "bulk_cycle"
FLAG_EXPIRY = "expiry"
FLAG_ANALOGY = "analogy"
FLAG_DOMESTIC = "domestic"
FLAG_LOW_COUNT = "low_count"  # tiny on-hand — Odoo counts this small are often wrong
FLAG_NEW_PRODUCT = "new_product"  # no history, no analogy — engine can't suggest


def ceil_to_case(qty: float, case: int) -> int:
    """Excel CEILING(qty, case): round qty UP to the nearest multiple of case."""
    if qty <= 0:
        return 0
    if case <= 1:
        return int(math.ceil(qty - 1e-9))
    return int(math.ceil((qty - 1e-9) / case) * case)


@dataclass
class ProductInput:
    global_sku: str
    name: str = ""
    us_sku: str = ""
    category: str = ""
    case_size: int | None = None
    unit_weight_g: float | None = None
    hsn_code: str = ""
    cost: float = 0.0  # COGS / landed cost
    retail_price: float = 0.0
    target_moh_override: float | None = None
    # rule switches (derived from product tags / vendor by the inputs builder)
    is_domestic: bool = False
    moq: int | None = None  # domestic vendors
    air_only: bool = False
    sea_only: bool = False
    bulk_cycle: bool = False
    expiry_sensitive: bool = False


@dataclass
class SkuSnapshot:
    product: ProductInput
    on_hand: float  # useable on-hand units
    avg_monthly_sales: float  # sell-through velocity (sold per in-stock month)
    incoming_units_by_month: list[float]  # in-transit arriving in proj month 1..H
    forecast: Forecast | None = None  # per-month demand; None => flat
    units_sold: float = 0.0  # total units sold in the trailing window
    months_active: int = 0  # months the SKU was selling


@dataclass
class Suggestion:
    global_sku: str
    name: str
    us_sku: str
    category: str
    # demand
    avg_monthly_sales: float
    units_sold: float
    months_active: int
    forecast_monthly: list[float]
    forecast_mean: float
    baseline_monthly_sales: float
    forecast_method: str
    forecast_confidence: str
    diverges_from_baseline: bool
    # stock / projection
    on_hand: float
    current_moh: float
    incoming_units_by_month: list[float]
    projected_moh: list[float]  # month 1..H
    projected_moh_m4: float
    projected_moh_m6: float
    projected_moh_with_order: list[float]  # coverage IF this order is placed
    target_moh: float
    case_size: int
    # quantities (smart forecast)
    suggested_sea_qty: float
    suggested_air_qty: float
    suggested_sea_round: int
    suggested_air_round: int
    # quantities (workbook flat baseline, always shown alongside)
    baseline_sea_round: int
    baseline_air_round: int
    # economics
    unit_cost: float
    retail_price: float
    margin: float
    profit_lost_by_air: float
    # explainability
    air_split_reason: str
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _project_moh(
    current_moh: float, demand_moh: list[float], incoming_moh: list[float]
) -> list[float]:
    """Forward MOH projection; demand_moh of 1.0 per month is exactly the
    workbook's '-1 per month'."""
    oh = current_moh
    out = []
    for n in range(len(incoming_moh)):
        d = demand_moh[n] if n < len(demand_moh) else 1.0
        oh = max(0.0, oh - d + incoming_moh[n])
        out.append(oh)
    return out


def suggest_one(snap: SkuSnapshot, rules: OrderingRules) -> Suggestion:
    p = snap.product
    avg = snap.avg_monthly_sales
    horizon = rules.horizon
    case = rules.case_size_for(p.category, p.case_size)
    target = rules.target_moh_for(p.category, p.target_moh_override)

    flags: list[str] = []
    notes: list[str] = list(snap.forecast.notes) if snap.forecast else []

    # Target adjustments from category rules. An item that is somehow both
    # bulk-cycle and expiry-sensitive gets the cap — expiry risk wins.
    if p.bulk_cycle and p.target_moh_override is None:
        target = max(target, rules.bulk_cycle_target_moh)
        flags.append(FLAG_BULK_CYCLE)
        notes.append(
            f"bulk-cycle item: ordered ~yearly, refill target raised to {target:g} months"
        )
    if p.expiry_sensitive:
        if target > rules.expiry_max_target_moh:
            target = rules.expiry_max_target_moh
            notes.append(
                f"expiry-sensitive: refill target capped at {target:g} months of cover"
            )
        flags.append(FLAG_EXPIRY)

    inc_units = list(snap.incoming_units_by_month) + [0.0] * (
        horizon - len(snap.incoming_units_by_month)
    )
    inc_units = inc_units[:horizon]
    incoming_moh = [(u / avg if avg > 0 else 0.0) for u in inc_units]

    current_moh = (snap.on_hand / avg) if avg > 0 else 0.0

    # ---- demand multipliers (per-month MOH consumed) ---------------------
    if snap.forecast and avg > 0:
        demand_moh = [(m / avg) for m in snap.forecast.monthly[:horizon]]
    else:
        demand_moh = [1.0] * horizon  # flat: identical to the workbook

    proj = _project_moh(current_moh, demand_moh, incoming_moh)
    ai = min(rules.air_lead_months, horizon) - 1
    si = min(rules.sea_lead_months, horizon) - 1
    proj_m4 = proj[ai] if proj else current_moh
    proj_m6 = proj[si] if proj else current_moh

    # ---- baseline (workbook flat) projection for comparison --------------
    base_proj = _project_moh(current_moh, [1.0] * horizon, incoming_moh)
    base_m4, base_m6 = base_proj[ai], base_proj[si]

    if p.is_domestic:
        # MOQ-driven: order one MOQ when MOH < trigger, no sea/air split.
        moq = p.moq or 0
        order = moq if current_moh < rules.domestic_moq_trigger_moh else 0
        sea_qty, air_qty = float(order), 0.0
        base_sea_qty, base_air_qty = float(order), 0.0
        flags.append(FLAG_DOMESTIC)
        reason = (
            f"Domestic vendor item; {current_moh:.1f} months on hand is below the "
            f"{rules.domestic_moq_trigger_moh:g}-month trigger — order one MOQ of {moq}."
            if order
            else f"Domestic vendor item; {current_moh:.1f} months on hand is above the "
            f"{rules.domestic_moq_trigger_moh:g}-month trigger — no order needed."
        )
    elif p.air_only:
        # AIR sheet rule: top up to the minimum MOH, everything ships air.
        min_moh = (
            p.target_moh_override
            if p.target_moh_override is not None
            else rules.air_only_min_moh
        )
        effective_moh = current_moh
        inflight_note = ""
        if rules.air_only_count_incoming and avg > 0:
            inflight = sum(inc_units[: rules.air_lead_months])
            if inflight > 0:
                effective_moh += inflight / avg
                inflight_note = f" ({inflight:g} units already in transit counted)"
        air_qty = max(0.0, min_moh - effective_moh) * avg
        sea_qty = 0.0
        base_air_qty, base_sea_qty = air_qty, 0.0
        target = min_moh
        flags.append(FLAG_AIR_ONLY)
        reason = (
            f"Air-only item: topped up to {min_moh:g} months on hand "
            f"(currently {current_moh:.1f}){inflight_note}. Never ships by sea."
            if air_qty > 0
            else f"Air-only item: {current_moh:.1f} months on hand already meets the "
            f"{min_moh:g}-month minimum{inflight_note}. Never ships by sea."
        )
    else:
        # SEA: refill to target at month 6.
        sea_qty = max(0.0, target - proj_m6) * avg
        # AIR: cover a near-term floor breach at month 4.
        air_qty = max(0.0, rules.air_nearterm_floor_moh - proj_m4) * avg
        base_sea_qty = max(0.0, target - base_m6) * avg
        base_air_qty = max(0.0, rules.air_nearterm_floor_moh - base_m4) * avg
        if p.sea_only and (air_qty > 0 or base_air_qty > 0):
            # the near-term gap still exists — surface it, don't order air
            notes.append(
                f"sea-only item: would breach the {rules.air_nearterm_floor_moh:g}-month "
                f"floor at month {rules.air_lead_months} "
                f"({proj_m4:.1f} MOH) but air is not allowed"
            )
            sea_qty += air_qty  # cover the gap on the sea leg instead
            base_sea_qty += base_air_qty
            air_qty, base_air_qty = 0.0, 0.0
            flags.append(FLAG_SEA_ONLY)
            reason = "Sea-only item: near-term gap folded into the sea quantity."
        elif air_qty > 0:
            weeks = round((rules.air_nearterm_floor_moh - proj_m4) * 4.345)
            reason = (
                f"Projected months-on-hand at month {rules.air_lead_months} is "
                f"{proj_m4:.1f}, below the {rules.air_nearterm_floor_moh:g}-month floor "
                f"(~{weeks} wks short); the sea container only lands month "
                f"{rules.sea_lead_months}. Air-cover the gap."
            )
        else:
            reason = (
                f"No air needed: months-on-hand at month {rules.air_lead_months} "
                f"({proj_m4:.1f}) stays above the {rules.air_nearterm_floor_moh:g}-month floor."
            )

    sea_round = ceil_to_case(sea_qty, case)
    air_round = ceil_to_case(air_qty, case)
    base_sea_round = ceil_to_case(base_sea_qty, case)
    base_air_round = ceil_to_case(base_air_qty, case)

    # ---- "with planned order" projection: the same forward model plus the
    # suggested air arriving at the air-lead month and sea at the sea-lead
    # month — the coverage the buyer is about to buy.
    arrivals = list(incoming_moh)
    if avg > 0:
        if 0 <= ai < horizon:
            arrivals[ai] += air_round / avg
        if 0 <= si < horizon:
            arrivals[si] += sea_round / avg
    proj_with_order = _project_moh(current_moh, demand_moh, arrivals)

    fc = snap.forecast
    if fc:
        if fc.confidence == "low":
            flags.append(FLAG_LOW_CONFIDENCE)
        if fc.diverges_from_baseline:
            flags.append(FLAG_DIVERGENCE)
        if fc.method == "analogy":
            flags.append(FLAG_ANALOGY)
    else:
        flags.append(FLAG_LOW_CONFIDENCE)
    if 0 < snap.on_hand <= 2:
        flags.append(FLAG_LOW_COUNT)
        notes.append(f"{snap.on_hand:g} on hand — low counts are often wrong; verify physically")
    if snap.months_active == 0 and avg <= 0:
        flags.append(FLAG_NEW_PRODUCT)
        notes.append("new product: no sales history — assign an analogy or a monthly estimate")

    margin = p.retail_price - p.cost
    return Suggestion(
        global_sku=p.global_sku,
        name=p.name,
        us_sku=p.us_sku,
        category=p.category,
        avg_monthly_sales=round(avg, 3),
        units_sold=round(snap.units_sold or 0.0, 1),
        months_active=snap.months_active or 0,
        forecast_monthly=[round(m, 2) for m in (fc.monthly if fc else [avg] * horizon)],
        forecast_mean=round(fc.forecast_mean if fc else avg, 3),
        baseline_monthly_sales=round(fc.baseline if fc else avg, 3),
        forecast_method=fc.method if fc else "flat_avg",
        forecast_confidence=fc.confidence if fc else "low",
        diverges_from_baseline=fc.diverges_from_baseline if fc else False,
        on_hand=round(snap.on_hand, 2),
        current_moh=round(current_moh, 3),
        incoming_units_by_month=[round(u, 1) for u in inc_units],
        projected_moh=[round(x, 3) for x in proj],
        projected_moh_m4=round(proj_m4, 3),
        projected_moh_m6=round(proj_m6, 3),
        projected_moh_with_order=[round(x, 3) for x in proj_with_order],
        target_moh=target,
        case_size=case,
        suggested_sea_qty=round(sea_qty, 2),
        suggested_air_qty=round(air_qty, 2),
        suggested_sea_round=sea_round,
        suggested_air_round=air_round,
        baseline_sea_round=base_sea_round,
        baseline_air_round=base_air_round,
        unit_cost=round(p.cost, 4),
        retail_price=round(p.retail_price, 2),
        margin=round(margin, 4),
        profit_lost_by_air=round(margin * air_round, 2),
        air_split_reason=reason,
        flags=flags,
        notes=notes,
    )


def suggest_all(snapshots: list[SkuSnapshot], rules: OrderingRules) -> list[Suggestion]:
    return [suggest_one(s, rules) for s in snapshots]
