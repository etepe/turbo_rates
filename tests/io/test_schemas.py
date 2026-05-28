"""Pydantic v2 output schemas (M-013, contract C-012).

Covers from_domain mapping, schema_version invariant, frozen-extra-forbid,
JSON round-trip, and model_json_schema() shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rates.io.schemas import (
    FX_SCHEMA_VERSION,
    SCHEMA_VERSION,
    ComparisonRowOut,
    ConfigSnapshot,
    DiagnosticOut,
    ForwardRow,
    FXBasisPillarOut,
    FXConfigSnapshot,
    FXForwardPillarOut,
    FXOisMeta,
    FXParityCheckRow,
    FXSummary,
    PillarOut,
    Summary,
)


@pytest.mark.phase4
def test_schema_version_constant():
    assert SCHEMA_VERSION == 1


@pytest.mark.phase4
def test_summary_from_domain_happy_path(
    hand_curve,
    hand_scenario_with_band,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_with_band,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
        as_of_timestamp=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
    )

    assert summary.schema_version == 1
    assert summary.valuation_date == hand_curve.valuation_date
    assert summary.as_of_timestamp == datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)

    assert len(summary.pillars) == len(hand_curve.pillars_tuple)
    assert all(isinstance(p, PillarOut) for p in summary.pillars)

    assert len(summary.forward_ladder) == len(hand_curve.forward_ladder_dates)
    assert all(isinstance(f, ForwardRow) for f in summary.forward_ladder)

    assert len(summary.comparison) == len(hand_comparison.rows)
    assert all(isinstance(c, ComparisonRowOut) for c in summary.comparison)

    assert len(summary.diagnostics) == 2
    assert summary.diagnostics[0].severity == "WARN"
    assert summary.diagnostics[1].severity == "ERROR"

    assert isinstance(summary.config_snapshot, ConfigSnapshot)


@pytest.mark.phase4
def test_summary_from_domain_accepts_dict_for_config(
    hand_curve, hand_scenario_mid_only, hand_comparison, hand_diagnostics
):
    cfg_dict = {
        "day_count": "Act/360",
        "interpolation": "log_linear_df",
        "business_day_convention": "modified_following",
        "bullet_until": "1Y",
        "calendar": "TR",
        "band_bps": 0,
        "horizon_business_days": 1,
        "initial_tlref": 0.45,
    }
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=cfg_dict,
        diagnostics=hand_diagnostics,
    )
    assert summary.config_snapshot.band_bps == 0


@pytest.mark.phase4
def test_summary_from_domain_default_as_of_is_utc_now(
    hand_curve,
    hand_scenario_mid_only,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    before = datetime.now(tz=UTC)
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
    )
    after = datetime.now(tz=UTC)
    assert summary.as_of_timestamp.tzinfo is not None
    assert before <= summary.as_of_timestamp <= after


@pytest.mark.phase4
def test_summary_is_frozen(
    hand_curve,
    hand_scenario_mid_only,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
    )
    with pytest.raises(ValidationError):
        summary.schema_version = 2  # type: ignore[misc]


@pytest.mark.phase4
def test_summary_rejects_extra_fields():
    with pytest.raises(ValidationError):
        Summary.model_validate(
            {
                "schema_version": 1,
                "valuation_date": "2026-06-12",
                "as_of_timestamp": "2026-06-12T00:00:00Z",
                "pillars": [],
                "forward_ladder": [],
                "comparison": [],
                "diagnostics": [],
                "config_snapshot": {
                    "day_count": "Act/360",
                    "interpolation": "log_linear_df",
                    "business_day_convention": "modified_following",
                    "bullet_until": "1Y",
                    "calendar": "TR",
                    "band_bps": 0,
                    "horizon_business_days": 0,
                    "initial_tlref": 0.45,
                },
                "EXTRA": "boom",
            }
        )


@pytest.mark.phase4
def test_summary_json_round_trip(
    hand_curve,
    hand_scenario_with_band,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_with_band,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
        as_of_timestamp=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
    )
    payload = summary.model_dump_json()
    decoded = json.loads(payload)
    assert decoded["schema_version"] == 1
    assert decoded["valuation_date"] == "2026-06-12"
    rebuilt = Summary.model_validate_json(payload)
    assert rebuilt == summary


@pytest.mark.phase4
def test_comparison_row_optional_fields_preserve_none(
    hand_curve,
    hand_scenario_mid_only,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    """ComparisonRow with scenario_rate=None survives JSON round-trip as null."""
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
    )
    one_year = next(r for r in summary.comparison if r.tenor == "TYSO1Y")
    assert one_year.scenario_rate_act365 is None
    assert one_year.spread_bps is None

    payload = json.loads(summary.model_dump_json())
    one_year_json = next(r for r in payload["comparison"] if r["tenor"] == "TYSO1Y")
    assert one_year_json["scenario_rate_act365"] is None
    assert one_year_json["spread_bps"] is None


@pytest.mark.phase4
def test_diagnostic_severity_literal_validation():
    """Only WARN/ERROR allowed in the output enum."""
    DiagnosticOut(severity="WARN", code="X", message="msg")
    DiagnosticOut(severity="ERROR", code="X", message="msg")
    with pytest.raises(ValidationError):
        DiagnosticOut(severity="INFO", code="X", message="msg")  # type: ignore[arg-type]


@pytest.mark.phase4
def test_model_json_schema_has_top_level_fields():
    schema = Summary.model_json_schema()
    assert schema["type"] == "object"
    props = schema["properties"]
    expected_keys = {
        "schema_version",
        "valuation_date",
        "as_of_timestamp",
        "pillars",
        "forward_ladder",
        "comparison",
        "diagnostics",
        "config_snapshot",
    }
    assert expected_keys.issubset(props.keys())
    # ConfigSnapshot, PillarOut, etc. should appear as $defs.
    defs = schema.get("$defs", {})
    assert "ConfigSnapshot" in defs
    assert "PillarOut" in defs
    assert "ComparisonRowOut" in defs


# ---------------------------------------------------------------------------
# FX schemas (M-109, v0.3.0)
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_fx_schema_version_constant() -> None:
    assert FX_SCHEMA_VERSION == 2


@pytest.mark.phase8
def test_fx_summary_round_trip() -> None:
    """FXSummary builds, JSON-serializes, and round-trips back to an equal model."""
    from datetime import date

    summary = FXSummary(
        schema_version=FX_SCHEMA_VERSION,
        pair_code="USDTRY",
        valuation_date=date(2026, 5, 28),
        spot_date=date(2026, 6, 1),
        as_of_timestamp=datetime(2026, 5, 28, 16, 30, tzinfo=UTC),
        spot_rate=32.4520,
        forward_pillars=[
            FXForwardPillarOut(
                tenor_code="1M",
                tenor_days=30,
                settle_date=date(2026, 7, 1),
                forward_rate=32.6355,
            )
        ],
        basis_pillars=[
            FXBasisPillarOut(
                tenor_code="1Y",
                tenor_days=365,
                maturity_date=date(2027, 6, 1),
                spread_bps=-180.0,
                quoted_on_foreign=True,
                strip_residual=0.0,
            )
        ],
        parity_checks=[
            FXParityCheckRow(
                tenor_code="1M",
                settle_date=date(2026, 7, 1),
                quoted_forward=32.6355,
                parity_forward=32.6354,
                diff_bps_of_spot=0.03,
            )
        ],
        diagnostics=[
            DiagnosticOut(severity="WARN", code="FX_PARITY_MISMATCH", message="x", context={}),
        ],
        config_snapshot=FXConfigSnapshot(
            pair_code="USDTRY",
            domestic_currency="TRY",
            foreign_currency="USD",
            quote_convention="direct",
            spot_lag_days=2,
            settlement_calendars=["TR", "US"],
            forward_point_scale=10000,
        ),
        dom_ois_pillars=[
            PillarOut(
                tenor_code="TYSO1Y",
                tenor_days=365,
                end_date=date(2027, 5, 28),
                rate=0.42,
                discount_factor=0.70,
            )
        ],
        for_ois_pillars=[
            PillarOut(
                tenor_code="USSO1Y",
                tenor_days=365,
                end_date=date(2027, 5, 28),
                rate=0.05,
                discount_factor=0.952,
            )
        ],
        dom_ois_meta=FXOisMeta(
            valuation_date=date(2026, 5, 28),
            day_count="Act/360",
            interpolation="log_linear_df",
        ),
        for_ois_meta=FXOisMeta(
            valuation_date=date(2026, 5, 28),
            day_count="Act/365",
            interpolation="linear_zero",
        ),
    )

    payload = summary.model_dump_json()
    parsed = FXSummary.model_validate_json(payload)
    assert parsed == summary


@pytest.mark.phase8
def test_fx_summary_forbids_extra_fields() -> None:
    """The frozen-extra-forbid invariant holds for FXSummary too."""
    from datetime import date

    valid_payload = {
        "schema_version": FX_SCHEMA_VERSION,
        "pair_code": "USDTRY",
        "valuation_date": date(2026, 5, 28),
        "spot_date": date(2026, 6, 1),
        "as_of_timestamp": datetime(2026, 5, 28, 16, 30, tzinfo=UTC),
        "spot_rate": 32.4520,
        "forward_pillars": [],
        "basis_pillars": [],
        "parity_checks": [],
        "diagnostics": [],
        "config_snapshot": {
            "pair_code": "USDTRY",
            "domestic_currency": "TRY",
            "foreign_currency": "USD",
            "quote_convention": "direct",
            "spot_lag_days": 2,
            "settlement_calendars": ["TR", "US"],
            "forward_point_scale": 10000,
        },
        "dom_ois_pillars": [],
        "for_ois_pillars": [],
        "dom_ois_meta": {
            "valuation_date": date(2026, 5, 28),
            "day_count": "Act/360",
            "interpolation": "log_linear_df",
        },
        "for_ois_meta": {
            "valuation_date": date(2026, 5, 28),
            "day_count": "Act/365",
            "interpolation": "linear_zero",
        },
        # unexpected:
        "rogue_field": "boo",
    }
    with pytest.raises(ValidationError):
        FXSummary.model_validate(valid_payload)
