"""Phase 6 — bootstrap reprice invariant under varying pillar rates (Hypothesis).

We sample rate vectors over fixed tenor geometry and assert that every pillar
reprices to 0 within ``REPRICE_TOLERANCE``. Two geometries are exercised:

* **Bullet chain** (5 short tenors, all ≤ 1Y) — closed-form, well-conditioned.
* **Annual chain** (5 yearly tenors, 1Y..5Y) — closed-form alignment because
  every Ny pillar's intermediate coupons (1y..(N-1)y) match prior pillars.

Both geometries avoid the brentq fallback, so the property holds independently
of solver tuning.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rates.core.bootstrap import REPRICE_TOLERANCE, bootstrap_curve
from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import MarketData, MarketQuote

_PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _bullet_quote(
    val: date, tenor_code: str, days: int, rate: float, cal: HolidayCalendar
) -> MarketQuote:
    end = val + timedelta(days=days)
    while not cal.is_business_day(end):
        end = end + timedelta(days=1)
    return MarketQuote(
        tenor_code=tenor_code,
        tenor_days=(end - val).days,
        start_date=val,
        end_date=end,
        bid=None,
        ask=None,
        mid=rate,
        source="hypothesis",
    )


def _annual_quote(
    val: date, years: int, rate: float, cal: HolidayCalendar
) -> MarketQuote:
    end = date(val.year + years, val.month, val.day)
    while not cal.is_business_day(end):
        end = end + timedelta(days=1)
    return MarketQuote(
        tenor_code=f"TYSO{years}Y",
        tenor_days=(end - val).days,
        start_date=val,
        end_date=end,
        bid=None,
        ask=None,
        mid=rate,
        source="hypothesis",
    )


@pytest.mark.phase6
@given(
    rates=st.lists(
        st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    )
)
@_PROPERTY_SETTINGS
def test_bullet_chain_reprice_holds_for_any_rate_vector(
    rates: list[float],
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """Five short bullet pillars reprice to 1e-10 for any rate vector in (5%, 100%)."""
    specs = (("1W", 7), ("1M", 30), ("3M", 90), ("6M", 180), ("9M", 270))
    quotes = tuple(
        _bullet_quote(val_date, code, days, r, tr_calendar)
        for (code, days), r in zip(specs, rates, strict=True)
    )
    market = MarketData(quotes=quotes, valuation_date=val_date, source="hypothesis")
    dg = DiagnosticsCollector()
    curve = bootstrap_curve(market, conventions, tr_calendar, dg)
    # Every pillar reprices on its own DF — bullet form is closed-form and exact.
    for p in curve.pillars_tuple:
        tau = (p.end_date - val_date).days / 360.0  # TRY = Act/360
        fixed = p.rate * tau * p.discount_factor
        floating = 1.0 - p.discount_factor
        assert abs(fixed - floating) <= REPRICE_TOLERANCE


@pytest.mark.phase6
@given(
    rates=st.lists(
        st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    )
)
@_PROPERTY_SETTINGS
def test_annual_chain_reprice_holds_for_any_rate_vector(
    rates: list[float],
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """1Y..5Y annual pillars reprice to 1e-10 (closed-form alignment by construction)."""
    quotes = tuple(
        _annual_quote(val_date, years, r, tr_calendar)
        for years, r in zip(range(1, 6), rates, strict=True)
    )
    market = MarketData(quotes=quotes, valuation_date=val_date, source="hypothesis")
    dg = DiagnosticsCollector()
    curve = bootstrap_curve(market, conventions, tr_calendar, dg)

    # Errors come from BS_REPRICE_FAIL diagnostics; no error means every pillar passed
    # the 1e-10 acceptance enforced inside bootstrap_curve.
    assert not dg.has_errors()
    assert len(curve.pillars_tuple) == 5
