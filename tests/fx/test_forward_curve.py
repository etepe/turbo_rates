"""Phase 7 — FX forward curve (M-103) scaffold tests.

Construction + invariants are exercised; ``forward_at`` interpolation is
deferred to V2 and marked skip.
"""

from __future__ import annotations

from datetime import date

import pytest

from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar


def _pillar(days: int, rate: float, code: str = "T") -> FXForwardPillar:
    return FXForwardPillar(
        tenor_code=f"USDTRY{code}",
        tenor_days=days,
        settle_date=date(2026, 6, 15) + _td(days),
        forward_rate=rate,
    )


def _td(d: int):
    from datetime import timedelta

    return timedelta(days=d)


@pytest.mark.phase7
def test_construction_rejects_empty_pillars() -> None:
    with pytest.raises(ValueError, match="at least one pillar"):
        FXForwardCurve(
            pair_code="USDTRY",
            spot_date=date(2026, 6, 15),
            spot_rate=32.5,
            pillars_tuple=(),
        )


@pytest.mark.phase7
def test_construction_rejects_non_positive_spot() -> None:
    with pytest.raises(ValueError, match="spot_rate"):
        FXForwardCurve(
            pair_code="USDTRY",
            spot_date=date(2026, 6, 15),
            spot_rate=0.0,
            pillars_tuple=(_pillar(30, 32.7),),
        )


@pytest.mark.phase7
def test_construction_rejects_non_ascending_pillars() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        FXForwardCurve(
            pair_code="USDTRY",
            spot_date=date(2026, 6, 15),
            spot_rate=32.5,
            pillars_tuple=(_pillar(30, 32.7), _pillar(30, 32.8, code="X")),
        )


@pytest.mark.phase7
def test_pillars_dataframe_is_defensive_copy() -> None:
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        spot_rate=32.5,
        pillars_tuple=(_pillar(30, 32.7),),
    )
    df1 = curve.pillars()
    df1.loc[0, "forward_rate"] = 999.0
    df2 = curve.pillars()
    assert df2.loc[0, "forward_rate"] == pytest.approx(32.7)


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — interpolation not yet implemented")
def test_forward_at_log_linear_returns_expected_value() -> None:
    """When V2 lands: between two pillars (P1, P2), forward_at(mid) equals
    ``exp((1-w)*log(F1) + w*log(F2))`` with w = (mid - P1)/(P2 - P1)."""
    raise NotImplementedError


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — interpolation not yet implemented")
def test_forward_at_anchors_to_spot() -> None:
    """At spot_date, forward_at returns spot_rate exactly."""
    raise NotImplementedError
