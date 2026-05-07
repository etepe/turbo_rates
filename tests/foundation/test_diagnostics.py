"""Tests for rates.core.diagnostics (M-005).

Acceptance covers: WARN-only -> exit 0; any ERROR -> exit 2; record order preserved;
to_list() returns a defensive copy.
"""

from __future__ import annotations

import pytest


@pytest.mark.phase1
def test_fresh_collector_has_no_errors_and_exits_zero() -> None:
    """A new DiagnosticsCollector reports has_errors() == False and exit_code() == 0."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_warn_only_collector_exits_zero() -> None:
    """Multiple warn() calls without any error() still yield exit_code 0."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_single_error_yields_exit_two() -> None:
    """Even one error() call flips exit_code() to 2 and has_errors() to True."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_to_list_returns_defensive_copy() -> None:
    """Mutating the returned list must not affect the collector's internal state."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_diagnostic_record_preserves_context_dict() -> None:
    """warn(code='X', message='m', context={'k': 'v'}) -> Diagnostic with context['k'] == 'v'."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_records_appear_in_insertion_order() -> None:
    """to_list() should return records in the order they were recorded."""
    pytest.skip("not yet implemented")
