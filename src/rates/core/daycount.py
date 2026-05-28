"""rates.core.daycount — pure day-count fraction functions (M-003).

A single public function, ``year_fraction``, accepts a basis enum and returns the
year-fraction between two dates. This is the ONLY module where the literal numbers 360
and 365 appear; everywhere else uses ``Conventions.day_count``.

Contracts: C-010.
Acceptance (NFR per F-011): rate -> DF -> rate round-trip is reproducible to 1e-12 under
the declared convention.
"""

from __future__ import annotations

from datetime import date

from rates.core.types import DayCount


def year_fraction(d1: date, d2: date, basis: DayCount) -> float:
    """Year fraction between two dates under the given basis.

    Args:
        d1:    Start date.
        d2:    End date. May be before, equal to, or after d1.
        basis: Day-count convention (``Act/360``, ``Act/365``, ``30/360``).

    Returns:
        Signed float: positive when d2 > d1, zero when d2 == d1, negative when d2 < d1.

    Raises:
        ValueError: If ``basis`` is not a recognised :class:`DayCount` member.
    """
    if basis is DayCount.ACT_360:
        return (d2 - d1).days / 360.0
    if basis is DayCount.ACT_365:
        return (d2 - d1).days / 365.0
    if basis is DayCount.THIRTY_360:
        d1_day = min(d1.day, 30)
        d2_day = min(d2.day, 30) if d1_day == 30 else d2.day
        days = 360 * (d2.year - d1.year) + 30 * (d2.month - d1.month) + (d2_day - d1_day)
        return days / 360.0
    raise ValueError(f"unsupported day-count basis: {basis!r}")
