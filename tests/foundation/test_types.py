"""Tests for rates.core.types (M-016).

Frozen dataclasses are mostly construction tests — verify immutability, slots, and that
default factories behave correctly. No NotImplementedError stubs to test here; these
tests should run *as-is* once implementation is in place since types are pure data.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from rates.core.types import (
    BusinessDayConvention,
    DayCount,
    MarketData,
    MarketQuote,
    MPCMeeting,
    MPCPath,
)


@pytest.mark.phase1
def test_market_quote_is_frozen() -> None:
    """MarketQuote should be a frozen dataclass: assigning to a field raises."""
    quote = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=365,
        start_date=date(2026, 5, 7),
        end_date=date(2027, 5, 7),
        bid=0.40,
        ask=0.41,
        mid=0.405,
        source="TYSO",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        quote.mid = 0.50  # type: ignore[misc]


@pytest.mark.phase1
def test_market_data_default_special_rates_is_empty() -> None:
    """MarketData() with no special_rates argument should yield an empty dict."""
    md = MarketData(quotes=())
    assert md.special_rates == {}
    assert md.valuation_date is None
    assert md.source == ""


@pytest.mark.phase1
def test_mpc_path_meetings_is_immutable_tuple() -> None:
    """MPCPath.meetings should be a tuple, not a list (immutability)."""
    meeting = MPCMeeting(meeting_date=date(2026, 6, 12), bps_change=-250)
    path = MPCPath(meetings=(meeting,))
    assert isinstance(path.meetings, tuple)


@pytest.mark.phase1
def test_day_count_enum_str_values() -> None:
    """DayCount enum members should round-trip via their string values."""
    assert DayCount("Act/360") is DayCount.ACT_360
    assert DayCount("Act/365") is DayCount.ACT_365
    assert DayCount("30/360") is DayCount.THIRTY_360


@pytest.mark.phase1
def test_business_day_convention_enum_has_modified_following() -> None:
    """BusinessDayConvention should include MODIFIED_FOLLOWING (default for TRY)."""
    assert BusinessDayConvention("modified_following") is BusinessDayConvention.MODIFIED_FOLLOWING
