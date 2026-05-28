"""Phase 6 property-test fixtures.

Re-exports the calendar / conventions / valuation-date triple used by the
strategies in :mod:`test_curve_properties`, :mod:`test_bootstrap_properties`,
and :mod:`test_scenario_properties`. Kept session-scoped so Hypothesis does
not rebuild a Turkey-holidays object per example.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions

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
