"""rates fx <verb> argparse wiring (M-112).

Two layers:

1. **Parser shape** — verbs are registered, required args are enforced, mutex
   groups behave per architecture C-103. No real pipeline runs here.
2. **End-to-end via main()** — patch the `rates.app.run_fx_*` entry points so
   the test asserts on the Namespace the CLI built, decoupled from the math
   layer (the orchestrator itself is already covered by tests/app/test_run_fx_*).
"""

from __future__ import annotations

import argparse
import ast
import inspect
from datetime import date
from pathlib import Path

import pytest

from rates import cli as cli_mod
from rates.cli import _build_parser, main

# ---------------------------------------------------------------------------
# Parser shape
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_fx_subgroup_requires_verb() -> None:
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["fx"])


@pytest.mark.phase8
def test_fx_diagnose_defaults_populated() -> None:
    p = _build_parser()
    ns = p.parse_args([
        "fx", "diagnose", "--pair", "USDTRY", "--as-of", "2026-06-12",
    ])
    assert ns.command == "fx"
    assert ns.fx_command == "diagnose"
    assert ns.pair == "USDTRY"
    assert ns.as_of == date(2026, 6, 12)
    assert ns.conventions_yaml == Path("config/conventions.yaml")
    assert ns.fx_snapshot is None
    assert ns.domestic_snapshot is None
    assert ns.foreign_snapshot is None
    assert ns.quiet is False
    # --output-root NOT attached to diagnose (no persistence side).
    assert not hasattr(ns, "output_root")


@pytest.mark.phase8
def test_fx_bootstrap_attaches_output_root() -> None:
    p = _build_parser()
    ns = p.parse_args([
        "fx", "bootstrap", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--output-root", "/tmp/x",
    ])
    assert ns.fx_command == "bootstrap"
    assert ns.output_root == Path("/tmp/x")


@pytest.mark.phase8
def test_fx_requires_pair_and_as_of() -> None:
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["fx", "diagnose"])
    with pytest.raises(SystemExit):
        p.parse_args(["fx", "diagnose", "--pair", "USDTRY"])
    with pytest.raises(SystemExit):
        p.parse_args(["fx", "diagnose", "--as-of", "2026-06-12"])


@pytest.mark.phase8
def test_fx_as_of_rejects_non_iso() -> None:
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "diagnose", "--pair", "USDTRY", "--as-of", "12-06-2026",
        ])


@pytest.mark.phase8
def test_fx_price_outright_tenor_value_date_mutex() -> None:
    p = _build_parser()
    # exactly one required
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-outright", "--pair", "USDTRY", "--as-of", "2026-06-12",
        ])
    # both ⇒ argparse mutex SystemExit
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-outright", "--pair", "USDTRY", "--as-of", "2026-06-12",
            "--tenor", "3M", "--value-date", "2026-09-14",
        ])

    # tenor alone works
    ns = p.parse_args([
        "fx", "price-outright", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--tenor", "3M",
    ])
    assert ns.tenor_code == "3M"
    assert ns.value_date is None

    # value-date alone works
    ns = p.parse_args([
        "fx", "price-outright", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--value-date", "2026-09-14",
    ])
    assert ns.tenor_code is None
    assert ns.value_date == date(2026, 9, 14)


@pytest.mark.phase8
def test_fx_price_swap_per_leg_mutex() -> None:
    p = _build_parser()
    # missing near
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-swap", "--pair", "USDTRY", "--as-of", "2026-06-12",
            "--far-tenor", "3M",
        ])
    # missing far
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-swap", "--pair", "USDTRY", "--as-of", "2026-06-12",
            "--near-tenor", "1M",
        ])
    # mixed (tenor near, value-date far) works
    ns = p.parse_args([
        "fx", "price-swap", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--near-tenor", "1M", "--far-value-date", "2026-09-14",
    ])
    assert ns.near_tenor == "1M"
    assert ns.near_value_date is None
    assert ns.far_tenor is None
    assert ns.far_value_date == date(2026, 9, 14)


@pytest.mark.phase8
def test_fx_price_xccy_requires_tenor() -> None:
    p = _build_parser()
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-xccy", "--pair", "USDTRY", "--as-of", "2026-06-12",
        ])
    ns = p.parse_args([
        "fx", "price-xccy", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--tenor", "1Y",
    ])
    assert ns.tenor_code == "1Y"


@pytest.mark.phase10
def test_fx_price_xccy_mtm_parses_flags() -> None:
    p = _build_parser()
    # missing required --spread/--notional and the tenor/maturity mutex ⇒ SystemExit
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-xccy-mtm", "--pair", "USDTRY", "--as-of", "2026-06-12",
        ])
    # tenor path (use --flag=-value form for the negative spread)
    ns = p.parse_args([
        "fx", "price-xccy-mtm", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--spread=-150", "--notional", "1e7", "--tenor", "1Y",
    ])
    assert ns.tenor_code == "1Y"
    assert ns.maturity_date is None
    assert ns.spread == pytest.approx(-150.0)
    assert ns.notional == pytest.approx(1e7)
    assert ns.direction == "receive-domestic"  # default
    # maturity path + explicit direction
    ns = p.parse_args([
        "fx", "price-xccy-mtm", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--spread=-180", "--notional", "5e6", "--maturity", "2027-06-15",
        "--direction", "pay-domestic",
    ])
    assert ns.tenor_code is None
    assert ns.maturity_date == date(2027, 6, 15)
    assert ns.direction == "pay-domestic"
    # both --tenor and --maturity ⇒ argparse mutex SystemExit
    with pytest.raises(SystemExit):
        p.parse_args([
            "fx", "price-xccy-mtm", "--pair", "USDTRY", "--as-of", "2026-06-12",
            "--spread=-150", "--notional", "1e7", "--tenor", "1Y",
            "--maturity", "2027-06-15",
        ])


