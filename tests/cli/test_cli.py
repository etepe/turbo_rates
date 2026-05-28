"""argparse wiring (M-015.cli)."""

from __future__ import annotations

import argparse
import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from rates import cli as cli_mod
from rates.cli import _build_parser, _latest_snapshot, main


@pytest.mark.phase5
def test_parser_requires_subcommand():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args([])


@pytest.mark.phase5
def test_bootstrap_defaults_populated():
    p = _build_parser()
    ns = p.parse_args(["bootstrap"])
    assert ns.command == "bootstrap"
    assert ns.band == 0
    assert ns.interp == "log_linear_df"
    assert ns.quiet is False
    assert ns.mpc_path == Path("config/mpc_path.csv")
    assert ns.conventions_yaml == Path("config/conventions.yaml")
    assert ns.output_root == Path(".")


@pytest.mark.phase5
def test_bootstrap_interp_choice_validated():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["bootstrap", "--interp", "cubic_spline"])


@pytest.mark.phase5
def test_diagnose_requires_snapshot():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["diagnose"])
    ns = p.parse_args(["diagnose", "--snapshot", "x.csv"])
    assert ns.snapshot == Path("x.csv")


@pytest.mark.phase5
def test_forward_parses_iso_dates():
    p = _build_parser()
    ns = p.parse_args(["forward", "--start", "2026-07-01", "--end", "2026-10-01"])
    assert ns.start == date(2026, 7, 1)
    assert ns.end == date(2026, 10, 1)


@pytest.mark.phase5
def test_forward_rejects_non_iso_dates():
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["forward", "--start", "01/07/2026", "--end", "2026-10-01"])


@pytest.mark.phase5
def test_convention_override_collects_pairs():
    p = _build_parser()
    ns = p.parse_args(
        [
            "bootstrap",
            "--convention-override",
            "ois.TRY.day_count=Act/365",
            "--convention-override",
            "tlref.day_count=Act/360",
        ]
    )
    assert ns.convention_override == [
        "ois.TRY.day_count=Act/365",
        "tlref.day_count=Act/360",
    ]


@pytest.mark.phase5
def test_subcommand_body_under_30_lines():
    """F-009 acceptance: each subcommand body must be <30 LoC."""
    for name in ("_bootstrap_command", "_diagnose_command", "_forward_command"):
        fn = getattr(cli_mod, name)
        source = inspect.getsource(fn)
        tree = ast.parse(source)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        # End-line minus start-line of the function body.
        first_body_line = func.body[0].lineno
        last_body_line = func.body[-1].end_lineno or func.body[-1].lineno
        body_lines = last_body_line - first_body_line + 1
        assert body_lines < 30, f"{name} body is {body_lines} LoC (>=30)"


@pytest.mark.phase5
def test_latest_snapshot_returns_lexical_max(tmp_path):
    (tmp_path / "snapshot_20260601.csv").write_text("x")
    (tmp_path / "snapshot_20260615.csv").write_text("x")
    (tmp_path / "snapshot_20260610.csv").write_text("x")
    assert _latest_snapshot(tmp_path).name == "snapshot_20260615.csv"


@pytest.mark.phase5
def test_latest_snapshot_returns_none_for_empty(tmp_path):
    assert _latest_snapshot(tmp_path) is None


@pytest.mark.phase5
def test_latest_snapshot_returns_none_for_missing_dir(tmp_path):
    assert _latest_snapshot(tmp_path / "nope") is None


@pytest.mark.phase5
def test_main_delegates_to_subcommand(monkeypatch):
    captured: dict[str, argparse.Namespace] = {}

    def fake_run_forward(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 7

    monkeypatch.setattr(cli_mod, "run_forward", fake_run_forward)
    code = main(["forward", "--start", "2026-07-01", "--end", "2026-10-01"])
    assert code == 7
    assert captured["args"].start == date(2026, 7, 1)
    assert captured["args"].end == date(2026, 10, 1)
