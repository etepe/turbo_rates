"""Phase 7 — FXForwardCurve invariants (Hypothesis).

Two properties:

* ``forward_at(pillar.settle_date) == pillar.forward_rate`` for every pillar,
  any positive spot, any ascending pillar dates.
* ``forward_at(spot_date) == spot_rate`` exactly.

Conservative tuning (max_examples=20, deadline=500ms), matching the V1.5
property suite.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar

_PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


_SPOT_DATE = date(2026, 6, 15)


@st.composite
def _pillar_list(draw: st.DrawFn) -> tuple[FXForwardPillar, ...]:
    """Generate 1-5 pillars with strictly increasing settle dates."""
    n = draw(st.integers(min_value=1, max_value=5))
    day_offsets = sorted(
        draw(
            st.lists(
                st.integers(min_value=7, max_value=720),
                min_size=n,
                max_size=n,
                unique=True,
            )
        )
    )
    rates = draw(
        st.lists(
            st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    return tuple(
        FXForwardPillar(
            tenor_code=f"T{i}D",
            tenor_days=d,
            settle_date=_SPOT_DATE + timedelta(days=d),
            forward_rate=r,
        )
        for i, (d, r) in enumerate(zip(day_offsets, rates, strict=True))
    )


@pytest.mark.phase7
@given(
    spot=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    pillars=_pillar_list(),
)
@_PROPERTY_SETTINGS
def test_forward_at_returns_pillar_rate_exactly(
    spot: float, pillars: tuple[FXForwardPillar, ...]
) -> None:
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        spot_rate=spot,
        pillars_tuple=pillars,
    )
    for p in pillars:
        assert curve.forward_at(p.settle_date) == p.forward_rate


@pytest.mark.phase7
@given(
    spot=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    pillars=_pillar_list(),
)
@_PROPERTY_SETTINGS
def test_forward_at_anchors_to_spot_under_any_input(
    spot: float, pillars: tuple[FXForwardPillar, ...]
) -> None:
    curve = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        spot_rate=spot,
        pillars_tuple=pillars,
    )
    assert curve.forward_at(_SPOT_DATE) == spot
