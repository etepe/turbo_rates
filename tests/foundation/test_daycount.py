"""Tests for rates.core.daycount (M-003).

Acceptance: rate -> DF -> rate round-trip is reproducible to 1e-12 absolute under the
declared day-count basis (per F-011 NFR).
"""

from __future__ import annotations

import pytest


@pytest.mark.phase1
def test_act_360_full_year_returns_365_over_360() -> None:
    """year_fraction over a 365-day calendar year under Act/360 is 365/360."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_act_365_full_year_returns_one() -> None:
    """year_fraction over a 365-day calendar year under Act/365 is exactly 1.0."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_zero_distance_returns_zero() -> None:
    """year_fraction(d, d, basis) == 0.0 for every basis."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_negative_distance_returns_negative_fraction() -> None:
    """year_fraction(later, earlier, basis) is negative."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_round_trip_rate_to_df_to_rate_is_within_1e_12() -> None:
    """abs(r - decompound(compound(r))) < 1e-12 under any supported basis."""
    pytest.skip("not yet implemented — sample r in [-0.05, 1.0]")
