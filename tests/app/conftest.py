"""Phase 5 app test fixtures.

Uses the Phase 4 IO fixtures (``tests/io/fixtures/``) as the realistic input
surface so the orchestrator runs through the full pipeline like the CLI would.
The valuation date and MPC schedule are chosen so all meetings are after the
snapshot date — past meetings get double-counted under the current scenario
semantics (they're already priced into BISTTREF).

Phase 8 (v0.3.0) adds an ``fx_diagnose_args`` fixture mirroring the same
shape for the FX orchestrator.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_IO_FIXTURES = _REPO_ROOT / "tests" / "io" / "fixtures"


@pytest.fixture()
def bootstrap_args(tmp_path) -> argparse.Namespace:
    """argparse.Namespace mirroring what the CLI would build for `rates bootstrap`."""
    return argparse.Namespace(
        snapshot=_IO_FIXTURES / "snapshot_happy.csv",
        mpc_path=_IO_FIXTURES / "mpc_happy.csv",
        band=0,
        interp="log_linear_df",
        quiet=True,  # avoid noisy capfd; tests inspect files, not stdout
        convention_override=None,
        conventions_yaml=_REPO_ROOT / "config" / "conventions.yaml",
        output_root=tmp_path,
        horizon_days=60,  # short enough to keep tests fast
    )


@pytest.fixture()
def diagnose_args() -> argparse.Namespace:
    return argparse.Namespace(
        snapshot=_IO_FIXTURES / "snapshot_happy.csv",
        conventions_yaml=_REPO_ROOT / "config" / "conventions.yaml",
        convention_override=None,
        quiet=True,
    )


@pytest.fixture()
def fx_diagnose_args() -> argparse.Namespace:
    """argparse.Namespace mirroring what the CLI builds for `rates fx diagnose`.

    Paths are explicit (no auto-discovery) so the test rig is independent of
    cwd. All three fixtures are dated 2026-06-12 to keep the orchestrator's
    cross-CSV date check happy.
    """
    return argparse.Namespace(
        pair="USDTRY",
        as_of=date(2026, 6, 12),
        fx_snapshot=_IO_FIXTURES / "fx_snapshot_USDTRY_20260612.csv",
        domestic_snapshot=_IO_FIXTURES / "snapshot_happy.csv",
        foreign_snapshot=_IO_FIXTURES / "usd_ois_20260612.csv",
        conventions_yaml=_REPO_ROOT / "config" / "conventions.yaml",
        convention_override=None,
        quiet=True,
    )


@pytest.fixture()
def fx_bootstrap_args(fx_diagnose_args, tmp_path) -> argparse.Namespace:
    """`rates fx bootstrap` Namespace = diagnose Namespace + output_root.

    Persists summary JSON + Parquet partitions under tmp_path.
    """
    fx_diagnose_args.output_root = tmp_path
    return fx_diagnose_args


def _make_fx_price_args(tmp_path) -> argparse.Namespace:
    """Build the shared subset of args common to all three price verbs."""
    return argparse.Namespace(
        pair="USDTRY",
        as_of=date(2026, 6, 12),
        conventions_yaml=_REPO_ROOT / "config" / "conventions.yaml",
        convention_override=None,
        output_root=tmp_path,
        quiet=False,
    )


@pytest.fixture()
def fx_price_outright_args(tmp_path) -> argparse.Namespace:
    """`rates fx price-outright` Namespace; tenor path by default."""
    args = _make_fx_price_args(tmp_path)
    args.tenor_code = "3M"
    args.value_date = None
    return args


@pytest.fixture()
def fx_price_swap_args(tmp_path) -> argparse.Namespace:
    """`rates fx price-swap` Namespace; both legs tenor-path by default."""
    args = _make_fx_price_args(tmp_path)
    args.near_tenor = "1M"
    args.near_value_date = None
    args.far_tenor = "3M"
    args.far_value_date = None
    return args


@pytest.fixture()
def fx_price_xccy_args(tmp_path) -> argparse.Namespace:
    """`rates fx price-xccy` Namespace; tenor-keyed."""
    args = _make_fx_price_args(tmp_path)
    args.tenor_code = "1Y"
    return args


@pytest.fixture()
def fx_price_xccy_mtm_args(tmp_path) -> argparse.Namespace:
    """`rates fx price-xccy-mtm` Namespace; tenor-keyed by default (MON-022)."""
    args = _make_fx_price_args(tmp_path)
    args.tenor_code = "1Y"
    args.maturity_date = None
    args.spread = -150.0
    args.notional = 10_000_000.0
    args.direction = "receive-domestic"
    return args
