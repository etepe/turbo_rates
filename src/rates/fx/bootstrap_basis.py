"""rates.fx.bootstrap_basis — cross-currency basis strip (M-106, V0.4).

Strips a :class:`CrossCurrencyBasisCurve` from a vector of par xccy basis-swap
quotes by **Method (i)** (D-11): each pillar is calibrated so a constant-notional
float-float xccy swap maturing at the quoted tenor re-prices to net PV = 0, with
the foreign leg's cashflows converted to domestic units at the **full FX forward
curve** and discounted on the **domestic OIS** curve.

The basis is therefore *forward-implied*: the XCCY_BASIS quote **values are not
strip inputs** — only their maturity grid and the ``quoted_on_foreign`` sign are
(architecture §5.4). A non-zero basis is exactly the forward-vs-CIP deviation
(§5.3): CIP-tight forwards strip to ``b ≈ 0``.

The strip is sequential (par→pillar): with ``b_1..b_{n-1}`` fixed, pillar ``n`` is
solved by ``scipy.optimize.brentq`` over a ±10000 bps bracket (net PV is linear in
``b_n`` with a non-zero slope, so the root is unique), then a reprice assert pins
``|NPV(b_n*)| < 1e-9`` (D-13); failure emits
:data:`rates.fx.types.FX_XCCY_REPRICE_FAIL` and the curve is not produced. The
brentq path mirrors V1's closed-form-then-brentq structure and keeps the seam
MtM-ready.

Notional normalization: ``N_dom = 1`` (hence ``N_for = 1/S``); ``N`` cancels out of
``b_n`` (it scales the base PV and the basis annuity identically). The day-count
(Act/360, both legs — D-14) and roll (modified-following — D-14) are fixed here;
only the joint calendars cross the seam (C-104').

Sign convention: :attr:`CrossCurrencyBasisQuote.quoted_on_foreign` selects which
leg carries the spread (foreign/USD leg by default, FX-O5). All input quotes must
agree (mixed ⇒ ERROR); the stripped curve carries the same flag. Adjacent
stripped pillars with opposing signs emit :data:`rates.fx.types.FX_BASIS_INVERTED`.

Contract: C-104' (consumer-facing, gains the two calendars); produces C-105 output.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date

from scipy.optimize import brentq

from rates.core.calendar import HolidayCalendar
from rates.core.curve import OISCurve
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import (
    FX_BASIS_INVERTED,
    FX_BOOTSTRAP_NON_CONVERGENT,
    FX_XCCY_FORWARD_COVERAGE,
    FX_XCCY_QUOTE_SKIPPED,
    FX_XCCY_REPRICE_FAIL,
    CrossCurrencyBasisQuote,
    FXMarketData,
)
from rates.fx.xccy_pv import XccyCouponTerm, build_xccy_leg_terms, net_pv

_BRACKET_BPS = 10000.0  # D-13: ±10000 bps brentq bracket.
_REPRICE_TOL_NPV = 1e-9  # D-13: matches FX_FORWARD_REPRICE_TOLERANCE.
# Below this |spread| (bps) a stripped pillar is numerically zero: the brentq
# solve leaves ~1e-11 bps noise on a true-zero basis, and opposing-sign noise
# must not fire a spurious FX_BASIS_INVERTED.
_SIGN_ZERO_EPS_BPS = 1e-6


class XccyBootstrapError(RuntimeError):
    """Raised when the xccy basis strip cannot produce a curve."""


class MixedQuotedLegError(ValueError):
    """Raised when xccy quotes disagree on ``quoted_on_foreign``."""


class FXBootstrapNonConvergentError(RuntimeError):
    """Raised when brentq cannot bracket/converge a pillar's basis."""


@dataclass(frozen=True, slots=True)
class _GridPillar:
    """A tenor-grid anchor derived from a usable xccy quote (value not used)."""

    tenor_code: str
    tenor_days: int
    maturity_date: date