@pytest.mark.phase10
def test_main_dispatches_fx_price_xccy_mtm(monkeypatch) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli_mod, "run_fx_price_xccy_mtm", fake)
    code = main([
        "fx", "price-xccy-mtm", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--spread=-150", "--notional", "1e7", "--tenor", "1Y",
    ])
    assert code == 0
    assert captured["args"].spread == pytest.approx(-150.0)
    assert captured["args"].direction == "receive-domestic"


@pytest.mark.phase8
def test_fx_convention_override_collects_pairs() -> None:
    p = _build_parser()
    ns = p.parse_args([
        "fx", "diagnose", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--convention-override", "fx_conventions.USDTRY.spot_lag_days=1",
        "--convention-override", "ois.USD.day_count=Act/360",
    ])
    assert ns.convention_override == [
        "fx_conventions.USDTRY.spot_lag_days=1",
        "ois.USD.day_count=Act/360",
    ]


# ---------------------------------------------------------------------------
# Delegation via main()
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_main_dispatches_fx_diagnose(monkeypatch) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 11

    monkeypatch.setattr(cli_mod, "run_fx_diagnose", fake)
    code = main(["fx", "diagnose", "--pair", "USDTRY", "--as-of", "2026-06-12"])
    assert code == 11
    assert captured["args"].pair == "USDTRY"
    assert captured["args"].as_of == date(2026, 6, 12)


@pytest.mark.phase8
def test_main_dispatches_fx_bootstrap(monkeypatch, tmp_path) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli_mod, "run_fx_bootstrap", fake)
    code = main([
        "fx", "bootstrap", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--output-root", str(tmp_path),
    ])
    assert code == 0
    assert captured["args"].output_root == tmp_path


@pytest.mark.phase8
def test_main_dispatches_fx_price_outright(monkeypatch) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli_mod, "run_fx_price_outright", fake)
    code = main([
        "fx", "price-outright", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--tenor", "3M",
    ])
    assert code == 0
    assert captured["args"].tenor_code == "3M"


@pytest.mark.phase8
def test_main_dispatches_fx_price_swap(monkeypatch) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli_mod, "run_fx_price_swap", fake)
    code = main([
        "fx", "price-swap", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--near-tenor", "1M", "--far-tenor", "3M",
    ])
    assert code == 0
    assert captured["args"].near_tenor == "1M"
    assert captured["args"].far_tenor == "3M"


@pytest.mark.phase8
def test_main_dispatches_fx_price_xccy(monkeypatch) -> None:
    captured: dict[str, argparse.Namespace] = {}

    def fake(args: argparse.Namespace) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr(cli_mod, "run_fx_price_xccy", fake)
    code = main([
        "fx", "price-xccy", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--tenor", "1Y",
    ])
    assert code == 0
    assert captured["args"].tenor_code == "1Y"


# ---------------------------------------------------------------------------
# Subcommand body LoC budget (F-009: each ≤30 LoC, mirrors V1 OIS test)
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_fx_subcommand_bodies_under_30_lines() -> None:
    """Every `_fx_*_command` body must fit in <30 LoC per architecture §3 M-112."""
    for name in (
        "_fx_diagnose_command",
        "_fx_bootstrap_command",
        "_fx_price_outright_command",
        "_fx_price_swap_command",
        "_fx_price_xccy_command",
    ):
        fn = getattr(cli_mod, name)
        source = inspect.getsource(fn)
        tree = ast.parse(source)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))
        first = func.body[0].lineno
        last = func.body[-1].end_lineno or func.body[-1].lineno
        body_lines = last - first + 1
        assert body_lines < 30, f"{name} body is {body_lines} LoC (>=30)"


# ---------------------------------------------------------------------------
# End-to-end through the real orchestrator (single integration smoke)
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[2]
_IO_FIXTURES = _REPO_ROOT / "tests" / "io" / "fixtures"


@pytest.mark.phase8
def test_e2e_fx_bootstrap_then_price_outright(tmp_path, capfd) -> None:
    """End-to-end: `rates fx bootstrap` writes outputs, then `rates fx
    price-outright` reads them and prints a rate. Catches integration
    breakage between the CLI surface and the M-111 orchestrator."""
    code = main([
        "fx", "bootstrap", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--fx-snapshot", str(_IO_FIXTURES / "fx_snapshot_USDTRY_20260612.csv"),
        "--domestic-snapshot", str(_IO_FIXTURES / "snapshot_happy.csv"),
        "--foreign-snapshot", str(_IO_FIXTURES / "usd_ois_20260612.csv"),
        "--output-root", str(tmp_path),
        "--quiet",
    ])
    assert code == 0
    assert (tmp_path / "data" / "latest" / "usdtry_fx_summary.json").exists()
    capfd.readouterr()

    code = main([
        "fx", "price-outright", "--pair", "USDTRY", "--as-of", "2026-06-12",
        "--tenor", "3M", "--output-root", str(tmp_path),
    ])
    out, _ = capfd.readouterr()
    assert code == 0
    assert "outright(USDTRY" in out
