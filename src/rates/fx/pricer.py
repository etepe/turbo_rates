"""rates.fx.pricer — FX pricing functions (M-107, V2 scaffold).

Pure functions that consume bootstrapped curves to produce fair prices for
outright forwards, FX swaps, and cross-currency basis swaps. No mutation of
inputs; no IO; deterministic.

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
        settle_date: Target forward settlement date.

    Returns:
        Fair outright forward rate (domestic per foreign).

    Raises:
        ValueError: When ``settle_date`` is out of range.
        NotImplementedError: V2 — implementation deferred.
    """
    _ = (fx_forward, settle_date)
    raise NotImplementedError("V2: price_outright_forward")


def price_fx_swap(
    fx_forward: FXForwardCurve,
    near_date: date,
    far_date: date,
) -> FXSwapPricing:
    """Fair near + far rates for a par FX swap.

    Args:
        fx_forward: Bootstrapped FX forward curve.
        near_date:  Near leg settlement.
        far_date:   Far leg settlement (must be strictly after ``near_date``).

    Returns:
        :class:`FXSwapPricing` with near rate, far rate, fair swap points,
        and a provenance context dict.

    Raises:
        ValueError: When ``near_date >= far_date`` or either date is out of range.
        NotImplementedError: V2 — implementation deferred.
    """
    if near_date >= far_date:
        raise ValueError(
            f"price_fx_swap requires near_date < far_date "
            f"(got {near_date} >= {far_date})"
        )
    _ = fx_forward
    raise NotImplementedError("V2: price_fx_swap")


def price_xccy_basis_swap(
    basis: CrossCurrencyBasisCurve,
    dom_ois: OISCurve,
    for_ois: OISCurve,
    maturity_date: date,
) -> float:
    """Fair basis spread (bps) for a par cross-currency basis swap.

    Args:
        basis:         Bootstrapped cross-currency basis curve.
        dom_ois:       Domestic OIS curve (for discounting the term leg).
        for_ois:       Foreign OIS curve (for discounting the base leg).
        maturity_date: Final settlement of the basis swap.

    Returns:
        Fair basis spread in basis points, signed per the curve's quote
        convention (``basis.quoted_on_foreign``).

    Raises:
        ValueError: When ``maturity_date`` is out of range.
        NotImplementedError: V2 — implementation deferred.
    """
    _ = (basis, dom_ois, for_ois, maturity_date)
    raise NotImplementedError("V2: price_xccy_basis_swap")


__all__ = [
    "price_fx_swap",
    "price_outright_forward",
    "price_xccy_basis_swap",
]
