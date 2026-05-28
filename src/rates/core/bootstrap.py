"""rates.core.bootstrap — unified OIS swap bootstrap (M-007).

Public function :func:`bootstrap_curve` consumes ``MarketData`` plus ``Conventions`` and
``HolidayCalendar`` and produces a deterministic, byte-identically reproducible
:class:`OISCurve` (M-006). Per A14 / G3 / G10:

* All pillars start at T+0 spot (``valuation_date``). The Excel "chained" pillar start is
  explicitly rejected.
* Pillars with tenor ≤ ``bullet_until`` (``"1Y"`` for TRY) bootstrap as a single bullet
  payment: ``DF = 1 / (1 + r * tau)``.
* Longer pillars bootstrap as annual-coupon OIS swaps. Each step is **closed-form when
  every intermediate yearly coupon date aligns with a previously bootstrapped pillar**
  (which is the common case for V1 TRY pillar sets). When alignment fails (irregular
  grid), :func:`scipy.optimize.brentq` is the fallback.
* Quote fallback order per F-006 / G13: ``mid`` → ``avg(bid, ask)`` → ``ask`` → ``bid``
  → skip pillar (neighbor interpolation occurs naturally via curve interpolation).
* Tenor collisions (multiple quotes for the same maturity date) keep the *direct* quote
  and emit a WARN with the implied-vs-direct spread.

The reprice acceptance criterion is **1e-10 absolute** per F-001 / C-002. A violation
triggers an ERROR diagnostic.

Contract: C-002 (consumer-facing). Owns_data: ``BootstrapResult`` is currently not
introduced because :func:`bootstrap_curve` returns ``OISCurve`` directly per C-002. If
richer post-bootstrap diagnostics are needed later, BootstrapResult can be added as a
wrapper around the curve without changing the public signature.
"""

from __future__ import annotations

import calendar as _stdlib_calendar
import math
from dataclasses import dataclass
from datetime import date

from scipy.optimize import brentq

from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions, CurrencyConvention
from rates.core.curve import (
    FORWARD_LADDER_LABELS,
    InterpolationScheme,
    OISCurve,
    Pillar,
)
from rates.core.daycount import year_fraction
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import BusinessDayConvention, DayCount, MarketData, MarketQuote

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: Reprice tolerance per F-001 / C-002. Each pillar must price its own swap to 0 within
#: this absolute error or an ERROR diagnostic is emitted.
REPRICE_TOLERANCE: float = 1e-10

#: Numerical tolerance used by :func:`scipy.optimize.brentq`.
BRENTQ_XTOL: float = 1e-14
BRENTQ_RTOL: float = 1e-12
BRENTQ_MAXITER: int = 200

#: Lower/upper bounds on the trial DF for brentq search. DFs are in (0, 1] for any
#: economically sensible curve over V1's 10y horizon.
_BRENTQ_DF_LO: float = 1e-9
_BRENTQ_DF_HI: float = 1.0 - 1e-12


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_period_years(s: str) -> int:
    """Parse a period string like ``"1Y"`` / ``"2Y"`` into integer years.

    Only year-suffixed periods are supported in V1 (the only ``bullet_until`` value used
    for TRY). Other suffixes raise ValueError.
    """
    s = s.strip()
    if not s.endswith(("Y", "y")) or len(s) < 2:
        raise ValueError(f"unsupported period format (expected 'NY'): {s!r}")
    try:
        return int(s[:-1])
    except ValueError as e:
        raise ValueError(f"invalid period magnitude in {s!r}") from e


def _bullet_threshold_days(bullet_until: str) -> int:
    """Translate ``bullet_until`` (e.g. ``"1Y"``) into a calendar-day threshold.

    A pillar whose ``tenor_days`` is ``<= threshold`` is treated as a bullet payment.
    We use 366 days per declared year so 1Y pillars that span a leap day still count as
    bullets.
    """
    years = _parse_period_years(bullet_until)
    return 366 * years


def _add_months(d: date, n: int) -> date:
    """Add ``n`` calendar months to ``d``; clamp the day to the new month's last day.

    Examples: ``Jan 31 + 1M -> Feb 28/29``; ``Mar 31 + 1M -> Apr 30``.
    """
    total = d.month - 1 + n
    new_year = d.year + total // 12
    new_month = total % 12 + 1
    last = _stdlib_calendar.monthrange(new_year, new_month)[1]
    return date(new_year, new_month, min(d.day, last))


