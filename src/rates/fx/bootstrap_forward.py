"""rates.fx.bootstrap_forward — FX forward curve bootstrap (M-105, V2 scaffold).

Builds an :class:`FXForwardCurve` from spot + market forward-point quotes
under covered interest parity::

    F(t) = S * DF_for(t) / DF_dom(t)

The dual-OIS-curve seam is internal: the consumer of :class:`FXForwardCurve`
sees only ``forward_at(settle_date)``. Mismatches between the implied parity
forward and the quoted forward emit ``FX_PARITY_MISMATCH`` WARN per pillar
when the absolute difference exceeds 1 bp.

Contract: C-102 (consumer-facing); produces C-103 output.
"""

from __future__ import annotations

from rates.core.curve import OISCurve
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import FXMarketData

#: Reprice tolerance (absolute, on forward rate).
FX_FORWARD_REPRICE_TOLERANCE: float = 1e-9


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
        dom_ois:        Domestic-currency OIS curve (e.g. TRY OIS for USDTRY).
        for_ois:        Foreign-currency OIS curve (e.g. USD OIS for USDTRY).
        fx_convention:  Per-pair FX convention.
        diagnostics:    Single mutable collector threaded by the orchestrator.

    Returns:
        Frozen :class:`FXForwardCurve` with one pillar per forward-point quote.
        Each pillar's reprice residual against covered interest parity is below
        ``FX_FORWARD_REPRICE_TOLERANCE``; violations emit
        :data:`rates.fx.types.FX_PARITY_MISMATCH` WARNs.

    Raises:
        FXSpotMissingError: When spot is absent or unusable.
        FXBootstrapError:   When no usable forward-point quotes remain.
        NotImplementedError: V2 — implementation deferred.
    """
    _ = (market, dom_ois, for_ois, fx_convention, diagnostics)
    raise NotImplementedError("V2: build_fx_forward_curve")


__all__ = [
    "FX_FORWARD_REPRICE_TOLERANCE",
    "FXBootstrapError",
    "FXSpotMissingError",
    "build_fx_forward_curve",
]
