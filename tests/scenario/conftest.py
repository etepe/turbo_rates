"""Phase 3 scenario test fixtures.

Phase-isolated: hand-built MarketData and MPCPath instances, no dependency on
the bootstrap module or its golden fixture.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import MarketData, MPCMeeting, MPCPath

_VAL_DATE = date(2026, 6, 12)


@pytest.fixture(scope="session")
def val_date() -> date:
    return _VAL_DATE


@pytest.fixture(scope="session")
def tr_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "TR",
        years=range(_VAL_DATE.year - 1, _VAL_DATE.year + 12),
        override_dir=Path("config/holidays"),
    )


@pytest.fixture(scope="session")
def conventions(conventions_yaml: Path) -> Conventions:
    return Conventions.load(conventions_yaml)


@pytest.fixture()
def diagnostics() -> DiagnosticsCollector:
    return DiagnosticsCollector()


@pytest.fixture()
def market_with_tlref(val_date) -> MarketData:
    """Empty quote list but populated special_rates with BISTTREF = 45%."""
    return MarketData(
        quotes=(),
        special_rates={"BISTTREF": 0.4500},
        valuation_date=val_date,
        source="synthetic",
    )


@pytest.fixture()
def market_no_tlref(val_date) -> MarketData:
    """MarketData missing BISTTREF; bootstrap-style ERROR fixture."""
    return MarketData(
        quotes=(),
        special_rates={},
        valuation_date=val_date,
        source="synthetic",
    )


@pytest.fixture()
def mpc_path_three_cuts(val_date) -> MPCPath:
    """Three rate cuts spread across ~1 year — exercises stepping behaviour."""
    meetings = (
        MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=-250, rationale="cut1"),
        MPCMeeting(meeting_date=date(2026, 12, 18), bps_change=-250, rationale="cut2"),
        MPCMeeting(meeting_date=date(2027, 3, 19), bps_change=-150, rationale="cut3"),
    )
    return MPCPath(meetings=meetings)


@pytest.fixture()
def mpc_path_single_cut() -> MPCPath:
    """One meeting at day ~63 (around 2026-09-11) — clean step test fixture."""
    meetings = (MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=-500, rationale="big cut"),)
    return MPCPath(meetings=meetings)


@pytest.fixture()
def mpc_path_empty() -> MPCPath:
    """No meetings — rate stays flat at the initial level."""
    return MPCPath(meetings=())