def _roll(d: date, cal: HolidayCalendar, conv: BusinessDayConvention) -> date:
    """Adjust ``d`` to a business day according to ``conv``.

    If ``d`` is already a business day or ``conv`` is NONE, returns ``d`` unchanged.
    """
    if conv is BusinessDayConvention.NONE or cal.is_business_day(d):
        return d
    if conv is BusinessDayConvention.FOLLOWING:
        return cal.add_business_days(d, 1)
    if conv is BusinessDayConvention.PRECEDING:
        return cal.add_business_days(d, -1)
    if conv is BusinessDayConvention.MODIFIED_FOLLOWING:
        nxt = cal.add_business_days(d, 1)
        if nxt.month != d.month:
            return cal.add_business_days(d, -1)
        return nxt
    if conv is BusinessDayConvention.MODIFIED_PRECEDING:
        prev = cal.add_business_days(d, -1)
        if prev.month != d.month:
            return cal.add_business_days(d, 1)
        return prev
    raise ValueError(f"unsupported business-day convention: {conv!r}")


def _resolve_currency_convention(conventions: Conventions, currency: str) -> CurrencyConvention:
    """Look up the per-currency OIS convention; raise if absent."""
    if currency not in conventions.ois_conventions:
        raise KeyError(
            f"no OIS convention configured for currency {currency!r}; "
            f"available: {sorted(conventions.ois_conventions)}"
        )
    return conventions.ois_conventions[currency]


# ---------------------------------------------------------------------------
# Quote resolution (F-006 / G13)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ResolvedQuote:
    """Internal record of a quote after fallback resolution."""

    quote: MarketQuote
    rate: float
    source_path: str  # which field/strategy supplied the rate (for diagnostics)


def _resolve_quote(q: MarketQuote, dg: DiagnosticsCollector) -> _ResolvedQuote | None:
    """Apply the F-006 fallback ladder; emit WARNs along the way.

    Returns ``None`` if no usable rate could be derived (caller emits BS_QUOTE_SKIPPED).
    """
    bid, ask, mid = q.bid, q.ask, q.mid

    # 1. Direct mid
    if mid is not None and math.isfinite(mid):
        return _ResolvedQuote(quote=q, rate=mid, source_path="mid")

    # 2. avg(bid, ask)
    if bid is not None and ask is not None and math.isfinite(bid) and math.isfinite(ask):
        if bid > ask:
            dg.warn(
                "BS_BID_GT_ASK",
                f"{q.tenor_code}: bid > ask ({bid} > {ask}); using avg as fallback",
                {"tenor_code": q.tenor_code, "bid": bid, "ask": ask},
            )
        else:
            dg.warn(
                "BS_QUOTE_FALLBACK_AVG",
                f"{q.tenor_code}: mid missing; using avg(bid, ask)",
                {"tenor_code": q.tenor_code, "bid": bid, "ask": ask},
            )
        return _ResolvedQuote(quote=q, rate=0.5 * (bid + ask), source_path="avg")

    # 3. ask alone
    if ask is not None and math.isfinite(ask):
        dg.warn(
            "BS_QUOTE_FALLBACK_ASK",
            f"{q.tenor_code}: mid and bid missing; using ask alone",
            {"tenor_code": q.tenor_code, "ask": ask},
        )
        return _ResolvedQuote(quote=q, rate=ask, source_path="ask")

    # 4. bid alone
    if bid is not None and math.isfinite(bid):
        dg.warn(
            "BS_QUOTE_FALLBACK_BID",
            f"{q.tenor_code}: mid and ask missing; using bid alone",
            {"tenor_code": q.tenor_code, "bid": bid},
        )
        return _ResolvedQuote(quote=q, rate=bid, source_path="bid")

    # 5. nothing usable — caller emits skip diagnostic
    return None


def _dedupe_collisions(
    quotes: list[_ResolvedQuote], dg: DiagnosticsCollector
) -> list[_ResolvedQuote]:
    """Collapse same-tenor_days collisions: keep the first (direct) quote.

    A WARN is emitted for each colliding quote, including the implied-vs-direct
    spread in basis points (per G4 / F-001 acceptance).
    """
    seen: dict[int, _ResolvedQuote] = {}
    out: list[_ResolvedQuote] = []
    for rq in quotes:
        key = rq.quote.tenor_days
        if key in seen:
            primary = seen[key]
            spread_bps = (rq.rate - primary.rate) * 1e4
            dg.warn(
                "BS_TENOR_COLLISION",
                (
                    f"tenor_days={key}: duplicate quote {rq.quote.tenor_code} "
                    f"shadowed by direct quote {primary.quote.tenor_code}; "
                    f"implied-vs-direct spread {spread_bps:+.4f} bps"
                ),
                {
                    "tenor_days": key,
                    "direct_tenor_code": primary.quote.tenor_code,
                    "shadowed_tenor_code": rq.quote.tenor_code,
                    "direct_rate": primary.rate,
                    "shadowed_rate": rq.rate,
                    "spread_bps": spread_bps,
                },
            )
            continue
        seen[key] = rq
        out.append(rq)
    return out


