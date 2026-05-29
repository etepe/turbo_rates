"""rates.fx.bootstrap_forward — FX forward curve bootstrap (M-105).

Builds an :class:`FXForwardCurve` from spot + market forward-point quotes. The
consumer-facing pillars carry the **quoted** outright forward
(``S + points / forward_point_scale``), consumed verbatim.

**Parity reconciliation moved out of M-105 (A-6 / OQ-503).** The old pure-CIP
``FX_PARITY_MISMATCH`` WARN (quoted forward vs ``S·DF_for/DF_dom``) is gone: under
V0.4 Method (i) the basis is *defined* from the forward-vs-CIP deviation, so the
authoritative reconciliation is the **basis-aware** gate in ``rates.app`` —
forward-implied basis ``b_n`` vs the quoted XCCY_BASIS spread — which runs
post-strip once both curves exist (single gate, no double-reporting). M-105 no
longer reads the dual OIS curves; ``dom_ois`` / ``for_ois`` stay in the signature
only for contract stability (C-102 unchanged).

Quote resolution: ``mid`` → ``avg(bid, ask)`` → ``ask`` → ``bid`` → skip.
Quotes without a usable rate are dropped with ``FX_XCCY_QUOTE_SKIPPED`` (the
xccy code is reused here for any FX-side quote skip; semantically identical).

Contract: C-102 (consumer-facing); produces C-103 output.
"""

from __future__ import annotations

import math

from rates.core.curve import OISCurve
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.types import (
    FX_FWD_POINTS_NON_MONOTONE,
    FX_SPOT_MISSING,
    FX_XCCY_QUOTE_SKIPPED,
    FXForwardPointQuote,
    FXMarketData,
    QuoteConvention,
)

#: Reprice tolerance (absolute, on forward rate). Not used directly today
#: (quotes are taken verbatim) but kept for parity with V1 bootstrap_curve.
FX_FORWARD_REPRICE_TOLERANCE: float = 1e-9

#: Parity mismatch threshold in basis points. Consumed by the basis-aware
#: ``FX_PARITY_MISMATCH`` gate in ``rates.app`` (A-6): a WARN fires when the
#: forward-implied basis ``b_n`` and the quoted xccy spread differ by more bps.
PARITY_MISMATCH_BPS: float = 1.0


class FXSpotMissingError(KeyError):
    """Raised when :attr:`FXMarketData.spot` is absent or unusable."""


class FXBootstrapError(RuntimeError):
    """Raised when the FX forward bootstrap cannot produce a curve."""


