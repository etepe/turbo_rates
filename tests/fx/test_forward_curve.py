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
def test_forward_at_anchors_to_spot() -> None:
    """At spot_date, forward_at returns spot_rate exactly."""
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        spot_rate=32.5,
        pillars_tuple=(_pillar(30, 32.7),),
    )
    assert curve.forward_at(date(2026, 6, 15)) == 32.5


@pytest.mark.phase7
def test_forward_at_exact_at_each_pillar() -> None:
    """forward_at(pillar.settle_date) returns the pillar's forward_rate exactly."""
    pillars = (_pillar(30, 32.7), _pillar(90, 33.1), _pillar(180, 33.9))
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        spot_rate=32.5,
        pillars_tuple=pillars,
    )
    for p in pillars:
        assert curve.forward_at(p.settle_date) == p.forward_rate


@pytest.mark.phase7
def test_forward_at_log_linear_between_spot_and_first_pillar() -> None:
    """Half-way between spot and first pillar: F = S * (F_p/S)^0.5."""
    from math import sqrt

    spot = 32.5
    f_pillar = 33.5
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        spot_rate=spot,
        pillars_tuple=(_pillar(60, f_pillar),),
    )
    mid = date(2026, 6, 15) + _td(30)
    assert curve.forward_at(mid) == pytest.approx(spot * sqrt(f_pillar / spot), abs=1e-12)


@pytest.mark.phase7
def test_forward_at_log_linear_between_two_pillars() -> None:
    """Between pillars P1, P2 with weight w: F = S * exp((1-w)*log(F1/S) + w*log(F2/S))."""
    from math import exp, log

    spot = 32.5
    pillars = (_pillar(30, 32.7), _pillar(90, 33.4))
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        spot_rate=spot,
        pillars_tuple=pillars,
    )
    target = date(2026, 6, 15) + _td(60)  # halfway between day 30 and day 90
    expected = spot * exp(0.5 * log(32.7 / spot) + 0.5 * log(33.4 / spot))
    assert curve.forward_at(target) == pytest.approx(expected, abs=1e-12)


@pytest.mark.phase7
def test_forward_at_rejects_dates_before_spot_or_after_last() -> None:
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=date(2026, 6, 15),
        spot_rate=32.5,
        pillars_tuple=(_pillar(30, 32.7),),
    )
    with pytest.raises(ValueError, match="DateOutOfRange"):
        curve.forward_at(date(2026, 6, 14))
    with pytest.raises(ValueError, match="DateOutOfRange"):
        curve.forward_at(date(2099, 1, 1))
