"""CSV market provider (M-010, contract C-001).

Covers forgiving-reader behaviour, BISTTREF routing, locale-strict validation,
empty-snapshot and missing-column ERROR paths, valuation_date inference, and
Protocol conformance.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.diagnostics import DiagnosticsCollector
from rates.io.market import (
    CsvProvider,
    CsvSchemaError,
    EmptyDataError,
    MarketDataProvider,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


@pytest.mark.phase4
def test_happy_path_parses_quotes_and_routes_bisttref():
    dg = DiagnosticsCollector()
    md = CsvProvider(_FIXTURES / "snapshot_happy.csv", dg).load()

    assert len(md.quotes) == 3
    codes = [q.tenor_code for q in md.quotes]
    assert codes == ["TYSO1M", "TYSO3M", "TYSO1Y"]
    assert "BISTTREF" not in codes
    assert md.special_rates == {"BISTTREF": 0.4275}
    assert md.valuation_date == date(2026, 6, 12)
    assert md.source == "csv:snapshot_happy.csv"
    assert not dg.has_errors()


@pytest.mark.phase4
def test_extra_columns_ignored():
    dg = DiagnosticsCollector()
    md = CsvProvider(_FIXTURES / "snapshot_extra_cols.csv", dg).load()
    assert len(md.quotes) == 2
    assert all(hasattr(q, "tenor_code") for q in md.quotes)
    assert not dg.has_errors()


@pytest.mark.phase4
def test_missing_required_column_raises():
    dg = DiagnosticsCollector()
    with pytest.raises(CsvSchemaError, match="required column"):
        CsvProvider(_FIXTURES / "snapshot_missing_col.csv", dg).load()
    assert "IO_SCHEMA_MISSING_COL" in _codes(dg)
    assert dg.has_errors()


@pytest.mark.phase4
def test_comma_decimal_locale_rejected():
    dg = DiagnosticsCollector()
    with pytest.raises(CsvSchemaError):
        CsvProvider(_FIXTURES / "snapshot_comma_decimal.csv", dg).load()
    assert "IO_ROW_PARSE_FAIL" in _codes(dg)


@pytest.mark.phase4
def test_dmy_date_locale_rejected():
    dg = DiagnosticsCollector()
    with pytest.raises(CsvSchemaError):
        CsvProvider(_FIXTURES / "snapshot_dmy_date.csv", dg).load()
    assert "IO_ROW_PARSE_FAIL" in _codes(dg)


@pytest.mark.phase4
def test_empty_snapshot_raises():
    dg = DiagnosticsCollector()
    with pytest.raises(EmptyDataError):
        CsvProvider(_FIXTURES / "snapshot_empty.csv", dg).load()
    assert "IO_EMPTY_SNAPSHOT" in _codes(dg)


@pytest.mark.phase4
def test_file_not_found_raises(tmp_path):
    dg = DiagnosticsCollector()
    with pytest.raises(FileNotFoundError):
        CsvProvider(tmp_path / "ghost.csv", dg).load()
    assert "IO_FILE_NOT_FOUND" in _codes(dg)


@pytest.mark.phase4
def test_valuation_date_override_takes_precedence():
    dg = DiagnosticsCollector()
    md = CsvProvider(_FIXTURES / "snapshot_happy.csv", dg).load(valuation_date=date(2026, 1, 1))
    assert md.valuation_date == date(2026, 1, 1)


@pytest.mark.phase4
def test_valuation_date_inferred_from_earliest_start():
    dg = DiagnosticsCollector()
    md = CsvProvider(_FIXTURES / "snapshot_happy.csv", dg).load()
    assert md.valuation_date == date(2026, 6, 12)


@pytest.mark.phase4
def test_provider_satisfies_protocol():
    dg = DiagnosticsCollector()
    provider: MarketDataProvider = CsvProvider(_FIXTURES / "snapshot_happy.csv", dg)
    assert isinstance(provider, MarketDataProvider)
    md = provider.load()
    assert len(md.quotes) == 3


@pytest.mark.phase4
def test_first_line_schema_comment_skipped():
    """Files with '# schema: v1' first line still parse correctly."""
    dg = DiagnosticsCollector()
    md = CsvProvider(_FIXTURES / "snapshot_happy.csv", dg).load()
    # Three quote rows means the comment line was skipped (not parsed as a row).
    assert len(md.quotes) == 3
