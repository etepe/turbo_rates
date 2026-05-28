"""rates.core.scenario — MPC path → daily TLREF index (M-008).

Builds a daily business-day index from ``valuation_date`` forward, using the user's
expected MPC policy path and the initial TLREF level read from
``market.special_rates["BISTTREF"]``. Optionally produces parallel-shifted ``low``
and ``high`` bands per F-005 / G1.

Open-question resolutions (this module fixes them; revisit only on explicit ask):

* **O3 — daily index granularity.** Business-day only. TLREF is set on TR business
  days; holidays inherit the prior business-day value. A calendar-day grid would
  multiply by ``(1 + r * 0)`` on holidays — same numerical result, larger vector.
  Business-day granularity is canonical and matches the F-004 acceptance "3723
  business days (~10y)".
* **MPC effective-date convention.** The rate value emitted by a meeting on
  ``meeting_date`` applies *from* ``meeting_date`` forward (i.e. the meeting_date
  is the first business day on which the new rate accrues for the overnight
  period to the next business day). Equivalent statement:
  ``rate_at(d) = initial_tlref + Σ bps_change_i / 10_000 for meetings i with
  meeting_date_i <= d``.
* **Band semantic (G1).** ``band_bps > 0`` shifts every rate value in the
  resulting time series by ``±band_bps / 10_000`` — including the initial TLREF.
  This is the "full path parallel shift" reading of F-005.

Index recursion (F-004):
    index[t_0] = 1.0  at t_0 = valuation_date
    index[t_i] = index[t_{i-1}] * (1 + rate(t_{i-1}) * delta_t / 365)
    delta_t    = (t_i - t_{i-1}).days   # calendar days between consecutive BDs

Diagnostics codes:
    SC_BISTTREF_MISSING        (ERROR)  initial TLREF absent.
    SC_INVALID_MPC             (ERROR)  meetings unsorted or duplicated.
    SC_NEGATIVE_POLICY_RATE    (WARN)   band shift produces negative rate.
    SC_MEETING_BEYOND_HORIZON  (WARN)   meeting after horizon end (ignored).
    SC_MEETING_IN_PAST         (WARN)   meeting on or before val_date (ignored).

V1.5 hardening — SC_MEETING_IN_PAST. Past-dated meetings are *already baked
into* the initial BISTTREF reading (which is the policy rate observed at
val_date). Replaying them would double-count the rate move. Behaviour:
explicit WARN per offending meeting (so the user notices stale input)
+ silent removal from the active path. Not an ERROR: the rest of the
``mpc_path`` may still drive a meaningful forward index.

Contract: C-003 (consumer-facing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import MarketData, MPCMeeting, MPCPath

#: Default horizon in calendar years when ``horizon_days`` is None.
_DEFAULT_HORIZON_YEARS: int = 10

#: Convention key used to look up the initial TLREF level in ``market.special_rates``.
INITIAL_TLREF_KEY: str = "BISTTREF"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MissingInitialTLREFError(KeyError):
    """Raised when ``market.special_rates["BISTTREF"]`` is absent."""


class InvalidMPCScheduleError(ValueError):
    """Raised when the MPC path violates ordering / uniqueness invariants."""


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DailyIndex:
    """One daily compounded TLREF index (mid / low / high).

    Attributes:
        label:  ``"mid"`` | ``"low"`` | ``"high"``.
        dates:  Ascending business-day dates starting at valuation_date.
        values: Index values; ``values[0] == 1.0``. Length matches ``dates``.
    """

    label: str
    dates: tuple[date, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.label not in ("mid", "low", "high"):
            raise ValueError(f"DailyIndex.label must be mid/low/high (got {self.label!r})")
        if len(self.dates) != len(self.values):
            raise ValueError(
                "DailyIndex.dates and .values must have the same length "
                f"({len(self.dates)} vs {len(self.values)})"
            )
        if not self.values:
            raise ValueError("DailyIndex must contain at least one point")
        if self.values[0] != 1.0:
            raise ValueError(f"DailyIndex.values[0] must be 1.0 (got {self.values[0]})")


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Output of :func:`build_scenario` covering mid plus optional ±band.

    Attributes:
        valuation_date:        T+0 spot date; matches ``mid.dates[0]``.
        initial_tlref:         Mid initial TLREF level (BISTTREF, decimal — 0.45 = 45%).
        band_bps:              Configured band magnitude (≥0). 0 ⇒ low/high = None.
        horizon_business_days: Length of the business-day grid (excluding the anchor).
        mid:                   Mid-scenario index.
        low:                   Low-band index (initial_tlref - band) or None.
        high:                  High-band index (initial_tlref + band) or None.
    """

    valuation_date: date
    initial_tlref: float
    band_bps: int
    horizon_business_days: int
    mid: DailyIndex
    low: DailyIndex | None
    high: DailyIndex | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_mpc_path(mpc: MPCPath, dg: DiagnosticsCollector) -> None:
    """Enforce ascending unique meeting_date ordering; emit ERROR on violation."""
    dates = [m.meeting_date for m in mpc.meetings]
    for a, b in pairwise(dates):
        if a >= b:
            msg = f"MPC meetings must be strictly ascending; got {a} >= {b}"
            dg.error("SC_INVALID_MPC", msg, {"prev": a.isoformat(), "next": b.isoformat()})
            raise InvalidMPCScheduleError(msg)


