"""Phase 7 — cross-currency basis curve (M-104) scaffold tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve


def _pillar(days: int, bps: float, code: str = "T") -> BasisPillar:
    return BasisPillar(
        tenor_code=f"USDTRY{code}",
        tenor_days=days,
        maturity_date=date(2026, 6, 15) + timedelta(days=days),
        spread_bps=bps,
    )


@pytest.mark.phase7
def test_construction_rejects_empty_pillars() -> None:
    with pytest.raises(ValueError, match="at least one pillar"):
        CrossCurrencyBasisCurve(
            pair_code="USDTRY",
            spot_date=date(2026, 6, 15),
            pillars_tuple=(),
            quoted_on_foreign=True,
        )


@pytest.mark.phase7
def test_construction_rejects_non_ascending_pillars() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        CrossCurrencyBasisCurve(
            pair_code="USDTRY",
            spot_date=date(2026, 6, 15),
            pillars_tuple=(_pillar(90, -25.0), _pillar(90, -30.0, code="X")),
            quoted_on_foreign=True,
        )


@pytest.mark.phase7
def test_pillars_dataframe_is_defensive_copy() -> None:
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        pillars_tuple=(_pillar(90, -25.0), _pillar(180, -30.0)),
        quoted_on_foreign=True,
    )
    df1 = curve.pillars()
    df1.loc[0, "spread_bps"] = 999.0
    df2 = curve.pillars()
    assert df2.loc[0, "spread_bps"] == pytest.approx(-25.0)


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — interpolation not yet implemented")
def test_basis_at_linear_interp_between_pillars() -> None:
    """When V2 lands: linear interp on bps in calendar-day weighting (FX-O2 default)."""
    raise NotImplementedError