def build_fx_forward_curve(
    market: FXMarketData,
    dom_ois: OISCurve,
    for_ois: OISCurve,
    fx_convention: FXConvention,
    diagnostics: DiagnosticsCollector,
) -> FXForwardCurve:
    """Bootstrap an :class:`FXForwardCurve` for the pair carried by ``market``.

    Args:
        market:         FX snapshot. Must carry spot + ≥1 forward-point quote.
        dom_ois:        Domestic-currency OIS curve — unused since A-6 moved the
                        parity reconciliation to the orchestrator; kept for C-102
                        signature stability.
        for_ois:        Foreign-currency OIS curve — unused, same rationale.
        fx_convention:  Per-pair FX convention.
        diagnostics:    Single mutable collector threaded by the orchestrator.

    Returns:
        Frozen :class:`FXForwardCurve` with one pillar per usable forward-point
        quote (ascending by settle_date). Quoted outright forwards are kept
        verbatim; parity reconciliation now happens post-strip in ``rates.app``.

    Raises:
        FXSpotMissingError: When spot has no usable bid/ask/mid.
        FXBootstrapError:   When no forward-point quotes remain after resolution.
    """
    spot_rate = _resolve_spot(market, diagnostics)
    if fx_convention.quote_convention is QuoteConvention.INDIRECT:
        # FX-O4 leaves embedding the spot rate as the canonical anchor. We
        # convert indirect quotes to direct internally so the curve always
        # exposes "domestic per foreign". V2-FX validators may relax this.
        spot_rate = 1.0 / spot_rate

    if not market.forward_points:
        msg = "FXMarketData carries no forward_points; cannot build curve"
        diagnostics.error(
            "FX_NO_FORWARD_QUOTES",
            msg,
            {"pair_code": market.spot.pair.code},
        )
        raise FXBootstrapError(msg)

    sorted_quotes = sorted(market.forward_points, key=lambda q: q.settle_date)
    _warn_non_monotone_tenor_days(sorted_quotes, diagnostics)

    from datetime import date as _date

    pillars: list[FXForwardPillar] = []
    seen_dates: set[_date] = set()
    scale = float(fx_convention.forward_point_scale)
    for q in sorted_quotes:
        if q.settle_date in seen_dates:
            # Already flagged by _warn_non_monotone_tenor_days above; keep the
            # first quote, drop later collisions to satisfy strictly-ascending
            # settle_date invariant on FXForwardCurve.
            continue
        rate = _resolve_forward_rate(q, spot_rate, scale, fx_convention, diagnostics)
        if rate is None:
            continue
        pillars.append(
            FXForwardPillar(
                tenor_code=q.tenor_code,
                tenor_days=q.tenor_days,
                settle_date=q.settle_date,
                forward_rate=rate,
            )
        )
        seen_dates.add(q.settle_date)

    if not pillars:
        msg = "no usable forward-point quotes after resolution"
        diagnostics.error(
            "FX_NO_FORWARD_QUOTES",
            msg,
            {"pair_code": market.spot.pair.code, "skipped": len(market.forward_points)},
        )
        raise FXBootstrapError(msg)

    return FXForwardCurve(
        pair_code=market.spot.pair.code,
        spot_date=market.spot.spot_date,
        spot_rate=spot_rate,
        pillars_tuple=tuple(pillars),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_spot(market: FXMarketData, dg: DiagnosticsCollector) -> float:
    """Return a usable spot rate, applying the mid/avg/ask/bid fallback."""
    spot = market.spot
    if spot.mid is not None and math.isfinite(spot.mid) and spot.mid > 0:
        return float(spot.mid)
    if (
        spot.bid is not None
        and spot.ask is not None
        and math.isfinite(spot.bid)
        and math.isfinite(spot.ask)
        and spot.bid > 0
        and spot.ask > 0
    ):
        return 0.5 * (spot.bid + spot.ask)
    if spot.ask is not None and math.isfinite(spot.ask) and spot.ask > 0:
        return float(spot.ask)
    if spot.bid is not None and math.isfinite(spot.bid) and spot.bid > 0:
        return float(spot.bid)
    msg = f"{spot.pair.code}: spot quote has no usable bid/ask/mid"
    dg.error(
        FX_SPOT_MISSING,
        msg,
        {"pair_code": spot.pair.code, "spot_date": spot.spot_date.isoformat()},
    )
    raise FXSpotMissingError(msg)


def _resolve_forward_rate(
    q: FXForwardPointQuote,
    spot_rate: float,
    scale: float,
    fx_conv: FXConvention,
    dg: DiagnosticsCollector,
) -> float | None:
    """Apply the mid/avg/ask/bid fallback ladder to ``q``; return an outright rate."""
    points: float | None
    if q.mid is not None and math.isfinite(q.mid):
        points = float(q.mid)
    elif (
        q.bid is not None and q.ask is not None and math.isfinite(q.bid) and math.isfinite(q.ask)
    ):
        points = 0.5 * (q.bid + q.ask)
    elif q.ask is not None and math.isfinite(q.ask):
        points = float(q.ask)
    elif q.bid is not None and math.isfinite(q.bid):
        points = float(q.bid)
    else:
        points = None

    if points is None:
        dg.warn(
            FX_XCCY_QUOTE_SKIPPED,
            f"{q.tenor_code}: no usable bid/ask/mid forward points; skipped",
            {"tenor_code": q.tenor_code, "settle_date": q.settle_date.isoformat()},
        )
        return None

    outright = spot_rate + points / scale
    if fx_conv.quote_convention is QuoteConvention.INDIRECT:
        # Quoted on the indirect convention; convert outright to direct to
        # match the curve's stored convention.
        return 1.0 / outright
    return outright


def _warn_non_monotone_tenor_days(
    quotes: list[FXForwardPointQuote], dg: DiagnosticsCollector
) -> None:
    """Detect forward-point tenor_days that are not strictly ascending."""
    for i in range(1, len(quotes)):
        if quotes[i].tenor_days <= quotes[i - 1].tenor_days:
            dg.warn(
                FX_FWD_POINTS_NON_MONOTONE,
                (
                    f"forward-point quotes not strictly ascending by tenor: "
                    f"{quotes[i - 1].tenor_code} -> {quotes[i].tenor_code}"
                ),
                {
                    "left_tenor": quotes[i - 1].tenor_code,
                    "right_tenor": quotes[i].tenor_code,
                    "left_tenor_days": quotes[i - 1].tenor_days,
                    "right_tenor_days": quotes[i].tenor_days,
                },
            )


__all__ = [
    "FX_FORWARD_REPRICE_TOLERANCE",
    "PARITY_MISMATCH_BPS",
    "FXBootstrapError",
    "FXSpotMissingError",
    "build_fx_forward_curve",
]
