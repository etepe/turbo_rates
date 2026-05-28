"""rates.fx.schedule — quarterly xccy coupon-schedule generator (M-113, V0.4).

Builds the 3M (quarterly) coupon schedule shared by both legs of a
cross-currency basis swap: coupon dates are rolled on the *joint* domestic +
foreign calendar under a business-day convention, accruals use a single common
day-count (Act/360 per D-14), and the final period is a back stub that ends
*exactly* on the requested maturity (its accrual may be shorter — or longer —
than a regular 3M period).

Contract C-109 (consumed by M-106 strip and a future MtM path). The
``_add_months`` / ``_roll`` idioms mirror :mod:`rates.core.bootstrap` but are
re-implemented locally — the joint-calendar variant is genuinely different and
re-implementing keeps M-113 decoupled from ``rates.core`` internals (DV-4).

See ``docs/v04-xccy-calibration-architecture.md`` §3.2 (M-113), §4 (C-109),
§5.1. Pure stdlib + ``rates.core`` value types; no IO, no external deps.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
from dataclasses import dataclass
from datetime import date, timedelta

from rates.core.calendar import HolidayCalendar
from rates.core.daycount import year_fraction
from rates.core.types import BusinessDayConvention, DayCount

_MONTHS_PER_QUARTER = 3


@dataclass(frozen=True, slots=True)
class XccySchedule:
    """A generated quarterly xccy coupon schedule.

    All three tuples are parallel and share one length ``N`` = number of
    periods. Period ``i`` runs from ``period_starts[i]`` to ``coupon_dates[i]``
    and accrues ``taus[i]`` year-fractions.

    Attributes:
        coupon_dates:  Period end dates, strictly ascending. ``coupon_dates[-1]``
                       is the maturity exactly (back stub); interior dates are
                       joint-calendar rolled.
        taus:          Per-period accrual (common day-count, Act/360 per D-14).
                       ``len(taus) == len(coupon_dates)``; the final stub entry
                       may be ``< 0.25``.
        period_starts: Period start dates. ``period_starts[0]`` is the spot date
                       exactly; ``period_starts[i] == coupon_dates[i - 1]`` for
                       ``i >= 1``.
    """

    coupon_dates: tuple[date, ...]
    taus: tuple[float, ...]
    period_starts: tuple[date, ...]


def build_quarterly_xccy_schedule(
    spot_date: date,
    maturity: date,
    dom_calendar: HolidayCalendar,
    for_calendar: HolidayCalendar,
    bdc: BusinessDayConvention,
    day_count: DayCount,
) -> XccySchedule:
    """Build the 3M coupon schedule for a xccy basis swap (C-109).

    Coupon targets are ``spot_date + 3k`` months, each rolled to a joint
    domestic+foreign business day under ``bdc``. The schedule ends on a back
    stub whose final coupon date equals ``maturity`` exactly; the stub accrual
    is the true day-count fraction and may be shorter than a regular quarter.
    When not even one whole 3M period fits before ``maturity`` (a sub-quarter
    swap), the result is a single back-stub period ``spot_date..maturity``.

    Args:
        spot_date:    Curve spot ``t_0``; becomes ``period_starts[0]`` verbatim.
        maturity:     Final settlement ``t_N``; becomes ``coupon_dates[-1]``
                      verbatim (not rolled — D-14 back stub).
        dom_calendar: Domestic holiday calendar.
        for_calendar: Foreign holiday calendar.
        bdc:          Business-day convention for interior coupon rolls
                      (modified_following per D-14).
        day_count:    Common accrual basis for both legs (Act/360 per D-14).

    Returns:
        XccySchedule with parallel ``coupon_dates`` / ``taus`` / ``period_starts``.

    Raises:
        ValueError: If ``maturity <= spot_date``.
    """
    if maturity <= spot_date:
        raise ValueError(
            f"maturity ({maturity.isoformat()}) must be after "
            f"spot_date ({spot_date.isoformat()})"
        )

    coupon_dates: list[date] = []
    k = 1
    while True:
        candidate = _roll_joint(
            _add_months(spot_date, _MONTHS_PER_QUARTER * k),
            dom_calendar,
            for_calendar,
            bdc,
        )
        if candidate >= maturity:
            break
        coupon_dates.append(candidate)
        k += 1
    coupon_dates.append(maturity)

    period_starts = [spot_date, *coupon_dates[:-1]]
    taus = [
        year_fraction(start, end, day_count)
        for start, end in zip(period_starts, coupon_dates, strict=True)
    ]

    return XccySchedule(
        coupon_dates=tuple(coupon_dates),
        taus=tuple(taus),
        period_starts=tuple(period_starts),
    )


def _add_months(d: date, n: int) -> date:
    """Add ``n`` calendar months to ``d``; clamp the day to the new month's last.

    Examples: ``Nov 30 + 3M -> Feb 28/29``; ``Aug 31 + 1M -> Sep 30``.
    """
    total = d.month - 1 + n
    new_year = d.year + total // 12
    new_month = total % 12 + 1
    last = _stdlib_calendar.monthrange(new_year, new_month)[1]
    return date(new_year, new_month, min(d.day, last))


def _is_joint_business_day(
    d: date, dom_calendar: HolidayCalendar, for_calendar: HolidayCalendar
) -> bool:
    """True iff ``d`` is a business day in both the domestic and foreign calendars."""
    return dom_calendar.is_business_day(d) and for_calendar.is_business_day(d)


def _next_joint_business_day(
    d: date,
    dom_calendar: HolidayCalendar,
    for_calendar: HolidayCalendar,
    *,
    forward: bool,
) -> date:
    """First joint business day strictly after (``forward``) or before ``d``."""
    step = timedelta(days=1 if forward else -1)
    cur = d + step
    while not _is_joint_business_day(cur, dom_calendar, for_calendar):
        cur += step
    return cur


def _roll_joint(
    d: date,
    dom_calendar: HolidayCalendar,
    for_calendar: HolidayCalendar,
    conv: BusinessDayConvention,
) -> date:
    """Adjust ``d`` to a joint business day per ``conv`` (mirrors core ``_roll``).

    Returns ``d`` unchanged when it is already a joint business day or ``conv``
    is ``NONE``. Modified conventions fall back to the opposite direction if the
    primary roll would cross a month boundary.
    """
    if conv is BusinessDayConvention.NONE or _is_joint_business_day(
        d, dom_calendar, for_calendar
    ):
        return d
    if conv is BusinessDayConvention.FOLLOWING:
        return _next_joint_business_day(d, dom_calendar, for_calendar, forward=True)
    if conv is BusinessDayConvention.PRECEDING:
        return _next_joint_business_day(d, dom_calendar, for_calendar, forward=False)
    if conv is BusinessDayConvention.MODIFIED_FOLLOWING:
        nxt = _next_joint_business_day(d, dom_calendar, for_calendar, forward=True)
        if nxt.month != d.month:
            return _next_joint_business_day(d, dom_calendar, for_calendar, forward=False)
        return nxt
    if conv is BusinessDayConvention.MODIFIED_PRECEDING:
        prev = _next_joint_business_day(d, dom_calendar, for_calendar, forward=False)
        if prev.month != d.month:
            return _next_joint_business_day(d, dom_calendar, for_calendar, forward=True)
        return prev
    raise ValueError(f"unsupported business-day convention: {conv!r}")


__all__ = [
    "XccySchedule",
    "build_quarterly_xccy_schedule",
]