# ---------------------------------------------------------------------------
# Bootstrap math
# ---------------------------------------------------------------------------


def _bullet_df(rate: float, tau: float) -> float:
    """Closed-form bullet DF: ``DF = 1 / (1 + r * tau)``."""
    return 1.0 / (1.0 + rate * tau)


def _annual_coupon_schedule(
    val_date: date,
    maturity: date,
    cal: HolidayCalendar,
    bdc: BusinessDayConvention,
) -> list[date]:
    """Annual coupon dates from ``val_date`` to ``maturity`` (inclusive, ascending).

    Each ``val_date + kY`` is rolled per the business-day convention. The final entry is
    ``maturity`` itself, regardless of whether it aligns with an exact whole-year offset
    (i.e., maturity is appended explicitly if the last yearly-rolled date precedes it).
    """
    out: list[date] = []
    k = 1
    while True:
        candidate = _roll(_add_months(val_date, 12 * k), cal, bdc)
        if candidate >= maturity:
            break
        out.append(candidate)
        k += 1
    out.append(maturity)
    return out


def _coupons_aligned(coupon_dates: list[date], known_pillar_end_dates: set[date]) -> bool:
    """True iff every coupon date except the last (= maturity) matches a known pillar."""
    return all(c in known_pillar_end_dates for c in coupon_dates[:-1])


def _bootstrap_annual_closed_form(
    rate: float,
    coupon_dates: list[date],
    val_date: date,
    day_count: DayCount,
    df_by_date: dict[date, float],
) -> float:
    """Closed-form bootstrap when every intermediate coupon DF is already known.

    Solves ``R * sum(DF_i * tau_i) = 1 - DF_N`` for ``DF_N``.
    """
    s_known = 0.0
    prev = val_date
    for c in coupon_dates[:-1]:
        tau_i = year_fraction(prev, c, day_count)
        df_i = df_by_date[c]
        s_known += df_i * tau_i
        prev = c
    tau_n = year_fraction(prev, coupon_dates[-1], day_count)
    return (1.0 - rate * s_known) / (1.0 + rate * tau_n)


def _bootstrap_annual_brentq(
    rate: float,
    coupon_dates: list[date],
    val_date: date,
    day_count: DayCount,
    prior_pillars: list[Pillar],
) -> float:
    """Brentq fallback for irregular grids.

    Intermediate coupon DFs are interpolated log-linearly in DF using prior pillars
    plus a tentative log-linear segment from the last known pillar to the maturity
    trial DF. The unknown is ``DF_N`` (DF at the new pillar's maturity).
    """
    # Anchor: synthetic (val_date, 1.0)
    anchor_dates = [val_date] + [p.end_date for p in prior_pillars]
    anchor_dfs = [1.0] + [p.discount_factor for p in prior_pillars]

    def _df_among_known(d: date) -> float:
        # Linear scan; <=22 entries.
        for i in range(1, len(anchor_dates)):
            a_d, b_d = anchor_dates[i - 1], anchor_dates[i]
            if a_d <= d <= b_d:
                if d == a_d:
                    return anchor_dfs[i - 1]
                if d == b_d:
                    return anchor_dfs[i]
                w = (d - a_d).days / (b_d - a_d).days
                return math.exp(
                    (1.0 - w) * math.log(anchor_dfs[i - 1]) + w * math.log(anchor_dfs[i])
                )
        raise ValueError(f"brentq: coupon date {d} outside known anchor range")

    last_known_date = anchor_dates[-1]
    last_known_df = anchor_dfs[-1]
    maturity = coupon_dates[-1]

    def _df_trial(d: date, df_trial: float) -> float:
        if d <= last_known_date:
            return _df_among_known(d)
        if d == maturity:
            return df_trial
        # Log-linear between (last_known_date, last_known_df) and (maturity, df_trial)
        w = (d - last_known_date).days / (maturity - last_known_date).days
        return math.exp((1.0 - w) * math.log(last_known_df) + w * math.log(df_trial))

    def swap_pv(df_n: float) -> float:
        s_total = 0.0
        prev = val_date
        for c in coupon_dates:
            tau_i = year_fraction(prev, c, day_count)
            s_total += _df_trial(c, df_n) * tau_i
            prev = c
        fixed_pv = rate * s_total
        df_at_maturity = _df_trial(maturity, df_n)
        float_pv = 1.0 - df_at_maturity
        return fixed_pv - float_pv

    try:
        sol = brentq(
            swap_pv,
            _BRENTQ_DF_LO,
            _BRENTQ_DF_HI,
            xtol=BRENTQ_XTOL,
            rtol=BRENTQ_RTOL,
            maxiter=BRENTQ_MAXITER,
            full_output=False,
            disp=True,
        )
    except (ValueError, RuntimeError) as e:
        raise BootstrapNonConvergentError(
            f"brentq failed to bracket/converge for rate={rate}: {e}"
        ) from e
    return float(sol)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BootstrapNonConvergentError(RuntimeError):
    """Raised when brentq fails to converge for an irregular-grid pillar."""


