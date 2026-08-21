"""Ordering engine vs HAND-CHECKED rows pulled directly from the workbook's
SEA sheet, plus the tag-driven category rules the workbook keeps on separate
sheets (AIR, DOMESTIC).

Each SEA fixture carries the workbook's own computed projection (OH MTH 1..6),
SEA QTY and AIR QTY; the pure engine must reproduce them within rounding —
the acceptance criterion. The full 281-row sweep lives in
test_workbook_parity.py; these rows are the hand-audited core.
"""

from __future__ import annotations

import pytest
from app.ordering.engine import (
    FLAG_AIR_ONLY,
    FLAG_BULK_CYCLE,
    FLAG_DIVERGENCE,
    FLAG_DOMESTIC,
    FLAG_EXPIRY,
    FLAG_LOW_CONFIDENCE,
    FLAG_LOW_COUNT,
    FLAG_SEA_ONLY,
    ProductInput,
    SkuSnapshot,
    _project_moh,
    ceil_to_case,
    suggest_one,
)
from app.ordering.forecasting import Forecast, flat_forecast
from app.ordering.rules import OrderingRules, normalize_category

# fixtures: (name, monthly_sales, current_MOH, target, incoming_MOH[6],
#            expected OH MTH1..6, expected sea_qty, expected air_qty)
SEA_FIXTURES = [
    ("Devi-Car-Hanging", 67.25, 0.1635687732, 6.0,
     [0, 3.1822, 0, 5.3383, 0.5204, 0],
     [0, 2.1822, 1.1822, 5.5204, 5.0409, 4.0409], 131.75, 0),
    ('Adiyogi miniature 2" car stand - Black', 50.4167, 0.0, 6.0,
     [0, 2.8165, 0, 5.6331, 0.595, 0],
     [0, 1.8165, 0.8165, 5.4496, 5.0446, 4.0446], 98.5833, 0),
    ("Jasmine Orient Solid Perfume", 82.5, 0.0121212121, 8.0,
     [0, 10.3758, 0, 1.7939, 0, 0],
     [0, 9.3758, 8.3758, 9.1697, 8.1697, 7.1697], 68.5, 0),
    ("Copper Devi Pendant", 30.0, 0.0, 10.0,
     [0, 0, 0, 0.0333, 10, 0],
     [0, 0, 0, 0, 9, 8], 60, 90),
    ("Instant Sanjeevini 20 kg Bag", 6.0, 0.0, 8.0,
     [0, 0, 0, 0, 0, 0],
     [0, 0, 0, 0, 0, 0], 48, 18),
]


def _mk(monthly, moh, target, inc_moh, **product_kwargs):
    on_hand = moh * monthly
    inc_units = [m * monthly for m in inc_moh]
    p = ProductInput(global_sku="X", category="TEST", cost=2.0, retail_price=5.0,
                     target_moh_override=target, **product_kwargs)
    return SkuSnapshot(product=p, on_hand=on_hand, avg_monthly_sales=monthly,
                       incoming_units_by_month=inc_units, forecast=None)


def test_projection_matches_workbook():
    for name, _mon, moh, _target, inc, exp_proj, _sea, _air in SEA_FIXTURES:
        proj = _project_moh(moh, [1.0] * 6, inc)
        for got, want in zip(proj, exp_proj, strict=True):
            assert abs(got - want) < 0.01, f"{name}: proj {got} != {want}"


def test_sea_air_quantities_match_workbook():
    rules = OrderingRules()
    for name, mon, moh, target, inc, _proj, exp_sea, exp_air in SEA_FIXTURES:
        s = suggest_one(_mk(mon, moh, target, inc), rules)
        assert abs(s.suggested_sea_qty - exp_sea) < 1.0, \
            f"{name}: sea {s.suggested_sea_qty} != {exp_sea}"
        assert abs(s.suggested_air_qty - exp_air) < 1.0, \
            f"{name}: air {s.suggested_air_qty} != {exp_air}"


def test_ceiling_to_case():
    assert ceil_to_case(131.75, 1) == 132
    assert ceil_to_case(98.58, 1) == 99
    assert ceil_to_case(60, 60) == 60
    assert ceil_to_case(61, 60) == 120
    assert ceil_to_case(0, 32) == 0
    assert ceil_to_case(1, 32) == 32


