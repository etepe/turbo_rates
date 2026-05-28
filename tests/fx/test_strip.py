"""Phase 9 — V0.4 cross-currency basis strip (M-106).

Exercises the Method (i) sequential net-PV=0 calibration (architecture §5):

* CIP degeneracy (§5.3): parity-tight forwards strip to b ≈ 0.
* Hand-computed 1-period micro-case (grill G-2): the strip reproduces an
  independently-derived closed form to machine precision.
* Reprice identity (§5.4): the stripped (piecewise-flat) basis re-prices each
  par pillar to net PV ≈ 0, verified by an independent recompute.
* quoted_on_foreign sign branch (D-17).
* FX_XCCY_FORWARD_COVERAGE (OQ-505) and the non-convergent bracket.
* OQ-403 day-count bias: production Act/360 vs currency-native Act/365, bounded.
"""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, timedelta

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
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.bootstrap_basis import (
    FXBootstrapNonConvergentError,
    XccyBootstrapError,
    build_cross_basis_curve,
)
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.types import FX_XCCY_FORWARD_COVERAGE, FX_XCCY_REPRICE_FAIL

pytestmark = pytest.mark.phase9

_DOM_RATE = 0.45  # TRY OIS
_FOR_RATE = 0.05  # USD OIS


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


def _build(
    *quotes,
    fx_forward: FXForwardCurve,
    dom_ois=None,
    for_ois=None,
    dg: DiagnosticsCollector | None = None,
) -> tuple[CrossCurrencyBasisCurve, DiagnosticsCollector]:
    dom = dom_ois if dom_ois is not None else flat_ois(_DOM_RATE)
    fer = for_ois if for_ois is not None else flat_ois(_FOR_RATE)
    collector = dg or DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market(*quotes), dom, fer, fx_forward, convention(), TR_CALENDAR, US_CALENDAR, collector
    )
    return curve, collector


# ---------------------------------------------------------------------------
# Independent net-PV recompute (piecewise-flat per bucket, mirrors §5.2 but
# written separately from the production code).
# ---------------------------------------------------------------------------


def _independent_net_pv(
    maturity: date,
    pillars: tuple[BasisPillar, ...],
    dom_ois,
    for_ois,
    fwd: FXForwardCurve,
    *,
    quoted_on_foreign: bool,
) -> float:
    grid = [p.maturity_date for p in pillars]
    coupons = quarterly_coupons(maturity)
    period_starts = (SPOT_DATE, *coupons[:-1])
    df_dom_spot = dom_ois.df_at(SPOT_DATE)
    s = fwd.spot_rate

    acc_for = 0.0
    spread_sum = 0.0
    df_dom_last = 1.0
    fwd_last = s
    for start, c in zip(period_starts, coupons, strict=True):
        tau = (c - start).days / 360.0
        df_dom = dom_ois.df_at(c) / df_dom_spot
        f_for = (for_ois.df_at(start) / for_ois.df_at(c) - 1.0) / tau
        f = fwd.forward_at(c)
        acc_for += f * f_for * tau * df_dom
        spread = pillars[bisect_left(grid, c)].spread_bps / 1e4
        spread_sum += spread * (f if quoted_on_foreign else 1.0) * tau * df_dom
        df_dom_last, fwd_last = df_dom, f

    pv_for0 = (1.0 / s) * (-s + acc_for + fwd_last * df_dom_last)
    if quoted_on_foreign:
        return -(pv_for0 + spread_sum / s)
    return spread_sum - pv_for0


# ---------------------------------------------------------------------------
# §5.3 — CIP degeneracy
# ---------------------------------------------------------------------------


def test_cip_tight_forwards_strip_to_zero() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons = quarterly_coupons(SPOT_DATE + timedelta(days=360))
    fwd = cip_forward_curve(dom, fer, coupons)  # deviation=0 ⇒ exact CIP

    t1, t_last = coupons[1], coupons[-1]  # 6M and 1Y on the quarterly grid
    curve, dg = _build(
        basis_quote(t1), basis_quote(t_last), fx_forward=fwd, dom_ois=dom, for_ois=fer
    )

    assert not dg.has_errors()
    assert len(curve.pillars_tuple) == 2
    for p in curve.pillars_tuple:
        assert abs(p.spread_bps) < 1e-6  # b ≈ 0 to numerical noise


# ---------------------------------------------------------------------------
# Grill G-2 — hand-computed 1-period micro-case (foreign-quoted, Act/360)
# ---------------------------------------------------------------------------


