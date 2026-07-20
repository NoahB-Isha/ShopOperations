"""Tests for the pure demand forecaster (method ladder, stockout exclusion,
divergence flagging)."""

from __future__ import annotations

from app.ordering.forecasting import MonthlySalesSeries, MonthPoint, forecast_demand
from app.ordering.rules import ForecastRules


def _series(values, start_year=2024, start_month=1, stockouts=None):
    stockouts = stockouts or set()
    pts = []
    y, m = start_year, start_month
    for i, v in enumerate(values):
        pts.append(MonthPoint(y, m, v, is_stockout=(i in stockouts)))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return MonthlySalesSeries(points=pts)


def test_sparse_history_falls_back_to_baseline():
    fc = forecast_demand(_series([10, 12, 8]), 6, ForecastRules())
    assert fc.method == "flat_avg"
    assert fc.confidence == "low"
    assert fc.low_data
    assert all(abs(x - 10.0) < 1e-6 for x in fc.monthly)


def test_flat_series_baseline_equals_forecast():
    fc = forecast_demand(_series([100] * 18), 6, ForecastRules())
    assert abs(fc.baseline - 100) < 1e-6
    assert all(abs(x - 100) < 1.0 for x in fc.monthly)
    assert not fc.diverges_from_baseline


def test_seasonality_detected_with_two_years():
    # strong December peak, 24 months
    base = [50, 50, 60, 60, 70, 70, 80, 80, 70, 90, 120, 200]
    fc = forecast_demand(_series(base + base), 6, ForecastRules())
    assert fc.method == "seasonal_trend"
    assert fc.n_history_months == 24
    assert max(fc.monthly) - min(fc.monthly) > 5  # not a flat line


def test_seasonal_forecast_peaks_in_the_right_month():
    # 24 months ending Dec 2025 -> forecast months Jan..Jun 2026; March triples.
    base = [50, 50, 150, 50, 50, 50, 50, 50, 50, 50, 50, 50]
    fc = forecast_demand(_series(base + base), 6, ForecastRules())
    assert fc.method == "seasonal_trend"
    march = fc.monthly[2]  # month 3 of the future window (Jan, Feb, MAR...)
    others = [v for i, v in enumerate(fc.monthly) if i != 2]
    assert march > max(others) * 1.5


def test_stockout_month_excluded():
    vals = [100, 100, 100, 0, 100, 100, 100, 100, 100, 100, 100, 100]
    fc = forecast_demand(_series(vals, stockouts={3}), 6, ForecastRules())
    assert fc.baseline > 95  # the suppressed zero didn't drag the mean down
    assert any("stock-out" in n for n in fc.notes)


def test_trend_increases_forecast():
    vals = list(range(50, 50 + 18 * 3, 3))  # rising
    fc = forecast_demand(_series(vals), 6, ForecastRules())
    assert fc.forecast_mean > fc.baseline


def test_divergence_flag():
    vals = list(range(20, 20 + 18 * 6, 6))  # steep rise
    fc = forecast_demand(_series(vals), 6, ForecastRules())
    assert fc.diverges_from_baseline
    assert any("diverges" in n for n in fc.notes)


def test_empty_series_yields_zero_baseline():
    fc = forecast_demand(MonthlySalesSeries(points=[]), 6, ForecastRules())
    assert fc.baseline == 0.0
    assert fc.monthly == [0.0] * 6
    assert fc.confidence == "low"
