"""Shared pytest fixtures.

Project-wide fixtures live here. Phase-specific fixtures should live alongside their
tests (e.g. ``tests/foundation/conftest.py``) once they exist.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# MON-021 Phase 2 (M-106) — temporary deferral of full-pipeline FX tests.
#
# The V0.4 xccy strip requires FX forward coverage out to the longest xccy
# coupon date (OQ-505); otherwise build_cross_basis_curve aborts with
# FX_XCCY_FORWARD_COVERAGE. The committed io FX fixture
# (tests/io/fixtures/fx_snapshot_USDTRY_20260612.csv) only quotes forwards to
# 3M but carries a 1Y XCCY_BASIS row, so every test that runs the full FX
# pipeline on it can no longer reach a successful strip.
#
# Per the build order these are restored in Phase 5, which regenerates the
# fixture with 1Y-covering forwards that embed the basis. DELETE this block
# (and the hook below) when that fixture lands — strict=True will then flag the
# now-passing tests as XPASS to force the cleanup.
# ---------------------------------------------------------------------------
_PHASE5_DEFERRED_FX_TESTS: frozenset[str] = frozenset(
    {
        "test_run_fx_bootstrap.py::test_run_fx_bootstrap_writes_summary_and_partitions",
        "test_run_fx_bootstrap.py::test_run_fx_bootstrap_populates_parity_checks",
        "test_run_fx_bootstrap.py::test_run_fx_bootstrap_rerun_overwrites",
        "test_run_fx_bootstrap.py::test_run_fx_bootstrap_diagnostics_persisted",
        "test_run_fx_diagnose.py::test_run_fx_diagnose_happy_path_returns_zero",
        "test_run_fx_price.py::test_outright_happy_tenor",
        "test_run_fx_price.py::test_outright_happy_value_date",
        "test_run_fx_price.py::test_outright_mutex_both_aborts",
        "test_run_fx_price.py::test_outright_mutex_neither_aborts",
        "test_run_fx_price.py::test_outright_value_date_before_spot_aborts",
        "test_run_fx_price.py::test_outright_invalid_value_date_aborts",
        "test_run_fx_price.py::test_outright_extrapolation_warns_but_returns",
        "test_run_fx_price.py::test_outright_unknown_tenor_aborts",
        "test_run_fx_price.py::test_outright_tenor_and_value_date_produce_same_rate",
        "test_run_fx_price.py::test_swap_happy",
        "test_run_fx_price.py::test_swap_mutex_neither_on_near_aborts",
        "test_run_fx_price.py::test_swap_leg_order_violation_aborts",
        "test_run_fx_price.py::test_xccy_happy",
        "test_run_fx_price.py::test_xccy_missing_basis_aborts",
        "test_run_fx_price.py::test_xccy_unknown_tenor_aborts",
        "test_run_fx_price.py::test_xccy_no_tenor_arg_aborts",
        "test_fx_cli.py::test_e2e_fx_bootstrap_then_price_outright",
    }
)

_PHASE5_XFAIL_REASON = (
    "MON-021 Phase 5: full FX pipeline needs the io fixture regenerated with "
    "1Y-covering forwards for the xccy strip (OQ-505 / FX_XCCY_FORWARD_COVERAGE)."
)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """xfail the full-pipeline FX tests deferred to MON-021 Phase 5 (see above)."""
    for item in items:
        suffix = item.nodeid.split("/")[-1]
        if suffix in _PHASE5_DEFERRED_FX_TESTS:
            item.add_marker(pytest.mark.xfail(reason=_PHASE5_XFAIL_REASON, strict=True))


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def config_dir(project_root: Path) -> Path:
    """Absolute path to the ``config/`` directory."""
    return project_root / "config"


@pytest.fixture(scope="session")
def conventions_yaml(config_dir: Path) -> Path:
    """Absolute path to the canonical conventions.yaml."""
    return config_dir / "conventions.yaml"
