"""rates.fx.bootstrap_basis — cross-currency basis bootstrap (M-106).

Strips a :class:`CrossCurrencyBasisCurve` from a vector of xccy par-swap
basis quotes, using the dual OIS curves and the already-bootstrapped FX
forward curve as anchors. The basis is a per-tenor spread (bps) that, when
added to the leg quoted on (FX-O5: USD-leg by default), re-prices the xccy
swap to par.

V2 takes quoted spreads verbatim and surfaces consistency issues as
diagnostics (mirroring the M-105 "quoted forwards held verbatim"
discipline). A full stripping calibration that re-prices each xccy swap to
1e-9 against the dual OIS curves + FX forward curve is deferred to V3 —
the seam is in place so the bootstrap entry point and contract stay stable.

Sign convention: :attr:`CrossCurrencyBasisQuote.quoted_on_foreign` carries
the quote's understanding; the bootstrap requires every input quote to
agree (mixed quotes ⇒ ERROR) and writes the resulting curve with the same
flag. Adjacent pillars with opposing signs emit
:data:`rates.fx.types.FX_BASIS_INVERTED` WARN.

Contract: C-104 (consumer-facing); produces C-105 output.
"""

from __future__ import annotations

import math
from datetime import date

from rates.core.curve import OISCurve
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import (
    FX_BASIS_INVERTED,
    FX_XCCY_QUOTE_SKIPPED,
    CrossCurrencyBasisQuote,
    FXMarketData,
)


class XccyBootstrapError(RuntimeError):
    """Raised when the xccy basis bootstrap cannot produce a curve."""


class MixedQuotedLegError(ValueError):
    """Raised when xccy quotes disagree on ``quoted_on_foreign``."""


def build_cross_basis_curve(
    market: FXMarketData,
    dom_ois: OISCurve,
    for_ois: OISCurve,
    fx_forward: FXForwardCurve,
    fx_convention: FXConvention,
    diagnostics: DiagnosticsCollector,
) -> CrossCurrencyBasisCurve:
    """Bootstrap a :class:`CrossCurrencyBasisCurve` for ``market.basis_quotes``.

    Args:
        market:         FX snapshot. Must carry ≥1 xccy basis quote.
        dom_ois:        Domestic-currency OIS curve.
        for_ois:        Foreign-currency OIS curve.
        fx_forward:     Already-bootstrapped FX forward curve for the pair.
        fx_convention:  Per-pair FX convention.
        diagnostics:    Single mutable collector threaded by the orchestrator.

    Returns:
        Frozen :class:`CrossCurrencyBasisCurve` with one pillar per usable
        xccy quote (ascending by maturity_date). Sign convention is taken
        from the input quotes; emit
        :data:`rates.fx.types.FX_BASIS_INVERTED` WARN for adjacent pillars
        with opposing signs.

    Raises:
        XccyBootstrapError:  When no usable xccy quotes remain.
        MixedQuotedLegError: When quotes disagree on ``quoted_on_foreign``.
    """
    # The OIS curves and FX forward curve are accepted today so the contract
    # surface is stable; V3 stripping calibration will consume them. Reference
    # them to satisfy ruff's unused-arg lint (B007) without altering the seam.
    _ = (dom_ois, for_ois, fx_forward, fx_convention)

    if not market.basis_quotes:
        msg = "FXMarketData carries no basis_quotes; cannot build basis curve"
        diagnostics.error(
            "FX_NO_BASIS_QUOTES",
            msg,
            {"pair_code": market.spot.pair.code},
        )
        raise XccyBootstrapError(msg)

    quoted_on_foreign = _resolve_sign_convention(market.basis_quotes, diagnostics)

    sorted_quotes = sorted(market.basis_quotes, key=lambda q: q.maturity_date)

    pillars: list[BasisPillar] = []
    seen_dates: set[date] = set()
    for q in sorted_quotes:
        if not math.isfinite(q.spread_bps):
            diagnostics.warn(
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
            # Maturity collision: keep the first quote, drop later duplicates
            # so the CrossCurrencyBasisCurve strictly-ascending invariant holds.
            diagnostics.warn(
                FX_XCCY_QUOTE_SKIPPED,
                (
                    f"{q.tenor_code}: maturity {q.maturity_date.isoformat()} "
                    "already covered by an earlier quote; duplicate dropped"
                ),
                {
                    "tenor_code": q.tenor_code,
                    "maturity_date": q.maturity_date.isoformat(),
                },
            )
            continue
        tenor_days = (q.maturity_date - market.spot.spot_date).days
        pillars.append(
            BasisPillar(
                tenor_code=q.tenor_code,
                tenor_days=tenor_days,
                maturity_date=q.maturity_date,
                spread_bps=q.spread_bps,
            )
        )
        seen_dates.add(q.maturity_date)

    if not pillars:
        msg = "no usable xccy basis quotes after resolution"
        diagnostics.error(
            "FX_NO_BASIS_QUOTES",
            msg,
            {
                "pair_code": market.spot.pair.code,
                "skipped": len(market.basis_quotes),
            },
        )
        raise XccyBootstrapError(msg)

    _warn_sign_inversions(pillars, diagnostics)

    return CrossCurrencyBasisCurve(
        pair_code=market.spot.pair.code,
        spot_date=market.spot.spot_date,
        pillars_tuple=tuple(pillars),
        quoted_on_foreign=quoted_on_foreign,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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


def _warn_sign_inversions(
    pillars: list[BasisPillar], dg: DiagnosticsCollector
) -> None:
    """Emit ``FX_BASIS_INVERTED`` for each adjacent pillar pair whose spreads
    have opposing strict signs (zero spreads are treated as same-sign as the
    neighbour to avoid noisy WARNs around the zero-crossing edge case)."""
    for i in range(1, len(pillars)):
        left = pillars[i - 1].spread_bps
        right = pillars[i].spread_bps
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
    "MixedQuotedLegError",
    "XccyBootstrapError",
    "build_cross_basis_curve",
]