class ZeroValidQuotesError(ValueError):
    """Raised when no usable quotes remain after fallback resolution."""


# ---------------------------------------------------------------------------
# Reprice validation
# ---------------------------------------------------------------------------


def _reprice_pillar(
    pillar: Pillar,
    val_date: date,
    day_count: DayCount,
    cal: HolidayCalendar,
    bdc: BusinessDayConvention,
    bullet_days: int,
    df_by_date: dict[date, float],
) -> float:
    """Reprice the pillar from its own DF (and the existing curve) and return |residual|.

    For bullet pillars the residual is ``|fixed_pv - float_pv|`` where both sides use
    the bootstrapped DF directly. For annual-coupon pillars the same formula is applied
    using the full coupon schedule, drawing intermediate DFs from ``df_by_date``.
    """
    if pillar.tenor_days <= bullet_days:
        tau = year_fraction(val_date, pillar.end_date, day_count)
        fixed_pv = pillar.rate * tau * pillar.discount_factor
        float_pv = 1.0 - pillar.discount_factor
        return abs(fixed_pv - float_pv)

    schedule = _annual_coupon_schedule(val_date, pillar.end_date, cal, bdc)
    s_total = 0.0
    prev = val_date
    for c in schedule:
        tau_i = year_fraction(prev, c, day_count)
        df_i = df_by_date.get(c)
        if df_i is None:
            # Intermediate coupon used brentq path: we don't have its exact DF cached.
            # Repricing for the reprice-test side uses log-linear DF interp among known
            # anchors — same as the bootstrap's brentq path did.
            df_i = _log_linear_df_among(
                c, val_date, df_by_date, pillar.end_date, pillar.discount_factor
            )
        s_total += df_i * tau_i
        prev = c
    fixed_pv = pillar.rate * s_total
    float_pv = 1.0 - pillar.discount_factor
    return abs(fixed_pv - float_pv)


def _log_linear_df_among(
    d: date,
    val_date: date,
    df_by_date: dict[date, float],
    final_end: date,
    final_df: float,
) -> float:
    """Log-linear DF interp among ``{val_date: 1.0} | df_by_date | {final_end: final_df}``."""
    knots_d = [val_date, *sorted(df_by_date.keys()), final_end]
    knots_df = [1.0, *(df_by_date[k] for k in sorted(df_by_date.keys())), final_df]
    for i in range(1, len(knots_d)):
        a_d, b_d = knots_d[i - 1], knots_d[i]
        if a_d <= d <= b_d:
            if d == a_d:
                return knots_df[i - 1]
            if d == b_d:
                return knots_df[i]
            w = (d - a_d).days / (b_d - a_d).days
            return math.exp((1.0 - w) * math.log(knots_df[i - 1]) + w * math.log(knots_df[i]))
    raise ValueError(f"date {d} outside known knot range during reprice")


# ---------------------------------------------------------------------------
# Diagnostic scans (F-006)
# ---------------------------------------------------------------------------


