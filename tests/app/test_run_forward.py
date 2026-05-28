"""Forward orchestrator (M-014.run_forward) — reads persisted Summary."""

from __future__ import annotations

import argparse
from datetime import date

import pytest

from rates.app import _curve_from_summary, run_bootstrap, run_forward
from rates.io.schemas import Summary


@pytest.mark.phase5
def test_run_forward_prints_rate_after_bootstrap(bootstrap_args, capfd, tmp_path):
    """E2E: run bootstrap, then run forward over a slice of the curve domain."""
    assert run_bootstrap(bootstrap_args) == 0
    capfd.readouterr()  # drain bootstrap output

    fwd_args = argparse.Namespace(
        start=date(2026, 7, 13),
        end=date(2026, 9, 14),
        summary_path=None,
        output_root=tmp_path,
    )
    code = run_forward(fwd_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "forward(2026-07-13, 2026-09-14)" in out


@pytest.mark.phase5
def test_run_forward_missing_summary_returns_two(tmp_path, capfd):
    args = argparse.Namespace(
        start=date(2026, 7, 13),
        end=date(2026, 9, 14),
        summary_path=tmp_path / "absent.json",
        output_root=tmp_path,
    )
    code = run_forward(args)
    _, err = capfd.readouterr()
    assert code == 2
    assert "not found" in err


@pytest.mark.phase5
def test_run_forward_rejects_start_ge_end(bootstrap_args, tmp_path, capfd):
    assert run_bootstrap(bootstrap_args) == 0
    capfd.readouterr()

    fwd_args = argparse.Namespace(
        start=date(2026, 9, 14),
        end=date(2026, 9, 14),
        summary_path=None,
        output_root=tmp_path,
    )
    code = run_forward(fwd_args)
    _, err = capfd.readouterr()
    assert code == 2
    assert "start" in err.lower()


@pytest.mark.phase5
def test_curve_from_summary_round_trips_forward(bootstrap_args, tmp_path):
    """Reconstructed curve gives the same forward rate as the in-memory curve."""
    assert run_bootstrap(bootstrap_args) == 0
    summary_path = tmp_path / "data" / "latest" / "try_ois_summary.json"
    summary = Summary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    curve = _curve_from_summary(summary)
    # Anchor identity preserved through reconstruction.
    assert curve.df_at(curve.valuation_date) == 1.0
    # df_at each persisted pillar end returns its DF exactly.
    for p in summary.pillars:
        assert curve.df_at(p.end_date) == p.discount_factor
