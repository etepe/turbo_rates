"""Tests for rates.core.calendar (M-002).

CSV-override-wins-on-conflict and is_business_day basics. Per F-010, fidelity check
against the Excel ``tatiller`` reference list is a separate one-off script under
``scripts/``, not a unit test.
"""

from __future__ import annotations

import pytest


@pytest.mark.phase1
def test_for_currency_returns_calendar_with_correct_ccy_attr() -> None:
    """HolidayCalendar.for_currency('TR').currency == 'TR'."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_known_turkish_public_holiday_is_holiday() -> None:
    """For example 2026-04-23 (National Sovereignty Day) is_holiday() -> True."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_csv_override_wins_on_conflict(tmp_path: object) -> None:
    """A CSV row not in the holidays package becomes a holiday after merge."""
    pytest.skip("not yet implemented — write a tmp_path/tr.csv with a synthetic date")


@pytest.mark.phase1
def test_csv_override_with_no_rows_is_valid() -> None:
    """An empty CSV (header only) means 'no overrides'; package output unchanged."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_is_business_day_excludes_weekends() -> None:
    """A Saturday is_business_day() == False even if not in the holiday set."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_add_business_days_skips_weekend_and_holiday() -> None:
    """add_business_days(friday_eve_of_holiday, 1) should jump over the holiday."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_add_business_days_supports_negative_n() -> None:
    """Walking backwards is symmetric: add_business_days(d, -n) reverses add_business_days(d, n)."""
    pytest.skip("not yet implemented")
