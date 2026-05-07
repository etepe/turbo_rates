"""Tests for rates.core.types (M-016).

Frozen dataclasses are mostly construction tests — verify immutability, slots, and that
default factories behave correctly. No NotImplementedError stubs to test here; these
tests should run *as-is* once implementation is in place since types are pure data.
"""

from __future__ import annotations

import pytest


@pytest.mark.phase1
def test_market_quote_is_frozen() -> None:
    """MarketQuote should be a frozen dataclass: assigning to a field raises."""
    pytest.skip("not yet implemented — assert FrozenInstanceError on attribute assign")


@pytest.mark.phase1
def test_market_data_default_special_rates_is_empty() -> None:
    """MarketData() with no special_rates argument should yield an empty dict."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_mpc_path_meetings_is_immutable_tuple() -> None:
    """MPCPath.meetings should be a tuple, not a list (immutability)."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_day_count_enum_str_values() -> None:
    """DayCount enum members should round-trip via their string values."""
    pytest.skip("not yet implemented — DayCount('Act/360') == DayCount.ACT_360")


@pytest.mark.phase1
def test_business_day_convention_enum_has_modified_following() -> None:
    """BusinessDayConvention should include MODIFIED_FOLLOWING (default for TRY)."""
    pytest.skip("not yet implemented")
