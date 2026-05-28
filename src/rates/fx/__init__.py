"""rates.fx — V2 FX layer (scaffold).

This package holds the V2 FX layer: forward curve, cross-currency basis curve,
their bootstraps, and the pricer. All numerical bodies currently raise
``NotImplementedError("V2")``. See ``docs/fx-architecture.md`` for module
breakdown, contracts (C-101..C-108), and diagnostic codes.

The layer consumes ``rates.core`` (OISCurve, Conventions, DiagnosticsCollector)
unchanged. It never modifies ``rates.core``.
"""

from __future__ import annotations

__all__: list[str] = []