def test_single_period_matches_hand_closed_form() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=80)  # sub-quarter ⇒ single period

    df_dom_rel = dom.df_at(maturity) / dom.df_at(SPOT_DATE)
    df_for_rel = fer.df_at(maturity) / fer.df_at(SPOT_DATE)
    f_cip = SPOT_RATE * df_for_rel / df_dom_rel
    f1 = f_cip * 1.01  # 1% forward deviation from CIP
    tau = (maturity - SPOT_DATE).days / 360.0

    # Independent closed form (foreign-quoted, single period; §5.2 solved by hand):
    #   b = S/(F·tau·DF_dom) - 1/(tau·DF_for)   (decimal), spot-anchored DFs.
    expected_bps = (
        SPOT_RATE / (f1 * tau * df_dom_rel) - 1.0 / (tau * df_for_rel)
    ) * 1e4

    fwd = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=SPOT_DATE,
        spot_rate=SPOT_RATE,
        pillars_tuple=(
            FXForwardPillar(
                tenor_code="FWD80D", tenor_days=80, settle_date=maturity, forward_rate=f1
            ),
        ),
    )
    curve, dg = _build(basis_quote(maturity), fx_forward=fwd, dom_ois=dom, for_ois=fer)

    assert not dg.has_errors()
    assert curve.pillars_tuple[0].spread_bps == pytest.approx(expected_bps, abs=1e-6)
    # And it actually re-prices to par.
    assert abs(
        _independent_net_pv(
            maturity, curve.pillars_tuple, dom, fer, fwd, quoted_on_foreign=True
        )
    ) < 1e-9


# ---------------------------------------------------------------------------
# §5.4 — sequential strip reprices every pillar to net PV ≈ 0
# ---------------------------------------------------------------------------


def test_multi_pillar_strip_reprices_to_zero() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons = quarterly_coupons(SPOT_DATE + timedelta(days=360))
    # A deviation term-structure (one per coupon) ⇒ each pillar a distinct basis.
    fwd = cip_forward_curve(dom, fer, coupons, deviation=(0.004, 0.009, 0.016, 0.025))

    t1, t2, t3 = coupons[1], coupons[2], coupons[3]  # 6M, 9M, 1Y
    curve, dg = _build(
        basis_quote(t1),
        basis_quote(t2),
        basis_quote(t3),
        fx_forward=fwd,
        dom_ois=dom,
        for_ois=fer,
    )

    assert not dg.has_errors()
    assert len(curve.pillars_tuple) == 3
    # Each calibrated par pillar re-prices to net PV ≈ 0 (independent recompute).
    for p in curve.pillars_tuple:
        npv = _independent_net_pv(
            p.maturity_date, curve.pillars_tuple, dom, fer, fwd, quoted_on_foreign=True
        )
        assert abs(npv) < 1e-9
    # Round-trip identity: basis_at at a pillar returns the stored b_n.
    for p in curve.pillars_tuple:
        assert curve.basis_at(p.maturity_date) == pytest.approx(p.spread_bps, abs=1e-12)
    # A rising deviation term-structure ⇒ a genuine, distinct basis per pillar.
    spreads = [p.spread_bps for p in curve.pillars_tuple]
    assert all(abs(b) > 1.0 for b in spreads)
    assert spreads[0] != pytest.approx(spreads[1], abs=1.0)
    assert spreads[1] != pytest.approx(spreads[2], abs=1.0)


# ---------------------------------------------------------------------------
# D-17 — quoted_on_foreign sign branch
# ---------------------------------------------------------------------------


def test_domestic_quoted_sign_branch_reprices() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons = quarterly_coupons(SPOT_DATE + timedelta(days=360))
    fwd = cip_forward_curve(dom, fer, coupons, deviation=0.01)

    t_last = coupons[-1]
    curve, dg = _build(
        basis_quote(t_last, quoted_on_foreign=False),
        fx_forward=fwd,
        dom_ois=dom,
        for_ois=fer,
    )

    assert not dg.has_errors()
    assert curve.quoted_on_foreign is False
    npv = _independent_net_pv(
        t_last, curve.pillars_tuple, dom, fer, fwd, quoted_on_foreign=False
    )
    assert abs(npv) < 1e-9


