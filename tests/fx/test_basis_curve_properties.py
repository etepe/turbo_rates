"""Phase 7 — CrossCurrencyBasisCurve invariants (Hypothesis).

Two properties:

* ``basis_at(pillar.maturity_date) == pillar.spread_bps`` for every pillar,
  any spread vector.
* ``basis_at(spot_date) == 0.0`` exactly — anchor identity.

Conservative tuning (max_examples=20, deadline=500ms), matching the M-103
property test layout.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve

_PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


_SPOT_DATE = date(2026, 6, 15)


@st.composite
def _pillar_list(draw: st.DrawFn) -> tuple[BasisPillar, ...]:
    """Generate 1-5 basis pillars with strictly increasing maturity dates."""
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
    spreads = draw(
        st.lists(
            st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
        )
    )
    return tuple(
        BasisPillar(
            tenor_code=f"T{i}D",
            tenor_days=d,
            maturity_date=_SPOT_DATE + timedelta(days=d),
            spread_bps=s,
        )
        for i, (d, s) in enumerate(zip(day_offsets, spreads, strict=True))
    )


@pytest.mark.phase7
@given(pillars=_pillar_list())
@_PROPERTY_SETTINGS
def test_basis_at_returns_pillar_spread_exactly(
    pillars: tuple[BasisPillar, ...],
) -> None:
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        pillars_tuple=pillars,
        quoted_on_foreign=True,
    )
    for p in pillars:
        assert curve.basis_at(p.maturity_date) == p.spread_bps


@pytest.mark.phase7
@given(pillars=_pillar_list())
@_PROPERTY_SETTINGS
def test_basis_at_zero_at_spot_under_any_spread_vector(
    pillars: tuple[BasisPillar, ...],
) -> None:
    curve = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        pillars_tuple=pillars,
        quoted_on_foreign=True,
    )
    assert curve.basis_at(_SPOT_DATE) == 0.0
