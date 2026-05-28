"""Bootstrap determinism (F-001 acceptance: byte-identical across runs).

Two independent calls on identical input must produce identical pillar DFs (no
hash-order, floating-point summation, or scipy-iteration nondeterminism leaks).
"""

from __future__ import annotations

import pytest

from rates.core.bootstrap import bootstrap_curve
from rates.core.diagnostics import DiagnosticsCollector


@pytest.mark.phase2
def test_aligned_annual_byte_identical(aligned_annual_market, conventions, tr_calendar):
    """Two runs on the aligned-annual fixture produce byte-identical DFs and rates."""
    dg1 = DiagnosticsCollector()
    dg2 = DiagnosticsCollector()

    c1 = bootstrap_curve(aligned_annual_market, conventions, tr_calendar, dg1)
    c2 = bootstrap_curve(aligned_annual_market, conventions, tr_calendar, dg2)

    dfs1 = [p.discount_factor for p in c1.pillars_tuple]
    dfs2 = [p.discount_factor for p in c2.pillars_tuple]
    rates1 = [p.rate for p in c1.pillars_tuple]
    rates2 = [p.rate for p in c2.pillars_tuple]

    # Use bit-exact equality, not approx.
    assert dfs1 == dfs2
    assert rates1 == rates2


@pytest.mark.phase2
def test_irregular_brentq_byte_identical(irregular_market, conventions, tr_calendar):
    """Brentq fallback path is also deterministic across runs."""
    dg1 = DiagnosticsCollector()
    dg2 = DiagnosticsCollector()
    c1 = bootstrap_curve(irregular_market, conventions, tr_calendar, dg1)
    c2 = bootstrap_curve(irregular_market, conventions, tr_calendar, dg2)
    dfs1 = [p.discount_factor for p in c1.pillars_tuple]
    dfs2 = [p.discount_factor for p in c2.pillars_tuple]
    assert dfs1 == dfs2
