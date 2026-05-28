"""FX pricing orchestrators (M-111 complete, contract C-103).

Three verbs (`price-outright`, `price-swap`, `price-xccy`) live in one test
file because they share the same persistence-read setup: each test first runs
``run_fx_bootstrap`` to write a summary, then runs a pricing verb against it.

Covers per C-103 / D11 from docs/fx-io-architecture.md:

* Tenor / value-date mutex (both, neither)
* FX_PRICE_VALUE_DATE_BEFORE_SPOT
* FX_PRICE_INVALID_VALUE_DATE (non-business day)
* FX_PRICE_EXTRAPOLATION (WARN-only, price returned)
* `--tenor` ↔ `--value-date` equivalence under D11's single pricing code path
* FX_PRICE_BASIS_MISSING for the XCCY verb
"""

from __future__ import annotations

from datetime import date

import pytest

from rates.app import (
    _resolve_tenor_to_value_date,
    run_fx_bootstrap,
    run_fx_price_outright,
    run_fx_price_swap,
    run_fx_price_xccy,
)
from rates.core.calendar import HolidayCalendar
from rates.core.diagnostics import DiagnosticsCollector
from rates.io.schemas import FXSummary

# ---------------------------------------------------------------------------
# Outright forward
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_outright_happy_tenor(
    fx_bootstrap_args, fx_price_outright_args, capfd,
) -> None:
    """`--tenor 3M`: bootstrap then price returns exit 0 with a printed rate."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()  # drain bootstrap output

    code = run_fx_price_outright(fx_price_outright_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "outright(USDTRY" in out
    assert " = " in out


@pytest.mark.phase8
def test_outright_happy_value_date(
    fx_bootstrap_args, fx_price_outright_args, capfd,
) -> None:
    """`--value-date <date>`: pricing path same as tenor — returns 0 with printed rate."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()

    # Need a value-date on the joint US ∩ TR calendar. The 3M pillar
    # (2026-09-14) is a Monday and falls on a good joint business day in the
    # fixture — the 1M pillar (2026-07-15) sits on a TR holiday, so pillars
    # are not always joint-good-BD by construction (fx_market does calendar
    # arithmetic without rolling); validation here is independent.
    summary_path = (
        fx_bootstrap_args.output_root / "data" / "latest" / "usdtry_fx_summary.json"
    )
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    pillar_settle = summary.forward_pillars[-1].settle_date  # 3M

    fx_price_outright_args.tenor_code = None
    fx_price_outright_args.value_date = pillar_settle
    code = run_fx_price_outright(fx_price_outright_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert pillar_settle.isoformat() in out


@pytest.mark.phase8
def test_outright_mutex_both_aborts(
    fx_bootstrap_args, fx_price_outright_args,
) -> None:
    """Both --tenor and --value-date set ⇒ FX_PRICE_TENOR_VALUE_DATE_MUTEX ERROR."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_outright_args.tenor_code = "3M"
    fx_price_outright_args.value_date = date(2026, 9, 14)
    assert run_fx_price_outright(fx_price_outright_args) == 2


@pytest.mark.phase8
def test_outright_mutex_neither_aborts(
    fx_bootstrap_args, fx_price_outright_args,
) -> None:
    """Neither --tenor nor --value-date set ⇒ FX_PRICE_TENOR_VALUE_DATE_MUTEX."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_outright_args.tenor_code = None
    fx_price_outright_args.value_date = None
    assert run_fx_price_outright(fx_price_outright_args) == 2


@pytest.mark.phase8
def test_outright_value_date_before_spot_aborts(
    fx_bootstrap_args, fx_price_outright_args,
) -> None:
    """--value-date earlier than (or equal to) spot ⇒ FX_PRICE_VALUE_DATE_BEFORE_SPOT."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_outright_args.tenor_code = None
    fx_price_outright_args.value_date = date(2026, 6, 12)  # = as_of, before spot
    assert run_fx_price_outright(fx_price_outright_args) == 2


@pytest.mark.phase8
def test_outright_invalid_value_date_aborts(
    fx_bootstrap_args, fx_price_outright_args,
) -> None:
    """--value-date on a weekend ⇒ FX_PRICE_INVALID_VALUE_DATE (no auto-roll)."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    # 2026-06-13 is a Saturday — definitely not a joint business day for US ∩ TR.
    fx_price_outright_args.tenor_code = None
    fx_price_outright_args.value_date = date(2026, 6, 13)
    assert run_fx_price_outright(fx_price_outright_args) == 2


@pytest.mark.phase8
def test_outright_extrapolation_warns_but_returns(
    fx_bootstrap_args, fx_price_outright_args, capfd,
) -> None:
    """--tenor past the last pillar emits FX_PRICE_EXTRAPOLATION WARN AND returns
    a price (clipped to last-pillar forward per architecture D11)."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()
    # Fixture's last pillar is 3M (~91 days). 1Y is beyond.
    fx_price_outright_args.tenor_code = "1Y"
    code = run_fx_price_outright(fx_price_outright_args)
    out, err = capfd.readouterr()
    assert code == 0  # WARN-only
    assert "outright(USDTRY" in out
    assert "FX_PRICE_EXTRAPOLATION" in out or "FX_PRICE_EXTRAPOLATION" in err


@pytest.mark.phase8
def test_outright_unknown_tenor_aborts(
    fx_bootstrap_args, fx_price_outright_args,
) -> None:
    """--tenor outside the CLI table ⇒ FX_PRICE_UNKNOWN_TENOR."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_outright_args.tenor_code = "9X"
    assert run_fx_price_outright(fx_price_outright_args) == 2


@pytest.mark.phase8
def test_outright_tenor_and_value_date_produce_same_rate(
    fx_bootstrap_args, fx_price_outright_args, capfd,
) -> None:
    """D11 single pricing code path: --tenor 3M and --value-date <resolved>
    print the same rate to stdout."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()

    # First: --tenor 3M
    fx_price_outright_args.tenor_code = "3M"
    fx_price_outright_args.value_date = None
    assert run_fx_price_outright(fx_price_outright_args) == 0
    out_tenor, _ = capfd.readouterr()
    rate_tenor = float(out_tenor.split(" = ")[1].split()[0])

    # Resolve the tenor to a value date using the same helper the orchestrator
    # uses; then compare.
    summary_path = (
        fx_bootstrap_args.output_root / "data" / "latest" / "usdtry_fx_summary.json"
    )
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    dg = DiagnosticsCollector()
    cals = (HolidayCalendar.for_currency("US"), HolidayCalendar.for_currency("TR"))
    resolved = _resolve_tenor_to_value_date("3M", summary.spot_date, cals, dg)
    assert resolved is not None

    fx_price_outright_args.tenor_code = None
    fx_price_outright_args.value_date = resolved
    assert run_fx_price_outright(fx_price_outright_args) == 0
    out_vd, _ = capfd.readouterr()
    rate_vd = float(out_vd.split(" = ")[1].split()[0])

    assert rate_tenor == pytest.approx(rate_vd, abs=1e-12)


@pytest.mark.phase8
def test_outright_summary_missing_aborts(
    fx_price_outright_args, tmp_path,
) -> None:
    """Pricing without a prior bootstrap ⇒ FX_PRICE_SUMMARY_NOT_FOUND ERROR."""
    # output_root points at a clean tmp_path; no summary written.
    fx_price_outright_args.output_root = tmp_path
    assert run_fx_price_outright(fx_price_outright_args) == 2


# ---------------------------------------------------------------------------
# FX swap
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_swap_happy(
    fx_bootstrap_args, fx_price_swap_args, capfd,
) -> None:
    """Both legs by tenor (1M near, 3M far) — exit 0, near < far points printed."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()
    code = run_fx_price_swap(fx_price_swap_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "fx_swap(USDTRY)" in out
    assert "near=" in out and "far=" in out and "points=" in out


@pytest.mark.phase8
def test_swap_mutex_neither_on_near_aborts(
    fx_bootstrap_args, fx_price_swap_args,
) -> None:
    """Near leg with neither tenor nor value-date ⇒ mutex ERROR."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_swap_args.near_tenor = None
    fx_price_swap_args.near_value_date = None
    assert run_fx_price_swap(fx_price_swap_args) == 2


@pytest.mark.phase8
def test_swap_leg_order_violation_aborts(
    fx_bootstrap_args, fx_price_swap_args,
) -> None:
    """near >= far ⇒ FX_PRICE_SWAP_LEG_ORDER ERROR."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_swap_args.near_tenor = "3M"
    fx_price_swap_args.near_value_date = None
    fx_price_swap_args.far_tenor = "1M"
    fx_price_swap_args.far_value_date = None
    assert run_fx_price_swap(fx_price_swap_args) == 2


# ---------------------------------------------------------------------------
# Cross-currency basis swap
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_xccy_happy(
    fx_bootstrap_args, fx_price_xccy_args, capfd,
) -> None:
    """1Y tenor on the fixture (which has exactly a 1Y XCCY_BASIS row) — exit 0."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    capfd.readouterr()
    code = run_fx_price_xccy(fx_price_xccy_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "xccy_basis(USDTRY 1Y)" in out
    assert "bps" in out


@pytest.mark.phase8
def test_xccy_missing_basis_aborts(
    fx_bootstrap_args, fx_price_xccy_args, tmp_path,
) -> None:
    """When the persisted summary has zero basis pillars ⇒ FX_PRICE_BASIS_MISSING."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    # Strip basis_pillars from the persisted JSON to simulate a no-XCCY snapshot.
    summary_path = (
        fx_bootstrap_args.output_root / "data" / "latest" / "usdtry_fx_summary.json"
    )
    summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    stripped = summary.model_copy(update={"basis_pillars": []})
    summary_path.write_text(stripped.model_dump_json(indent=2), encoding="utf-8")

    assert run_fx_price_xccy(fx_price_xccy_args) == 2


@pytest.mark.phase8
def test_xccy_unknown_tenor_aborts(
    fx_bootstrap_args, fx_price_xccy_args,
) -> None:
    """--tenor outside the CLI table ⇒ FX_PRICE_UNKNOWN_TENOR (resolved before basis_at)."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_xccy_args.tenor_code = "9X"
    assert run_fx_price_xccy(fx_price_xccy_args) == 2


@pytest.mark.phase8
def test_xccy_no_tenor_arg_aborts(
    fx_bootstrap_args, fx_price_xccy_args,
) -> None:
    """--tenor unset ⇒ FX_PRICE_TENOR_REQUIRED ERROR."""
    assert run_fx_bootstrap(fx_bootstrap_args) == 0
    fx_price_xccy_args.tenor_code = None
    assert run_fx_price_xccy(fx_price_xccy_args) == 2
