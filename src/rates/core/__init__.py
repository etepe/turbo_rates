"""rates.core — pure math and foundation.

Modules in this package have NO IO dependencies. They are deterministic and explicitly
parameterised. External systems (filesystem, network) are reached only through rates.io.

Phase 1 modules (this commit):
    conventions  — config/conventions.yaml loader; per-currency day-count and payment rules.
    calendar     — holiday calendar (holidays package + CSV override).
    daycount     — pure day-count fraction functions.
    log          — log() indirection helper.
    diagnostics  — DiagnosticsCollector for WARN/ERROR records and exit-code mapping.
    types        — frozen input domain dataclasses (MarketData, MPCPath, ...).

Phase 2-3 modules (future commits): curve, bootstrap, scenario, report.
"""

from __future__ import annotations
