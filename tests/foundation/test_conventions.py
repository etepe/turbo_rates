"""Tests for rates.core.conventions (M-001)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.phase1
def test_load_default_conventions_yaml_succeeds(conventions_yaml: Path) -> None:
    """The committed config/conventions.yaml loads without error."""
    pytest.skip("not yet implemented — Conventions.load(conventions_yaml)")


@pytest.mark.phase1
def test_loaded_try_currency_has_act_360_day_count(conventions_yaml: Path) -> None:
    """Default TRY OIS day-count is Act/360 (per F-011)."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_loaded_try_currency_has_modified_following_bdc(conventions_yaml: Path) -> None:
    """Default TRY business_day_convention is modified_following (per O1)."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_loaded_tlref_basis_is_act_365(conventions_yaml: Path) -> None:
    """TLREF day-count is Act/365 (per F-011)."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_loaded_spread_reporting_is_act_365(conventions_yaml: Path) -> None:
    """Spread reporting day-count is Act/365 (per F-012, common basis)."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_dotted_override_replaces_leaf(conventions_yaml: Path) -> None:
    """Conventions.load(path, {'ois.TRY.day_count': 'Act/365'}) overrides the leaf."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_missing_yaml_raises_file_not_found_error() -> None:
    """Conventions.load(non-existent path) raises FileNotFoundError clearly."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_unknown_day_count_value_raises_value_error(tmp_path: Path) -> None:
    """A YAML with day_count='Bogus/000' must raise ValueError, not silently default."""
    pytest.skip("not yet implemented")
