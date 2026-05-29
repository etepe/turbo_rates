"""Phase 10 — MtM xccy basis swap pricing verb (M-111, C-111).

Orchestration tests for ``run_fx_price_xccy_mtm``: each first runs
``run_fx_bootstrap`` to persist an FXSummary v2 (the fixture carries a 1Y
XCCY_BASIS row), then prices against it. The PV math itself is covered by
``tests/fx/test_pricer_mtm.py``; here we exercise the verb wiring — maturity
resolution (``--tenor`` xor ``--maturity``), the schema/basis guards, and the
contract-term flags.
"""

from __future__ import annotations

import pytest

from rates.app import run_fx_bootstrap, run_fx_price_xccy_mtm
from rates.io.schemas import FXSummary

pytestmark = pytest.mark.phase10


def _summary_path(args):
    return args.output_root / "data" / "latest" / "usdtry_fx_summary.json"


def test_mtm_happy_tenor(fx_bootstrap_args, fx_price_xccy_mtm_args, capfd) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()  # drain bootstrap output

    code = run_fx_price_xccy_mtm(fx_price_xccy_mtm_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "mtm_xccy(USDTRY 1Y" in out
    assert "PV = " in out and "TRY" in out
    assert "spread=-150.00 bps" in out


def test_mtm_happy_maturity(fx_bootstrap_args, fx_price_xccy_mtm_args, capfd) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()

    summary = FXSummary.model_validate_json(
        _summary_path(fx_bootstrap_args).read_text(encoding="utf-8")
    )
    maturity = summary.basis_pillars[0].maturity_date

    fx_price_xccy_mtm_args.tenor_code = None
    fx_price_xccy_mtm_args.maturity_date = maturity
    code = run_fx_price_xccy_mtm(fx_price_xccy_mtm_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert f"mtm_xccy(USDTRY {maturity.isoformat()}" in out


def test_mtm_mutex_both_aborts(fx_bootstrap_args, fx_price_xccy_mtm_args) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    summary = FXSummary.model_validate_json(
        _summary_path(fx_bootstrap_args).read_text(encoding="utf-8")
    )
    fx_price_xccy_mtm_args.tenor_code = "1Y"
    fx_price_xccy_mtm_args.maturity_date = summary.basis_pillars[0].maturity_date
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 2


def test_mtm_mutex_neither_aborts(fx_bootstrap_args, fx_price_xccy_mtm_args) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_xccy_mtm_args.tenor_code = None
    fx_price_xccy_mtm_args.maturity_date = None
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 2


def test_mtm_unknown_tenor_aborts(fx_bootstrap_args, fx_price_xccy_mtm_args) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_xccy_mtm_args.tenor_code = "9X"
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 2


def test_mtm_negative_notional_aborts(fx_bootstrap_args, fx_price_xccy_mtm_args) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_xccy_mtm_args.notional = -1.0
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 2


def test_mtm_pay_direction_runs(fx_bootstrap_args, fx_price_xccy_mtm_args, capfd) -> None:
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()
    fx_price_xccy_mtm_args.direction = "pay-domestic"
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 0
    out, _ = capfd.readouterr()
    assert "mtm_xccy(USDTRY 1Y" in out


def test_mtm_missing_basis_aborts(fx_bootstrap_args, fx_price_xccy_mtm_args) -> None:
    """No basis pillars ⇒ FX_PRICE_BASIS_MISSING (require_basis=True, A-609)."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    path = _summary_path(fx_bootstrap_args)
    summary = FXSummary.model_validate_json(path.read_text(encoding="utf-8"))
    stripped = summary.model_copy(update={"basis_pillars": []})
    path.write_text(stripped.model_dump_json(indent=2), encoding="utf-8")
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 2


def test_mtm_schema_too_old_aborts(fx_bootstrap_args, fx_price_xccy_mtm_args) -> None:
    """A v1 summary (no embedded OIS pillars) ⇒ FX_PRICE_SCHEMA_TOO_OLD."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    path = _summary_path(fx_bootstrap_args)
    summary = FXSummary.model_validate_json(path.read_text(encoding="utf-8"))
    downgraded = summary.model_copy(update={"schema_version": 1})
    path.write_text(downgraded.model_dump_json(indent=2), encoding="utf-8")
    assert run_fx_price_xccy_mtm(fx_price_xccy_mtm_args) == 2
