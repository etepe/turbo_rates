"""Phase 10 — V0.5 MtM xccy basis swap pricer (M-107, C-111).

Black-box tests of :func:`rates.fx.pricer.price_xccy_swap_mtm` (F-604):

* (a) par-flat self-consistency: pricing at the swap's par flat spread -> PV ~ 0.
* (c) independent hand-computed PV micro-case (single CIP period).
* (d) notional linearity PV(2N) == 2*PV(N); direction flips the sign.
* (e) quoted_on_foreign sign branch (foreign vs domestic leg).
* (f) FX_XCCY_FORWARD_COVERAGE abort on a short forward curve.
* (g) Bulgu 1 negative guard: contract_spread = marginal basis_at(T_n) gives
      PV != 0 for n>1 on a non-flat curve (= 0 only at the first pillar).
"""

from __future__ import annotations

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

from rates.core.curve import OISCurve
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.bootstrap_basis import build_cross_basis_curve
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.pricer import price_xccy_swap_mtm
from rates.fx.types import FX_XCCY_FORWARD_COVERAGE, XccySwapDirection

pytestmark = pytest.mark.phase10

_DOM_RATE = 0.45  # TRY OIS
_FOR_RATE = 0.05  # USD OIS
_RECV = XccySwapDirection.RECEIVE_DOMESTIC
_PAY = XccySwapDirection.PAY_DOMESTIC


def _price(
    dom: OISCurve,
    fer: OISCurve,
    fwd: FXForwardCurve,
    maturity,
    spread_bps: float,
    notional: float = 1.0,
    *,
    quoted_on_foreign: bool = True,
    direction: XccySwapDirection = _RECV,
    dg: DiagnosticsCollector | None = None,
) -> float:
    return price_xccy_swap_mtm(
        dom,
        fer,
        fwd,
        maturity_date=maturity,
        contract_spread_bps=spread_bps,
        notional_domestic=notional,
        quoted_on_foreign=quoted_on_foreign,
        direction=direction,
        dom_calendar=TR_CALENDAR,
        for_calendar=US_CALENDAR,
        diagnostics=dg or DiagnosticsCollector(),
    )


def _par_flat(
    dom: OISCurve, fer: OISCurve, fwd: FXForwardCurve, maturity, *, quoted_on_foreign: bool = True
) -> float:
    """Par flat spread (bps) via a two-point linear solve on the public pricer."""
    pv0 = _price(dom, fer, fwd, maturity, 0.0, quoted_on_foreign=quoted_on_foreign)
    pv1 = _price(dom, fer, fwd, maturity, 1.0, quoted_on_foreign=quoted_on_foreign)
    return -pv0 / (pv1 - pv0)


# ---------------------------------------------------------------------------
# (a) round-trip — par-flat self-consistency
# ---------------------------------------------------------------------------


def test_par_flat_spread_reprices_to_zero() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity), deviation=0.012)

    s_par = _par_flat(dom, fer, fwd, maturity)
    assert _price(dom, fer, fwd, maturity, s_par, notional=1.0) == pytest.approx(0.0, abs=1e-9)
    # Scales with notional: |PV| < 1e-9 * N.
    assert abs(_price(dom, fer, fwd, maturity, s_par, notional=1e7)) < 1e-9 * 1e7


def test_par_flat_holds_for_domestic_quoted() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity), deviation=0.012)

    s_par = _par_flat(dom, fer, fwd, maturity, quoted_on_foreign=False)
    assert _price(
        dom, fer, fwd, maturity, s_par, notional=1.0, quoted_on_foreign=False
    ) == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# (c) independent hand-computed micro-case (single CIP period, foreign-quoted)
# ---------------------------------------------------------------------------


def test_single_period_pv_matches_hand_value() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=80)  # sub-quarter ⇒ single period
    coupons = quarterly_coupons(maturity)
    assert len(coupons) == 1  # single back stub
    fwd = cip_forward_curve(dom, fer, coupons)  # exact CIP ⇒ pv_for0 = 0

    # Independent recompute (foreign-quoted, RECEIVE_DOMESTIC, CIP ⇒ base PV 0):
    #   PV = N * net_pv,  net_pv = -(b/1e4) * F * tau * DF_dom / S.
    f = fwd.forward_at(maturity)
    tau = (maturity - SPOT_DATE).days / 360.0
    df_dom = dom.df_at(maturity) / dom.df_at(SPOT_DATE)
    b = -180.0
    notional = 5_000_000.0
    expected = notional * (-(b / 1e4) * f * tau * df_dom / SPOT_RATE)

    got = _price(dom, fer, fwd, maturity, b, notional=notional)
    assert got == pytest.approx(expected, rel=1e-12)


