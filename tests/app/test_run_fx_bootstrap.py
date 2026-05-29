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
from rates.io.schemas import FX_SCHEMA_VERSION, FXSummary


@pytest.mark.phase8
def test_run_fx_bootstrap_writes_summary_and_partitions(
    fx_bootstrap_args, tmp_path,
) -> None:
    """Happy path: clean exit 0 + summary JSON (v2) + both Parquet partitions."""
    code = run_fx_bootstrap(fx_bootstrap_args)
    assert code == 0

    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    assert summary_path.exists()
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    assert summary.schema_version == FX_SCHEMA_VERSION  # v2 (M-109)
    assert summary.pair_code == "USDTRY"
    assert summary.valuation_date == date(2026, 6, 12)
    assert len(summary.forward_pillars) == 5  # 1M,3M,6M,9M,12M in V0.4 fixture
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
def test_run_fx_bootstrap_arbitrage_consistent_fixture_has_no_parity_checks(
    fx_bootstrap_args, tmp_path,
) -> None:
    """V0.4 (A-6): the fixture's forwards EMBED the -180 bps basis, so the
    basis-aware gate finds forward-implied ≈ quoted and emits zero
    FX_PARITY_MISMATCH — hence ``parity_checks`` is empty. This is the A-1
    deliverable: the gate passes *meaningfully*, not vacuously. (The WARN path
    itself is unit-tested in tests/app/test_fx_parity_gate.py.)"""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    assert summary.parity_checks == []
    # And the stripped basis recovers the embedded -180 bps target.
    assert summary.basis_pillars[0].spread_bps == pytest.approx(-180.0, abs=0.5)


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
    """summary.diagnostics is a list and the arbitrage-consistent V0.4 fixture
    persists a clean envelope: no ERROR rows and no FX_PARITY_MISMATCH (A-6)."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    summary_path = tmp_path / "data" / "latest" / "usdtry_fx_summary.json"
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    assert isinstance(summary.diagnostics, list)
    assert [d for d in summary.diagnostics if d.code == "FX_PARITY_MISMATCH"] == []
    assert [d for d in summary.diagnostics if d.severity == "ERROR"] == []
