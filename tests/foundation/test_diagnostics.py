"""Tests for rates.core.diagnostics (M-005).

Acceptance covers: WARN-only -> exit 0; any ERROR -> exit 2; record order preserved;
to_list() returns a defensive copy.
"""

from __future__ import annotations

import pytest

from rates.core.diagnostics import DiagnosticsCollector, Severity


@pytest.mark.phase1
def test_fresh_collector_has_no_errors_and_exits_zero() -> None:
    """A new DiagnosticsCollector reports has_errors() == False and exit_code() == 0."""
    dc = DiagnosticsCollector()
    assert dc.has_errors() is False
    assert dc.exit_code() == 0
    assert dc.to_list() == []


@pytest.mark.phase1
def test_warn_only_collector_exits_zero() -> None:
    """Multiple warn() calls without any error() still yield exit_code 0."""
    dc = DiagnosticsCollector()
    dc.warn("W1", "first warning")
    dc.warn("W2", "second warning", {"k": 1})
    assert dc.has_errors() is False
    assert dc.exit_code() == 0
    assert len(dc.to_list()) == 2


@pytest.mark.phase1
def test_single_error_yields_exit_two() -> None:
    """Even one error() call flips exit_code() to 2 and has_errors() to True."""
    dc = DiagnosticsCollector()
    dc.warn("W", "warn")
    dc.error("E", "boom")
    assert dc.has_errors() is True
    assert dc.exit_code() == 2


@pytest.mark.phase1
def test_to_list_returns_defensive_copy() -> None:
    """Mutating the returned list must not affect the collector's internal state."""
    dc = DiagnosticsCollector()
    dc.warn("W", "msg")
    snapshot = dc.to_list()
    snapshot.clear()
    assert len(dc.to_list()) == 1


@pytest.mark.phase1
def test_diagnostic_record_preserves_context_dict() -> None:
    """warn(code='X', message='m', context={'k': 'v'}) -> Diagnostic with context['k'] == 'v'."""
    dc = DiagnosticsCollector()
    dc.warn("X", "m", {"k": "v"})
    [record] = dc.to_list()
    assert record.code == "X"
    assert record.message == "m"
    assert record.context == {"k": "v"}
    assert record.severity is Severity.WARN


@pytest.mark.phase1
def test_records_appear_in_insertion_order() -> None:
    """to_list() should return records in the order they were recorded."""
    dc = DiagnosticsCollector()
    dc.warn("W1", "first")
    dc.error("E1", "second")
    dc.warn("W2", "third")
    codes = [r.code for r in dc.to_list()]
    assert codes == ["W1", "E1", "W2"]
