"""rates.io — adapters: providers, schemas, persistence.

This package is intentionally empty in Phase 1; real modules arrive in Phase 4 per the
build order in docs/architecture.md (M-010 io.market, M-011 io.mpc, M-012 io.persistence,
M-013 io.schemas).

Layering rule: rates.io may import from rates.core; rates.core may NOT import from
rates.io. Domain input types (MarketData, MPCPath, etc.) live in rates.core.types so that
this rule holds.
"""

from __future__ import annotations
