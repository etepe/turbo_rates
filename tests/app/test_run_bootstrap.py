"""Bootstrap orchestrator (M-014.run_bootstrap)."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from rates.app import run_bootstrap
from rates.io.schemas import Summary

_REPO_ROOT = Path(__file__).resolve().parents[2]
_IO_FIXTURES = _REPO_ROOT / "tests" / "io" / "fixtures"


@pytest.mark.phase5
def test_run_bootstrap_writes_outputs(bootstrap_args, tmp_path):
    code = run_bootstrap(bootstrap_args)
    assert code == 0

    summary_path = tmp_path / "data" / "latest" / "try_ois_summary.json"
    assert summary_path.exists()
    summary = Summary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    assert summary.schema_version == 1
    assert summary.config_snapshot.band_bps == 0
    assert summary.config_snapshot.initial_tlref == 0.4275
    assert len(summary.pillars) == 3  # snapshot_happy has 3 TYSO rows.
    assert len(summary.forward_ladder) == 15

    partition = tmp_path / "data" / "curves" / "try_ois" / "date=2026-06-12"
    assert partition.is_dir()
    parquet_files = list(partition.glob("*.parquet"))
    assert len(parquet_files) == 1
    pf = pq.ParquetFile(parquet_files[0])
    assert pf.schema_arrow.names == ["index_date", "scenario_label", "index_value"]


@pytest.mark.phase5
def test_run_bootstrap_with_band_populates_low_high(bootstrap_args, tmp_path):
    bootstrap_args.band = 150
    code = run_bootstrap(bootstrap_args)
    assert code == 0

    partition = tmp_path / "data" / "curves" / "try_ois" / "date=2026-06-12"
    table = pq.read_table(next(partition.glob("*.parquet")))
    labels = set(table.column("scenario_label").to_pylist())
    assert labels == {"mid", "low", "high"}


@pytest.mark.phase5
def test_run_bootstrap_missing_snapshot_returns_two(bootstrap_args):
    bootstrap_args.snapshot = Path("/no/such/file.csv")
    assert run_bootstrap(bootstrap_args) == 2


@pytest.mark.phase5
def test_run_bootstrap_missing_mpc_returns_two(bootstrap_args):
    bootstrap_args.mpc_path = Path("/no/such/mpc.csv")
    assert run_bootstrap(bootstrap_args) == 2


@pytest.mark.phase5
def test_run_bootstrap_invalid_convention_override(bootstrap_args, tmp_path):
    """Bad --convention-override KEY=VALUE is captured as APP_CONVENTIONS_LOAD_FAIL
    and surfaces as exit 2 — not a traceback."""
    bootstrap_args.convention_override = ["this-is-not-a-pair"]
    assert run_bootstrap(bootstrap_args) == 2
    # No outputs were written.
    assert not (tmp_path / "data" / "latest" / "try_ois_summary.json").exists()


@pytest.mark.phase5
def test_run_bootstrap_unknown_snapshot_dir_no_outputs(bootstrap_args, tmp_path):
    """ERROR path: no summary or partition created when load fails."""
    bootstrap_args.snapshot = tmp_path / "ghost.csv"
    code = run_bootstrap(bootstrap_args)
    assert code == 2
    summary_path = tmp_path / "data" / "latest" / "try_ois_summary.json"
    assert not summary_path.exists()


@pytest.mark.phase5
def test_run_bootstrap_band_zero_short_circuits_to_mid_only(bootstrap_args, tmp_path):
    code = run_bootstrap(bootstrap_args)
    assert code == 0
    partition = tmp_path / "data" / "curves" / "try_ois" / "date=2026-06-12"
    table = pq.read_table(next(partition.glob("*.parquet")))
    labels = set(table.column("scenario_label").to_pylist())
    assert labels == {"mid"}


@pytest.mark.phase5
def test_run_bootstrap_quiet_suppresses_stdout(bootstrap_args, capfd):
    bootstrap_args.quiet = True
    code = run_bootstrap(bootstrap_args)
    out, _ = capfd.readouterr()
    assert code == 0
    # Pretty comparison table not printed when --quiet.
    assert "tenor" not in out and "spread" not in out


@pytest.mark.phase5
def test_run_bootstrap_loud_prints_comparison(bootstrap_args, capfd):
    bootstrap_args.quiet = False
    code = run_bootstrap(bootstrap_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "tenor" in out
    assert "spread" in out


@pytest.mark.phase5
def test_run_bootstrap_includes_diagnostics_in_summary(bootstrap_args, tmp_path):
    """Even on a clean run with no WARN/ERROR, summary.diagnostics is a list (may be []).
    Add a deliberately problematic CSV to confirm WARNs surface."""
    bootstrap_args.snapshot = _IO_FIXTURES / "snapshot_happy.csv"
    run_bootstrap(bootstrap_args)
    summary_path = tmp_path / "data" / "latest" / "try_ois_summary.json"
    summary = Summary.model_validate_json(summary_path.read_text())
    assert isinstance(summary.diagnostics, list)


@pytest.mark.phase5
def test_run_bootstrap_overwrites_outputs(bootstrap_args, tmp_path):
    """Re-running on same day overwrites summary JSON and partition dir."""
    run_bootstrap(bootstrap_args)
    summary_path = tmp_path / "data" / "latest" / "try_ois_summary.json"
    first = summary_path.read_text()

    bootstrap_args.band = 150
    run_bootstrap(bootstrap_args)
    second = summary_path.read_text()
    assert first != second
    assert "150" in second
