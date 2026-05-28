"""Annual-coupon bootstrap (>1Y pillars).

Two cases:
  1. Aligned yearly grid -> closed-form path. Every pillar reprices within 1e-12.
  2. Irregular grid (missing 4Y / 6Y) -> brentq fallback for 5Y and 7Y. Every pillar
     still reprices within REPRICE_TOLERANCE (1e-10).
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from rates.core.bootstrap import (
    REPRICE_TOLERANCE,
    _annual_coupon_schedule,
    bootstrap_curve,
)
from rates.core.daycount import year_fraction
from rates.core.types import BusinessDayConvention


def _reprice_residual_annual(curve, pillar, calendar):
    """Recompute swap residual using full coupon schedule."""
    val = curve.valuation_date
    schedule = _annual_coupon_schedule(
        val, pillar.end_date, calendar, BusinessDayConvention.MODIFIED_FOLLOWING
    )
    s = 0.0
    prev = val
    for c in schedule:
        tau = year_fraction(prev, c, curve.day_count)
        s += curve.df_at(c) * tau
        prev = c
    fixed_pv = pillar.rate * s
    float_pv = 1.0 - pillar.discount_factor
    return abs(fixed_pv - float_pv)


@pytest.mark.phase2
def test_aligned_annual_reprice(aligned_annual_market, conventions, tr_calendar, diagnostics):
    """All-yearly pillar set: every pillar reprices within 1e-12 (closed-form)."""
    curve = bootstrap_curve(aligned_annual_market, conventions, tr_calendar, diagnostics)

    assert len(curve.pillars_tuple) == 13
    for p in curve.pillars_tuple:
        if p.tenor_days <= 366:
            tau = year_fraction(curve.valuation_date, p.end_date, curve.day_count)
            residual = abs(p.rate * tau * p.discount_factor - (1.0 - p.discount_factor))
        else:
            residual = _reprice_residual_annual(curve, p, tr_calendar)
        assert residual < 1e-12, f"{p.tenor_code} residual {residual}"
    assert not diagnostics.has_errors()


@pytest.mark.phase2
def test_aligned_annual_pillar_exactness(
    aligned_annual_market, conventions, tr_calendar, diagnostics
):
    """df_at(p.end_date) == p.discount_factor for every pillar (both interp modes)."""
    for scheme in ("log_linear_df", "linear_zero"):
        curve = bootstrap_curve(
            aligned_annual_market,
            conventions,
            tr_calendar,
            diagnostics,
            interp=scheme,
        )
        for p in curve.pillars_tuple:
            assert curve.df_at(p.end_date) == p.discount_factor, (
                f"{scheme}: {p.tenor_code} df_at != bootstrap DF"
            )


@pytest.mark.phase2
def test_irregular_grid_brentq(irregular_market, conventions, tr_calendar, diagnostics):
    """Missing 4Y/6Y forces brentq for 5Y and 7Y; reprice still within 1e-10."""
    curve = bootstrap_curve(irregular_market, conventions, tr_calendar, diagnostics)

    assert len(curve.pillars_tuple) == 7
    for p in curve.pillars_tuple:
        if p.tenor_days <= 366:
            tau = year_fraction(curve.valuation_date, p.end_date, curve.day_count)
            residual = abs(p.rate * tau * p.discount_factor - (1.0 - p.discount_factor))
        else:
            residual = _reprice_residual_annual(curve, p, tr_calendar)
        assert residual < REPRICE_TOLERANCE, f"{p.tenor_code} residual {residual}"
    assert not diagnostics.has_errors()


@pytest.mark.phase2
def test_dfs_strictly_decreasing(aligned_annual_market, conventions, tr_calendar, diagnostics):
    """Positive-rate curve produces strictly decreasing DFs across pillars."""
    curve = bootstrap_curve(aligned_annual_market, conventions, tr_calendar, diagnostics)
    dfs = [p.discount_factor for p in curve.pillars_tuple]
    for a, b in pairwise(dfs):
        assert b < a, f"DFs not strictly decreasing: {dfs}"
