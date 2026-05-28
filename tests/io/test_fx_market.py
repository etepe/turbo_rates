"""FX CSV market provider (M-108, contract C-101).

Covers happy-path parsing, all FX_IO_* diagnostic paths from the architecture
doc (§4 C-101), pair filtering with WARN-skip, cross-row invariants (
exactly-one-SPOT, duplicate dedup, pair-not-found ordering), date math
against the joint calendar, and Protocol conformance.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.conventions import FXConvention
from rates.fx.types import CurrencyPair, QuoteConvention
from rates.io.fx_market import (
    FxCsvProvider,
    FXCsvSchemaError,
    FXEmptyDataError,
    FXMarketDataProvider,
)

_FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Test scaffolding (hand-built so tests stay independent of YAML + holidays pkg)
# ---------------------------------------------------------------------------


@pytest.fixture()
def usdtry_pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")


@pytest.fixture()
def eurtry_pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="EUR", code="EURTRY")


@pytest.fixture()
def usdtry_conv() -> FXConvention:
    return FXConvention(
        pair_code="USDTRY",
        quote_convention=QuoteConvention.DIRECT,
        spot_lag_days=2,
        settlement_calendars=("TR", "US"),
        forward_point_scale=10000,
    )


@pytest.fixture()
def empty_tr_calendar() -> HolidayCalendar:
    """TR calendar with no holidays — weekdays are business days."""
    return HolidayCalendar(currency="TR", _holidays=frozenset(), _override_source=None)


@pytest.fixture()
def empty_us_calendar() -> HolidayCalendar:
    """US calendar with no holidays — weekdays are business days."""
    return HolidayCalendar(currency="US", _holidays=frozenset(), _override_source=None)


@pytest.fixture()
def joint_calendars(
    empty_tr_calendar: HolidayCalendar, empty_us_calendar: HolidayCalendar
) -> tuple[HolidayCalendar, ...]:
    return (empty_tr_calendar, empty_us_calendar)


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


def _provider(
    fixture_name: str,
    pair: CurrencyPair,
    conv: FXConvention,
    calendars: tuple[HolidayCalendar, ...],
    dg: DiagnosticsCollector,
) -> FxCsvProvider:
    return FxCsvProvider(
        _FIXTURES / fixture_name,
        pair=pair,
        conv=conv,
        calendars=calendars,
        diagnostics=dg,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_protocol_conformance(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    prov = _provider("fx_snapshot_happy.csv", usdtry_pair, usdtry_conv, joint_calendars, dg)
    assert isinstance(prov, FXMarketDataProvider)


@pytest.mark.phase8
def test_happy_path_parses_spot_forwards_and_basis(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    val = date(2026, 5, 28)  # Thursday
    md = _provider(
        "fx_snapshot_happy.csv", usdtry_pair, usdtry_conv, joint_calendars, dg
    ).load(val)

    # spot quote
    assert md.spot.pair == usdtry_pair
    assert md.spot.mid == pytest.approx(32.4520)
    assert md.spot.bid == pytest.approx(32.4500)
    assert md.spot.ask == pytest.approx(32.4540)
    assert md.spot.source == "REUTERS"
    # 2 BD from Thu 2026-05-28 over weekday-only joint calendar → Mon 2026-06-01
    assert md.spot.spot_date == date(2026, 6, 1)

    # forward points (sorted by tenor_days)
    assert len(md.forward_points) == 3
    assert [f.tenor_code for f in md.forward_points] == ["1M", "3M", "6M"]
    assert [f.tenor_days for f in md.forward_points] == [30, 91, 183]
    # settle_date = spot + tenor_days (calendar arithmetic per FXForwardPointQuote docstring)
    assert md.forward_points[0].settle_date == date(2026, 7, 1)
    assert md.forward_points[1].settle_date == date(2026, 8, 31)

    # basis quotes (sorted by tenor_days)
    assert len(md.basis_quotes) == 2
    assert [b.tenor_code for b in md.basis_quotes] == ["1Y", "2Y"]
    assert md.basis_quotes[0].spread_bps == pytest.approx(-180.0)
    assert md.basis_quotes[0].maturity_date == date(2027, 6, 1)
    # quoted_on_foreign hardcoded True for v0.3.0 (USDTRY, EURTRY market convention).
    assert all(b.quoted_on_foreign is True for b in md.basis_quotes)

    # no swap_quotes from CSV (V3 path)
    assert md.swap_quotes == ()

    # source + valuation_date plumbing
    assert md.source == "csv:fx_snapshot_happy.csv"
    assert md.valuation_date == val
    assert not dg.has_errors()


# ---------------------------------------------------------------------------
# Error paths (one per FX_IO_* diagnostic code)
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_file_not_found(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    prov = FxCsvProvider(
        _FIXTURES / "does_not_exist.csv",
        pair=usdtry_pair,
        conv=usdtry_conv,
        calendars=joint_calendars,
        diagnostics=dg,
    )
    with pytest.raises(FileNotFoundError):
        prov.load(date(2026, 5, 28))
    assert "FX_IO_FILE_NOT_FOUND" in _codes(dg)


@pytest.mark.phase8
def test_missing_required_column_raises(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    with pytest.raises(FXCsvSchemaError, match="required column"):
        _provider(
            "fx_snapshot_missing_col.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    assert "FX_IO_SCHEMA_MISSING_COL" in _codes(dg)


@pytest.mark.phase8
def test_unknown_instrument_type_raises(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    with pytest.raises(FXCsvSchemaError, match="unknown instrument_type"):
        _provider(
            "fx_snapshot_unknown_instrument.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    assert "FX_IO_UNKNOWN_INSTRUMENT" in _codes(dg)


@pytest.mark.phase8
def test_empty_snapshot_raises(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    with pytest.raises(FXEmptyDataError):
        _provider(
            "fx_snapshot_empty.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    assert "FX_IO_EMPTY_SNAPSHOT" in _codes(dg)


@pytest.mark.phase8
def test_comma_decimal_locale_rejected(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    with pytest.raises(FXCsvSchemaError):
        _provider(
            "fx_snapshot_comma_decimal.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    assert "FX_IO_ROW_PARSE_FAIL" in _codes(dg)


@pytest.mark.phase8
def test_no_spot_for_requested_pair_raises(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    dg = DiagnosticsCollector()
    with pytest.raises(FXCsvSchemaError, match="zero SPOT rows"):
        _provider(
            "fx_snapshot_no_spot.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    assert "FX_IO_NO_SPOT" in _codes(dg)


@pytest.mark.phase8
def test_multiple_spot_rejected_no_silent_first_wins(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    """Two SPOT rows for the same pair must be a hard error — no silent first-wins."""
    dg = DiagnosticsCollector()
    with pytest.raises(FXCsvSchemaError, match="2 SPOT rows"):
        _provider(
            "fx_snapshot_multi_spot.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    assert "FX_IO_MULTIPLE_SPOT" in _codes(dg)


@pytest.mark.phase8
def test_pair_not_found_after_filter(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    """File contains EURTRY rows; requesting USDTRY yields PAIR_NOT_FOUND
    (specific) rather than NO_SPOT (misleading)."""
    dg = DiagnosticsCollector()
    with pytest.raises(FXCsvSchemaError, match="zero rows remain"):
        _provider(
            "fx_snapshot_pair_not_found.csv",
            usdtry_pair,
            usdtry_conv,
            joint_calendars,
            dg,
        ).load(date(2026, 5, 28))
    codes = _codes(dg)
    assert "FX_IO_PAIR_NOT_FOUND" in codes
    # NO_SPOT must NOT fire — the more specific error takes precedence.
    assert "FX_IO_NO_SPOT" not in codes


# ---------------------------------------------------------------------------
# Pair filtering (this is the migrated skipped test from tests/fx/test_types.py)
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_pair_mismatch_rows_skipped_with_warn(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    """Multi-pair master CSV: rows for non-requested pair are skipped with
    FX_IO_PAIR_MISMATCH WARN; FXMarketData contains ONLY requested-pair quotes.

    This is the IO-boundary enforcement of pair consistency referenced by the
    (formerly skipped) test_fx_market_data_rejects_inconsistent_pair_across_quotes
    in tests/fx/test_types.py.
    """
    dg = DiagnosticsCollector()
    md = _provider(
        "fx_snapshot_multi_pair.csv",
        usdtry_pair,
        usdtry_conv,
        joint_calendars,
        dg,
    ).load(date(2026, 5, 28))

    # Only USDTRY rows survive.
    assert md.spot.pair == usdtry_pair
    assert md.spot.mid == pytest.approx(32.4520)
    assert all(fp.pair == usdtry_pair for fp in md.forward_points)
    assert len(md.forward_points) == 1  # only the USDTRY 3M
    # EURTRY rows emitted PAIR_MISMATCH WARN but no errors.
    codes = _codes(dg)
    assert "FX_IO_PAIR_MISMATCH" in codes
    assert not dg.has_errors()


# ---------------------------------------------------------------------------
# Cross-row dedup
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_duplicate_tenor_keeps_first_with_warn(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    """Duplicate (instrument_type, tenor_code) emits WARN, keeps first row."""
    dg = DiagnosticsCollector()
    md = _provider(
        "fx_snapshot_duplicate_tenor.csv",
        usdtry_pair,
        usdtry_conv,
        joint_calendars,
        dg,
    ).load(date(2026, 5, 28))

    # Only one 1M forward despite the duplicate row in the fixture.
    assert len(md.forward_points) == 1
    assert md.forward_points[0].mid == pytest.approx(1835)  # the first row's mid
    assert md.forward_points[0].source == "REUTERS"  # the first row's source
    assert "FX_IO_DUPLICATE_TENOR" in _codes(dg)
    assert not dg.has_errors()


# ---------------------------------------------------------------------------
# Date math
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_spot_date_walks_joint_calendar_over_weekends(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
    joint_calendars: tuple[HolidayCalendar, ...],
) -> None:
    """Valuation Thursday + 2 BD = Monday (skips Sat/Sun)."""
    dg = DiagnosticsCollector()
    md = _provider(
        "fx_snapshot_happy.csv",
        usdtry_pair,
        usdtry_conv,
        joint_calendars,
        dg,
    ).load(date(2026, 5, 28))  # Thursday
    assert md.spot.spot_date == date(2026, 6, 1)  # Monday


@pytest.mark.phase8
def test_spot_date_respects_intersection_of_calendars(
    usdtry_pair: CurrencyPair,
    usdtry_conv: FXConvention,
) -> None:
    """If TR is open but US is closed on a date, the joint calendar skips it."""
    # TR open all weekdays; US closes on 2026-06-01 (e.g., Memorial Day).
    tr_cal = HolidayCalendar(currency="TR", _holidays=frozenset(), _override_source=None)
    us_cal = HolidayCalendar(
        currency="US",
        _holidays=frozenset({date(2026, 6, 1)}),  # Monday — would have been spot.
        _override_source=None,
    )
    dg = DiagnosticsCollector()
    md = FxCsvProvider(
        _FIXTURES / "fx_snapshot_happy.csv",
        pair=usdtry_pair,
        conv=usdtry_conv,
        calendars=(tr_cal, us_cal),
        diagnostics=dg,
    ).load(date(2026, 5, 28))  # Thursday

    # 2 BD over joint = Thu → Fri → skip Mon (US holiday) → Tue 2026-06-02.
    assert md.spot.spot_date == date(2026, 6, 2)
