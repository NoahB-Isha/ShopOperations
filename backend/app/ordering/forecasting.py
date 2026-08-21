"""Demand forecasting — a PURE, testable module (no I/O).

The workbook collapses a year of sales into a flat monthly average
(annual / 12). That throws away trend and seasonality — exactly the
information worth keeping when the goods ordered now land 4-6 months out.
Odoo holds 24 months of history (synced into `sales_monthly`), which is
enough to fit multiplicative seasonal indices.

Method ladder, sized for 12-36 monthly data points:

  * < 6 useable months   -> flat average (the workbook baseline), low conf
  * 6-23 useable months  -> recent moving-average level + gentle OLS trend
  * >= 24 useable months -> multiplicative seasonal indices + trend

A month with zero sales because the item was OUT OF STOCK is not zero
demand: `is_stockout` points are excluded from the fit and flagged.

Every forecast carries the flat baseline alongside the smart number plus a
divergence flag when they disagree by more than `divergence_flag_pct` — the
buyer always sees both (project brief §1).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date

from .rules import ForecastRules

METHOD_FLAT = "flat_avg"
METHOD_TREND = "moving_avg_trend"
METHOD_SEASONAL = "seasonal_trend"
METHOD_ANALOGY = "analogy"  # forecast-by-analogy for new products (inputs.py)


@dataclass
class MonthPoint:
    year: int
    month: int  # 1-12
    units: float
    is_stockout: bool = False  # True => suppressed demand, exclude from fit

    @property
    def ord(self) -> int:
        return self.year * 12 + (self.month - 1)


@dataclass
class MonthlySalesSeries:
    """Trailing monthly sales for one SKU (oldest -> newest)."""

    points: list[MonthPoint] = field(default_factory=list)

    def useable(self) -> list[MonthPoint]:
        return [p for p in self.points if not p.is_stockout]


@dataclass
class Forecast:
    monthly: list[float]  # expected units for each future month
    method: str  # METHOD_* above
    baseline: float  # workbook flat average (units / month) — TRAILING rate
    confidence: str  # "high" | "medium" | "low"
    n_history_months: int
    low_data: bool
    uncertainty_pct: float  # rough +/- band as a fraction
    diverges_from_baseline: bool
    # The deseasonalised rate demand is expected to run at going FORWARD, used
    # to price months of cover. The baseline is what demand DID; this is what
    # it is expected to do, and buying cover at the trailing rate when the two
    # disagree is what finding 01 was about. 0.0 falls back to the baseline.
    forward_level: float = 0.0
    # Standard deviation of monthly units over the useable months — the input
    # to safety stock. 0.0 means "not enough history to say".
    demand_sd: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def forecast_mean(self) -> float:
        return sum(self.monthly) / len(self.monthly) if self.monthly else 0.0


def flat_forecast(avg_monthly: float, horizon: int, method: str = METHOD_FLAT,
                  notes: list[str] | None = None, n_history_months: int = 0) -> Forecast:
    """A constant-demand forecast — the fallback shape shared by the low-data
    path, analogy estimates, and CSV imports that carry only an average."""
    return Forecast(
        monthly=[round(avg_monthly, 4)] * horizon,
        method=method,
        baseline=round(avg_monthly, 4),
        confidence="low",
        n_history_months=n_history_months,
        low_data=True,
        uncertainty_pct=0.5,
        diverges_from_baseline=False,
        notes=list(notes or []),
    )


def _linear_trend(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Ordinary least squares slope & intercept."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / denom
    return slope, my - slope * mx


def _seasonal_indices(
    pts: list[MonthPoint], slope: float, intercept: float, shrink_k: float = 0.0
) -> dict[int, float]:
    """Multiplicative seasonal index per calendar month = mean(actual/level),
    normalised so the indices average ~1 across the months present."""
    ratios: dict[int, list[float]] = {}
    for p in pts:
        level = slope * p.ord + intercept
        if level > 0:
            ratios.setdefault(p.month, []).append(p.units / level)
    idx = {}
    for m, v in ratios.items():
        if not v:
            continue
        raw = sum(v) / len(v)
        # SHRINK toward 1.0 by how much evidence there is. Seasonality switches
        # on at 24 months of history, which is exactly TWO Januaries — a raw
        # index is then the mean of two ratios, and one odd month becomes a
        # permanent seasonal claim. lambda = k_obs / (k_obs + shrink_k), so two
        # observations move half way to the raw index and ten move most of it.
        # Over a 12-month order an over-confident index compounds instead of
        # washing out, which is why this matters more than it used to.
        lam = len(v) / (len(v) + shrink_k) if shrink_k > 0 else 1.0
        idx[m] = 1.0 + lam * (raw - 1.0)
    if idx:
        avg = sum(idx.values()) / len(idx)
        if avg > 0:
            idx = {m: v / avg for m, v in idx.items()}
    return idx


def forecast_demand(
    series: MonthlySalesSeries,
    horizon: int,
    rules: ForecastRules,
    first_future: date | None = None,
) -> Forecast:
    """Per-month demand forecast across `horizon` months.

    `first_future` is the calendar month of forecast month #1 (used for
    seasonal indexing). Defaults to the month after the last data point.
    """
    pts = series.useable()
    n = len(pts)
    notes: list[str] = []

    n_stockout = len(series.points) - n
    if n_stockout:
        notes.append(f"{n_stockout} stock-out month(s) excluded from the fit")

    # Baseline = sell-through velocity: average over IN-STOCK months only, so
    # stock-out months don't deflate demand.
    baseline = (sum(p.units for p in pts) / n) if n else 0.0

    if first_future is None:
        if pts:
            y, m = pts[-1].year, pts[-1].month + 1
            if m > 12:
                y, m = y + 1, 1
        else:
            today = date.today()
            y, m = today.year, today.month
        first_future = date(y, m, 1)

    future_months: list[tuple[int, int]] = []
    fy, fm = first_future.year, first_future.month
    for _ in range(horizon):
        future_months.append((fy, fm))
        fm += 1
        if fm > 12:
            fy, fm = fy + 1, 1

    if n < rules.low_confidence_months:
        monthly = [baseline] * horizon
        method = METHOD_FLAT
        confidence = "low"
        low_data = True
        cv = 0.0
        sd = statistics.pstdev([p.units for p in pts]) if n > 1 else 0.0
        forward_level = baseline
        notes.append(f"only {n} month(s) of history -> flat average")
    else:
        xs = [float(p.ord) for p in pts]
        ys = [p.units for p in pts]
        slope, intercept = _linear_trend(xs, ys)

        mean = statistics.mean(ys)
        sd = statistics.pstdev(ys) if n > 1 else 0.0
        cv = (sd / mean) if mean else 0.0

        if n >= rules.min_months_for_seasonal:
            seasonal = _seasonal_indices(pts, slope, intercept, rules.seasonal_shrink_k)
            method = METHOD_SEASONAL
            monthly = []
            for yy, mm in future_months:
                level = slope * (yy * 12 + mm - 1) + intercept
                monthly.append(max(0.0, level * seasonal.get(mm, 1.0)))
            confidence = "high" if cv < 0.5 else "medium"
            # the trend line at the MIDDLE of the horizon, seasonality removed:
            # indices normalise to ~1 across a full cycle, so the level is the
            # rate that prices a year of cover, not the seasonal mean of
            # whichever months the horizon happens to cover
            mid = future_months[len(future_months) // 2] if future_months else None
            forward_level = (
                max(0.0, slope * (mid[0] * 12 + mid[1] - 1) + intercept) if mid else baseline
            )
        else:
            window = pts[-min(6, n):]
            level0 = sum(p.units for p in window) / len(window)
            method = METHOD_TREND
            last_ord = pts[-1].ord
            monthly = [
                max(0.0, level0 + slope * ((yy * 12 + mm - 1) - last_ord))
                for yy, mm in future_months
            ]
            confidence = "medium" if n >= rules.min_months_for_trend else "low"
            forward_level = (
                sum(monthly) / len(monthly) if monthly else baseline
            )  # no seasonality in this method: the mean IS the level
        low_data = n < rules.min_months_for_trend

    # Uncertainty band: grows with CV, shrinks with history.
    uncertainty = min(0.9, (cv if n else 1.0) / math.sqrt(max(n, 1)) + 0.05)

    fmean = sum(monthly) / len(monthly) if monthly else 0.0
    diverges = baseline > 0 and abs(fmean - baseline) / baseline >= rules.divergence_flag_pct
    if diverges:
        notes.append(
            f"forecast mean {fmean:.1f}/mo diverges "
            f"{(fmean - baseline) / baseline * 100:+.0f}% from flat baseline {baseline:.1f}/mo"
        )

    return Forecast(
        monthly=[round(v, 4) for v in monthly],
        forward_level=round(forward_level, 4),
        demand_sd=round(sd, 4),
        method=method,
        baseline=round(baseline, 4),
        confidence=confidence,
        n_history_months=n,
        low_data=low_data,
        uncertainty_pct=round(uncertainty, 3),
        diverges_from_baseline=diverges,
        notes=notes,
    )
