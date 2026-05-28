"""Phase 6 — OISCurve invariants (Hypothesis).

Three curve-level invariants exercised over random pillar rate vectors:

* anchor identity: ``df_at(valuation_date) == 1.0``.
* forward consistency: ``forward(s, e) == (df_s/df_e - 1)/yf(s, e)``.
* log-linear DF monotonicity: between two positive-rate pillars, interpolated
  DFs strictly decrease with maturity.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from rates.core.bootstrap import bootstrap_curve
from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.daycount import year_fraction
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import DayCount, MarketData, MarketQuote

_PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _build_curve(
    rates: list[float], val_date: date, cal: HolidayCalendar, conv: Conventions
):
    """5-pillar short-end bullet curve from a rate vector. No brentq, no diagnostics."""
    specs = (("1W", 7), ("1M", 30), ("3M", 90), ("6M", 180), ("1Y", 365))
    quotes = []
    for (code, days), r in zip(specs, rates, strict=True):
        end = val_date + timedelta(days=days)
        while not cal.is_business_day(end):
            end = end + timedelta(days=1)
        quotes.append(
            MarketQuote(
                tenor_code=code,
                tenor_days=(end - val_date).days,
                start_date=val_date,
                end_date=end,
                bid=None,
                ask=None,
                mid=r,
                source="hypothesis",
            )
        )
    market = MarketData(quotes=tuple(quotes), valuation_date=val_date, source="hyp")
    return bootstrap_curve(market, conv, cal, DiagnosticsCollector())


@pytest.mark.phase6
@given(
    rates=st.lists(
        st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    )
)
@_PROPERTY_SETTINGS
def test_curve_anchor_identity(
    rates: list[float],
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """``df_at(valuation_date) == 1.0`` for any positive-rate bullet curve."""
    curve = _build_curve(rates, val_date, tr_calendar, conventions)
    assert curve.df_at(val_date) == 1.0


@pytest.mark.phase6
@given(
    rates=st.lists(
        st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    ),
    s_off=st.integers(min_value=5, max_value=60),
    e_off=st.integers(min_value=80, max_value=300),
)
@_PROPERTY_SETTINGS
def test_forward_equals_df_ratio(
    rates: list[float],
    s_off: int,
    e_off: int,
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """``forward(s, e) == (df_s/df_e - 1) / yf(s, e, day_count)`` (definition, F-003)."""
    curve = _build_curve(rates, val_date, tr_calendar, conventions)
    s = val_date + timedelta(days=s_off)
    e = val_date + timedelta(days=e_off)
    assert s < e
    last = curve.pillars_tuple[-1].end_date
    if e > last:
        return  # respect DateOutOfRange; sampled e past the 1Y pillar is fine to skip
    df_s = curve.df_at(s)
    df_e = curve.df_at(e)
    yf = year_fraction(s, e, DayCount.ACT_360)
    expected = (df_s / df_e - 1.0) / yf
    actual = curve.forward(s, e)
    assert abs(actual - expected) <= 1e-15


@pytest.mark.phase6
@given(
    rates=st.lists(
        st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=5,
    )
)
@_PROPERTY_SETTINGS
def test_log_linear_df_monotone_decreasing(
    rates: list[float],
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """Log-linear DF interpolation preserves pillar DF monotonicity.

    Precondition: bootstrapped pillar DFs themselves are monotonically decreasing.
    (Without this, the input curve has a non-monotone segment — Hypothesis will
    occasionally generate inverted regimes where the BS_NON_MONOTONE_DF WARN
    would fire and the property is vacuously inapplicable.)

    Under the precondition: walk weekly between val_date and the last pillar;
    assert ``df_at(t_i) >= df_at(t_{i+1})``.
    """
    curve = _build_curve(rates, val_date, tr_calendar, conventions)
    pillar_dfs = [p.discount_factor for p in curve.pillars_tuple]
    assume(all(pillar_dfs[i] > pillar_dfs[i + 1] for i in range(len(pillar_dfs) - 1)))

    last = curve.pillars_tuple[-1].end_date
    prev_df = 1.0
    cur = val_date + timedelta(days=1)
    while cur <= last:
        df = curve.df_at(cur)
        assert df <= prev_df, f"non-monotone DF at {cur}: {prev_df} -> {df}"
        prev_df = df
        cur = cur + timedelta(days=7)  # weekly sample; daily would be ~365 ops/example
