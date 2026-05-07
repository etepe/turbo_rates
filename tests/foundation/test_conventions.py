"""Tests for rates.core.conventions (M-001)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rates.core.conventions import Conventions
from rates.core.types import BusinessDayConvention, DayCount


@pytest.mark.phase1
def test_load_default_conventions_yaml_succeeds(conventions_yaml: Path) -> None:
    """The committed config/conventions.yaml loads without error."""
    conv = Conventions.load(conventions_yaml)
    assert "TRY" in conv.ois_conventions


@pytest.mark.phase1
def test_loaded_try_currency_has_act_360_day_count(conventions_yaml: Path) -> None:
    """Default TRY OIS day-count is Act/360 (per F-011)."""
    conv = Conventions.load(conventions_yaml)
    assert conv.ois_conventions["TRY"].day_count is DayCount.ACT_360


@pytest.mark.phase1
def test_loaded_try_currency_has_modified_following_bdc(conventions_yaml: Path) -> None:
    """Default TRY business_day_convention is modified_following (per O1)."""
    conv = Conventions.load(conventions_yaml)
    assert (
        conv.ois_conventions["TRY"].business_day_convention
        is BusinessDayConvention.MODIFIED_FOLLOWING
    )


@pytest.mark.phase1
def test_loaded_tlref_basis_is_act_365(conventions_yaml: Path) -> None:
    """TLREF day-count is Act/365 (per F-011)."""
    conv = Conventions.load(conventions_yaml)
    assert conv.tlref_convention.day_count is DayCount.ACT_365


@pytest.mark.phase1
def test_loaded_spread_reporting_is_act_365(conventions_yaml: Path) -> None:
    """Spread reporting day-count is Act/365 (per F-012, common basis)."""
    conv = Conventions.load(conventions_yaml)
    assert conv.spread_reporting.day_count is DayCount.ACT_365


@pytest.mark.phase1
def test_dotted_override_replaces_leaf(conventions_yaml: Path) -> None:
    """Conventions.load(path, {'ois.TRY.day_count': 'Act/365'}) overrides the leaf."""
    conv = Conventions.load(conventions_yaml, {"ois.TRY.day_count": "Act/365"})
    assert conv.ois_conventions["TRY"].day_count is DayCount.ACT_365


@pytest.mark.phase1
def test_missing_yaml_raises_file_not_found_error() -> None:
    """Conventions.load(non-existent path) raises FileNotFoundError clearly."""
    with pytest.raises(FileNotFoundError):
        Conventions.load(Path("/no/such/conventions.yaml"))


@pytest.mark.phase1
def test_unknown_day_count_value_raises_value_error(tmp_path: Path) -> None:
    """A YAML with day_count='Bogus/000' must raise ValueError, not silently default."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "ois_conventions:\n"
        "  TRY:\n"
        "    day_count: Bogus/000\n"
        "    business_day_convention: modified_following\n"
        "    calendar: TR\n"
        "    payment:\n"
        "      default_frequency: annual\n"
        "      bullet_until: 1Y\n"
        "tlref_convention:\n"
        "  day_count: Act/365\n"
        "spread_reporting:\n"
        "  day_count: Act/365\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Bogus/000"):
        Conventions.load(bad)
