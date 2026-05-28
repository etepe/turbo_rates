"""rates.fx.basis_curve — cross-currency basis curve (M-104, V2 scaffold).

:class:`CrossCurrencyBasisCurve` carries tenor-keyed basis spreads in basis
points and exposes a single accessor ``basis_at(date)``. Default interpolation
scheme: piecewise-linear on basis bps in calendar days (per FX-O2 default in
``docs/fx-architecture.md`` §9).

Contracts: C-105 (consumer); receives output from C-104.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class BasisPillar:
    """A single bootstrapped cross-currency basis pillar.

    Attributes:
        tenor_code:    Provider tenor identifier.
        tenor_days:    Calendar days from spot to maturity.
        maturity_date: Final settlement date of the basis swap.
        spread_bps:    Stripped basis spread in basis points.
    """

    tenor_code: str
    tenor_days: int
    maturity_date: date
    spread_bps: float


@dataclass(frozen=True, slots=True)
class CrossCurrencyBasisCurve:
    """Cross-currency basis curve for a single FX pair.

    Constructed exclusively by
    :func:`rates.fx.bootstrap_basis.build_cross_basis_curve`. The basis is
    expressed as a spread added to the leg quoted on (see FX-O5).

    Attributes:
        pair_code:        Canonical pair code.
        spot_date:        Spot settlement date; basis is zero by convention here.
        pillars_tuple:    Ordered basis pillars (ascending ``maturity_date``).
        quoted_on_foreign: True iff the basis is added to the foreign-currency leg.
    """

    pair_code: str
    spot_date: date
    pillars_tuple: tuple[BasisPillar, ...]
    quoted_on_foreign: bool

    def __post_init__(self) -> None:
        if not self.pillars_tuple:
            raise ValueError("CrossCurrencyBasisCurve requires at least one pillar")
        prev = self.spot_date
        for p in self.pillars_tuple:
            if p.maturity_date <= prev:
                raise ValueError(
                    "pillars must have strictly increasing maturity_dates "
                    f"(offender: {p.tenor_code} @ {p.maturity_date})"
                )
            prev = p.maturity_date

    # ------------------------------------------------------------------
    # Public API (C-105)
    # ------------------------------------------------------------------
    def basis_at(self, target: date) -> float:
        """Basis spread (bps) at ``target``.

        Convention: at ``spot_date`` the basis is zero (no xccy spread on the
        spot leg). Between knots — ``(spot_date, 0.0)`` and the bootstrapped
        pillars — the spread is piecewise-linear on bps weighted by calendar
        days (FX-O2 default).

        Args:
            target: Target date; must satisfy
                ``spot_date <= target <= last_pillar.maturity_date``.

        Returns:
            Basis spread in basis points.

        Raises:
            ValueError: When ``target`` is out of range.
        """
        if target < self.spot_date:
            raise ValueError(
                f"DateOutOfRange: {target} is before spot_date {self.spot_date}"
            )
        last = self.pillars_tuple[-1].maturity_date
        if target > last:
            raise ValueError(
                f"DateOutOfRange: {target} is after last pillar maturity_date {last}"
            )
        if target == self.spot_date:
            return 0.0

        # Bracket the target between two knots, treating (spot_date, 0.0) as
        # the implicit zeroth knot.
        a_date: date = self.spot_date
        a_bps: float = 0.0
        b_date: date = self.pillars_tuple[0].maturity_date
        b_bps: float = self.pillars_tuple[0].spread_bps
        for i in range(1, len(self.pillars_tuple)):
            left = self.pillars_tuple[i - 1]
            right = self.pillars_tuple[i]
            if left.maturity_date <= target <= right.maturity_date:
                a_date, a_bps = left.maturity_date, left.spread_bps
                b_date, b_bps = right.maturity_date, right.spread_bps
                break

        if target == a_date:
            return a_bps
        if target == b_date:
            return b_bps
        w = (target - a_date).days / (b_date - a_date).days
        return (1.0 - w) * a_bps + w * b_bps

    def pillars(self) -> pd.DataFrame:
        """Pillar table as a fresh DataFrame (defensive copy, safe to mutate)."""
        rows = [
            {
                "tenor_code": p.tenor_code,
                "tenor_days": p.tenor_days,
                "maturity_date": p.maturity_date,
                "spread_bps": p.spread_bps,
            }
            for p in self.pillars_tuple
        ]
        return pd.DataFrame(
            rows, columns=["tenor_code", "tenor_days", "maturity_date", "spread_bps"]
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """JSON-serialisable summary suitable for FX summary persistence."""
        return {
            "pair_code": self.pair_code,
            "spot_date": self.spot_date.isoformat(),
            "quoted_on_foreign": self.quoted_on_foreign,
            "pillars": [
                {
                    "tenor_code": p.tenor_code,
                    "tenor_days": p.tenor_days,
                    "maturity_date": p.maturity_date.isoformat(),
                    "spread_bps": p.spread_bps,
                }
                for p in self.pillars_tuple
            ],
        }


__all__ = ["BasisPillar", "CrossCurrencyBasisCurve"]
