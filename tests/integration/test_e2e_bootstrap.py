"""End-to-end integration test (Phase 5 exit criterion).

Drives the pipeline through the public CLI entry point ``rates.cli.main``,
verifying that a real snapshot CSV produces both persisted artefacts and an
ad-hoc forward rate from the persisted summary — fully exercising every
module M-001..M-015.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow.dataset as ds
import pytest

from rates.cli import main
from rates.io.schemas import Summary

_REPO_ROOT = Path(__file__).resolve().parents[2]
_IO_FIXTURES = _REPO_ROOT / "tests" / "io" / "fixtures"
_CONFIG = _REPO_ROOT / "config"


@pytest.mark.phase5
def test_end_to_end_bootstrap_then_forward(tmp_path, capfd):
    snapshot = _IO_FIXTURES / "snapshot_happy.csv"
    mpc = _IO_FIXTURES / "mpc_happy.csv"

    # ── bootstrap ──────────────────────────────────────────────────────────
    rc = main(
        [
            "bootstrap",
            "--snapshot",
            str(snapshot),
            "--mpc-path",
            str(mpc),
            "--band",
            "150",
            "--interp",
            "log_linear_df",
            "--conventions-yaml",
            str(_CONFIG / "conventions.yaml"),
            "--output-root",
            str(tmp_path),
            "--horizon-days",
            "60",
        ]
    )
    out, err = capfd.readouterr()
    assert rc == 0, f"bootstrap failed (rc={rc}): out={out!r} err={err!r}"

    summary_path = tmp_path / "data" / "latest" / "try_ois_summary.json"
    assert summary_path.exists()
    summary = Summary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    assert summary.config_snapshot.band_bps == 150
    assert summary.config_snapshot.interpolation == "log_linear_df"
    assert summary.valuation_date.isoformat() == "2026-06-12"

    partition_root = tmp_path / "data" / "curves" / "try_ois"
    dataset = ds.dataset(partition_root, format="parquet", partitioning="hive")
    table = dataset.to_table()
    labels = set(table.column("scenario_label").to_pylist())
    assert labels == {"mid", "low", "high"}

    # ── forward ad-hoc ─────────────────────────────────────────────────────
    rc = main(
        [
            "forward",
            "--start",
            "2026-07-13",
            "--end",
            "2026-09-14",
            "--output-root",
            str(tmp_path),
        ]
    )
    out, err = capfd.readouterr()
    assert rc == 0
    assert "forward(2026-07-13, 2026-09-14)" in out


@pytest.mark.phase5
def test_end_to_end_diagnose_writes_no_outputs(tmp_path, capfd):
    snapshot = _IO_FIXTURES / "snapshot_happy.csv"
    rc = main(
        [
            "diagnose",
            "--snapshot",
            str(snapshot),
            "--conventions-yaml",
            str(_CONFIG / "conventions.yaml"),
            "--quiet",
        ]
    )
    capfd.readouterr()
    assert rc == 0
    # No summary or partition created.
    assert not (tmp_path / "data" / "latest" / "try_ois_summary.json").exists()
    assert not (tmp_path / "data" / "curves").exists()


@pytest.mark.phase5
def test_end_to_end_forward_without_summary_fails(tmp_path, capfd):
    rc = main(
        [
            "forward",
            "--start",
            "2026-07-13",
            "--end",
            "2026-09-14",
            "--output-root",
            str(tmp_path),
        ]
    )
    _, err = capfd.readouterr()
    assert rc == 2
    assert "summary not found" in err
