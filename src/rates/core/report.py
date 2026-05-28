"""rates.core.report — market-vs-scenario comparison table (M-009).

Produces a tenor-by-tenor :class:`ComparisonTable` comparing market spot/forward
rates (from the bootstrapped :class:`OISCurve`) against scenario-implied
spot/forward rates (from :class:`ScenarioResult`). Both rates are expressed in a
common day-count basis (Act/365 — the TLREF basis per ``spread_reporting``
convention and G6) so that the spread in bps is meaningful.

Coverage per F-012 acceptance:
* Every bootstrapped pillar (spot leg).
* The 15-entry canonical forward ladder (1x2, …, 11x12, 1x3, 3x6, 6x9, 9x12).

Out-of-scenario-horizon handling: if a pillar/forward maturity exceeds the
scenario grid's last date, the corresponding ``scenario_rate_act365`` and
``spread_bps`` are emitted as ``None`` so callers can render "n/a" without the
table losing rows.

Contract: C-011. Depends_on: M-001 conventions, M-003 daycount, M-006 curve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from rates.core.conventions import Conventions
from rates.core.curve import OISCurve
from rates.core.scenario import DailyIndex, ScenarioResult

#: Common reporting basis (Act/365 — TLREF basis per G6).
_REPORTING_BASIS_DAYS: int = 365

RowKind = Literal["pillar", "forward"]


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """A single tenor row in the comparison table.

    Attributes:
        tenor:                  Provider tenor code (pillar) or forward label
                                ("1x3", etc.).
        kind:                   ``"pillar"`` or ``"forward"``.
        start_date:             Spot/forward start (valuation_date for spots).
        end_date:               Spot/forward maturity.
        days_to_maturity:       ``(end_date - valuation_date).days``.
        market_rate_act365:     Curve rate converted to Act/365.
        scenario_rate_act365:   Scenario-implied rate in Act/365 (or None if
                                the maturity date is outside the scenario grid).
        spread_bps:             ``(market - scenario) * 1e4`` (or None when
                                ``scenario_rate_act365`` is None).
    """

    tenor: str
    kind: RowKind
    start_date: date
    end_date: date
    days_to_maturity: int
    market_rate_act365: float
    scenario_rate_act365: float | None
    spread_bps: float | None


@dataclass(frozen=True, slots=True)
class ComparisonTable:
    """Ordered collection of :class:`ComparisonRow`."""

    rows: tuple[ComparisonRow, ...]

    def to_dataframe(self) -> pd.DataFrame:
        """Return a fresh DataFrame mirroring :class:`ComparisonRow` field order."""
        return pd.DataFrame(
            [
                {
                    "tenor": r.tenor,
                    "kind": r.kind,
                    "start_date": r.start_date,
                    "end_date": r.end_date,
                    "days_to_maturity": r.days_to_maturity,
                    "market_rate_act365": r.market_rate_act365,
                    "scenario_rate_act365": r.scenario_rate_act365,
                    "spread_bps": r.spread_bps,
                }
                for r in self.rows
            ],
            columns=[
                "tenor",
                "kind",
                "start_date",
                "end_date",
                "days_to_maturity",
                "market_rate_act365",
                "scenario_rate_act365",
                "spread_bps",
            ],
        )

    def pretty(self) -> str:
        """Aligned ASCII rendering for stdout (suppressible via CLI ``--quiet``)."""
        header = (
            f"{'tenor':<12} {'kind':<8} {'days':>6} "
            f"{'market(%)':>12} {'scen(%)':>12} {'spread(bp)':>14}"
        )
        sep = "-" * len(header)
        lines = [header, sep]
        for r in self.rows:
            scen = (
                f"{r.scenario_rate_act365 * 100:>12.4f}"
                if r.scenario_rate_act365 is not None
                else f"{'n/a':>12}"
            )
            spread = f"{r.spread_bps:>14.2f}" if r.spread_bps is not None else f"{'n/a':>14}"
            lines.append(
                f"{r.tenor:<12} {r.kind:<8} {r.days_to_maturity:>6d} "
                f"{r.market_rate_act365 * 100:>12.4f} {scen} {spread}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _index_lookup(idx: DailyIndex) -> dict[date, float]:
    """Build a date→value map for O(1) lookup of the scenario index."""
    return dict(zip(idx.dates, idx.values, strict=True))


def _act365_spot(df: float, days: int) -> float:
    """Simple Act/365 spot rate from a DF over ``days`` calendar days."""
    return (1.0 / df - 1.0) * _REPORTING_BASIS_DAYS / days


def _act365_forward(df_s: float, df_e: float, span_days: int) -> float:
    """Simple Act/365 forward rate from two DFs over ``span_days`` calendar days."""
    return (df_s / df_e - 1.0) * _REPORTING_BASIS_DAYS / span_days


def _scenario_spot(val_date: date, end_date: date, lut: dict[date, float]) -> float | None:
    """Scenario-implied spot rate Act/365 from val_date to end_date.

    Returns ``None`` if ``end_date`` is not in the scenario grid.
    """
    if end_date not in lut:
        return None
    idx_end = lut[end_date]
    days = (end_date - val_date).days
    if days <= 0:
        return None
    return (idx_end - 1.0) * _REPORTING_BASIS_DAYS / days


def _scenario_forward(start_date: date, end_date: date, lut: dict[date, float]) -> float | None:
    """Scenario-implied forward rate Act/365 between two grid dates."""
    if start_date not in lut or end_date not in lut:
        return None
    idx_s = lut[start_date]
    idx_e = lut[end_date]
    days = (end_date - start_date).days
    if days <= 0:
        return None
    return (idx_e / idx_s - 1.0) * _REPORTING_BASIS_DAYS / days


# ---------------------------------------------------------------------------
# Public API (C-011)
# ---------------------------------------------------------------------------


def build_comparison(
    curve: OISCurve,
    scenario: ScenarioResult,
    conventions: Conventions,
) -> ComparisonTable:
    """Compare market (curve) and scenario rates tenor-by-tenor in Act/365.

    Args:
        curve:        Bootstrapped OIS curve (M-006).
        scenario:     Mid daily TLREF index plus optional band (M-008).
        conventions:  Loaded conventions (kept on the signature per C-011; the
                      reporting basis is fixed at Act/365 per G6).

    Returns:
        Frozen :class:`ComparisonTable` covering each pillar plus the canonical
        15-entry forward ladder.
    """
    # ``conventions`` is required by C-011 for symmetry with other contracts and
    # to allow future per-currency basis switches; the V1 spread basis is fixed.
    _ = conventions

    val = curve.valuation_date
    lut = _index_lookup(scenario.mid)

    rows: list[ComparisonRow] = []

    for p in curve.pillars_tuple:
        days = (p.end_date - val).days
        market = _act365_spot(p.discount_factor, days)
        scen = _scenario_spot(val, p.end_date, lut)
        spread = (market - scen) * 1e4 if scen is not None else None
        rows.append(
            ComparisonRow(
                tenor=p.tenor_code,
                kind="pillar",
                start_date=val,
                end_date=p.end_date,
                days_to_maturity=days,
                market_rate_act365=market,
                scenario_rate_act365=scen,
                spread_bps=spread,
            )
        )

    for label, s, e in curve.forward_ladder_dates:
        df_s = curve.df_at(s)
        df_e = curve.df_at(e)
        span = (e - s).days
        market = _act365_forward(df_s, df_e, span)
        scen = _scenario_forward(s, e, lut)
        spread = (market - scen) * 1e4 if scen is not None else None
        rows.append(
            ComparisonRow(
                tenor=label,
                kind="forward",
                start_date=s,
                end_date=e,
                days_to_maturity=(e - val).days,
                market_rate_act365=market,
                scenario_rate_act365=scen,
                spread_bps=spread,
            )
        )

    return ComparisonTable(rows=tuple(rows))


__all__ = [
    "ComparisonRow",
    "ComparisonTable",
    "RowKind",
    "build_comparison",
]
