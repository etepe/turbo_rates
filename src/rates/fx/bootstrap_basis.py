"""rates.fx.bootstrap_basis — cross-currency basis bootstrap (M-106, V2 scaffold).

Strips a :class:`CrossCurrencyBasisCurve` from a vector of xccy par-swap
basis quotes, using the dual OIS curves and the already-bootstrapped FX
forward curve as anchors. The basis is a per-tenor spread (bps) that, when
added to the leg quoted on (FX-O5: USD-leg by default), re-prices the xccy
swap to par.

Sign convention: ``CrossCurrencyBasisQuote.quoted_on_foreign`` carries the
quote's understanding; the bootstrap respects it and writes the resulting
curve with the same flag.

Contract: C-104 (consumer-facing); produces C-105 output.
"""

from __future__ import annotations

from rates.core.curve import OISCurve
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.basis_curve import CrossCurrencyBasisCurve
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import FXMarketData


class XccyBootstrapError(RuntimeError):
    """Raised when the xccy basis bootstrap cannot produce a curve."""


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
        Frozen :class:`CrossCurrencyBasisCurve`. Sign convention is taken from
        the input quotes; emit :data:`rates.fx.types.FX_BASIS_INVERTED` WARN
        for adjacent pillars with opposing signs.

    Raises:
        XccyBootstrapError: When no usable xccy quotes remain.
        NotImplementedError: V2 — implementation deferred.
    """
    _ = (market, dom_ois, for_ois, fx_forward, fx_convention, diagnostics)
    raise NotImplementedError("V2: build_cross_basis_curve")


__all__ = ["XccyBootstrapError", "build_cross_basis_curve"]
