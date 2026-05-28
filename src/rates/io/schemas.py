"""rates.io.schemas — Pydantic v2 output models + JSON Schema export (M-013).

The single :class:`Summary` model is the persistence and V3-web-UI contract:

* ``data/latest/try_ois_summary.json`` is a serialised :class:`Summary` (M-012
  ``write_summary``).
* ``docs/schemas/summary.schema.json`` is the auto-exported JSON Schema
  (regenerated via ``scripts/export_summary_schema.py``).

Architecture A4: Pydantic only at the IO boundary; the domain objects in
``rates.core`` stay frozen dataclasses. ``Summary.from_domain`` is the single
mapper from domain (OISCurve / ScenarioResult / ComparisonTable / Diagnostic)
to the Pydantic surface.

A6: ``schema_version`` is mandatory on every output. V1 == 1. A V2 read
migrator is the future hook; not implemented here.

Contract: C-012 (consumer-facing).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from rates.core.curve import OISCurve
from rates.core.diagnostics import Diagnostic
from rates.core.report import ComparisonTable
from rates.core.scenario import ScenarioResult
from rates.fx.basis_curve import CrossCurrencyBasisCurve
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import CurrencyPair

#: V1 schema version embedded in every Summary.
SCHEMA_VERSION: int = 1


_FROZEN = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Component models
# ---------------------------------------------------------------------------


class PillarOut(BaseModel):
    """Bootstrapped pillar row in the persisted Summary."""

    model_config = _FROZEN

    tenor_code: str
    tenor_days: int
    end_date: date
    rate: float
    discount_factor: float


class ForwardRow(BaseModel):
    """Forward-ladder entry in the persisted Summary."""

    model_config = _FROZEN

    label: str
    start_date: date
    end_date: date
    rate: float


class ComparisonRowOut(BaseModel):
    """Market-vs-scenario comparison row in the persisted Summary."""

    model_config = _FROZEN

    tenor: str
    kind: Literal["pillar", "forward"]
    start_date: date
    end_date: date
    days_to_maturity: int
    market_rate_act365: float
    scenario_rate_act365: float | None = None
    spread_bps: float | None = None


class DiagnosticOut(BaseModel):
    """Diagnostic record in the persisted Summary."""

    model_config = _FROZEN

    severity: Literal["WARN", "ERROR"]
    code: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class ConfigSnapshot(BaseModel):
    """Pipeline configuration snapshot embedded in every Summary for reproducibility."""

    model_config = _FROZEN

    day_count: str
    interpolation: str
    business_day_convention: str
    bullet_until: str
    calendar: str
    band_bps: int
    horizon_business_days: int
    initial_tlref: float


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


class Summary(BaseModel):
    """Top-level persisted summary. The IO contract for both human users
    (pretty-printed JSON) and the V3 web UI (typed schema).

    Fields are in stable order; reordering is a schema_version bump per A6.
    """

    model_config = _FROZEN

    schema_version: int
    valuation_date: date
    as_of_timestamp: datetime
    pillars: list[PillarOut]
    forward_ladder: list[ForwardRow]
    comparison: list[ComparisonRowOut]
    diagnostics: list[DiagnosticOut]
    config_snapshot: ConfigSnapshot

    @classmethod
    def from_domain(
        cls,
        *,
        curve: OISCurve,
        scenario: ScenarioResult,
        comparison: ComparisonTable,
        config_snapshot: ConfigSnapshot | dict[str, Any],
        diagnostics: list[Diagnostic],
        as_of_timestamp: datetime | None = None,
    ) -> Summary:
        """Build a Summary from domain objects.

        Args:
            curve:             Bootstrapped OISCurve (M-006).
            scenario:          Daily TLREF index plus optional band (M-008).
            comparison:        Market-vs-scenario comparison table (M-009).
            config_snapshot:   Either an already-built :class:`ConfigSnapshot` or
                               a plain dict that satisfies its field set.
            diagnostics:       List of domain Diagnostic records.
            as_of_timestamp:   When the snapshot was produced. Defaults to
                               ``datetime.now(UTC)`` if None.

        Returns:
            Frozen :class:`Summary` instance.
        """
        if as_of_timestamp is None:
            as_of_timestamp = datetime.now(tz=UTC)

        cfg = (
            config_snapshot
            if isinstance(config_snapshot, ConfigSnapshot)
            else ConfigSnapshot.model_validate(config_snapshot)
        )

        pillars = [
            PillarOut(
                tenor_code=p.tenor_code,
                tenor_days=p.tenor_days,
                end_date=p.end_date,
                rate=p.rate,
                discount_factor=p.discount_factor,
            )
            for p in curve.pillars_tuple
        ]
        forwards = [
            ForwardRow(
                label=label,
                start_date=s,
                end_date=e,
                rate=curve.forward(s, e),
            )
            for label, s, e in curve.forward_ladder_dates
        ]
        comparison_out = [
            ComparisonRowOut(
                tenor=r.tenor,
                kind=r.kind,
                start_date=r.start_date,
                end_date=r.end_date,
                days_to_maturity=r.days_to_maturity,
                market_rate_act365=r.market_rate_act365,
                scenario_rate_act365=r.scenario_rate_act365,
                spread_bps=r.spread_bps,
            )
            for r in comparison.rows
        ]
        diagnostics_out = [
            DiagnosticOut(
                severity=d.severity.value,  # "WARN" | "ERROR"
                code=d.code,
                message=d.message,
                context=d.context,
            )
            for d in diagnostics
        ]

        # ``scenario`` reserved for future use (e.g. embedding the daily index in
        # Summary); current shape leaves the daily grid to the Parquet partition
        # writer in M-012 to keep summary.json human-skim-friendly (~5-10 KB).
        _ = scenario

        return cls(
            schema_version=SCHEMA_VERSION,
            valuation_date=curve.valuation_date,
            as_of_timestamp=as_of_timestamp,
            pillars=pillars,
            forward_ladder=forwards,
            comparison=comparison_out,
            diagnostics=diagnostics_out,
            config_snapshot=cfg,
        )


# ---------------------------------------------------------------------------
# FX persistence schemas (M-109, v0.3.0)
# ---------------------------------------------------------------------------
#
# Persistence-side counterparts to rates.fx domain types. Consumed by M-110
# (rates.io.persistence FX extension). The from_domain mapper lives there to
# avoid pulling rates.fx imports into this module's V1 surface.
#
# Contract: feeds C-102 (rates.app → rates.io.persistence FX).

#: FX summary schema version embedded in every FXSummary. Independent of
#: SCHEMA_VERSION (V1 OIS) so the two can evolve separately.
#:
#: v2 (M-109, V0.4): embeds the dom/for OIS pillar lists + per-ccy FXOisMeta and
#: a per-basis-pillar strip residual so the persisted summary is the single audit
#: record of which curves produced the stripped basis (DV-2 / D-16). New fields
#: are appended; existing fields keep their order.
FX_SCHEMA_VERSION: int = 2


class FXForwardPillarOut(BaseModel):
    """Forward-curve pillar in the persisted FXSummary."""

    model_config = _FROZEN

    tenor_code: str
    tenor_days: int
    settle_date: date
    forward_rate: float


class FXBasisPillarOut(BaseModel):
    """Cross-currency basis pillar in the persisted FXSummary."""

    model_config = _FROZEN

    tenor_code: str
    tenor_days: int
    maturity_date: date
    spread_bps: float
    quoted_on_foreign: bool
    # |NPV(b_n*)| at strip time (D-13 reprice residual, v2). Phase 3 persists a
    # 0.0 placeholder (the reprice assert already pins |NPV| < 1e-9); Phase 4
    # (M-111) wires the real per-pillar values from the strip.
    strip_residual: float


class FXOisMeta(BaseModel):
    """Per-currency OIS curve metadata for FXSummary v2 reconstruction (D-16).

    Carries exactly the scalars an OIS curve needs to be rebuilt alongside its
    persisted ``PillarOut`` list (C-110, Phase 4): the valuation date, the native
    day-count, and the interpolation scheme.
    """

    model_config = _FROZEN

    valuation_date: date
    day_count: str          # OISCurve.day_count.value ("Act/360" | "Act/365")
    interpolation: str      # OISCurve.interp ("log_linear_df" | "linear_zero")


class FXParityCheckRow(BaseModel):
    """Per-pillar covered-interest-parity check result (M-105)."""

    model_config = _FROZEN

    tenor_code: str
    settle_date: date
    quoted_forward: float
    parity_forward: float
    diff_bps_of_spot: float


class FXConfigSnapshot(BaseModel):
    """FX pipeline configuration snapshot embedded in every FXSummary."""

    model_config = _FROZEN

    pair_code: str
    domestic_currency: str
    foreign_currency: str
    quote_convention: Literal["direct", "indirect"]
    spot_lag_days: int
    settlement_calendars: list[str]
    forward_point_scale: int


class FXSummary(BaseModel):
    """Top-level persisted FX summary.

    Mirrors :class:`Summary` (V1 OIS) at the FX layer. Fields are in stable
    order; reordering bumps ``schema_version`` per the same policy.
    """

    model_config = _FROZEN

    schema_version: int
    pair_code: str
    valuation_date: date
    spot_date: date
    as_of_timestamp: datetime
    spot_rate: float
    forward_pillars: list[FXForwardPillarOut]
    basis_pillars: list[FXBasisPillarOut]
    parity_checks: list[FXParityCheckRow]
    diagnostics: list[DiagnosticOut]
    config_snapshot: FXConfigSnapshot
    # v2 (M-109): dual OIS pillar lists + per-ccy meta for audit / MtM-readiness
    # (D-16, A-8). Appended after the v1 fields to keep their order stable.
    dom_ois_pillars: list[PillarOut]
    for_ois_pillars: list[PillarOut]
    dom_ois_meta: FXOisMeta
    for_ois_meta: FXOisMeta

    @classmethod
    def from_domain(
        cls,
        *,
        pair: CurrencyPair,
        valuation_date: date,
        forward: FXForwardCurve,
        basis: CrossCurrencyBasisCurve | None,
        parity_checks: list[FXParityCheckRow],
        conv: FXConvention,
        diagnostics: list[Diagnostic],
        dom_ois: OISCurve,
        for_ois: OISCurve,
        strip_residuals: dict[str, float],
        as_of_timestamp: datetime | None = None,
    ) -> FXSummary:
        """Build an FXSummary from domain objects (C-102', schema v2).

        Args:
            pair:             Currency pair (drives domestic/foreign labels).
            valuation_date:   As-of date of the snapshot.
            forward:          Bootstrapped FX forward curve (M-103).
            basis:            Bootstrapped cross-currency basis curve (M-104),
                              or ``None`` when no XCCY_BASIS rows were present.
            parity_checks:    Pillar-level parity check rows (built by the
                              orchestrator from M-105 diagnostics).
            conv:             FX convention block for the pair (M-102).
            diagnostics:      List of domain Diagnostic records (collector
                              snapshot at write time).
            dom_ois:          Domestic OIS curve; its pillars + meta are embedded
                              for audit / MtM-readiness (D-16, A-8).
            for_ois:          Foreign OIS curve; embedded likewise.
            strip_residuals:  Per-pillar ``tenor_code -> |NPV(b_n*)|`` strip
                              residuals (D-13). A missing tenor maps to ``0.0``.
                              Phase 3 passes an empty dict (placeholder); Phase 4
                              (M-111) supplies the real values.
            as_of_timestamp:  When the snapshot was produced. Defaults to
                              ``datetime.now(UTC)``.

        Returns:
            Frozen :class:`FXSummary` instance.
        """
        if as_of_timestamp is None:
            as_of_timestamp = datetime.now(tz=UTC)

        forward_pillars = [
            FXForwardPillarOut(
                tenor_code=p.tenor_code,
                tenor_days=p.tenor_days,
                settle_date=p.settle_date,
                forward_rate=p.forward_rate,
            )
            for p in forward.pillars_tuple
        ]
        basis_pillars = (
            [
                FXBasisPillarOut(
                    tenor_code=p.tenor_code,
                    tenor_days=p.tenor_days,
                    maturity_date=p.maturity_date,
                    spread_bps=p.spread_bps,
                    quoted_on_foreign=basis.quoted_on_foreign,
                    strip_residual=strip_residuals.get(p.tenor_code, 0.0),
                )
                for p in basis.pillars_tuple
            ]
            if basis is not None
            else []
        )
        # OIS pillars reuse the V1 PillarOut model (D-16); same Pillar->PillarOut
        # map as Summary.from_domain.
        dom_ois_pillars = [
            PillarOut(
                tenor_code=p.tenor_code,
                tenor_days=p.tenor_days,
                end_date=p.end_date,
                rate=p.rate,
                discount_factor=p.discount_factor,
            )
            for p in dom_ois.pillars_tuple
        ]
        for_ois_pillars = [
            PillarOut(
                tenor_code=p.tenor_code,
                tenor_days=p.tenor_days,
                end_date=p.end_date,
                rate=p.rate,
                discount_factor=p.discount_factor,
            )
            for p in for_ois.pillars_tuple
        ]
        dom_ois_meta = FXOisMeta(
            valuation_date=dom_ois.valuation_date,
            day_count=dom_ois.day_count.value,
            interpolation=dom_ois.interp,
        )
        for_ois_meta = FXOisMeta(
            valuation_date=for_ois.valuation_date,
            day_count=for_ois.day_count.value,
            interpolation=for_ois.interp,
        )
        diagnostics_out = [
            DiagnosticOut(
                severity=d.severity.value,  # "WARN" | "ERROR"
                code=d.code,
                message=d.message,
                context=d.context,
            )
            for d in diagnostics
        ]
        cfg = FXConfigSnapshot(
            pair_code=conv.pair_code,
            domestic_currency=pair.domestic,
            foreign_currency=pair.foreign,
            # QuoteConvention is a StrEnum ("direct" | "indirect"); Pydantic
            # accepts the .value at the field boundary and validates the literal.
            quote_convention=conv.quote_convention.value,
            spot_lag_days=conv.spot_lag_days,
            settlement_calendars=list(conv.settlement_calendars),
            forward_point_scale=conv.forward_point_scale,
        )

        return cls(
            schema_version=FX_SCHEMA_VERSION,
            pair_code=pair.code,
            valuation_date=valuation_date,
            spot_date=forward.spot_date,
            as_of_timestamp=as_of_timestamp,
            spot_rate=forward.spot_rate,
            forward_pillars=forward_pillars,
            basis_pillars=basis_pillars,
            parity_checks=parity_checks,
            diagnostics=diagnostics_out,
            config_snapshot=cfg,
            dom_ois_pillars=dom_ois_pillars,
            for_ois_pillars=for_ois_pillars,
            dom_ois_meta=dom_ois_meta,
            for_ois_meta=for_ois_meta,
        )


__all__ = [
    "FX_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ComparisonRowOut",
    "ConfigSnapshot",
    "DiagnosticOut",
    "FXBasisPillarOut",
    "FXConfigSnapshot",
    "FXForwardPillarOut",
    "FXOisMeta",
    "FXParityCheckRow",
    "FXSummary",
    "ForwardRow",
    "PillarOut",
    "Summary",
]
