"""Diagnose orchestrator (M-014.run_diagnose)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rates.app import run_diagnose


@pytest.mark.phase5
def test_run_diagnose_clean_returns_zero(diagnose_args, tmp_path):
    """No errors on a valid snapshot ⇒ exit 0 and no outputs."""
    # Place output_root under tmp_path to detect any accidental writes.
    code = run_diagnose(diagnose_args)
    assert code == 0
    # Diagnose must not have written summary or partition anywhere.
    assert not (tmp_path / "data" / "latest" / "try_ois_summary.json").exists()


@pytest.mark.phase5
def test_run_diagnose_missing_snapshot_returns_two(diagnose_args):
    diagnose_args.snapshot = Path("/no/such/file.csv")
    assert run_diagnose(diagnose_args) == 2


@pytest.mark.phase5
def test_run_diagnose_missing_conventions_returns_two(diagnose_args):
    diagnose_args.conventions_yaml = Path("/no/such/conv.yaml")
    assert run_diagnose(diagnose_args) == 2