def build_cross_basis_curve(
    market: FXMarketData,
    dom_ois: OISCurve,
    for_ois: OISCurve,
    fx_forward: FXForwardCurve,
    fx_convention: FXConvention,
    dom_calendar: HolidayCalendar,
    for_calendar: HolidayCalendar,
    diagnostics: DiagnosticsCollector,
) -> CrossCurrencyBasisCurve:
    """Strip a :class:`CrossCurrencyBasisCurve` for ``market.basis_quotes`` (C-104').

    Args:
        market:         FX snapshot. Must carry ≥1 xccy basis quote.
        dom_ois:        Domestic-currency OIS curve (projection + discount).
        for_ois:        Foreign-currency OIS curve (projection + discount).
        fx_forward:     Bootstrapped FX forward curve; must cover the longest
                        xccy coupon date (no silent extrapolation, OQ-505).
        fx_convention:  Per-pair FX convention.
        dom_calendar:   Domestic holiday calendar (joint roll, C-109).
        for_calendar:   Foreign holiday calendar (joint roll, C-109).
        diagnostics:    Single mutable collector threaded by the orchestrator.

    Returns:
        Frozen :class:`CrossCurrencyBasisCurve` whose pillars carry the
        **stripped** (forward-implied) term-structure spreads in bps, ascending
        by ``maturity_date``. ``quoted_on_foreign`` is taken from the inputs.

    Raises:
        XccyBootstrapError:           No usable quotes; FX forward coverage too
                                      short (FX_XCCY_FORWARD_COVERAGE); or a
                                      reprice residual ≥ 1e-9 (FX_XCCY_REPRICE_FAIL).
        MixedQuotedLegError:          Quotes disagree on ``quoted_on_foreign``.
        FXBootstrapNonConvergentError: brentq cannot bracket/converge a pillar.
    """
    if not market.basis_quotes:
        msg = "FXMarketData carries no basis_quotes; cannot build basis curve"
        diagnostics.error("FX_NO_BASIS_QUOTES", msg, {"pair_code": market.spot.pair.code})
        raise XccyBootstrapError(msg)

    quoted_on_foreign = _resolve_sign_convention(market.basis_quotes, diagnostics)
    grid = _resolve_pillar_grid(market, diagnostics)
    if not grid:
        msg = "no usable xccy basis quotes after resolution"
        diagnostics.error(
            "FX_NO_BASIS_QUOTES",
            msg,
            {"pair_code": market.spot.pair.code, "skipped": len(market.basis_quotes)},
        )
        raise XccyBootstrapError(msg)

    _check_forward_coverage(grid, fx_forward, diagnostics)

    spot_date = market.spot.spot_date
    spot_rate = fx_forward.spot_rate
    grid_maturities = [g.maturity_date for g in grid]

    pillars: list[BasisPillar] = []
    solved_bps: list[float] = []
    for n, g in enumerate(grid, start=1):
        schedule, terms, pv_for0 = build_xccy_leg_terms(
            spot_date,
            g.maturity_date,
            dom_ois,
            for_ois,
            fx_forward,
            spot_rate,
            dom_calendar,
            for_calendar,
        )
        # 1-based piecewise-flat basis bucket per coupon (was the _CouponTerm
        # ``bucket`` field; now strip-local since the shared kernel is bucket-
        # agnostic — it takes an explicit per-coupon spread vector, C-112).
        buckets = [bisect_left(grid_maturities, c) + 1 for c in schedule.coupon_dates]
        b_n_bps = _solve_pillar(
            n=n,
            terms=terms,
            buckets=buckets,
            pv_for0=pv_for0,
            spot_rate=spot_rate,
            quoted_on_foreign=quoted_on_foreign,
            solved_bps=solved_bps,
            tenor_code=g.tenor_code,
            diagnostics=diagnostics,
        )
        solved_bps.append(b_n_bps)
        pillars.append(
            BasisPillar(
                tenor_code=g.tenor_code,
                tenor_days=g.tenor_days,
                maturity_date=g.maturity_date,
                spread_bps=b_n_bps,
            )
        )

    _warn_sign_inversions(pillars, diagnostics)

    return CrossCurrencyBasisCurve(
        pair_code=market.spot.pair.code,
        spot_date=spot_date,
        pillars_tuple=tuple(pillars),
        quoted_on_foreign=quoted_on_foreign,
    )


# ---------------------------------------------------------------------------
# Strip mechanics (§5)
# ---------------------------------------------------------------------------


def _solve_pillar(
    *,
    n: int,
    terms: list[XccyCouponTerm],
    buckets: list[int],
    pv_for0: float,
    spot_rate: float,
    quoted_on_foreign: bool,
    solved_bps: list[float],
    tenor_code: str,
    diagnostics: DiagnosticsCollector,
) -> float:
    """brentq-solve pillar ``n`` over ±10000 bps; reprice-assert |NPV| < 1e-9.

    Coupons in bucket ``n`` carry the trial ``b_n``; earlier buckets carry the
    already-solved spreads. The shared kernel (M-114 / C-112) evaluates the net
    PV from the resulting per-coupon spread vector.
    """

    def npv(trial_bps: float) -> float:
        spread_vec = [trial_bps if bk == n else solved_bps[bk - 1] for bk in buckets]
        return net_pv(
            terms,
            pv_for0,
            spot_rate,
            quoted_on_foreign=quoted_on_foreign,
            spread_bps_per_coupon=spread_vec,
        )

    try:
        b_star = float(brentq(npv, -_BRACKET_BPS, _BRACKET_BPS, xtol=1e-12, rtol=1e-14))
    except ValueError as e:
        msg = (
            f"{tenor_code}: brentq could not bracket the basis within "
            f"±{_BRACKET_BPS:.0f} bps ({e})"
        )
        diagnostics.error(
            FX_BOOTSTRAP_NON_CONVERGENT,
            msg,
            {"tenor_code": tenor_code, "bracket_bps": _BRACKET_BPS},
        )
        raise FXBootstrapNonConvergentError(msg) from e

    residual = abs(npv(b_star))
    if residual >= _REPRICE_TOL_NPV:
        msg = (
            f"{tenor_code}: xccy reprice residual {residual:.3e} ≥ {_REPRICE_TOL_NPV:.0e} "
            f"after solving b={b_star:+.4f} bps"
        )
        diagnostics.error(
            FX_XCCY_REPRICE_FAIL,
            msg,
            {"tenor_code": tenor_code, "residual": residual, "spread_bps": b_star},
        )
        raise XccyBootstrapError(msg)

    return b_star


