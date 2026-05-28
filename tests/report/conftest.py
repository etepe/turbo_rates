"""Phase 3 report test fixtures.

Hand-built OISCurve + real ScenarioResult (phase isolation: report tests do not
exercise bootstrap_curve — a regression there must not silently fail report
tests).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.curve import OISCurve, Pillar
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.scenario import build_scenario
from rates.core.types import DayCount, MarketData, MPCPath

_VAL = date(2026, 6, 12)


@pytest.fixture(scope="session")
def val_date() -> date:
    return _VAL


@pytest.fixture(scope="session")
def tr_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "TR",
        years=range(_VAL.year - 1, _VAL.year + 12),
        override_dir=Path("config/holidays"),
    )


@pytest.fixture(scope="session")
def conventions(conventions_yaml: Path) -> Conventions:
    return Conventions.load(conventions_yaml)


@pytest.fixture()
def hand_curve(val_date, tr_calendar) -> OISCurve:
    """Three-pillar hand-built curve with arithmetically clean DFs and a 1x3 ladder."""
    pillars = (
        Pillar("M-3M", 92, val_date, date(2026, 9, 14), 0.45, 0.8955),
        Pillar("M-6M", 185, val_date, date(2026, 12, 14), 0.43, 0.8198),
        Pillar("M-1Y", 367, val_date, date(2027, 6, 14), 0.42, 0.7000),
    )
    # Forward ladder limited to two entries that land on BD-grid dates within the
    # short scenario horizon used by these tests.
    ladder = (
        ("1x2", date(2026, 7, 13), date(2026, 8, 12)),
        ("1x3", date(2026, 7, 13), date(2026, 9, 14)),
    )
    return OISCurve(
        valuation_date=val_date,
        day_count=DayCount.ACT_360,
        interp="log_linear_df",
        pillars_tuple=pillars,
        forward_ladder_dates=ladder,
    )


@pytest.fixture()
def scenario_short(val_date, tr_calendar, conventions):
    """Mid-only scenario covering 280 BDs (~1.1y) so all hand_curve dates fit."""
    market = MarketData(
        quotes=(),
        special_rates={"BISTTREF": 0.45},
        valuation_date=val_date,
    )
    return build_scenario(
        market,
        MPCPath(meetings=()),
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=DiagnosticsCollector(),
        horizon_days=280,
    )


@pytest.fixture()
def scenario_truncated(val_date, tr_calendar, conventions):
    """Scenario with horizon=30 BDs so most hand_curve maturities fall out of range."""
    market = MarketData(
        quotes=(),
        special_rates={"BISTTREF": 0.45},
        valuation_date=val_date,
    )
    return build_scenario(
        market,
        MPCPath(meetings=()),
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=DiagnosticsCollector(),
        horizon_days=30,
    )
