"""Phase 9 — quarterly xccy coupon schedule (M-113) tests.

Covers C-109: 3M stepping, joint-calendar modified-following rolls, common
Act/360 accruals, the maturity-aligned back stub, sub-quarter single stub,
leap-day accrual, and the ``maturity <= spot`` error. Expected dates are
cross-checked against an independent in-test reference roll (not the module's
private helpers) so a divergence in the production roll is caught.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
from datetime import date, timedelta
from pathlib import Path

import pytest

from rates.core.calendar import HolidayCalendar
from rates.core.types import BusinessDayConvention, DayCount
from rates.fx.schedule import XccySchedule, build_quarterly_xccy_schedule

pytestmark = pytest.mark.phase9

_MODFOL = BusinessDayConvention.MODIFIED_FOLLOWING
_ACT360 = DayCount.ACT_360


# ---------------------------------------------------------------------------
# Calendars (joint TR + US, the USDTRY pair)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tr_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "TR", years=range(2025, 2035), override_dir=Path("config/holidays")
    )


@pytest.fixture(scope="module")
def us_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "US", years=range(2025, 2035), override_dir=Path("config/holidays")
    )


# ---------------------------------------------------------------------------
# Independent reference roll (deliberately NOT the module's private helpers)
# ---------------------------------------------------------------------------


def _ref_add_months(d: date, n: int) -> date:
    total = d.month - 1 + n
    y = d.year + total // 12
    m = total % 12 + 1
    last = _stdlib_calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def _ref_is_bd(d: date, dom: HolidayCalendar, fer: HolidayCalendar) -> bool:
    return dom.is_business_day(d) and fer.is_business_day(d)


def _ref_walk(d: date, dom: HolidayCalendar, fer: HolidayCalendar, *, fwd: bool) -> date:
    step = timedelta(days=1 if fwd else -1)
    cur = d + step
    while not _ref_is_bd(cur, dom, fer):
        cur += step
    return cur


def _ref_roll_modfol(d: date, dom: HolidayCalendar, fer: HolidayCalendar) -> date:
    if _ref_is_bd(d, dom, fer):
        return d
    nxt = _ref_walk(d, dom, fer, fwd=True)
    if nxt.month != d.month:
        return _ref_walk(d, dom, fer, fwd=False)
    return nxt


def _ref_coupons(
    spot: date, maturity: date, dom: HolidayCalendar, fer: HolidayCalendar
) -> list[date]:
    out: list[date] = []
    k = 1
    while True:
        cand = _ref_roll_modfol(_ref_add_months(spot, 3 * k), dom, fer)
        if cand >= maturity:
            break
        out.append(cand)
        k += 1
    out.append(maturity)
    return out


# ---------------------------------------------------------------------------
# Shared structural invariant check
# ---------------------------------------------------------------------------


def _assert_invariants(sched: XccySchedule, spot: date, maturity: date) -> None:
    n = len(sched.coupon_dates)
    assert n >= 1
    assert len(sched.taus) == n
    assert len(sched.period_starts) == n

    # Anchors used verbatim.
    assert sched.period_starts[0] == spot
    assert sched.coupon_dates[-1] == maturity

    # Strictly ascending coupon dates.
    assert all(a < b for a, b in zip(sched.coupon_dates, sched.coupon_dates[1:], strict=False))

    # period_starts[i] == coupon_dates[i-1]; contiguous, no gaps/overlaps.
    assert sched.period_starts[1:] == sched.coupon_dates[:-1]

    # Independent Act/360 recompute; positive accruals.
    for start, end, tau in zip(
        sched.period_starts, sched.coupon_dates, sched.taus, strict=True
    ):
        assert tau > 0.0
        assert tau == pytest.approx((end - start).days / 360.0, abs=1e-15)

    # Telescoping: the partition covers spot..maturity exactly once (leap-safe).
    total_days = sum(
        (e - s).days for s, e in zip(sched.period_starts, sched.coupon_dates, strict=True)
    )
    assert total_days == (maturity - spot).days


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_maturity_before_spot_raises(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    with pytest.raises(ValueError, match="must be after"):
        build_quarterly_xccy_schedule(
            spot, date(2026, 6, 1), tr_calendar, us_calendar, _MODFOL, _ACT360
        )


def test_maturity_equals_spot_raises(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    with pytest.raises(ValueError, match="must be after"):
        build_quarterly_xccy_schedule(
            spot, spot, tr_calendar, us_calendar, _MODFOL, _ACT360
        )


# ---------------------------------------------------------------------------
# USDTRY 1Y — correct 3M dates + Act/360 taus + clean 4-period structure
# ---------------------------------------------------------------------------


def test_usdtry_1y_quarterly_dates_and_taus(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    # Maturity aligned to the rolled 12M point => clean four quarters, no stub.
    maturity = _ref_roll_modfol(_ref_add_months(spot, 12), tr_calendar, us_calendar)

    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, _ACT360
    )

    expected = [
        _ref_roll_modfol(_ref_add_months(spot, 3), tr_calendar, us_calendar),
        _ref_roll_modfol(_ref_add_months(spot, 6), tr_calendar, us_calendar),
        _ref_roll_modfol(_ref_add_months(spot, 9), tr_calendar, us_calendar),
        maturity,
    ]
    assert list(sched.coupon_dates) == expected
    assert len(sched.coupon_dates) == 4

    _assert_invariants(sched, spot, maturity)

    # Every regular quarter is ~0.25 in Act/360 (88..95 days).
    for tau in sched.taus:
        assert 0.24 <= tau <= 0.27

    # Interior coupon dates are joint business days (rolled off weekends/holidays).
    for c in sched.coupon_dates[:-1]:
        assert tr_calendar.is_business_day(c)
        assert us_calendar.is_business_day(c)


def test_interior_coupons_roll_off_non_business_days(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    maturity = _ref_roll_modfol(_ref_add_months(spot, 12), tr_calendar, us_calendar)
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, _ACT360
    )

    # For each interior period, when the raw 3M target is not a joint business
    # day the produced coupon must differ from it and itself be a business day.
    for idx, coupon in enumerate(sched.coupon_dates[:-1], start=1):
        raw = _ref_add_months(spot, 3 * idx)
        if not _ref_is_bd(raw, tr_calendar, us_calendar):
            assert coupon != raw
        assert _ref_is_bd(coupon, tr_calendar, us_calendar)
        # Modified-following keeps the roll inside the target's month.
        assert coupon.month == _ref_roll_modfol(raw, tr_calendar, us_calendar).month


# ---------------------------------------------------------------------------
# Sub-quarter swap — single back-stub period spot..maturity
# ---------------------------------------------------------------------------


def test_sub_quarter_single_back_stub(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    maturity = date(2026, 8, 10)  # < 3M from spot
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, _ACT360
    )

    assert sched.coupon_dates == (maturity,)
    assert sched.period_starts == (spot,)
    assert sched.taus[0] == pytest.approx((maturity - spot).days / 360.0, abs=1e-15)
    assert sched.taus[0] < 0.25
    _assert_invariants(sched, spot, maturity)


# ---------------------------------------------------------------------------
# Back stub — whole quarters then a short final stub < 0.25
# ---------------------------------------------------------------------------


def test_back_stub_short_final_period(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    maturity = date(2027, 7, 15)  # ~13M => four whole quarters + ~1M stub
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, _ACT360
    )

    assert list(sched.coupon_dates) == _ref_coupons(spot, maturity, tr_calendar, us_calendar)
    assert len(sched.coupon_dates) == 5  # 3M,6M,9M,12M + maturity stub
    # The stub is the short final period.
    assert sched.taus[-1] < 0.25
    # ...and the four preceding periods are full quarters.
    for tau in sched.taus[:-1]:
        assert 0.24 <= tau <= 0.27
    _assert_invariants(sched, spot, maturity)


# ---------------------------------------------------------------------------
# Leap year — Feb 29 accrual counted exactly once
# ---------------------------------------------------------------------------


def test_leap_day_accrual(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2027, 11, 30)  # +3M lands on the 2028-02-29 leap day
    maturity = date(2028, 12, 29)
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, _ACT360
    )

    _assert_invariants(sched, spot, maturity)

    # The first period spans the leap day; its accrual must include 2028-02-29.
    leap = date(2028, 2, 29)
    spanning = [
        (s, e)
        for s, e in zip(sched.period_starts, sched.coupon_dates, strict=True)
        if s < leap <= e
    ]
    assert len(spanning) == 1
    s, e = spanning[0]
    # The spanning period's day count includes the leap day: a Nov 30 -> Feb 29
    # quarter is 91 days, one more than the equivalent non-leap (90) window.
    assert (e - s).days >= 91
    expected_first_target = _ref_roll_modfol(
        _ref_add_months(spot, 3), tr_calendar, us_calendar
    )
    assert sched.coupon_dates[0] == expected_first_target


# ---------------------------------------------------------------------------
# day_count is honoured (not hardcoded to /360)
# ---------------------------------------------------------------------------


def test_day_count_basis_is_applied(
    tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    maturity = _ref_roll_modfol(_ref_add_months(spot, 12), tr_calendar, us_calendar)
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, DayCount.ACT_365
    )
    for start, end, tau in zip(
        sched.period_starts, sched.coupon_dates, sched.taus, strict=True
    ):
        assert tau == pytest.approx((end - start).days / 365.0, abs=1e-15)


# ---------------------------------------------------------------------------
# Structural invariants across a spread of maturities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "maturity",
    [
        date(2026, 9, 14),  # sub-quarter
        date(2026, 12, 31),  # ~2 quarters + stub, year-end
        date(2027, 6, 15),  # ~1Y
        date(2028, 6, 15),  # ~2Y
        date(2029, 6, 15),  # ~3Y
    ],
)
def test_invariants_across_maturities(
    maturity: date, tr_calendar: HolidayCalendar, us_calendar: HolidayCalendar
) -> None:
    spot = date(2026, 6, 15)
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_calendar, us_calendar, _MODFOL, _ACT360
    )
    _assert_invariants(sched, spot, maturity)
    assert list(sched.coupon_dates) == _ref_coupons(spot, maturity, tr_calendar, us_calendar)
