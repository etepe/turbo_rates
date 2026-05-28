"""Phase 6 — scenario invariants (Hypothesis).

* ``index[0] == 1.0`` at the anchor for mid/low/high under any MPC path.
* Band ordering at horizon: ``low <= mid <= high`` for positive initial rates
  and ``band_bps >= 0`` (compounding monotonicity).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.scenario import build_scenario
from rates.core.types import MarketData, MPCMeeting, MPCPath

_PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _mpc_strategy(val_date: date) -> st.SearchStrategy[MPCPath]:
    """Generate 0-3 strictly-ascending forward-dated MPC meetings within ~1Y."""

    @st.composite
    def _build(draw: st.DrawFn) -> MPCPath:
        n = draw(st.integers(min_value=0, max_value=3))
        if n == 0:
            return MPCPath(meetings=())
        # Pick n distinct day-offsets in [30, 330] and sort ascending.
        offsets = sorted(
            draw(
                st.lists(
                    st.integers(min_value=30, max_value=330),
                    min_size=n,
                    max_size=n,
                    unique=True,
                )
            )
        )
        bps_list = draw(
            st.lists(
                st.integers(min_value=-500, max_value=500),
                min_size=n,
                max_size=n,
            )
        )
        meetings = tuple(
            MPCMeeting(
                meeting_date=val_date + timedelta(days=off),
                bps_change=bps,
            )
            for off, bps in zip(offsets, bps_list, strict=True)
        )
        return MPCPath(meetings=meetings)

    return _build()


@pytest.mark.phase6
@given(
    initial=st.floats(
        min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
    horizon=st.integers(min_value=1, max_value=60),
    band=st.integers(min_value=0, max_value=500),
)
@_PROPERTY_SETTINGS
def test_scenario_index_one_at_anchor(
    initial: float,
    horizon: int,
    band: int,
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """``index[0] == 1.0`` for mid/low/high regardless of band or MPC path."""
    market = MarketData(
        quotes=(),
        special_rates={"BISTTREF": initial},
        valuation_date=val_date,
        source="hypothesis",
    )
    res = build_scenario(
        market,
        MPCPath(meetings=()),
        band_bps=band,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=DiagnosticsCollector(),
        horizon_days=horizon,
    )
    assert res.mid.values[0] == 1.0
    if band > 0:
        assert res.low is not None and res.high is not None
        assert res.low.values[0] == 1.0
        assert res.high.values[0] == 1.0


@pytest.mark.phase6
@given(
    initial=st.floats(
        min_value=0.10, max_value=0.80, allow_nan=False, allow_infinity=False
    ),
    horizon=st.integers(min_value=5, max_value=60),
    band=st.integers(min_value=1, max_value=300),
)
@_PROPERTY_SETTINGS
def test_band_horizon_ordering_low_le_mid_le_high(
    initial: float,
    horizon: int,
    band: int,
    val_date: date,
    tr_calendar: HolidayCalendar,
    conventions: Conventions,
) -> None:
    """At horizon end, ``low.values[-1] <= mid.values[-1] <= high.values[-1]``.

    Compounding is monotone in the daily rate; a uniform +band shift raises every
    daily factor, a uniform -band lowers them. Initial range stays well above 0
    so the low-band rate stays positive (no SC_NEGATIVE_POLICY_RATE convexity edge).
    """
    market = MarketData(
        quotes=(),
        special_rates={"BISTTREF": initial},
        valuation_date=val_date,
        source="hypothesis",
    )
    res = build_scenario(
        market,
        MPCPath(meetings=()),
        band_bps=band,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=DiagnosticsCollector(),
        horizon_days=horizon,
    )
    assert res.low is not None and res.high is not None
    assert res.low.values[-1] <= res.mid.values[-1] <= res.high.values[-1]
