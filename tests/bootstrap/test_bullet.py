"""Bullet bootstrap (<=1Y pillars) — closed-form 1/(1 + r*tau) round-trips to 1e-12.

F-001 acceptance: reprice each pillar to within 1e-10 absolute. For pure bullets the
arithmetic is exact to machine epsilon; we assert the tighter 1e-12.
"""

from __future__ import annotations

import pytest

from rates.core.bootstrap import REPRICE_TOLERANCE, bootstrap_curve
from rates.core.daycount import year_fraction


@pytest.mark.phase2
def test_single_pillar_closed_form(single_bullet_market, conventions, tr_calendar, diagnostics):
    """One 1Y pillar @ 45% bootstraps to DF = 1/(1+0.45*tau) exactly."""
    curve = bootstrap_curve(single_bullet_market, conventions, tr_calendar, diagnostics)

    assert len(curve.pillars_tuple) == 1
    p = curve.pillars_tuple[0]
    tau = year_fraction(curve.valuation_date, p.end_date, curve.day_count)
    expected = 1.0 / (1.0 + 0.45 * tau)
    assert p.discount_factor == pytest.approx(expected, abs=1e-15)
    # Sanity: anchor identity.
    assert curve.df_at(curve.valuation_date) == 1.0
    # No errors emitted.
    assert not diagnostics.has_errors()


@pytest.mark.phase2
def test_short_curve_reprice_tolerance(short_curve_market, conventions, tr_calendar, diagnostics):
    """1M/3M/6M/9M/1Y curve: each pillar reprices within 1e-12."""
    curve = bootstrap_curve(short_curve_market, conventions, tr_calendar, diagnostics)

    assert len(curve.pillars_tuple) == 5
    for p in curve.pillars_tuple:
        tau = year_fraction(curve.valuation_date, p.end_date, curve.day_count)
        # Bullet reprice residual: p.rate*tau*DF - (1-DF)
        residual = abs(p.rate * tau * p.discount_factor - (1.0 - p.discount_factor))
        assert residual < REPRICE_TOLERANCE
        assert residual < 1e-12  # Tighter check for bullet pillars.
    assert not diagnostics.has_errors()


@pytest.mark.phase2
def test_short_curve_pillar_exact_at_date(
    short_curve_market, conventions, tr_calendar, diagnostics
):
    """df_at(p.end_date) returns the bootstrapped DF exactly (F-002 invariant)."""
    curve = bootstrap_curve(short_curve_market, conventions, tr_calendar, diagnostics)
    for p in curve.pillars_tuple:
        assert curve.df_at(p.end_date) == p.discount_factor