def test_sign_branch_changes_stripped_value() -> None:
    """Same forwards, opposite quoted leg ⇒ materially different stripped basis."""
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons = quarterly_coupons(SPOT_DATE + timedelta(days=360))
    fwd = cip_forward_curve(dom, fer, coupons, deviation=0.01)
    t_last = coupons[-1]

    foreign, _ = _build(
        basis_quote(t_last, quoted_on_foreign=True), fx_forward=fwd, dom_ois=dom, for_ois=fer
    )
    domestic, _ = _build(
        basis_quote(t_last, quoted_on_foreign=False), fx_forward=fwd, dom_ois=dom, for_ois=fer
    )
    assert foreign.pillars_tuple[0].spread_bps != pytest.approx(
        domestic.pillars_tuple[0].spread_bps, abs=1.0
    )


# ---------------------------------------------------------------------------
# OQ-505 — forward coverage; brentq non-convergence
# ---------------------------------------------------------------------------


def test_forward_coverage_error_when_forwards_too_short() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    # Forwards only to 6M, but the xccy quote matures at 1Y.
    short_coupons = quarterly_coupons(SPOT_DATE + timedelta(days=183))
    fwd = cip_forward_curve(dom, fer, short_coupons)
    dg = DiagnosticsCollector()

    with pytest.raises(XccyBootstrapError):
        build_cross_basis_curve(
            market(basis_quote(SPOT_DATE + timedelta(days=360))),
            dom,
            fer,
            fwd,
            convention(),
            TR_CALENDAR,
            US_CALENDAR,
            dg,
        )
    assert FX_XCCY_FORWARD_COVERAGE in _codes(dg)
    assert dg.has_errors()


def test_non_convergent_basis_raises() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=80)
    df_dom_rel = dom.df_at(maturity) / dom.df_at(SPOT_DATE)
    df_for_rel = fer.df_at(maturity) / fer.df_at(SPOT_DATE)
    f_cip = SPOT_RATE * df_for_rel / df_dom_rel
    # A forward at half CIP forces |b| well beyond the ±10000 bps bracket.
    fwd = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=SPOT_DATE,
        spot_rate=SPOT_RATE,
        pillars_tuple=(
            FXForwardPillar(
                tenor_code="FWD80D",
                tenor_days=80,
                settle_date=maturity,
                forward_rate=f_cip * 0.5,
            ),
        ),
    )
    dg = DiagnosticsCollector()
    with pytest.raises(FXBootstrapNonConvergentError):
        build_cross_basis_curve(
            market(basis_quote(maturity)),
            dom,
            fer,
            fwd,
            convention(),
            TR_CALENDAR,
            US_CALENDAR,
            dg,
        )


def test_reprice_fail_code_registered() -> None:
    """The reprice-fail diagnostic constant is the one the strip emits."""
    assert FX_XCCY_REPRICE_FAIL == "FX_XCCY_REPRICE_FAIL"


# ---------------------------------------------------------------------------
# OQ-403 — Act/360-both-legs vs currency-native day-count bias (bounded)
# ---------------------------------------------------------------------------


def test_act360_daycount_bias_is_bounded() -> None:
    """For the domestic (TRY) leg the V0.4 Act/360 simplification differs from
    the native Act/365 purely by the accrual ratio; measure and bound it."""
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=80)  # single period
    df_dom_rel = dom.df_at(maturity) / dom.df_at(SPOT_DATE)
    df_for_rel = fer.df_at(maturity) / fer.df_at(SPOT_DATE)
    f1 = SPOT_RATE * df_for_rel / df_dom_rel * 1.01
    days = (maturity - SPOT_DATE).days

    fwd = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=SPOT_DATE,
        spot_rate=SPOT_RATE,
        pillars_tuple=(
            FXForwardPillar(
                tenor_code="FWD80D", tenor_days=80, settle_date=maturity, forward_rate=f1
            ),
        ),
    )
    curve, dg = _build(
        basis_quote(maturity, quoted_on_foreign=False), fx_forward=fwd, dom_ois=dom, for_ois=fer
    )
    assert not dg.has_errors()
    b_prod = curve.pillars_tuple[0].spread_bps  # Act/360 (production)

    # Native: same hand value but with the TRY-leg accrual on Act/365.
    pv_for0 = -1.0 + f1 * df_dom_rel / (SPOT_RATE * df_for_rel)
    b_native = (pv_for0 / ((days / 365.0) * df_dom_rel)) * 1e4

    # The two differ exactly by the accrual ratio 360/365.
    assert b_prod == pytest.approx(b_native * (360.0 / 365.0), rel=1e-9)
    # ...which bounds the OQ-403 bias at ~1.4%.
    rel_bias = abs(b_prod - b_native) / abs(b_native)
    assert rel_bias < 0.02