def test_air_split_reason_present_when_air_ordered():
    # Copper Devi Pendant: stocks out before the sea leg lands -> air
    s = suggest_one(_mk(30.0, 0.0, 10.0, [0, 0, 0, 0.0333, 10, 0]), OrderingRules())
    assert s.suggested_air_round > 0
    assert "floor" in s.air_split_reason.lower()


def test_forecast_flat_equals_baseline():
    """When the forecast equals the flat average, the smart result must equal
    the workbook baseline exactly."""
    mon = 100.0
    fc = flat_forecast(mon, 6)
    p = ProductInput(global_sku="X", category="TEST", target_moh_override=8.0,
                     cost=1, retail_price=3)
    snap = SkuSnapshot(product=p, on_hand=0, avg_monthly_sales=mon,
                       incoming_units_by_month=[0] * 6, forecast=fc)
    s = suggest_one(snap, OrderingRules())
    assert s.suggested_sea_round == s.baseline_sea_round
    assert s.suggested_air_round == s.baseline_air_round


def test_seasonal_forecast_shifts_quantities_but_keeps_baseline():
    """A December-heavy forecast consumes MOH faster than flat -> more sea;
    the baseline columns still carry the workbook numbers for comparison."""
    mon = 100.0
    heavy = Forecast(monthly=[150, 150, 150, 150, 150, 150], method="seasonal_trend",
                     baseline=mon, confidence="high", n_history_months=24,
                     low_data=False, uncertainty_pct=0.1, diverges_from_baseline=True)
    p = ProductInput(global_sku="X", category="TEST", target_moh_override=8.0,
                     cost=1, retail_price=3)
    snap = SkuSnapshot(product=p, on_hand=700, avg_monthly_sales=mon,
                       incoming_units_by_month=[0] * 6, forecast=heavy)
    s = suggest_one(snap, OrderingRules())
    # flat: 7 MOH lasts past month 4 (no air) and leaves 1 MOH at month 6 -> sea 700
    # heavy: burns 1.5 MOH/mo -> dry by month 5 -> sea 800 and an air top-up
    assert s.baseline_sea_round == 700 and s.baseline_air_round == 0
    assert s.suggested_sea_round == 800
    assert s.suggested_air_round > 0
    assert FLAG_DIVERGENCE in s.flags


