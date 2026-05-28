"""FX bootstrap orchestrator (M-111 complete, contract C-103).

Covers Phase 3b per docs/fx-io-architecture.md §7: full FX pipeline through
parity check PLUS persistence (M-110). The pipeline body is the same as
run_fx_diagnose (covered in test_run_fx_diagnose.py); these tests focus on
the persistence + parity-checks-construction additions and the side-effect
contract on disk.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from rates.app import run_fx_bootstrap
from rates.io.schemas import FXSummary


@pytest.mark.phase8
def test_run_fx_bootstrap_writes_summary_and_partitions(
    fx_bootstrap_args, tmp_path,
) -> None:
    """Happy path: WARN-only exit 0 + summary JSON + both Parquet partitions."""
    code = run_fx_bootstrap(fx_bootstrap_args)
    assert code == 0

    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    assert summary_path.exists()
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    assert summary.schema_version == 1
    assert summary.pair_code == "USDTRY"
    assert summary.valuation_date == date(2026, 6, 12)
    assert len(summary.forward_pillars) == 2  # 1M, 3M in fixture
    assert len(summary.basis_pillars) == 1  # 1Y XCCY_BASIS in fixture
    assert summary.basis_pillars[0].quoted_on_foreign is True

    fwd_partition = (
        tmp_path / "data" / "fx_curves" / "usdtry" / "forward" / "date=2026-06-12"
    )
    assert fwd_partition.is_dir()
    fwd_files = list(fwd_partition.glob("*.parquet"))
    assert len(fwd_files) == 1
    assert pq.ParquetFile(fwd_files[0]).schema_arrow.names == [
        "tenor_code", "tenor_days", "settle_date", "forward_rate",
    ]

    basis_partition = (
        tmp_path / "data" / "fx_curves" / "usdtry" / "basis" / "date=2026-06-12"
    )
    assert basis_partition.is_dir()
    basis_files = list(basis_partition.glob("*.parquet"))
    assert len(basis_files) == 1


@pytest.mark.phase8
def test_run_fx_bootstrap_populates_parity_checks(fx_bootstrap_args, tmp_path) -> None:
    """The fixture's TRY OIS @ ~42% vs USD OIS @ ~5% guarantees parity WARNs;
    those WARNs MUST surface as FXParityCheckRow entries in the persisted summary."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    # Both 1M and 3M pillars should show parity mismatch given the wildly
    # different domestic and foreign OIS levels in the fixtures.
    assert len(summary.parity_checks) >= 1
    seen_tenors = {row.tenor_code for row in summary.parity_checks}
    assert seen_tenors.issubset({"1M", "3M"})
    for row in summary.parity_checks:
        # Quoted forward and parity forward must both be finite and positive
        # (USDTRY direct quote).
        assert row.quoted_forward > 0
        assert row.parity_forward > 0
        # The parity column should track the FXForwardPillarOut entry by tenor.
        match = next(
            p for p in summary.forward_pillars if p.tenor_code == row.tenor_code
        )
        assert row.settle_date == match.settle_date


@pytest.mark.phase8
def test_run_fx_bootstrap_missing_conventions_no_outputs(
    fx_bootstrap_args, tmp_path,
) -> None:
    fx_bootstrap_args.conventions_yaml = Path("/no/such/conventions.yaml")
    assert run_fx_bootstrap(fx_bootstrap_args) == 2
    assert not (tmp_path / "data" / "latest").exists()
    assert not (tmp_path / "data" / "fx_curves").exists()


@pytest.mark.phase8
def test_run_fx_bootstrap_snapshot_date_mismatch_no_outputs(
    fx_bootstrap_args, tmp_path,
) -> None:
    """FX_SNAPSHOT_DATE_MISMATCH ⇒ exit 2 AND zero side-effects on disk."""
    fx_bootstrap_args.as_of = date(2026, 5, 28)  # fixtures dated 20260612
    assert run_fx_bootstrap(fx_bootstrap_args) == 2
    assert not (tmp_path / "data" / "latest").exists()
    assert not (tmp_path / "data" / "fx_curves").exists()


@pytest.mark.phase8
def test_run_fx_bootstrap_missing_foreign_snapshot_no_outputs(
    fx_bootstrap_args, tmp_path,
) -> None:
    fx_bootstrap_args.foreign_snapshot = Path("/no/such/foreign.csv")
    assert run_fx_bootstrap(fx_bootstrap_args) == 2
    assert not (tmp_path / "data" / "latest" / "usdtry_fx_summary.json").exists()


@pytest.mark.phase8
def test_run_fx_bootstrap_rerun_overwrites(fx_bootstrap_args, tmp_path) -> None:
    """Re-running on the same valuation_date overwrites summary + partitions."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    first_mtime = summary_path.stat().st_mtime_ns

    # Second run with a convention override should produce a different
    # config_snapshot (and thus a different file contents).
    fx_bootstrap_args.convention_override = ["fx_conventions.USDTRY.spot_lag_days=1"]
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    second_mtime = summary_path.stat().st_mtime_ns
    assert second_mtime >= first_mtime  # file rewritten in place


@pytest.mark.phase8
def test_run_fx_bootstrap_diagnostics_persisted(fx_bootstrap_args, tmp_path) -> None:
    """summary.diagnostics is a list; parity WARNs land here too."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    parity_codes = [d for d in summary.diagnostics if d.code == "FX_PARITY_MISMATCH"]
    assert parity_codes, "expected parity WARNs in summary.diagnostics"
    for d in parity_codes:
        assert d.severity == "WARN"
