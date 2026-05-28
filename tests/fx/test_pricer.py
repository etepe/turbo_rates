"""Phase 7 — FX pricer (M-107) tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rates.core.curve import OISCurve, Pillar
from rates.core.types import DayCount
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.pricer import (
    price_fx_swap,
    price_outright_forward,
    price_xccy_basis_swap,
)
from rates.fx.types import FXSwapPricing

_VAL = date(2026, 6, 12)
_SPOT_DATE = date(2026, 6, 15)


# ---------------------------------------------------------------------------
# Fixture helpers — minimal curves built directly (no bootstrap).
# ---------------------------------------------------------------------------


def _fx_forward(spot: float = 32.5) -> FXForwardCurve:
    return FXForwardCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        spot_rate=spot,
        pillars_tuple=(
            FXForwardPillar(
                tenor_code="USDTRY30D",
                tenor_days=30,
                settle_date=_SPOT_DATE + timedelta(days=30),
                forward_rate=spot * 1.01,
            ),
            FXForwardPillar(
                tenor_code="USDTRY90D",
                tenor_days=90,
                settle_date=_SPOT_DATE + timedelta(days=90),
                forward_rate=spot * 1.03,
            ),
            FXForwardPillar(
                tenor_code="USDTRY180D",
                tenor_days=180,
                settle_date=_SPOT_DATE + timedelta(days=180),
                forward_rate=spot * 1.06,
            ),
        ),
    )


def _basis_curve() -> CrossCurrencyBasisCurve:
    return CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        pillars_tuple=(
            BasisPillar(
                tenor_code="USDTRY90D-XCCY",
                tenor_days=90,
                maturity_date=_SPOT_DATE + timedelta(days=90),
                spread_bps=-25.0,
            ),
            BasisPillar(
                tenor_code="USDTRY180D-XCCY",
                tenor_days=180,
                maturity_date=_SPOT_DATE + timedelta(days=180),
                spread_bps=-30.0,
            ),
        ),
        quoted_on_foreign=True,
    )


def _flat_ois(rate: float = 0.45) -> OISCurve:
    pillars: list[Pillar] = []
    for d in (30, 90, 180, 400):
        end = _VAL + timedelta(days=d)
        tau = d / 360.0
        df = 1.0 / (1.0 + rate * tau)
        pillars.append(
            Pillar(
                tenor_code=f"FLAT{d}D",
                tenor_days=d,
                start_date=_VAL,
                end_date=end,
                rate=rate,
                discount_factor=df,
            )
        )
    return OISCurve(
        valuation_date=_VAL,
        day_count=DayCount.ACT_360,
        interp="log_linear_df",
        pillars_tuple=tuple(pillars),
        forward_ladder_dates=(),
    )


# ---------------------------------------------------------------------------
# Argument validation (kept from scaffold)
# ---------------------------------------------------------------------------


@pytest.mark.phase7
def test_price_fx_swap_rejects_non_strict_ordering() -> None:
    with pytest.raises(ValueError, match="near_date < far_date"):
        price_fx_swap(
            fx_forward=None,  # type: ignore[arg-type]
            near_date=date(2026, 6, 15),
            far_date=date(2026, 6, 15),
        )


# ---------------------------------------------------------------------------
# Outright forward (C-106)
# ---------------------------------------------------------------------------


@pytest.mark.phase7
def test_outright_matches_curve_forward_at() -> None:
    curve = _fx_forward()
    for offset in (0, 15, 30, 60, 90, 150, 180):
        d = _SPOT_DATE + timedelta(days=offset)
        assert price_outright_forward(curve, d) == curve.forward_at(d)


@pytest.mark.phase7
def test_outright_propagates_date_out_of_range() -> None:
    curve = _fx_forward()
    with pytest.raises(ValueError, match="DateOutOfRange"):
        price_outright_forward(curve, date(2026, 6, 14))
    with pytest.raises(ValueError, match="DateOutOfRange"):
        price_outright_forward(curve, date(2099, 1, 1))


@pytest.mark.phase7
def test_outright_at_spot_returns_spot_rate() -> None:
    curve = _fx_forward(spot=32.5)
    assert price_outright_forward(curve, _SPOT_DATE) == 32.5


# ---------------------------------------------------------------------------
# FX swap (C-107)
# ---------------------------------------------------------------------------


@pytest.mark.phase7
def test_fx_swap_points_match_curve_difference() -> None:
    curve = _fx_forward()
    near = _SPOT_DATE + timedelta(days=30)
    far = _SPOT_DATE + timedelta(days=90)
    pricing = price_fx_swap(curve, near, far)
    assert isinstance(pricing, FXSwapPricing)
    assert pricing.near_rate == curve.forward_at(near)
    assert pricing.far_rate == curve.forward_at(far)
    assert pricing.swap_points == pytest.approx(
        curve.forward_at(far) - curve.forward_at(near), abs=1e-15
    )


@pytest.mark.phase7
def test_fx_swap_context_carries_spot_and_offsets() -> None:
    curve = _fx_forward(spot=32.5)
    near = _SPOT_DATE + timedelta(days=30)
    far = _SPOT_DATE + timedelta(days=90)
    pricing = price_fx_swap(curve, near, far)
    assert pricing.context["spot_rate"] == 32.5
    assert pricing.context["near_offset_days"] == 30.0
    assert pricing.context["far_offset_days"] == 90.0


@pytest.mark.phase7
def test_fx_swap_near_eq_spot_is_allowed() -> None:
    """Spot-far swap: near at spot, far at some forward pillar."""
    curve = _fx_forward()
    pricing = price_fx_swap(curve, _SPOT_DATE, _SPOT_DATE + timedelta(days=30))
    assert pricing.near_rate == curve.spot_rate
    assert pricing.swap_points > 0  # forward > spot in this fixture


@pytest.mark.phase7
def test_fx_swap_propagates_date_out_of_range() -> None:
    curve = _fx_forward()
    with pytest.raises(ValueError, match="DateOutOfRange"):
        price_fx_swap(curve, _SPOT_DATE + timedelta(days=15), date(2099, 1, 1))
    with pytest.raises(ValueError, match="DateOutOfRange"):
        price_fx_swap(curve, date(2026, 6, 14), _SPOT_DATE + timedelta(days=30))


# ---------------------------------------------------------------------------
# Cross-currency basis swap (C-108)
# ---------------------------------------------------------------------------


@pytest.mark.phase7
def test_xccy_basis_pricer_returns_curve_spread_at_maturity() -> None:
    basis = _basis_curve()
    dom = _flat_ois(0.45)
    for_ = _flat_ois(0.05)
    # Exact pillar date.
    assert price_xccy_basis_swap(
        basis, dom, for_, _SPOT_DATE + timedelta(days=90)
    ) == -25.0
    # Interpolated date — linear bps between -25 and -30 at the midpoint.
    target = _SPOT_DATE + timedelta(days=135)
    assert price_xccy_basis_swap(basis, dom, for_, target) == pytest.approx(-27.5, abs=1e-12)


@pytest.mark.phase7
def test_xccy_basis_pricer_at_spot_returns_zero() -> None:
    basis = _basis_curve()
    dom = _flat_ois(0.45)
    for_ = _flat_ois(0.05)
    assert price_xccy_basis_swap(basis, dom, for_, _SPOT_DATE) == 0.0


@pytest.mark.phase7
def test_xccy_basis_pricer_propagates_date_out_of_range() -> None:
    basis = _basis_curve()
    dom = _flat_ois(0.45)
    for_ = _flat_ois(0.05)
    with pytest.raises(ValueError, match="DateOutOfRange"):
        price_xccy_basis_swap(basis, dom, for_, date(2099, 1, 1))
