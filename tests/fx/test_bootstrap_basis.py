"""Phase 7 — FX cross-currency basis bootstrap (M-106) scaffold tests."""

from __future__ import annotations

import pytest


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_cross_basis_curve not yet implemented")
def test_bootstrap_emits_one_pillar_per_basis_quote() -> None:
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_cross_basis_curve not yet implemented")
def test_basis_inverted_warns_on_sign_flip_between_pillars() -> None:
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — build_cross_basis_curve not yet implemented")
def test_xccy_quote_skipped_warns_on_unusable_legs() -> None:
    raise NotImplementedError
