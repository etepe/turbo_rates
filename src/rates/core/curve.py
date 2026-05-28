"""rates.core.curve — OIS curve domain object (M-006).

The bootstrap module (M-007) produces an :class:`OISCurve`; consumers (report, schemas,
app, future ``rates forward`` subcommand) interact with it exclusively through the
public methods declared in C-005:

    df_at(date)              -> float
    zero_at(date, basis)     -> float
    forward(start, end)      -> float
    forward_ladder()         -> DataFrame (15 entries)
    pillars()                -> DataFrame (defensive copy)
    to_summary_dict()        -> dict (JSON-serialisable)

Key design choices:

* The pandas DataFrame is an implementation detail. It is never exposed; ``pillars()``
  returns a defensive copy.
* Interpolation distance metric is **calendar days** (G7 / A8). Weight ``w =
  (t - a) / (b - a)``.
* The curve anchors at ``valuation_date`` with a synthetic DF=1.0 so that
  ``df_at(valuation_date) == 1.0`` and ``df_at(d)`` is well-defined for
  ``valuation_date <= d <= last_pillar.end_date``.
* At every bootstrapped pillar date, both interpolation schemes return the
  bootstrapped DF **exactly** (test invariant per F-002).
* Forward ladder dates are computed by the bootstrap module (which owns the calendar)
  and frozen into the curve at construction. This keeps M-006 calendar-free
  (depends_on: M-001 conventions + M-003 daycount only).

Contracts: C-005 (consumer), C-010 (uses year_fraction).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

import numpy as np
import pandas as pd

from rates.core.daycount import year_fraction
from rates.core.types import DayCount

InterpolationScheme = Literal["log_linear_df", "linear_zero"]

# Canonical forward-ladder labels per F-003 / C-005.
# 11 monthly forwards (1x2..11x12) + 4 quarterly forwards (1x3, 3x6, 6x9, 9x12) = 15.
FORWARD_LADDER_LABELS: tuple[str, ...] = (
    "1x2",
    "2x3",
    "3x4",
    "4x5",
    "5x6",
    "6x7",
    "7x8",
    "8x9",
    "9x10",
    "10x11",
    "11x12",
    "1x3",
    "3x6",
    "6x9",
    "9x12",
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pillar:
    """A bootstrapped curve pillar.

    Attributes:
        tenor_code:      Provider tenor identifier (e.g. ``"TYSO1Y"``).
        tenor_days:      Calendar days from ``valuation_date`` to ``end_date``.
        start_date:      Effective swap start (T+0 spot per G10 — equal to valuation_date).
        end_date:        Pillar maturity / payment date (rolled per business-day conv).
        rate:            Bootstrapped spot OIS rate (native day-count from conventions).
        discount_factor: Discount factor to ``end_date``.
    """

    tenor_code: str
    tenor_days: int
    start_date: date
    end_date: date
    rate: float
    discount_factor: float


@dataclass(frozen=True, slots=True)
class ForwardLadderEntry:
    """A single forward-starting OIS rate in the canonical 15-entry ladder.

    Attributes:
        label:      Canonical label (``"1x2"`` ... ``"9x12"``).
        start_date: Forward start (calendar month offset from valuation, rolled).
        end_date:   Forward end (calendar month offset from valuation, rolled).
        rate:       Forward rate, native day-count (Act/360 for TRY).
    """

    label: str
    start_date: date
    end_date: date
    rate: float


# ---------------------------------------------------------------------------
# OISCurve
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OISCurve:
    """Bootstrapped OIS discount-factor curve with two interpolation schemes.

    Constructed exclusively by :func:`rates.core.bootstrap.bootstrap_curve`. The
    constructor accepts pre-computed pillar and forward-ladder geometry so that the
    curve has no dependency on a HolidayCalendar.

    Attributes:
        valuation_date:        T+0 spot date; ``df_at(valuation_date) == 1.0``.
        day_count:             Native day-count for spot/forward rate quotation.
        interp:                ``"log_linear_df"`` (default) or ``"linear_zero"``.
        pillars_tuple:         Ordered bootstrapped pillars (ascending end_date).
        forward_ladder_dates:  Pre-computed (label, start_date, end_date) triples for
                               the 15-entry canonical ladder. Constructed by bootstrap.
    """

    valuation_date: date
    day_count: DayCount
    interp: InterpolationScheme
    pillars_tuple: tuple[Pillar, ...]
    forward_ladder_dates: tuple[tuple[str, date, date], ...]

    def __post_init__(self) -> None:
        if self.interp not in ("log_linear_df", "linear_zero"):
            raise ValueError(
                f"unknown interpolation scheme: {self.interp!r}; "
                "expected 'log_linear_df' or 'linear_zero'"
            )
        if not self.pillars_tuple:
            raise ValueError("OISCurve requires at least one pillar")
        prev_date = self.valuation_date
        for p in self.pillars_tuple:
            if p.end_date <= prev_date:
                raise ValueError(
                    "pillars must have strictly increasing end_dates and "
                    f"all start after valuation_date (offender: {p.tenor_code} "
                    f"@ {p.end_date})"
                )
            prev_date = p.end_date

    # -----------------------------------------------------------------
    # Public API (C-005)
    # -----------------------------------------------------------------
    def df_at(self, d: date) -> float:
        """Discount factor at calendar date ``d``.

        Args:
            d: Target date; must satisfy ``valuation_date <= d <= last_pillar.end_date``.

        Returns:
            DF as float.

        Raises:
            ValueError: When ``d`` is before ``valuation_date`` or after the last pillar
                (``DateOutOfRange`` per C-005).
        """
        return self._df_at(d)

    def zero_at(self, d: date, basis: DayCount) -> float:
        """Simple zero rate from ``valuation_date`` to ``d`` under ``basis``.

        ``z = (1/DF(d) - 1) / year_fraction(valuation_date, d, basis)``.

        Args:
            d:     Target date (must be > valuation_date).
            basis: Day-count basis for the returned rate.

        Returns:
            Simple-compounded zero rate.

        Raises:
            ValueError: If ``d == valuation_date`` (rate undefined) or ``d`` out of range.
        """
        if d == self.valuation_date:
            raise ValueError("zero_at undefined at valuation_date (tau=0)")
        df = self._df_at(d)
        tau = year_fraction(self.valuation_date, d, basis)
        return (1.0 / df - 1.0) / tau

    def forward(self, start: date, end: date) -> float:
        """Simple-compounded forward rate between two dates.

        ``f = (DF(start) / DF(end) - 1) / year_fraction(start, end, day_count)``.

        Args:
            start: Forward start.
            end:   Forward end (must be strictly after ``start``).

        Returns:
            Forward rate in the curve's native day-count.

        Raises:
            ValueError: If ``start >= end`` or either date is out of range.
        """
        if start >= end:
            raise ValueError(f"forward requires start < end (got {start} >= {end})")
        df_s = self._df_at(start)
        df_e = self._df_at(end)
        tau = year_fraction(start, end, self.day_count)
        return (df_s / df_e - 1.0) / tau

    def forward_ladder(self) -> pd.DataFrame:
        """Canonical 15-entry monthly + quarterly forward ladder as a DataFrame.

        Columns: ``label``, ``start_date``, ``end_date``, ``rate``. Caller may mutate
        the returned DataFrame freely (it's a fresh object every call).
        """
        rows: list[dict[str, Any]] = []
        for label, s, e in self.forward_ladder_dates:
            rows.append(
                {
                    "label": label,
                    "start_date": s,
                    "end_date": e,
                    "rate": self.forward(s, e),
                }
            )
        return pd.DataFrame(rows, columns=["label", "start_date", "end_date", "rate"])

    def pillars(self) -> pd.DataFrame:
        """Pillar table as a fresh DataFrame (defensive copy; safe to mutate).

        Columns: ``tenor_code``, ``tenor_days``, ``start_date``, ``end_date``, ``rate``,
        ``discount_factor``.
        """
        rows = [
            {
                "tenor_code": p.tenor_code,
                "tenor_days": p.tenor_days,
                "start_date": p.start_date,
                "end_date": p.end_date,
                "rate": p.rate,
                "discount_factor": p.discount_factor,
            }
            for p in self.pillars_tuple
        ]
        return pd.DataFrame(
            rows,
            columns=[
                "tenor_code",
                "tenor_days",
                "start_date",
                "end_date",
                "rate",
                "discount_factor",
            ],
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary suitable for Summary persistence.

        Shape::

            {
                "valuation_date": "YYYY-MM-DD",
                "day_count": "Act/360",
                "interpolation": "log_linear_df",
                "pillars": [{tenor_code, tenor_days, end_date, rate, discount_factor}, ...],
                "forward_ladder": [{label, start_date, end_date, rate}, ...],
            }
        """
        return {
            "valuation_date": self.valuation_date.isoformat(),
            "day_count": str(self.day_count.value),
            "interpolation": self.interp,
            "pillars": [
                {
                    "tenor_code": p.tenor_code,
                    "tenor_days": p.tenor_days,
                    "end_date": p.end_date.isoformat(),
                    "rate": p.rate,
                    "discount_factor": p.discount_factor,
                }
                for p in self.pillars_tuple
            ],
            "forward_ladder": [
                {
                    "label": label,
                    "start_date": s.isoformat(),
                    "end_date": e.isoformat(),
                    "rate": self.forward(s, e),
                }
                for label, s, e in self.forward_ladder_dates
            ],
        }

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------
    def _df_at(self, d: date) -> float:
        """Interpolate DF at ``d`` per the curve's selected scheme."""
        if d < self.valuation_date:
            raise ValueError(f"DateOutOfRange: {d} is before valuation_date {self.valuation_date}")
        if d == self.valuation_date:
            return 1.0
        last = self.pillars_tuple[-1].end_date
        if d > last:
            raise ValueError(f"DateOutOfRange: {d} is after last pillar end_date {last}")

        a_date, a_df, b_date, b_df = self._bracket(d)

        # Exact-pillar hit -> bootstrapped DF returned verbatim (F-002 invariant).
        if d == a_date:
            return a_df
        if d == b_date:
            return b_df

        days_total = (b_date - a_date).days
        days_to_d = (d - a_date).days
        w = days_to_d / days_total

        if self.interp == "log_linear_df":
            return float(np.exp((1.0 - w) * np.log(a_df) + w * np.log(b_df)))

        # linear_zero: linear interpolation on simple zero rates in native day-count.
        # The anchor at valuation_date has an undefined zero rate; if the bracket
        # starts at val_date, flat-extrapolate the next pillar's zero rate.
        if a_date == self.valuation_date:
            z_b = self._zero_native(b_date, b_df)
            tau_d = year_fraction(self.valuation_date, d, self.day_count)
            return 1.0 / (1.0 + z_b * tau_d)

        z_a = self._zero_native(a_date, a_df)
        z_b = self._zero_native(b_date, b_df)
        z = z_a + w * (z_b - z_a)
        tau_d = year_fraction(self.valuation_date, d, self.day_count)
        return 1.0 / (1.0 + z * tau_d)

    def _zero_native(self, d: date, df: float) -> float:
        """Simple zero rate in the curve's native day-count, given a pillar DF."""
        tau = year_fraction(self.valuation_date, d, self.day_count)
        return (1.0 / df - 1.0) / tau

    def _bracket(self, d: date) -> tuple[date, float, date, float]:
        """Return ``(a_date, a_df, b_date, b_df)`` such that ``a_date <= d <= b_date``.

        The leftmost endpoint may be ``valuation_date`` (synthetic anchor with DF=1.0).
        """
        # Anchor segment: [valuation_date, first_pillar.end_date]
        first = self.pillars_tuple[0]
        if d <= first.end_date:
            return self.valuation_date, 1.0, first.end_date, first.discount_factor

        # Find the segment via linear scan (pillar counts are small, ~22).
        for i in range(1, len(self.pillars_tuple)):
            a = self.pillars_tuple[i - 1]
            b = self.pillars_tuple[i]
            if a.end_date <= d <= b.end_date:
                return a.end_date, a.discount_factor, b.end_date, b.discount_factor

        # Caller guarantees d <= last_pillar.end_date, so this is unreachable.
        raise AssertionError(f"unreachable: no bracket found for {d!r}")
