"""Phase 10 — V0.5 shared xccy net-PV kernel (M-114, C-112).

Unit-tests the pure kernel that both the strip (M-106) and the MtM pricer
(M-107) consume:

* ``forward_covers`` coverage predicate (OQ-505 / A-608).
* ``build_xccy_leg_terms`` shape + CIP spread-free base PV ≈ 0 (§5.3).
* ``net_pv`` length guard, CIP zero-spread degeneracy, linearity in the spread.
* Bucketed cross-check (F-604b): feeding the strip's solved ``b_1..b_n`` vector
  through the *production* kernel reproduces the strip's net PV = 0 at each
  calibrated pillar — validating M-114 against M-106.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from datetime import timedelta

import pytest
from tests.fx._strip_helpers import (
    SPOT_DATE,
    SPOT_RATE,
    TR_CALENDAR,
    US_CALENDAR,
    basis_quote,
    cip_forward_curve,
    convention,
    flat_ois,
    market,
    quarterly_coupons,
)

from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.bootstrap_basis import build_cross_basis_curve
from rates.fx.xccy_pv import build_xccy_leg_terms, forward_covers, net_pv

pytestmark = pytest.mark.phase10

_DOM_RATE = 0.45  # TRY OIS
_FOR_RATE = 0.05  # USD OIS


def test_forward_covers() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons_6m = quarterly_coupons(SPOT_DATE + timedelta(days=183))
    short_fwd = cip_forward_curve(dom, fer, coupons_6m)
    assert forward_covers(coupons_6m[-1], short_fwd) is True
    assert forward_covers(SPOT_DATE + timedelta(days=360), short_fwd) is False


def test_build_xccy_leg_terms_shape_and_cip_base_pv() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity))  # exact CIP
    schedule, terms, pv_for0 = build_xccy_leg_terms(
        SPOT_DATE, maturity, dom, fer, fwd, SPOT_RATE, TR_CALENDAR, US_CALENDAR
    )
    assert len(terms) == len(schedule.coupon_dates) == len(schedule.taus)
    assert schedule.coupon_dates[-1] == maturity
    assert all(t.tau > 0.0 for t in terms)
    assert math.isfinite(pv_for0)
    # §5.3: under exact CIP forwards the spread-free foreign base PV is ~ 0.
    assert abs(pv_for0) < 1e-9


def test_net_pv_length_mismatch_raises() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity))
    _schedule, terms, pv_for0 = build_xccy_leg_terms(
        SPOT_DATE, maturity, dom, fer, fwd, SPOT_RATE, TR_CALENDAR, US_CALENDAR
    )
    with pytest.raises(ValueError):
        net_pv(
            terms,
            pv_for0,
            SPOT_RATE,
            quoted_on_foreign=True,
            spread_bps_per_coupon=[0.0],  # too short on purpose
        )


def test_net_pv_zero_spread_under_cip_is_zero() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity))  # exact CIP
    _schedule, terms, pv_for0 = build_xccy_leg_terms(
        SPOT_DATE, maturity, dom, fer, fwd, SPOT_RATE, TR_CALENDAR, US_CALENDAR
    )
    zeros = [0.0] * len(terms)
    npv = net_pv(
        terms, pv_for0, SPOT_RATE, quoted_on_foreign=True, spread_bps_per_coupon=zeros
    )
    assert abs(npv) < 1e-9


def test_net_pv_linear_in_constant_spread() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity), deviation=0.01)
    _schedule, terms, pv_for0 = build_xccy_leg_terms(
        SPOT_DATE, maturity, dom, fer, fwd, SPOT_RATE, TR_CALENDAR, US_CALENDAR
    )
    k = len(terms)

    def npv(b: float) -> float:
        return net_pv(
            terms,
            pv_for0,
            SPOT_RATE,
            quoted_on_foreign=True,
            spread_bps_per_coupon=[b] * k,
        )

    n0, n1, n2 = npv(0.0), npv(50.0), npv(100.0)
    assert (n2 - n0) == pytest.approx(2.0 * (n1 - n0), rel=1e-12)


def test_net_pv_bucketed_vector_reproduces_strip_zero() -> None:
    """F-604b: the strip's solved b_1..b_n, fed through the production kernel,
    reprices each calibrated par pillar to net PV ≈ 0."""
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons = quarterly_coupons(SPOT_DATE + timedelta(days=360))
    fwd = cip_forward_curve(dom, fer, coupons, deviation=(0.004, 0.009, 0.016, 0.025))
    dg = DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market(basis_quote(coupons[1]), basis_quote(coupons[2]), basis_quote(coupons[3])),
        dom,
        fer,
        fwd,
        convention(),
        TR_CALENDAR,
        US_CALENDAR,
        dg,
    )
    assert not dg.has_errors()
    grid = [p.maturity_date for p in curve.pillars_tuple]
    for p in curve.pillars_tuple:
        schedule, terms, pv_for0 = build_xccy_leg_terms(
            SPOT_DATE, p.maturity_date, dom, fer, fwd, SPOT_RATE, TR_CALENDAR, US_CALENDAR
        )
        spread_vec = [
            curve.pillars_tuple[bisect_left(grid, c)].spread_bps
            for c in schedule.coupon_dates
        ]
        npv = net_pv(
            terms,
            pv_for0,
            SPOT_RATE,
            quoted_on_foreign=True,
            spread_bps_per_coupon=spread_vec,
        )
        assert abs(npv) < 1e-9