def _scan_non_monotone_df(pillars: list[Pillar], dg: DiagnosticsCollector) -> None:
    """Emit WARN ranges where DFs are non-monotone (DF should decrease with maturity)."""
    for i in range(1, len(pillars)):
        a, b = pillars[i - 1], pillars[i]
        if b.discount_factor > a.discount_factor:
            dg.warn(
                "BS_NON_MONOTONE_DF",
                (
                    f"non-monotone DF between {a.tenor_code} and {b.tenor_code}: "
                    f"{a.discount_factor:.10f} -> {b.discount_factor:.10f}"
                ),
                {
                    "left_tenor": a.tenor_code,
                    "right_tenor": b.tenor_code,
                    "left_df": a.discount_factor,
                    "right_df": b.discount_factor,
                },
            )


def _scan_negative_forwards(
    pillars: list[Pillar],
    val_date: date,
    day_count: DayCount,
    dg: DiagnosticsCollector,
) -> None:
    """Emit WARN for each pillar pair where the implied forward rate is negative."""
    anchors_d: list[date] = [val_date, *(p.end_date for p in pillars)]
    anchors_df: list[float] = [1.0, *(p.discount_factor for p in pillars)]
    for i in range(1, len(anchors_d)):
        a_d, b_d = anchors_d[i - 1], anchors_d[i]
        tau = year_fraction(a_d, b_d, day_count)
        if tau <= 0:
            continue
        fwd = (anchors_df[i - 1] / anchors_df[i] - 1.0) / tau
        if fwd < 0:
            # Segment i runs from anchors[i-1] to anchors[i]; the right-side pillar
            # is pillars[i-1] (i=1 -> first pillar, etc.). The left side is the
            # synthetic anchor (val_date) for i=1, else pillars[i-2].
            left_tenor = "anchor" if i == 1 else pillars[i - 2].tenor_code
            right_tenor = pillars[i - 1].tenor_code
            dg.warn(
                "BS_NEGATIVE_FORWARD",
                f"negative implied forward {fwd:.6f} between {a_d} and {b_d}",
                {
                    "left_date": a_d.isoformat(),
                    "right_date": b_d.isoformat(),
                    "left_tenor": left_tenor,
                    "right_tenor": right_tenor,
                    "forward_rate": fwd,
                },
            )


# ---------------------------------------------------------------------------
# Forward ladder dates
# ---------------------------------------------------------------------------


def _build_forward_ladder_dates(
    val_date: date, cal: HolidayCalendar, bdc: BusinessDayConvention
) -> tuple[tuple[str, date, date], ...]:
    """Construct (label, start_date, end_date) triples for the 15 canonical entries."""

    def m(n: int) -> date:
        return _roll(_add_months(val_date, n), cal, bdc)

    out: list[tuple[str, date, date]] = []
    for label in FORWARD_LADDER_LABELS:
        a_str, b_str = label.split("x")
        a, b = int(a_str), int(b_str)
        out.append((label, m(a), m(b)))
    return tuple(out)


# ---------------------------------------------------------------------------
# Public API (C-002)
# ---------------------------------------------------------------------------