def _filter_past_meetings(
    mpc: MPCPath, val_date: date, dg: DiagnosticsCollector
) -> MPCPath:
    """Drop meetings whose ``meeting_date <= val_date``; emit a WARN per offender.

    The initial BISTTREF rate read from the snapshot already reflects every past
    policy move. Replaying a past meeting would double-count its bps_change and
    pull the scenario rate away from the user's stated initial level.
    """
    kept: list[MPCMeeting] = []
    for m in mpc.meetings:
        if m.meeting_date <= val_date:
            dg.warn(
                "SC_MEETING_IN_PAST",
                (
                    f"MPC meeting {m.meeting_date.isoformat()} is on or before "
                    f"valuation_date {val_date.isoformat()}; already reflected in "
                    "BISTTREF — ignored"
                ),
                {
                    "meeting_date": m.meeting_date.isoformat(),
                    "valuation_date": val_date.isoformat(),
                    "bps_change": m.bps_change,
                },
            )
            continue
        kept.append(m)
    if len(kept) == len(mpc.meetings):
        return mpc
    return MPCPath(meetings=tuple(kept))


def _build_bd_grid(
    val_date: date, horizon_days: int, calendar: HolidayCalendar
) -> tuple[date, ...]:
    """Return business-day grid of length ``horizon_days + 1`` (anchor + horizon)."""
    grid = [val_date]
    cur = val_date
    for _ in range(horizon_days):
        cur = calendar.add_business_days(cur, 1)
        grid.append(cur)
    return tuple(grid)


def _resolve_horizon(val_date: date, horizon_days: int | None, calendar: HolidayCalendar) -> int:
    """Return the effective ``horizon_days`` (business days)."""
    if horizon_days is not None:
        if horizon_days < 0:
            raise ValueError(f"horizon_days must be >= 0 (got {horizon_days})")
        return horizon_days

    # Auto: walk business days until calendar-date >= val + 10Y.
    end_target = (
        date(val_date.year + _DEFAULT_HORIZON_YEARS, val_date.month, val_date.day)
        if not (val_date.month == 2 and val_date.day == 29)
        else date(val_date.year + _DEFAULT_HORIZON_YEARS, 2, 28)
    )
    count = 0
    cur = val_date
    while cur < end_target:
        cur = calendar.add_business_days(cur, 1)
        count += 1
    return count