def _check_forward_coverage(
    grid: list[_GridPillar],
    fx_forward: FXForwardCurve,
    dg: DiagnosticsCollector,
) -> None:
    """OQ-505: abort if the FX forward curve cannot reach the longest coupon date."""
    longest = grid[-1].maturity_date
    last_settle = fx_forward.pillars_tuple[-1].settle_date
    if longest > last_settle:
        msg = (
            f"FX forward curve last pillar {last_settle.isoformat()} precedes the longest "
            f"xccy coupon date {longest.isoformat()}; refusing to extrapolate"
        )
        dg.error(
            FX_XCCY_FORWARD_COVERAGE,
            msg,
            {
                "longest_coupon": longest.isoformat(),
                "last_forward_settle": last_settle.isoformat(),
            },
        )
        raise XccyBootstrapError(msg)


# ---------------------------------------------------------------------------
# Quote-grid resolution (tenor + sign only — §5.4)
# ---------------------------------------------------------------------------


def _resolve_pillar_grid(
    market: FXMarketData, dg: DiagnosticsCollector
) -> list[_GridPillar]:
    """Build the ascending pillar maturity grid from usable quotes.

    Non-finite quotes and maturity-collisions are skipped with
    :data:`FX_XCCY_QUOTE_SKIPPED`. Only the maturity + tenor identity is kept;
    the quote *value* is not a strip input (§5.4).
    """
    grid: list[_GridPillar] = []
    seen_dates: set[date] = set()
    for q in sorted(market.basis_quotes, key=lambda q: q.maturity_date):
        if not math.isfinite(q.spread_bps):
            dg.warn(
                FX_XCCY_QUOTE_SKIPPED,
                f"{q.tenor_code}: spread_bps is not finite; skipped",
                {
                    "tenor_code": q.tenor_code,
                    "maturity_date": q.maturity_date.isoformat(),
                    "spread_bps": q.spread_bps,
                },
            )
            continue
        if q.maturity_date in seen_dates:
            dg.warn(
                FX_XCCY_QUOTE_SKIPPED,
                (
                    f"{q.tenor_code}: maturity {q.maturity_date.isoformat()} "
                    "already covered by an earlier quote; duplicate dropped"
                ),
                {"tenor_code": q.tenor_code, "maturity_date": q.maturity_date.isoformat()},
            )
            continue
        grid.append(
            _GridPillar(
                tenor_code=q.tenor_code,
                tenor_days=(q.maturity_date - market.spot.spot_date).days,
                maturity_date=q.maturity_date,
            )
        )
        seen_dates.add(q.maturity_date)
    return grid


def _resolve_sign_convention(
    quotes: tuple[CrossCurrencyBasisQuote, ...], dg: DiagnosticsCollector
) -> bool:
    """Verify every quote agrees on ``quoted_on_foreign``; return the value."""
    first = quotes[0].quoted_on_foreign
    mismatched = [q for q in quotes if q.quoted_on_foreign is not first]
    if mismatched:
        msg = (
            "xccy basis quotes disagree on quoted_on_foreign; "
            f"first={first}, mismatched={len(mismatched)}"
        )
        dg.error(
            "FX_BASIS_MIXED_QUOTED_LEG",
            msg,
            {
                "first_quoted_on_foreign": first,
                "mismatched_tenor_codes": [q.tenor_code for q in mismatched],
            },
        )
        raise MixedQuotedLegError(msg)
    return first


def _warn_sign_inversions(pillars: list[BasisPillar], dg: DiagnosticsCollector) -> None:
    """Emit ``FX_BASIS_INVERTED`` for adjacent stripped pillars of opposing sign.

    Numerically-zero spreads (``|b| < _SIGN_ZERO_EPS_BPS``) are treated as
    same-sign as the neighbour to avoid noisy WARNs around the zero-crossing
    edge case (and the brentq noise on a true-zero basis).
    """
    for i in range(1, len(pillars)):
        left = pillars[i - 1].spread_bps
        right = pillars[i].spread_bps
        if abs(left) < _SIGN_ZERO_EPS_BPS or abs(right) < _SIGN_ZERO_EPS_BPS:
            continue
        if left * right < 0.0:
            dg.warn(
                FX_BASIS_INVERTED,
                (
                    f"basis spread changes sign between {pillars[i - 1].tenor_code} "
                    f"({left:+.2f}) and {pillars[i].tenor_code} ({right:+.2f})"
                ),
                {
                    "left_tenor": pillars[i - 1].tenor_code,
                    "right_tenor": pillars[i].tenor_code,
                    "left_spread_bps": left,
                    "right_spread_bps": right,
                },
            )


__all__ = [
    "FXBootstrapNonConvergentError",
    "MixedQuotedLegError",
    "XccyBootstrapError",
    "build_cross_basis_curve",
]