# ---------------------------------------------------------------------------
# (d) notional linearity + direction sign
# ---------------------------------------------------------------------------


def test_notional_linearity_and_direction_sign() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity), deviation=0.012)
    b = -150.0

    pv_n = _price(dom, fer, fwd, maturity, b, notional=1e6)
    pv_2n = _price(dom, fer, fwd, maturity, b, notional=2e6)
    assert pv_2n == pytest.approx(2.0 * pv_n, rel=1e-12)
    assert pv_n != pytest.approx(0.0, abs=1.0)  # genuinely off-market

    pv_pay = _price(dom, fer, fwd, maturity, b, notional=1e6, direction=_PAY)
    assert pv_pay == pytest.approx(-pv_n, rel=1e-12)


# ---------------------------------------------------------------------------
# (e) quoted_on_foreign sign branch
# ---------------------------------------------------------------------------


def test_quoted_on_foreign_branch_differs() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity), deviation=0.012)
    b = -150.0

    pv_foreign = _price(dom, fer, fwd, maturity, b, notional=1e6, quoted_on_foreign=True)
    pv_domestic = _price(dom, fer, fwd, maturity, b, notional=1e6, quoted_on_foreign=False)
    # Foreign leg carries the spread converted at the forward (~S), domestic does
    # not — the two branches price materially differently.
    assert pv_foreign != pytest.approx(pv_domestic, rel=1e-6)


# ---------------------------------------------------------------------------
# (f) FX forward coverage abort
# ---------------------------------------------------------------------------


def test_forward_coverage_error_when_forwards_too_short() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    short_coupons = quarterly_coupons(SPOT_DATE + timedelta(days=183))  # only to 6M
    fwd = cip_forward_curve(dom, fer, short_coupons)
    dg = DiagnosticsCollector()

    with pytest.raises(ValueError):
        _price(dom, fer, fwd, SPOT_DATE + timedelta(days=360), -150.0, dg=dg)
    assert FX_XCCY_FORWARD_COVERAGE in [d.code for d in dg.to_list()]
    assert dg.has_errors()


def test_negative_notional_and_degenerate_maturity_raise() -> None:
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    maturity = SPOT_DATE + timedelta(days=360)
    fwd = cip_forward_curve(dom, fer, quarterly_coupons(maturity))
    with pytest.raises(ValueError):
        _price(dom, fer, fwd, maturity, -150.0, notional=-1.0)
    with pytest.raises(ValueError):
        _price(dom, fer, fwd, SPOT_DATE - timedelta(days=1), -150.0)


# ---------------------------------------------------------------------------
# (g) Bulgu 1 — marginal basis_at(T_n) is NOT par for n>1 on a non-flat curve
# ---------------------------------------------------------------------------


def test_marginal_basis_is_not_par_for_later_pillars() -> None:
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
    t1 = curve.pillars_tuple[0].maturity_date   # first pillar (flat = marginal)
    t3 = curve.pillars_tuple[-1].maturity_date  # 1Y (bucketed ⇒ marginal != par)

    # First pillar: a single flat bucket ⇒ marginal == par flat ⇒ PV ~ 0.
    pv_t1 = _price(dom, fer, fwd, t1, curve.basis_at(t1), notional=1e6)
    assert pv_t1 == pytest.approx(0.0, abs=1e-9 * 1e6)

    # Last pillar: marginal b_3 != par flat spread ⇒ a flat-b_3 swap is NOT par.
    pv_t3 = _price(dom, fer, fwd, t3, curve.basis_at(t3), notional=1e6)
    assert abs(pv_t3) > 1.0
    # ...and the par flat spread there DOES reprice to zero (contrast).
    assert _price(dom, fer, fwd, t3, _par_flat(dom, fer, fwd, t3), notional=1e6) == pytest.approx(
        0.0, abs=1e-9 * 1e6
    )
