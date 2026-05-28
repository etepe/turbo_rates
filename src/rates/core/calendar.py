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

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import holidays

_DEFAULT_OVERRIDE_DIR = Path("config/holidays")
_DEFAULT_YEAR_RADIUS = 30


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
        if years is None:
            current_year = date.today().year
            years = range(
                current_year - _DEFAULT_YEAR_RADIUS,
                current_year + _DEFAULT_YEAR_RADIUS + 1,
            )

        pkg_holidays = holidays.country_holidays(ccy, years=list(years))
        merged: set[date] = {d for d in pkg_holidays}

        csv_path: Path | None
        if override_dir is not None:
            csv_path = override_dir / f"{ccy.lower()}.csv"
            if not csv_path.exists():
                raise FileNotFoundError(f"holiday override CSV not found for {ccy!r}: {csv_path}")
        else:
            candidate = _DEFAULT_OVERRIDE_DIR / f"{ccy.lower()}.csv"
            csv_path = candidate if candidate.exists() else None

        if csv_path is not None:
            for d in _read_holiday_csv(csv_path):
                merged.add(d)

        return cls(currency=ccy, _holidays=frozenset(merged), _override_source=csv_path)

    # ---------------------------------------------------------------------
    # Queries
    # ---------------------------------------------------------------------
    def is_holiday(self, d: date) -> bool:
        """True iff ``d`` is a holiday in this calendar."""
        return d in self._holidays

    def is_business_day(self, d: date) -> bool:
        """True iff ``d`` is a weekday and not a holiday."""
        return d.weekday() < 5 and not self.is_holiday(d)

    def add_business_days(self, d: date, n: int) -> date:
        """Return the date ``n`` business days from ``d`` (negative ``n`` walks backward)."""
        if n == 0:
            return d
        step = timedelta(days=1 if n > 0 else -1)
        remaining = abs(n)
        cur = d
        while remaining > 0:
            cur = cur + step
            if self.is_business_day(cur):
                remaining -= 1
        return cur


def _read_holiday_csv(path: Path) -> list[date]:
    """Parse a holiday-override CSV.

    Format: comment lines starting with ``#`` are skipped; the remainder must have a
    header ``date,description`` (description optional). Each ``date`` cell is parsed as
    ISO-8601 (YYYY-MM-DD).
    """
    dates: list[date] = []
    with path.open(encoding="utf-8") as f:
        non_comment = (line for line in f if not line.lstrip().startswith("#"))
        reader = csv.DictReader(non_comment)
        for row in reader:
            raw = (row.get("date") or "").strip()
            if not raw:
                continue
            dates.append(date.fromisoformat(raw))
    return dates
