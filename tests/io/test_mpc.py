"""MPC CSV reader (M-011, contract C-007)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.diagnostics import DiagnosticsCollector
from rates.io.mpc import MPCScheduleError, MPCSchemaError, load_mpc_path

_FIXTURES = Path(__file__).parent / "fixtures"


def _codes(dg):
    return [d.code for d in dg.to_list()]


@pytest.mark.phase4
def test_happy_path_parses_three_meetings():
    dg = DiagnosticsCollector()
    path = load_mpc_path(_FIXTURES / "mpc_happy.csv", dg)
    assert len(path.meetings) == 3
    assert [m.meeting_date for m in path.meetings] == [
        date(2026, 9, 11),
        date(2026, 12, 18),
        date(2027, 3, 19),
    ]
    assert [m.bps_change for m in path.meetings] == [-250, -250, -150]
    assert path.meetings[0].rationale == "first cut"
    assert path.meetings[1].rationale is None  # empty cell -> None
    assert not dg.has_errors()


@pytest.mark.phase4
def test_unsorted_rows_get_sorted_on_load():
    dg = DiagnosticsCollector()
    path = load_mpc_path(_FIXTURES / "mpc_unsorted.csv", dg)
    dates = [m.meeting_date for m in path.meetings]
    assert dates == sorted(dates)


@pytest.mark.phase4
def test_duplicate_dates_raise():
    dg = DiagnosticsCollector()
    with pytest.raises(MPCScheduleError, match="duplicate"):
        load_mpc_path(_FIXTURES / "mpc_duplicate.csv", dg)
    assert "IO_MPC_DUPLICATE_DATE" in _codes(dg)


@pytest.mark.phase4
def test_invalid_bps_raises():
    dg = DiagnosticsCollector()
    with pytest.raises(MPCSchemaError):
        load_mpc_path(_FIXTURES / "mpc_invalid_bps.csv", dg)
    assert "IO_MPC_ROW_PARSE_FAIL" in _codes(dg)


@pytest.mark.phase4
def test_missing_required_column_raises():
    dg = DiagnosticsCollector()
    with pytest.raises(MPCSchemaError, match="required column"):
        load_mpc_path(_FIXTURES / "mpc_missing_col.csv", dg)
    assert "IO_MPC_SCHEMA_MISSING_COL" in _codes(dg)


@pytest.mark.phase4
def test_file_not_found_raises(tmp_path):
    dg = DiagnosticsCollector()
    with pytest.raises(FileNotFoundError):
        load_mpc_path(tmp_path / "ghost.csv", dg)
    assert "IO_MPC_FILE_NOT_FOUND" in _codes(dg)