def _policy_rate_series(
    grid: tuple[date, ...],
    initial_tlref: float,
    mpc: MPCPath,
    shift_bps: int,
    dg: DiagnosticsCollector,
) -> tuple[float, ...]:
    """Piecewise-flat policy rate per business day, including a parallel ``shift_bps``.

    ``rate_at(grid[i]) = initial_tlref + Σ bps_change for meetings.meeting_date <=
    grid[i]``, then shifted by ``shift_bps / 10_000``.

    Meetings beyond ``grid[-1]`` are ignored with a WARN.
    """
    horizon_end = grid[-1]
    shift = shift_bps / 10_000.0
    rates: list[float] = []

    meeting_idx = 0
    cur_bps = 0
    sorted_meetings = mpc.meetings  # validated ascending upstream

    # WARN for any out-of-horizon meeting up front so the message order is stable.
    for m in sorted_meetings:
        if m.meeting_date > horizon_end:
            dg.warn(
                "SC_MEETING_BEYOND_HORIZON",
                (
                    f"MPC meeting {m.meeting_date.isoformat()} after horizon end "
                    f"{horizon_end.isoformat()}; ignored"
                ),
                {"meeting_date": m.meeting_date.isoformat(), "bps_change": m.bps_change},
            )

    for d in grid:
        while (
            meeting_idx < len(sorted_meetings)
            and sorted_meetings[meeting_idx].meeting_date <= d
            and sorted_meetings[meeting_idx].meeting_date <= horizon_end
        ):
            cur_bps += sorted_meetings[meeting_idx].bps_change
            meeting_idx += 1
        rate = initial_tlref + cur_bps / 10_000.0 + shift
        rates.append(rate)

    if any(r < 0 for r in rates):
        first_neg = next(i for i, r in enumerate(rates) if r < 0)
        dg.warn(
            "SC_NEGATIVE_POLICY_RATE",
            (
                f"policy rate goes negative at {grid[first_neg].isoformat()} "
                f"(shift_bps={shift_bps}, rate={rates[first_neg]:.6f})"
            ),
            {
                "first_negative_date": grid[first_neg].isoformat(),
                "rate": rates[first_neg],
                "shift_bps": shift_bps,
            },
        )

    return tuple(rates)


def _compound_index(
    grid: tuple[date, ...],
    rates: tuple[float, ...],
    tlref_basis_days: int,
) -> tuple[float, ...]:
    """Compounded index per F-004 recursion.

    ``index[0] = 1.0``; for i >= 1, ``index[i] = index[i-1] * (1 + rate[i-1] *
    delta_t / basis_days)`` where ``delta_t = (grid[i] - grid[i-1]).days``.
    """
    values: list[float] = [1.0]
    for i in range(1, len(grid)):
        delta_t = (grid[i] - grid[i - 1]).days
        rate = rates[i - 1]
        values.append(values[-1] * (1.0 + rate * delta_t / tlref_basis_days))
    return tuple(values)


def _basis_days(conv: Conventions) -> int:
    """Return the TLREF day-count basis in days (Act/365 ⇒ 365)."""
    dc = conv.tlref_convention.day_count.value
    if dc == "Act/360":
        return 360
    if dc == "Act/365":
        return 365
    raise ValueError(f"unsupported TLREF day-count for scenario: {dc}")


# ---------------------------------------------------------------------------
# Public API (C-003)
# ---------------------------------------------------------------------------


