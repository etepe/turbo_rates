"""rates.fx.forward_curve — FX forward curve domain object (M-103, V2 scaffold).

:class:`FXForwardCurve` is a first-class first-class dataclass: callers ask
``forward_at(settle_date)`` without seeing the underlying OIS-curve
decomposition. The bootstrap (M-105) builds the curve from spot + market
forward points + dual OIS curves under covered interest parity.

Interpolation scheme: log-linear in ``log(F/S)`` between pillar dates.
Anchors at the spot date with ``forward_at(spot_date) == spot_rate``.

Contracts: C-103 (consumer); receives output from C-102.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True, slots=True)
class FXForwardPillar:
    """A single bootstrapped FX forward pillar.

    Attributes:
        tenor_code:    Provider tenor identifier (e.g. ``"USDTRY1M"``).
        tenor_days:    Calendar days from spot to settlement.
        settle_date:   Forward settlement date.
        forward_rate:  Outright forward rate (domestic per foreign).
    """

    tenor_code: str
    tenor_days: int
    settle_date: date
    forward_rate: float


@dataclass(frozen=True, slots=True)
class FXForwardCurve:
    """Bootstrapped outright-forward curve for a single FX pair.

    Constructed exclusively by :func:`rates.fx.bootstrap_forward.build_fx_forward_curve`.
    Callers should treat the underlying OIS curves as an implementation detail.

    Attributes:
        pair_code:      Canonical pair code (e.g. ``"USDTRY"``).
        spot_date:      Spot settlement date; ``forward_at(spot_date) == spot_rate``.
        spot_rate:      Outright spot rate.
        pillars_tuple:  Ordered pillars (ascending ``settle_date``).
    """

    pair_code: str
    spot_date: date
    spot_rate: float
    pillars_tuple: tuple[FXForwardPillar, ...]

    def __post_init__(self) -> None:
        if not self.pillars_tuple:
            raise ValueError("FXForwardCurve requires at least one pillar")
        if self.spot_rate <= 0.0:
            raise ValueError(f"FXForwardCurve.spot_rate must be > 0 (got {self.spot_rate})")
        prev = self.spot_date
        for p in self.pillars_tuple:
            if p.settle_date <= prev:
                raise ValueError(
                    "pillars must have strictly increasing settle_dates "
                    f"(offender: {p.tenor_code} @ {p.settle_date})"
                )
            prev = p.settle_date

    # ------------------------------------------------------------------
    # Public API (C-103)
    # ------------------------------------------------------------------
    def forward_at(self, settle_date: date) -> float:
        """Outright forward rate at the given settlement date.

        Args:
            settle_date: Target settlement; must satisfy
                ``spot_date <= settle_date <= last_pillar.settle_date``.

        Returns:
            Outright forward rate (domestic per foreign).

        Raises:
            ValueError: When ``settle_date`` is out of range.
        """
        if settle_date < self.spot_date:
            raise ValueError(
                f"DateOutOfRange: {settle_date} is before spot_date {self.spot_date}"
            )
        last = self.pillars_tuple[-1].settle_date
        if settle_date > last:
            raise ValueError(
                f"DateOutOfRange: {settle_date} is after last pillar settle_date {last}"
            )
        if settle_date == self.spot_date:
            return self.spot_rate

        # Bracket the target between two knots, treating (spot_date, spot_rate)
        # as the implicit zeroth knot. Interpolation is log-linear in log(F/S)
        # weighted by calendar days.
        a_date: date = self.spot_date
        a_rate: float = self.spot_rate
        b_date: date = self.pillars_tuple[0].settle_date
        b_rate: float = self.pillars_tuple[0].forward_rate
        for i in range(1, len(self.pillars_tuple)):
            left = self.pillars_tuple[i - 1]
            right = self.pillars_tuple[i]
            if left.settle_date <= settle_date <= right.settle_date:
                a_date, a_rate = left.settle_date, left.forward_rate
                b_date, b_rate = right.settle_date, right.forward_rate
                break

        if settle_date == a_date:
            return a_rate
        if settle_date == b_date:
            return b_rate

        days_total = (b_date - a_date).days
        days_to_d = (settle_date - a_date).days
        w = days_to_d / days_total
        log_a = math.log(a_rate / self.spot_rate)
        log_b = math.log(b_rate / self.spot_rate)
        return self.spot_rate * math.exp((1.0 - w) * log_a + w * log_b)

    def pillars(self) -> pd.DataFrame:
        """Pillar table as a fresh DataFrame (defensive copy, safe to mutate)."""
        rows = [
            {
                "tenor_code": p.tenor_code,
                "tenor_days": p.tenor_days,
                "settle_date": p.settle_date,
                "forward_rate": p.forward_rate,
            }
            for p in self.pillars_tuple
        ]
        return pd.DataFrame(
            rows, columns=["tenor_code", "tenor_days", "settle_date", "forward_rate"]
        )

    def to_summary_dict(self) -> dict[str, Any]:
        """JSON-serialisable summary suitable for FX summary persistence."""
        return {
            "pair_code": self.pair_code,
            "spot_date": self.spot_date.isoformat(),
            "spot_rate": self.spot_rate,
            "pillars": [
                {
                    "tenor_code": p.tenor_code,
                    "tenor_days": p.tenor_days,
                    "settle_date": p.settle_date.isoformat(),
                    "forward_rate": p.forward_rate,
                }
                for p in self.pillars_tuple
            ],
        }


__all__ = ["FXForwardCurve", "FXForwardPillar"]
