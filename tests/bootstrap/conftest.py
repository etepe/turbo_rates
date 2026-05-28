"""Phase 2 bootstrap test fixtures.

All fixtures rely on the real ``config/conventions.yaml`` (TRY = Act/360,
modified_following, annual coupon, bullet_until=1Y) and the real TR holiday calendar so
that date rolls match the bootstrap's own logic. Pillar ``end_date`` values are
computed via the bootstrap module's private ``_add_months`` / ``_roll`` helpers so
that annual-coupon pillars align with each other (closed-form path).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from rates.core.bootstrap import _add_months, _roll
from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import BusinessDayConvention, MarketData, MarketQuote

# A Friday safely inside the V1 fidelity window. Avoiding year-end / quarter-end so
# downstream rolling rules don't surprise us.
_VAL_DATE = date(2026, 6, 12)


@pytest.fixture(scope="session")
def val_date() -> date:
    return _VAL_DATE


@pytest.fixture(scope="session")
def tr_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "TR",
        years=range(_VAL_DATE.year - 1, _VAL_DATE.year + 12),
        override_dir=Path("config/holidays"),
    )


@pytest.fixture(scope="session")
def conventions(conventions_yaml: Path) -> Conventions:
    return Conventions.load(conventions_yaml)


@pytest.fixture()
def diagnostics() -> DiagnosticsCollector:
    """One fresh DiagnosticsCollector per test."""
    return DiagnosticsCollector()


@pytest.fixture(scope="session")
def yearly_date(val_date: date, tr_calendar: HolidayCalendar) -> Callable[[int], date]:
    """Return ``val_date + N years`` rolled via modified_following on the TR calendar."""

    def _yr(n: int) -> date:
        return _roll(
            _add_months(val_date, 12 * n),
            tr_calendar,
            BusinessDayConvention.MODIFIED_FOLLOWING,
        )

    return _yr


@pytest.fixture(scope="session")
def monthly_date(val_date: date, tr_calendar: HolidayCalendar) -> Callable[[int], date]:
    """Return ``val_date + N months`` rolled via modified_following."""

    def _m(n: int) -> date:
        return _roll(
            _add_months(val_date, n),
            tr_calendar,
            BusinessDayConvention.MODIFIED_FOLLOWING,
        )

    return _m


# ---------------------------------------------------------------------------
# Market data fixtures
# ---------------------------------------------------------------------------


def _mk_quote(
    tenor_code: str,
    start: date,
    end: date,
    rate: float,
    *,
    use_mid: bool = True,
) -> MarketQuote:
    """Build a synthetic MarketQuote. ``rate`` is placed in ``mid`` by default."""
    if use_mid:
        return MarketQuote(
            tenor_code=tenor_code,
            tenor_days=(end - start).days,
            start_date=start,
            end_date=end,
            bid=None,
            ask=None,
            mid=rate,
            source="synthetic",
        )
    return MarketQuote(
        tenor_code=tenor_code,
        tenor_days=(end - start).days,
        start_date=start,
        end_date=end,
        bid=rate - 0.0005,
        ask=rate + 0.0005,
        mid=None,
        source="synthetic",
    )


@pytest.fixture()
def single_bullet_market(val_date: date, yearly_date: Callable[[int], date]) -> MarketData:
    """One 1Y bullet pillar @ 45% Act/360."""
    quote = _mk_quote("TYSO1Y", val_date, yearly_date(1), 0.45)
    return MarketData(quotes=(quote,), valuation_date=val_date, source="synthetic")


@pytest.fixture()
def short_curve_market(
    val_date: date,
    monthly_date: Callable[[int], date],
    yearly_date: Callable[[int], date],
) -> MarketData:
    """Five bullet pillars (1M, 3M, 6M, 9M, 1Y) at descending rates — inversion."""
    pillars = [
        ("TYSO1M", monthly_date(1), 0.4750),
        ("TYSO3M", monthly_date(3), 0.4650),
        ("TYSO6M", monthly_date(6), 0.4500),
        ("TYSO9M", monthly_date(9), 0.4400),
        ("TYSO1Y", yearly_date(1), 0.4300),
    ]
    quotes = tuple(_mk_quote(code, val_date, end, r) for code, end, r in pillars)
    return MarketData(quotes=quotes, valuation_date=val_date, source="synthetic")


@pytest.fixture()
def aligned_annual_market(
    val_date: date,
    monthly_date: Callable[[int], date],
    yearly_date: Callable[[int], date],
) -> MarketData:
    """Aligned-yearly pillar set so all >1Y bootstraps take the closed-form path.

    Pillars: 1M, 3M, 6M, 1Y, 2Y, 3Y, 5Y, 7Y, 10Y.
    Note: for 5Y to close-form correctly, val+4Y must align with a prior pillar's
    end_date. We include 1Y, 2Y, 3Y, 4Y... wait — we don't include 4Y here, so 5Y must
    use brentq. To keep this fixture closed-form-only, include every yearly pillar.
    """
    pillars = [
        ("TYSO1M", monthly_date(1), 0.4750),
        ("TYSO3M", monthly_date(3), 0.4600),
        ("TYSO6M", monthly_date(6), 0.4400),
        ("TYSO1Y", yearly_date(1), 0.4200),
        ("TYSO2Y", yearly_date(2), 0.3900),
        ("TYSO3Y", yearly_date(3), 0.3700),
        ("TYSO4Y", yearly_date(4), 0.3550),
        ("TYSO5Y", yearly_date(5), 0.3450),
        ("TYSO6Y", yearly_date(6), 0.3400),
        ("TYSO7Y", yearly_date(7), 0.3360),
        ("TYSO8Y", yearly_date(8), 0.3340),
        ("TYSO9Y", yearly_date(9), 0.3330),
        ("TYSO10Y", yearly_date(10), 0.3325),
    ]
    quotes = tuple(_mk_quote(code, val_date, end, r) for code, end, r in pillars)
    return MarketData(quotes=quotes, valuation_date=val_date, source="synthetic")


@pytest.fixture()
def irregular_market(
    val_date: date,
    monthly_date: Callable[[int], date],
    yearly_date: Callable[[int], date],
) -> MarketData:
    """Pillar set with missing 4Y / 6Y so 5Y and 7Y require brentq fallback."""
    pillars = [
        ("TYSO3M", monthly_date(3), 0.4600),
        ("TYSO1Y", yearly_date(1), 0.4200),
        ("TYSO2Y", yearly_date(2), 0.3900),
        ("TYSO3Y", yearly_date(3), 0.3700),
        ("TYSO5Y", yearly_date(5), 0.3450),
        ("TYSO7Y", yearly_date(7), 0.3360),
        ("TYSO10Y", yearly_date(10), 0.3325),
    ]
    quotes = tuple(_mk_quote(code, val_date, end, r) for code, end, r in pillars)
    return MarketData(quotes=quotes, valuation_date=val_date, source="synthetic")
