"""Phase 7 — FX pricer (M-107) scaffold tests."""

from __future__ import annotations

from datetime import date

import pytest

from rates.fx.pricer import price_fx_swap


@pytest.mark.phase7
def test_price_fx_swap_rejects_non_strict_ordering() -> None:
    """The argument-order check is in the stub, so this guards the contract today."""
    with pytest.raises(ValueError, match="near_date < far_date"):
        price_fx_swap(
            fx_forward=None,  # type: ignore[arg-type]
            near_date=date(2026, 6, 15),
            far_date=date(2026, 6, 15),
        )


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — price_outright_forward not yet implemented")
def test_outright_matches_curve_forward_at() -> None:
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — price_fx_swap not yet implemented")
def test_fx_swap_points_match_curve_difference() -> None:
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — price_xccy_basis_swap not yet implemented")
def test_xccy_basis_pricer_returns_curve_spread_at_maturity() -> None:
    raise NotImplementedError
