"""Tests for rates.core.calendar (M-002).

CSV-override-wins-on-conflict and is_business_day basics. Per F-010, fidelity check
against the Excel ``tatiller`` reference list is a separate one-off script under
``scripts/``, not a unit test.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar


@pytest.mark.phase1
def test_for_currency_returns_calendar_with_correct_ccy_attr() -> None:
    """HolidayCalendar.for_currency('TR').currency == 'TR'."""
    cal = HolidayCalendar.for_currency("TR", years=range(2025, 2027))
    assert cal.currency == "TR"


@pytest.mark.phase1
def test_known_turkish_public_holiday_is_holiday() -> None:
    """For example 2026-04-23 (National Sovereignty Day) is_holiday() -> True."""
    cal = HolidayCalendar.for_currency("TR", years=range(2025, 2027))
    assert cal.is_holiday(date(2026, 4, 23)) is True


@pytest.mark.phase1
def test_csv_override_wins_on_conflict(tmp_path: Path) -> None:
    """A CSV row not in the holidays package becomes a holiday after merge."""
    csv = tmp_path / "tr.csv"
    csv.write_text("date,description\n2026-07-15,Synthetic test holiday\n", encoding="utf-8")
    cal = HolidayCalendar.for_currency("TR", years=range(2026, 2027), override_dir=tmp_path)
    assert cal.is_holiday(date(2026, 7, 15)) is True


@pytest.mark.phase1
def test_csv_override_with_no_rows_is_valid(tmp_path: Path) -> None:
    """An empty CSV (header only) means 'no overrides'; package output unchanged."""
    csv = tmp_path / "tr.csv"
    csv.write_text("date,description\n", encoding="utf-8")
    cal = HolidayCalendar.for_currency("TR", years=range(2026, 2027), override_dir=tmp_path)
    assert cal.is_holiday(date(2026, 4, 23)) is True
    assert cal.is_holiday(date(2026, 6, 15)) is False


@pytest.mark.phase1
def test_is_business_day_excludes_weekends() -> None:
    """A Saturday is_business_day() == False even if not in the holiday set."""
    cal = HolidayCalendar.for_currency("TR", years=range(2026, 2027))
    saturday = date(2026, 5, 9)  # Saturday
    assert saturday.weekday() == 5
    assert cal.is_holiday(saturday) is False
    assert cal.is_business_day(saturday) is False


@pytest.mark.phase1
def test_add_business_days_skips_weekend_and_holiday() -> None:
    """add_business_days(friday_eve_of_holiday, 1) should jump over the holiday."""
    cal = HolidayCalendar.for_currency("TR", years=range(2026, 2027))
    # 2026-04-22 is a Wednesday; 2026-04-23 (Thu) is National Sovereignty Day.
    # +1 business day from Wed should land on Friday 2026-04-24.
    wed = date(2026, 4, 22)
    assert cal.is_business_day(wed) is True
    assert cal.add_business_days(wed, 1) == date(2026, 4, 24)


@pytest.mark.phase1
def test_add_business_days_supports_negative_n() -> None:
    """Walking backwards is symmetric: add_business_days(d, -n) reverses add_business_days(d, n)."""
    cal = HolidayCalendar.for_currency("TR", years=range(2026, 2027))
    start = date(2026, 6, 1)  # Monday
    assert cal.is_business_day(start) is True
    forward = cal.add_business_days(start, 7)
    back = cal.add_business_days(forward, -7)
    assert back == start
