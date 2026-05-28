"""Phase 4 IO test fixtures.

Hand-built domain objects (curve, scenario, comparison) so Phase 4 tests are
isolated from Phase 2/3 regressions.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.curve import OISCurve, Pillar
from rates.core.diagnostics import Diagnostic, Severity
from rates.core.report import ComparisonRow, ComparisonTable
from rates.core.scenario import DailyIndex, ScenarioResult
from rates.core.types import DayCount
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.types import CurrencyPair, QuoteConvention
from rates.io.schemas import ConfigSnapshot

_VAL = date(2026, 6, 12)


@pytest.fixture(scope="session")
def val_date() -> date:
    return _VAL


@pytest.fixture(scope="session")
def tr_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "TR",
        years=range(_VAL.year - 1, _VAL.year + 12),
        override_dir=Path("config/holidays"),
    )


@pytest.fixture(scope="session")
def conventions(conventions_yaml: Path) -> Conventions:
    return Conventions.load(conventions_yaml)


@pytest.fixture()
def hand_curve(val_date) -> OISCurve:
    pillars = (
        Pillar("TYSO3M", 92, val_date, date(2026, 9, 14), 0.45, 0.8955),
        Pillar("TYSO1Y", 367, val_date, date(2027, 6, 14), 0.42, 0.7000),
    )
    ladder = (("1x3", date(2026, 7, 13), date(2026, 9, 14)),)
    return OISCurve(
        valuation_date=val_date,
        day_count=DayCount.ACT_360,
        interp="log_linear_df",
        pillars_tuple=pillars,
        forward_ladder_dates=ladder,
    )


@pytest.fixture()
def hand_scenario_with_band(val_date) -> ScenarioResult:
    """3-point business-day grid with both bands populated (for parquet long-format tests)."""
    dates = (val_date, date(2026, 6, 15), date(2026, 6, 16))
    return ScenarioResult(
        valuation_date=val_date,
        initial_tlref=0.45,
        band_bps=150,
        horizon_business_days=2,
        mid=DailyIndex(label="mid", dates=dates, values=(1.0, 1.0005, 1.0010)),
        low=DailyIndex(label="low", dates=dates, values=(1.0, 1.00037, 1.00074)),
        high=DailyIndex(label="high", dates=dates, values=(1.0, 1.00063, 1.00126)),
    )


@pytest.fixture()
def hand_scenario_mid_only(val_date) -> ScenarioResult:
    dates = (val_date, date(2026, 6, 15))
    return ScenarioResult(
        valuation_date=val_date,
        initial_tlref=0.45,
        band_bps=0,
        horizon_business_days=1,
        mid=DailyIndex(label="mid", dates=dates, values=(1.0, 1.0005)),
        low=None,
        high=None,
    )


@pytest.fixture()
def hand_comparison(val_date) -> ComparisonTable:
    rows = (
        ComparisonRow(
            tenor="TYSO3M",
            kind="pillar",
            start_date=val_date,
            end_date=date(2026, 9, 14),
            days_to_maturity=94,
            market_rate_act365=0.4567,
            scenario_rate_act365=0.4550,
            spread_bps=17.0,
        ),
        ComparisonRow(
            tenor="TYSO1Y",
            kind="pillar",
            start_date=val_date,
            end_date=date(2027, 6, 14),
            days_to_maturity=367,
            market_rate_act365=0.4267,
            scenario_rate_act365=None,
            spread_bps=None,
        ),
    )
    return ComparisonTable(rows=rows)


@pytest.fixture()
def hand_config_snapshot() -> ConfigSnapshot:
    return ConfigSnapshot(
        day_count="Act/360",
        interpolation="log_linear_df",
        business_day_convention="modified_following",
        bullet_until="1Y",
        calendar="TR",
        band_bps=150,
        horizon_business_days=2520,
        initial_tlref=0.45,
    )


@pytest.fixture()
def hand_diagnostics() -> list[Diagnostic]:
    return [
        Diagnostic(
            severity=Severity.WARN,
            code="BS_QUOTE_FALLBACK_AVG",
            message="x",
            context={"k": 1},
        ),
        Diagnostic(
            severity=Severity.ERROR,
            code="BS_REPRICE_FAIL",
            message="y",
            context={},
        ),
    ]


# ---------------------------------------------------------------------------
# FX fixtures (Phase 2, M-110)
# ---------------------------------------------------------------------------


@pytest.fixture()
def hand_fx_pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")


@pytest.fixture()
def hand_fx_conv() -> FXConvention:
    return FXConvention(
        pair_code="USDTRY",
        quote_convention=QuoteConvention.DIRECT,
        spot_lag_days=2,
        settlement_calendars=("TR", "US"),
        forward_point_scale=10000,
    )


@pytest.fixture()
def hand_fx_forward() -> FXForwardCurve:
    """Hand-built USDTRY forward curve with spot 2026-06-01 + 3 pillars."""
    spot_date = date(2026, 6, 1)
    pillars = (
        FXForwardPillar("1M", 30, date(2026, 7, 1), 32.6355),
        FXForwardPillar("3M", 91, date(2026, 8, 31), 32.9950),
        FXForwardPillar("6M", 183, date(2026, 12, 1), 33.6240),
    )
    return FXForwardCurve(
        pair_code="USDTRY",
        spot_date=spot_date,
        spot_rate=32.4520,
        pillars_tuple=pillars,
    )


@pytest.fixture()
def hand_fx_basis() -> CrossCurrencyBasisCurve:
    """Hand-built USDTRY xccy basis curve with 2 pillars."""
    spot_date = date(2026, 6, 1)
    pillars = (
        BasisPillar("1Y", 365, date(2027, 6, 1), -180.0),
        BasisPillar("2Y", 730, date(2028, 6, 1), -215.0),
    )
    return CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=spot_date,
        pillars_tuple=pillars,
        quoted_on_foreign=True,
    )


@pytest.fixture()
def fx_valuation_date() -> date:
    return date(2026, 5, 28)
