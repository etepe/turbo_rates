"""rates.fx.pricer — FX pricing functions (M-107).

Pure functions that consume bootstrapped curves to produce fair prices for
outright forwards, FX swaps, and cross-currency basis swaps. No mutation
of inputs; no IO; deterministic.

V2 keeps the pricers as thin wrappers over the curve accessors implemented
in M-103 (FX forward) and M-104 (xccy basis). This is consistent with the
"quoted curves verbatim" discipline of M-105 and M-106: the curve
accessors already encode the chosen interpolation convention, so the
pricers expose them through a stable contract that V3 can swap for a
fully recalibrated implementation without breaking call sites.

Contracts: C-106 (outright), C-107 (swap), C-108 (xccy basis).
"""

from __future__ import annotations

from datetime import date

from rates.core.curve import OISCurve
from rates.fx.basis_curve import CrossCurrencyBasisCurve
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import FXSwapPricing


def price_outright_forward(
    fx_forward: FXForwardCurve,
    settle_date: date,
) -> float:
    """Fair outright forward rate at ``settle_date``.

    Args:
        fx_forward:  Bootstrapped FX forward curve.
        settle_date: Target forward settlement date; must satisfy
                     ``fx_forward.spot_date <= settle_date <=
                     fx_forward.pillars_tuple[-1].settle_date``.

    Returns:
        Fair outright forward rate (domestic per foreign), under the
        curve's interpolation convention (log-linear in log(F/S) for V2).

    Raises:
        ValueError: When ``settle_date`` is out of range.
    """
    return fx_forward.forward_at(settle_date)


def price_fx_swap(
    fx_forward: FXForwardCurve,
    near_date: date,
    far_date: date,
) -> FXSwapPricing:
    """Fair near + far rates for a par FX swap.

    The swap is taken as a pair of forward settlements at par notional;
    each leg's fair rate is the curve's outright forward at the leg's
    settlement date. The reported swap points are simply ``far - near``,
    *not* scaled to the pair's :attr:`forward_point_scale` — callers that
    need pip-quoted swap points multiply by the convention's scale.

    Args:
        fx_forward: Bootstrapped FX forward curve.
        near_date:  Near leg settlement.
        far_date:   Far leg settlement (must be strictly after ``near_date``).

    Returns:
        :class:`FXSwapPricing` with near rate, far rate, fair swap points,
        and a small provenance ``context`` dict carrying the curve's spot
        rate and the day-offset of each leg from spot.

    Raises:
        ValueError: When ``near_date >= far_date`` or either date is out
                    of range on the curve.
    """
    if near_date >= far_date:
        raise ValueError(
            f"price_fx_swap requires near_date < far_date "
            f"(got {near_date} >= {far_date})"
        )
    near_rate = fx_forward.forward_at(near_date)
    far_rate = fx_forward.forward_at(far_date)
    return FXSwapPricing(
        near_rate=near_rate,
        far_rate=far_rate,
        swap_points=far_rate - near_rate,
        context={
            "spot_rate": fx_forward.spot_rate,
            "near_offset_days": float((near_date - fx_forward.spot_date).days),
            "far_offset_days": float((far_date - fx_forward.spot_date).days),
        },
    )


def price_xccy_basis_swap(
    basis: CrossCurrencyBasisCurve,
    dom_ois: OISCurve,
    for_ois: OISCurve,
    maturity_date: date,
) -> float:
    """Fair basis spread (bps) for a par cross-currency basis swap.

    V2 returns the spread directly from the basis curve at the maturity
    date. The dual OIS curves are accepted today so the contract stays
    stable; V3 will tighten this into a full par xccy swap pricing pass
    against those curves + the FX forward curve.

    Args:
        basis:         Bootstrapped cross-currency basis curve.
        dom_ois:       Domestic OIS curve (held for V3 calibration; accepted
                       to keep the contract surface stable).
        for_ois:       Foreign OIS curve (same rationale as ``dom_ois``).
        maturity_date: Final settlement of the basis swap.

    Returns:
        Fair basis spread in basis points, signed per the curve's quote
        convention (:attr:`CrossCurrencyBasisCurve.quoted_on_foreign`).

    Raises:
        ValueError: When ``maturity_date`` is out of range on ``basis``.
    """
    # dom_ois / for_ois reserved for V3 calibration; referenced here so the
    # signature stays stable today without triggering an unused-arg lint.
    _ = (dom_ois, for_ois)
    return basis.basis_at(maturity_date)


__all__ = [
    "price_fx_swap",
    "price_outright_forward",
    "price_xccy_basis_swap",
]
