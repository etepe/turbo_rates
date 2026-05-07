"""Tests for rates.core.daycount (M-003).

Acceptance: rate -> DF -> rate round-trip is reproducible to 1e-12 absolute under the
declared day-count basis (per F-011 NFR).
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from rates.core.daycount import year_fraction
from rates.core.types import DayCount


@pytest.mark.phase1
def test_act_360_full_year_returns_365_over_360() -> None:
    """year_fraction over a 365-day calendar year under Act/360 is 365/360."""
    yf = year_fraction(date(2025, 1, 1), date(2026, 1, 1), DayCount.ACT_360)
    assert yf == 365.0 / 360.0


@pytest.mark.phase1
def test_act_365_full_year_returns_one() -> None:
    """year_fraction over a 365-day calendar year under Act/365 is exactly 1.0."""
    yf = year_fraction(date(2025, 1, 1), date(2026, 1, 1), DayCount.ACT_365)
    assert yf == 1.0


@pytest.mark.phase1
def test_zero_distance_returns_zero() -> None:
    """year_fraction(d, d, basis) == 0.0 for every basis."""
    d = date(2026, 5, 7)
    for basis in DayCount:
        assert year_fraction(d, d, basis) == 0.0


@pytest.mark.phase1
def test_negative_distance_returns_negative_fraction() -> None:
    """year_fraction(later, earlier, basis) is negative."""
    earlier = date(2025, 1, 1)
    later = date(2026, 1, 1)
    for basis in DayCount:
        forward = year_fraction(earlier, later, basis)
        backward = year_fraction(later, earlier, basis)
        assert forward > 0
        assert backward < 0
        assert forward == pytest.approx(-backward, abs=1e-15)


@pytest.mark.phase1
def test_round_trip_rate_to_df_to_rate_is_within_1e_12() -> None:
    """abs(r - decompound(compound(r))) < 1e-12 under any supported basis."""
    d1 = date(2025, 3, 15)
    d2 = date(2027, 9, 30)
    sample_rates = [-0.05, -0.001, 0.0, 0.0125, 0.05, 0.25, 0.50, 1.0]
    for basis in DayCount:
        tau = year_fraction(d1, d2, basis)
        for r in sample_rates:
            df = math.exp(-r * tau)
            r_back = -math.log(df) / tau
            assert abs(r - r_back) < 1e-12
