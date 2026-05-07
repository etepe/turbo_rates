"""rates.core.calendar — holiday calendar (M-002).

Combines the ``holidays`` PyPI package (baseline) with CSV overrides under
``config/holidays/{ccy}.csv`` (CSV wins on conflict). Exposes a small WORKDAY-equivalent
API used by core.bootstrap (pillar maturity rolling per business-day convention) and by
core.scenario (daily TLREF business-day grid).

The fidelity of the ``holidays`` package against the Excel ``tatiller`` reference list
for 2024-2026 is validated once by ``scripts/check_holidays_fidelity.py`` (Phase 1
exit). If the delta exceeds 5 days/year for TR, the strategy flips to CSV-primary; this
module honours that override transparently because CSV always wins.

Contracts: C-009.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HolidayCalendar:
    """Holiday calendar for one currency / locale.

    Internals are intentionally opaque: hold the merged set of holidays (package +
    overrides) and answer queries against it.
    """

    currency: str
    _holidays: frozenset[date]
    _override_source: Path | None

    # ---------------------------------------------------------------------
    # Constructors
    # ---------------------------------------------------------------------
    @classmethod
    def for_currency(
        cls,
        ccy: str,
        *,
        years: range | None = None,
        override_dir: Path | None = None,
    ) -> HolidayCalendar:
        """Build a calendar for ``ccy`` (e.g. ``"TR"``, ``"US"``, ``"EU"``).

        Args:
            ccy:          ISO/locale code understood by the ``holidays`` package.
            years:        Years to materialise (default: today's year ± 30).
            override_dir: Directory containing ``{ccy}.csv``; CSV rows win on conflict.
                          Default: ``config/holidays``.

        Raises:
            FileNotFoundError: When override_dir is provided but {ccy}.csv is missing.
        """
        raise NotImplementedError(
            "M-002: load holidays.<ccy>(years), then merge config/holidays/{ccy}.csv. "
            "CSV rows are added to the set; CSV-with-no-rows is valid (package-only)."
        )

    # ---------------------------------------------------------------------
    # Queries
    # ---------------------------------------------------------------------
    def is_holiday(self, d: date) -> bool:
        """True iff ``d`` is a holiday in this calendar."""
        raise NotImplementedError("M-002: return d in self._holidays.")

    def is_business_day(self, d: date) -> bool:
        """True iff ``d`` is a weekday and not a holiday."""
        raise NotImplementedError("M-002: weekday() < 5 and not self.is_holiday(d).")

    def add_business_days(self, d: date, n: int) -> date:
        """Return the date ``n`` business days from ``d`` (negative ``n`` walks backward)."""
        raise NotImplementedError("M-002: walk one day at a time skipping non-business days.")