# ---------------------------------------------------------------- rule tests
def test_air_only_tops_up_to_min_moh():
    """AIR sheet col E: =IF(J-I>0,(J-I)*G,0) — refill to MIN MOH, air only."""
    rules = OrderingRules()
    p = ProductInput(global_sku="G1", category="CONX", air_only=True, cost=1, retail_price=2)
    snap = SkuSnapshot(product=p, on_hand=20.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    # MOH 2, min 6 -> 4 months * 10/mo = 40 air, no sea
    assert s.suggested_air_qty == 40.0
    assert s.suggested_sea_qty == 0.0
    assert FLAG_AIR_ONLY in s.flags
    assert "air-only" in s.air_split_reason.lower()


def test_air_only_counts_in_transit_units():
    rules = OrderingRules()
    p = ProductInput(global_sku="G1", category="CONX", air_only=True)
    snap = SkuSnapshot(product=p, on_hand=20.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[30, 0, 0, 0, 0, 0], forecast=None)
    s = suggest_one(snap, rules)
    # effective MOH = 2 + 3 -> only 1 month short
    assert s.suggested_air_qty == 10.0
    assert "transit" in s.air_split_reason


def test_sea_only_folds_near_term_gap_into_sea():
    rules = OrderingRules()
    p = ProductInput(global_sku="S1", category="TEST", sea_only=True,
                     target_moh_override=8.0)
    snap = SkuSnapshot(product=p, on_hand=0.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    # workbook would say sea 80 + air 30; sea-only folds air into sea
    assert s.suggested_air_qty == 0.0
    assert s.suggested_sea_qty == 110.0
    assert FLAG_SEA_ONLY in s.flags


def test_bulk_cycle_raises_target():
    rules = OrderingRules()
    p = ProductInput(global_sku="B1", category="BODY CARE", bulk_cycle=True)
    snap = SkuSnapshot(product=p, on_hand=0.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    assert s.target_moh == 12.0  # raised from BODY CARE's 8 to the yearly cycle
    assert s.suggested_sea_qty == 120.0
    assert FLAG_BULK_CYCLE in s.flags


def test_expiry_caps_target():
    rules = OrderingRules()
    p = ProductInput(global_sku="E1", category="BODY CARE", expiry_sensitive=True)
    snap = SkuSnapshot(product=p, on_hand=0.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    assert s.target_moh == 6.0  # capped below BODY CARE's 8
    assert FLAG_EXPIRY in s.flags


def test_explicit_target_override_beats_bulk_rule():
    rules = OrderingRules()
    p = ProductInput(global_sku="B2", category="TEST", bulk_cycle=True,
                     target_moh_override=9.0)
    snap = SkuSnapshot(product=p, on_hand=0.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    assert s.target_moh == 9.0  # the buyer's per-SKU number wins


def test_domestic_moq_trigger():
    rules = OrderingRules()
    p = ProductInput(global_sku="D1", category="BODY CARE", is_domestic=True, moq=1080)
    low = SkuSnapshot(product=p, on_hand=32.0, avg_monthly_sales=164.25,
                      incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(low, rules)  # MOH 0.19 < 4 -> order one MOQ
    assert s.suggested_sea_qty == 1080.0
    assert s.suggested_air_qty == 0.0
    assert FLAG_DOMESTIC in s.flags
    high = SkuSnapshot(product=p, on_hand=1000.0, avg_monthly_sales=164.25,
                       incoming_units_by_month=[0] * 6, forecast=None)
    assert suggest_one(high, rules).suggested_sea_qty == 0.0


def test_low_count_and_low_confidence_flags():
    rules = OrderingRules()
    p = ProductInput(global_sku="L1", category="TEST")
    snap = SkuSnapshot(product=p, on_hand=2.0, avg_monthly_sales=1.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    assert FLAG_LOW_COUNT in s.flags
    assert FLAG_LOW_CONFIDENCE in s.flags
    assert any("verify physically" in n for n in s.notes)


def test_bloom_category_case_size_applies():
    rules = OrderingRules()
    p = ProductInput(global_sku="BL1", category="Isha Life USA / Bloom",
                     expiry_sensitive=True)
    snap = SkuSnapshot(product=p, on_hand=0.0, avg_monthly_sales=10.0,
                       incoming_units_by_month=[0] * 6, forecast=None)
    s = suggest_one(snap, rules)
    assert s.case_size == 32
    assert s.suggested_sea_round % 32 == 0


# ------------------------------------------------------------- rules plumbing
def test_normalize_category_handles_odoo_paths():
    assert normalize_category("Isha Life USA / Body Care") == "BODY CARE"
    assert normalize_category("BODY CARE") == "BODY CARE"
    assert normalize_category("  copper ") == "COPPER"
    assert normalize_category(None) == ""


def test_rules_merged_overrides():
    rules = OrderingRules().merged({
        "default_target_moh": 9,
        "air_only_min_moh": 5,
        "category_target_moh": {"Body Care": 7},
        "category_case_size": {"bloom": 24},
        "forecast": {"divergence_flag_pct": 0.5},
        "unknown_key": "ignored",
    })
    assert rules.default_target_moh == 9.0
    assert rules.air_only_min_moh == 5.0
    assert rules.target_moh_for("Isha Life USA / Body Care") == 7.0
    assert rules.case_size_for("BLOOM") == 24
    assert rules.forecast.divergence_flag_pct == 0.5
    # untouched values survive the merge
    assert rules.target_moh_for("COPPER") == 8.0
    assert rules.domestic_moq_trigger_moh == 4.0


def test_rules_merged_bad_values_never_raise():
    rules = OrderingRules().merged({"default_target_moh": "not-a-number"})
    assert rules.default_target_moh == 8.0


def _cov_snap(*, category: str, monthly: float, moh: float, **product_kwargs):
    """A snapshot with NO per-SKU target override, so the category/default
    target is what decides — which is the whole point of the coverage tests."""
    p = ProductInput(
        global_sku="COV", category=category, cost=2.0, retail_price=5.0, **product_kwargs
    )
    return SkuSnapshot(
        product=p,
        on_hand=moh * monthly,
        avg_monthly_sales=monthly,
        incoming_units_by_month=[0.0] * 6,
        forecast=None,
    )


# ------------------------------------------------------ coverage (a year's worth)
def test_coverage_moves_every_category_not_just_the_default():
    """Noah 2026-08-20: the first real shipment orders a YEAR, not a quarter.

    The trap this guards: `target_moh_for` reads the category map BEFORE
    `default_target_moh`, and every category has an entry — so setting the
    default alone would leave all of them at 8 and quietly under-order."""
    from app.ordering.rules import OrderingRules, coverage_of, coverage_overrides

    base = OrderingRules()
    assert coverage_of(base) is None  # ACCESSORY is 6 while the rest are 8

    year = OrderingRules().merged(coverage_overrides(12))
    assert coverage_of(year) == 12
    assert year.target_moh_for("Isha Life USA / Body Care") == 12
    assert year.target_moh_for("ACCESSORY") == 12  # the 6.0 outlier moved too
    assert year.target_moh_for("Something Unlisted") == 12

    # setting only the default is exactly the mistake — proof it isn't enough
    naive = OrderingRules().merged({"default_target_moh": 12})
    assert naive.target_moh_for("Isha Life USA / Body Care") == 8


def test_coverage_leaves_the_protective_limits_alone():
    """A year of cover must not become a year of face wash or a year of gold
    in the air."""
    from app.ordering.rules import OrderingRules, coverage_overrides

    year = OrderingRules().merged(coverage_overrides(12))
    assert year.expiry_max_target_moh == 6.0  # Bloom / expires still capped
    assert year.air_only_min_moh == 6.0  # Bhoomi / gold / silver unchanged
    assert year.sea_lead_months == 6 and year.horizon == 6  # WHEN, not how much


def test_a_year_of_cover_orders_more_but_expiry_items_hold_at_six():
    """End to end through the engine on one snapshot each way."""
    from app.ordering.engine import suggest_one
    from app.ordering.rules import OrderingRules, coverage_overrides

    quarter = OrderingRules()
    year = OrderingRules().merged(coverage_overrides(12))

    plain = _cov_snap(category="INCENSE", monthly=10, moh=10)
    q = suggest_one(plain, quarter)
    y = suggest_one(plain, year)
    assert y.target_moh == 12 and q.target_moh == 8
    assert y.suggested_sea_qty > q.suggested_sea_qty  # a year's cover is a bigger container
    # the extra is exactly the extra months of cover, in units
    assert round(y.suggested_sea_qty - q.suggested_sea_qty, 3) == round((12 - 8) * plain.avg_monthly_sales, 3)

    bloom = _cov_snap(category="BLOOM", monthly=10, moh=10, expiry_sensitive=True)
    assert suggest_one(bloom, year).target_moh == 6.0  # cap wins over coverage
    assert (
        suggest_one(bloom, year).suggested_sea_qty
        == suggest_one(bloom, quarter).suggested_sea_qty
    )


# --------------------------------------------- findings 01/02/04/06 (2026-08-20)
def _fc(monthly, baseline, *, sd=0.0, level=0.0):
    from app.ordering.forecasting import Forecast

    return Forecast(
        monthly=list(monthly),
        method="test",
        baseline=baseline,
        confidence="high",
        n_history_months=24,
        low_data=False,
        uncertainty_pct=0.1,
        diverges_from_baseline=True,
        forward_level=level or (sum(monthly) / len(monthly)),
        demand_sd=sd,
    )


def test_cover_is_priced_at_the_forward_rate_not_the_trailing_one():
    """Finding 01. The projection consumed stock at the forecast rate while the
    order bought baseline-sized months, so a rising forecast under-ordered by
    exactly the ratio it was warning about. Measured at 23% before the fix."""
    rules = OrderingRules()  # INCENSE target 8
    avg, fast = 10.0, 13.0
    snap = SkuSnapshot(
        product=ProductInput(global_sku="X", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=0.0,
        avg_monthly_sales=avg,
        incoming_units_by_month=[0.0] * 6,
        forecast=_fc([fast] * 6, avg),
    )
    s = suggest_one(snap, rules)
    assert s.suggested_sea_qty == pytest.approx(8 * fast)  # 104, not 80
    # the baseline column still shows the workbook's own answer, for comparison
    assert s.baseline_sea_round == 8 * int(avg)


def test_a_flat_forecast_is_still_exactly_the_workbook():
    """The fix must be invisible when the forecast agrees with history — that
    is what keeps the parity test meaningful."""
    rules = OrderingRules()
    avg = 10.0
    snap = SkuSnapshot(
        product=ProductInput(global_sku="X", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=30.0,
        avg_monthly_sales=avg,
        incoming_units_by_month=[0.0] * 6,
        forecast=_fc([avg] * 6, avg),
    )
    s = suggest_one(snap, rules)
    assert s.suggested_sea_round == s.baseline_sea_round


def test_safety_stock_is_off_by_default_and_scales_with_volatility():
    """Finding 02. Two items, same mean, very different swing."""
    steady = SkuSnapshot(
        product=ProductInput(global_sku="S", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=0.0, avg_monthly_sales=10.0, incoming_units_by_month=[0.0] * 6,
        forecast=_fc([10.0] * 6, 10.0, sd=1.0),
    )
    erratic = SkuSnapshot(
        product=ProductInput(global_sku="E", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=0.0, avg_monthly_sales=10.0, incoming_units_by_month=[0.0] * 6,
        forecast=_fc([10.0] * 6, 10.0, sd=8.0),
    )
    off = OrderingRules()
    assert off.safety_z == 0.0
    assert suggest_one(steady, off).suggested_sea_qty == suggest_one(erratic, off).suggested_sea_qty

    on = OrderingRules().merged({"safety_z": 1.28})
    q_steady = suggest_one(steady, on).suggested_sea_qty
    q_erratic = suggest_one(erratic, on).suggested_sea_qty
    assert q_erratic > q_steady > suggest_one(steady, off).suggested_sea_qty
    # and it is bounded, so one wild seller can't ask for a decade
    wild = SkuSnapshot(
        product=ProductInput(global_sku="W", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=0.0, avg_monthly_sales=10.0, incoming_units_by_month=[0.0] * 6,
        forecast=_fc([10.0] * 6, 10.0, sd=500.0),
    )
    assert suggest_one(wild, on).target_moh <= 8.0  # target itself is untouched
    capped = suggest_one(wild, on).suggested_sea_qty / 10.0
    assert capped <= 8.0 + on.safety_max_moh + 0.001


def test_an_item_that_sold_nothing_while_in_stock_is_flagged():
    """Finding 04, corrected: a brand-new product was already flagged. What
    wasn't is an item that sat in stock for months and sold none — it can only
    ever suggest zero, and on an annual cycle that silence costs a year."""
    from app.ordering.engine import FLAG_NEW_PRODUCT, FLAG_NO_DEMAND

    rules = OrderingRules()
    dead = SkuSnapshot(
        product=ProductInput(global_sku="D", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=40.0, avg_monthly_sales=0.0, incoming_units_by_month=[0.0] * 6,
        forecast=None, months_active=9,
    )
    s = suggest_one(dead, rules)
    assert s.suggested_sea_qty == 0
    assert FLAG_NO_DEMAND in s.flags
    assert FLAG_NEW_PRODUCT not in s.flags  # it isn't new, it just doesn't sell

    brand_new = SkuSnapshot(
        product=ProductInput(global_sku="N", category="INCENSE", cost=1.0, retail_price=3.0),
        on_hand=0.0, avg_monthly_sales=0.0, incoming_units_by_month=[0.0] * 6,
        forecast=None, months_active=0,
    )
    assert FLAG_NEW_PRODUCT in suggest_one(brand_new, rules).flags


def test_seasonal_indices_are_shrunk_toward_one():
    """Finding 06. At 24 months an index rests on two observations; a raw one
    turns a single odd month into a permanent seasonal claim."""
    from app.ordering.forecasting import MonthPoint, _seasonal_indices

    # two years, December triple-strength, everything else flat at 10
    pts = []
    for year in (2024, 2025):
        for month in range(1, 13):
            pts.append(MonthPoint(year=year, month=month, units=30.0 if month == 12 else 10.0))
    raw = _seasonal_indices(pts, slope=0.0, intercept=11.67, shrink_k=0.0)
    shrunk = _seasonal_indices(pts, slope=0.0, intercept=11.67, shrink_k=2.0)
    assert raw[12] > shrunk[12] > 1.0  # still seasonal, just less certain of it
    assert shrunk[6] > raw[6]  # and the quiet months are pulled up to match