def bootstrap_curve(
    market: MarketData,
    conventions: Conventions,
    calendar: HolidayCalendar,
    diagnostics: DiagnosticsCollector,
    interp: InterpolationScheme = "log_linear_df",
    *,
    currency: str = "TRY",
) -> OISCurve:
    """Bootstrap an :class:`OISCurve` from market quotes.

    Args:
        market:       Market snapshot. Must contain at least one usable pillar quote and
                      a ``valuation_date`` (or quotes from which it can be inferred).
        conventions:  Loaded conventions (per-currency day-count, payment, BDC).
        calendar:     Holiday calendar for the relevant currency.
        diagnostics:  Single mutable collector threaded by the orchestrator.
        interp:       Curve interpolation scheme stored on the returned OISCurve.
        currency:     Convention currency key (``"TRY"`` in V1).

    Returns:
        Frozen :class:`OISCurve`. Each pillar reprices to within ``REPRICE_TOLERANCE``
        absolute; this is enforced by emitting an ``BS_REPRICE_FAIL`` ERROR diagnostic
        when violated.

    Raises:
        ZeroValidQuotesError: When no quotes are usable after fallback resolution. The
            error is also reported as an ERROR diagnostic before the exception is
            raised; ``rates.app`` may catch it and continue per its abort policy.
    """
    ccy_conv = _resolve_currency_convention(conventions, currency)
    day_count = ccy_conv.day_count
    bdc = ccy_conv.business_day_convention
    bullet_days = _bullet_threshold_days(ccy_conv.payment.bullet_until)

    val_date = _resolve_valuation_date(market)

    # 1. Resolve quotes (mid/avg/ask/bid fallback)
    resolved: list[_ResolvedQuote] = []
    skipped: list[MarketQuote] = []
    for q in market.quotes:
        if q.tenor_days <= 0:
            # Filter out non-pillar rows (e.g. BISTTREF erroneously routed into quotes).
            continue
        r = _resolve_quote(q, diagnostics)
        if r is None:
            skipped.append(q)
            diagnostics.warn(
                "BS_QUOTE_SKIPPED",
                f"{q.tenor_code}: no usable bid/ask/mid; pillar skipped",
                {"tenor_code": q.tenor_code, "tenor_days": q.tenor_days},
            )
            continue
        resolved.append(r)

    if not resolved:
        msg = "no usable pillar quotes after fallback resolution"
        diagnostics.error(
            "BS_NO_VALID_QUOTES",
            msg,
            {"total_quotes": len(market.quotes), "skipped": len(skipped)},
        )
        raise ZeroValidQuotesError(msg)

    # 2. Sort by tenor_days; collisions get resolved (direct wins).
    resolved.sort(key=lambda r: (r.quote.tenor_days, r.quote.tenor_code))
    resolved = _dedupe_collisions(resolved, diagnostics)

    # 3. Bootstrap each pillar sequentially.
    pillars: list[Pillar] = []
    df_by_date: dict[date, float] = {}
    known_dates: set[date] = set()

    for rq in resolved:
        q = rq.quote
        end_date = q.end_date
        if rq.quote.tenor_days <= bullet_days:
            tau = year_fraction(val_date, end_date, day_count)
            df = _bullet_df(rq.rate, tau)
        else:
            schedule = _annual_coupon_schedule(val_date, end_date, calendar, bdc)
            if _coupons_aligned(schedule, known_dates):
                df = _bootstrap_annual_closed_form(
                    rq.rate, schedule, val_date, day_count, df_by_date
                )
            else:
                df = _bootstrap_annual_brentq(rq.rate, schedule, val_date, day_count, pillars)

        pillar = Pillar(
            tenor_code=q.tenor_code,
            tenor_days=q.tenor_days,
            start_date=val_date,  # G10: T+0 spot for all pillars
            end_date=end_date,
            rate=rq.rate,
            discount_factor=df,
        )
        pillars.append(pillar)
        df_by_date[end_date] = df
        known_dates.add(end_date)

    # 4. Validate reprice (1e-10 per F-001).
    for p in pillars:
        err = _reprice_pillar(p, val_date, day_count, calendar, bdc, bullet_days, df_by_date)
        if err > REPRICE_TOLERANCE:
            diagnostics.error(
                "BS_REPRICE_FAIL",
                (
                    f"{p.tenor_code}: reprice residual {err:.3e} exceeds tolerance "
                    f"{REPRICE_TOLERANCE:.0e}"
                ),
                {
                    "tenor_code": p.tenor_code,
                    "residual_abs": err,
                    "tolerance": REPRICE_TOLERANCE,
                },
            )

    # 5. Diagnostic scans (F-006).
    _scan_non_monotone_df(pillars, diagnostics)
    _scan_negative_forwards(pillars, val_date, day_count, diagnostics)

    # 6. Forward ladder geometry (calendar-rolled).
    ladder = _build_forward_ladder_dates(val_date, calendar, bdc)

    return OISCurve(
        valuation_date=val_date,
        day_count=day_count,
        interp=interp,
        pillars_tuple=tuple(pillars),
        forward_ladder_dates=ladder,
    )


def _resolve_valuation_date(market: MarketData) -> date:
    """Resolve the curve's valuation date from ``market``.

    Priority:
        1. ``market.valuation_date`` if set.
        2. Earliest ``quote.start_date`` across all quotes.

    Raises:
        ValueError: When no quotes are present and ``valuation_date`` is None.
    """
    if market.valuation_date is not None:
        return market.valuation_date
    pillar_quotes = [q for q in market.quotes if q.tenor_days > 0]
    if not pillar_quotes:
        raise ValueError(
            "cannot resolve valuation_date: MarketData has no valuation_date "
            "and no pillar quotes from which to infer one"
        )
    return min(q.start_date for q in pillar_quotes)


# Re-export for typing convenience.
__all__ = [
    "REPRICE_TOLERANCE",
    "BootstrapNonConvergentError",
    "ZeroValidQuotesError",
    "bootstrap_curve",
]
