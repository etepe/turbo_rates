"""Phase 7 — FX forward bootstrap (M-105) scaffold tests (skipped until V2)."""

from __future__ import annotations

import pytest


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_fx_forward_curve not yet implemented")
def test_bootstrap_emits_one_pillar_per_forward_point() -> None:
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_fx_forward_curve not yet implemented")
def test_parity_mismatch_warns_when_implied_differs_from_quoted() -> None:
    """When |implied parity forward - quoted forward| > 1 bp, emit FX_PARITY_MISMATCH."""
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_fx_forward_curve not yet implemented")
def test_missing_spot_raises_fx_spot_missing() -> None:
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_fx_forward_curve not yet implemented")
def test_non_monotone_forward_points_warns() -> None:
    raise NotImplementedError
