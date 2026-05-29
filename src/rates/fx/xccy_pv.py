"""rates.fx.xccy_pv — shared cross-currency net-PV kernel (M-114, V0.5).

The pure present-value core of a constant-notional, float-float cross-currency
basis swap, shared by **both**:

* the **strip** (M-106, :func:`rates.fx.bootstrap_basis.build_cross_basis_curve`),
  which solves a per-bucket spread so each par swap reprices to net PV = 0; and
* the **MtM pricer** (M-107, :func:`rates.fx.pricer.price_xccy_swap_mtm`), which
  applies a single flat *contract* spread and returns the PV.

This module is a pure leaf: **no diagnostics, no module-local exceptions, no IO**.
It depends only on the schedule generator (M-113), the OIS curve accessor, and
the FX forward accessor. The arithmetic is **verbatim** the V0.4 strip core
(``docs/v04-xccy-calibration-architecture.md`` §5.1-§5.2); the only generalisation
is that the spread enters :func:`net_pv` as an explicit **per-coupon vector**
(the strip fills it from its buckets; the pricer fills it with a constant). See
``docs/v05-xccy-mtm-pricer-architecture.md`` §5 (C-112, A-601).

Method (i) (D-11): foreign-leg cashflows are converted to domestic units at the
full FX forward curve and discounted on the domestic OIS curve, with domestic
discounting anchored at the spot date (``DF_dom(spot) = 1``) so every PV is
expressed **as of the spot date** (D-607).

Contract: C-112 (consumed by M-106 and M-107).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from rates.core.calendar import HolidayCalendar
from rates.core.curve import OISCurve
from rates.core.types import BusinessDayConvention, DayCount
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.schedule import XccySchedule, build_quarterly_xccy_schedule

#: Common day-count for both xccy legs (D-14 / D-605). Single source of truth
#: (the strip's former private ``_STRIP_DAY_COUNT``).
STRIP_DAY_COUNT = DayCount.ACT_360
#: Roll convention for interior coupon dates (D-14 / D-605).
STRIP_BDC = BusinessDayConvention.MODIFIED_FOLLOWING
#: bps → decimal scale.
BPS_PER_UNIT = 10000.0


@dataclass(frozen=True, slots=True)
class XccyCouponTerm:
    """Pre-computed per-coupon quantities for one xccy swap period.

    Attributes:
        tau:    Act/360 accrual for this period.
        df_dom: Domestic OIS discount factor at the coupon date, re-anchored at
                spot (``DF_dom(t_i)/DF_dom(spot)``), so PVs are as of spot.
        fwd:    FX outright forward ``F(t_i)`` at the coupon date.
    """

    tau: float
    df_dom: float
    fwd: float


def forward_covers(longest_coupon: date, fx_forward: FXForwardCurve) -> bool:
    """True iff the FX forward curve reaches ``longest_coupon`` (no extrapolation).

    The caller (strip or pricer) emits :data:`FX_XCCY_FORWARD_COVERAGE` itself —
    this predicate is pure (OQ-505 / A-608).
    """
    return longest_coupon <= fx_forward.pillars_tuple[-1].settle_date


def build_xccy_leg_terms(
    spot_date: date,
    maturity: date,
    dom_ois: OISCurve,
    for_ois: OISCurve,
    fx_forward: FXForwardCurve,
    spot_rate: float,
    dom_calendar: HolidayCalendar,
    for_calendar: HolidayCalendar,
) -> tuple[XccySchedule, list[XccyCouponTerm], float]:
    """Per-coupon terms + the spread-free foreign PV in domestic units (§5.2).

    Builds the quarterly schedule (M-113), then for each coupon computes the
    spot-anchored domestic discount factor, the FX forward, and the foreign OIS
    forward, accumulating the spread-free foreign-leg PV converted to domestic
    units and discounted on the domestic OIS curve.

    Domestic discounting is anchored at the spot date so ``DF_dom(t_0) = 1``: the
    strip discount factor is ``DF_dom(t_i)/DF_dom(spot)``. The notional exchange
    at ``t_0`` therefore contributes exactly ``-S``. The foreign OIS forward
    ``f_for,i = (DF_for(t_{i-1})/DF_for(t_i) - 1)/tau_i`` is a discount-factor
    ratio and so is anchor-invariant.

    ``pv_for0`` is computed with ``N_dom = 1`` (``N_for = 1/S``):
    ``PV_for0 = (1/S) * (-S + Sum F_i*f_for,i*tau_i*DF_dom_i + F_N*DF_dom_N)``.
    The domestic floating leg prices to par by the single-curve identity, so it
    contributes nothing here.

    Args:
        spot_date:    Curve spot ``t_0`` (the swap's first exchange; D-601).
        maturity:     Final settlement ``t_N``.
        dom_ois:      Domestic OIS curve (projection + discount).
        for_ois:      Foreign OIS curve (projection + discount).
        fx_forward:   FX forward curve; must cover the longest coupon date
                      (caller pre-checks via :func:`forward_covers`).
        spot_rate:    FX spot ``S`` (``fx_forward.spot_rate``).
        dom_calendar: Domestic holiday calendar (joint roll).
        for_calendar: Foreign holiday calendar (joint roll).

    Returns:
        ``(schedule, terms, pv_for0)`` — the quarterly schedule (so callers can
        derive per-coupon buckets), the per-coupon terms, and the spread-free
        foreign base PV in domestic units (unit ``N_dom = 1``).

    Raises:
        ValueError: ``maturity <= spot_date`` (from the schedule generator) or a
            coupon date out of range on ``dom_ois`` / ``for_ois`` / ``fx_forward``
            (propagated from the accessors; pre-check coverage to avoid this).
    """
    schedule = build_quarterly_xccy_schedule(
        spot_date, maturity, dom_calendar, for_calendar, STRIP_BDC, STRIP_DAY_COUNT
    )
    df_dom_spot = dom_ois.df_at(spot_date)

    terms: list[XccyCouponTerm] = []
    pv_acc_for = 0.0
    for start, coupon, tau in zip(
        schedule.period_starts, schedule.coupon_dates, schedule.taus, strict=True
    ):
        df_dom = dom_ois.df_at(coupon) / df_dom_spot
        df_for = for_ois.df_at(coupon)
        df_for_prev = for_ois.df_at(start)
        fwd = fx_forward.forward_at(coupon)
        f_for = (df_for_prev / df_for - 1.0) / tau
        pv_acc_for += fwd * f_for * tau * df_dom
        terms.append(XccyCouponTerm(tau=tau, df_dom=df_dom, fwd=fwd))

    final_notional = terms[-1].fwd * terms[-1].df_dom
    pv_for0 = (1.0 / spot_rate) * (-spot_rate + pv_acc_for + final_notional)
    return schedule, terms, pv_for0


def net_pv(
    terms: list[XccyCouponTerm],
    pv_for0: float,
    spot_rate: float,
    *,
    quoted_on_foreign: bool,
    spread_bps_per_coupon: Sequence[float],
) -> float:
    """Net PV (domestic units, unit ``N_dom = 1``, as of spot) at a spread vector.

    ``spread_bps_per_coupon[i]`` is the basis spread (bps) applied to coupon ``i``;
    the strip fills it per bucket, the MtM pricer fills it with a constant. The
    spread enters the foreign leg (converted at the forward) when
    ``quoted_on_foreign`` else the domestic leg (§5.2).

    Multiply the result by the domestic notional to obtain the reported PV;
    the strip drives this to zero per pillar (net PV = 0).

    Args:
        terms:                  Per-coupon terms from :func:`build_xccy_leg_terms`.
        pv_for0:                Spread-free foreign base PV (domestic units).
        spot_rate:              FX spot ``S``.
        quoted_on_foreign:      True iff the spread is on the foreign leg.
        spread_bps_per_coupon:  Per-coupon spread in bps; ``len`` must equal
                                ``len(terms)``.

    Returns:
        Unit-notional net PV in domestic units.

    Raises:
        ValueError: When ``len(spread_bps_per_coupon) != len(terms)``.
    """
    spread_sum = 0.0
    for t, b_bps in zip(terms, spread_bps_per_coupon, strict=True):
        b_dec = b_bps / BPS_PER_UNIT
        weight = (t.fwd if quoted_on_foreign else 1.0) * t.tau * t.df_dom
        spread_sum += b_dec * weight

    if quoted_on_foreign:
        # NPV = -(PV_for0 + N_for*Sum b*F*tau*DF_dom), N_for = 1/S.
        return -(pv_for0 + spread_sum / spot_rate)
    # NPV = N_dom*Sum b*tau*DF_dom - PV_for0, N_dom = 1.
    return spread_sum - pv_for0


__all__ = [
    "BPS_PER_UNIT",
    "STRIP_BDC",
    "STRIP_DAY_COUNT",
    "XccyCouponTerm",
    "build_xccy_leg_terms",
    "forward_covers",
    "net_pv",
]