def build_scenario(
    market: MarketData,
    mpc_path: MPCPath,
    band_bps: int,
    conventions: Conventions,
    calendar: HolidayCalendar,
    diagnostics: DiagnosticsCollector,
    *,
    horizon_days: int | None = None,
) -> ScenarioResult:
    """Build daily TLREF index plus optional ±band per F-004 / F-005.

    Args:
        market:        Market snapshot; ``special_rates["BISTTREF"]`` required.
        mpc_path:      User MPC policy path. Must be ascending and unique by date.
        band_bps:      Band magnitude in basis points (≥0). 0 short-circuits to mid.
        conventions:   Loaded conventions; ``tlref_convention`` provides the basis.
        calendar:      Holiday calendar used to walk business days.
        diagnostics:   Single mutable collector threaded by the orchestrator.
        horizon_days:  Length of the business-day grid (excluding anchor). ``None``
                       resolves to ``_DEFAULT_HORIZON_YEARS`` calendar years
                       converted to business days against ``calendar``.

    Returns:
        Frozen :class:`ScenarioResult` with ``mid`` always populated, ``low`` and
        ``high`` populated when ``band_bps > 0`` (else ``None`` per G2).

    Raises:
        MissingInitialTLREFError: BISTTREF absent from ``market.special_rates``.
        InvalidMPCScheduleError:  MPC meetings unsorted or duplicated.
        ValueError:               ``band_bps < 0`` or ``horizon_days < 0``.
    """
    if band_bps < 0:
        raise ValueError(f"band_bps must be >= 0 (got {band_bps})")

    val_date = _resolve_valuation_date(market)

    if INITIAL_TLREF_KEY not in market.special_rates:
        msg = (
            f"market.special_rates missing required key {INITIAL_TLREF_KEY!r}; "
            "initial TLREF cannot be resolved"
        )
        diagnostics.error(
            "SC_BISTTREF_MISSING",
            msg,
            {
                "available_keys": sorted(market.special_rates),
                "required": INITIAL_TLREF_KEY,
            },
        )
        raise MissingInitialTLREFError(msg)
    initial_tlref = market.special_rates[INITIAL_TLREF_KEY]

    _validate_mpc_path(mpc_path, diagnostics)
    mpc_path = _filter_past_meetings(mpc_path, val_date, diagnostics)

    horizon = _resolve_horizon(val_date, horizon_days, calendar)
    grid = _build_bd_grid(val_date, horizon, calendar)
    basis = _basis_days(conventions)

    mid_rates = _policy_rate_series(grid, initial_tlref, mpc_path, 0, diagnostics)
    mid_values = _compound_index(grid, mid_rates, basis)
    mid = DailyIndex(label="mid", dates=grid, values=mid_values)

    if band_bps == 0:
        return ScenarioResult(
            valuation_date=val_date,
            initial_tlref=initial_tlref,
            band_bps=0,
            horizon_business_days=horizon,
            mid=mid,
            low=None,
            high=None,
        )

    high_rates = _policy_rate_series(grid, initial_tlref, mpc_path, band_bps, diagnostics)
    low_rates = _policy_rate_series(grid, initial_tlref, mpc_path, -band_bps, diagnostics)
    high_values = _compound_index(grid, high_rates, basis)
    low_values = _compound_index(grid, low_rates, basis)
    high = DailyIndex(label="high", dates=grid, values=high_values)
    low = DailyIndex(label="low", dates=grid, values=low_values)

    return ScenarioResult(
        valuation_date=val_date,
        initial_tlref=initial_tlref,
        band_bps=band_bps,
        horizon_business_days=horizon,
        mid=mid,
        low=low,
        high=high,
    )


def _resolve_valuation_date(market: MarketData) -> date:
    """Resolve valuation date: ``market.valuation_date`` then earliest quote start."""
    if market.valuation_date is not None:
        return market.valuation_date
    if not market.quotes:
        raise ValueError(
            "cannot resolve valuation_date: MarketData has no valuation_date and no quotes"
        )
    return min(q.start_date for q in market.quotes)


# Silence unused-import for typing helpers consumed via dataclasses only.
_ = timedelta


__all__ = [
    "INITIAL_TLREF_KEY",
    "DailyIndex",
    "InvalidMPCScheduleError",
    "MissingInitialTLREFError",
    "ScenarioResult",
    "build_scenario",
]
