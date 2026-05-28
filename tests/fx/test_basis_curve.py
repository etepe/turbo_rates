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
def test_basis_at_anchors_to_zero_at_spot() -> None:
    """At spot_date the basis is zero by convention."""
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        pillars_tuple=(_pillar(90, -25.0), _pillar(180, -30.0)),
        quoted_on_foreign=True,
    )
    assert curve.basis_at(date(2026, 6, 15)) == 0.0


@pytest.mark.phase7
def test_basis_at_exact_at_each_pillar() -> None:
    pillars = (_pillar(30, -10.0), _pillar(90, -25.0), _pillar(180, -30.0))
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        pillars_tuple=pillars,
        quoted_on_foreign=True,
    )
    for p in pillars:
        assert curve.basis_at(p.maturity_date) == p.spread_bps


@pytest.mark.phase7
def test_basis_at_linear_midpoint_between_spot_and_first_pillar() -> None:
    """Halfway between spot and first pillar: bps = 0.5 * P1.spread_bps."""
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        pillars_tuple=(_pillar(60, -20.0),),
        quoted_on_foreign=True,
    )
    mid = date(2026, 6, 15) + timedelta(days=30)
    assert curve.basis_at(mid) == pytest.approx(-10.0, abs=1e-12)


@pytest.mark.phase7
def test_basis_at_linear_between_two_pillars() -> None:
    """Between pillars P1 (-25) and P2 (-30) at midpoint: bps = -27.5."""
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        pillars_tuple=(_pillar(30, -25.0), _pillar(90, -30.0)),
        quoted_on_foreign=True,
    )
    target = date(2026, 6, 15) + timedelta(days=60)  # halfway between 30 and 90
    assert curve.basis_at(target) == pytest.approx(-27.5, abs=1e-12)


@pytest.mark.phase7
def test_basis_at_rejects_dates_before_spot_or_after_last() -> None:
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        pillars_tuple=(_pillar(90, -25.0),),
        quoted_on_foreign=True,
    )
    with pytest.raises(ValueError, match="DateOutOfRange"):
        curve.basis_at(date(2026, 6, 14))
    with pytest.raises(ValueError, match="DateOutOfRange"):
        curve.basis_at(date(2099, 1, 1))
