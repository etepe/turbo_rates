"""Phase 9 — C-110 OIS reconstruction adapters + schema-too-old guard (M-111).

Covers the V0.4 pricing-side wiring:

* ``_dom_ois_from_summary`` / ``_for_ois_from_summary`` rebuild the dual OIS
  curves from a persisted FXSummary v2 (round-trip + dom/for not swapped +
  unknown-interpolation ValueError).
* ``run_fx_price_xccy`` prices on the reconstructed curves for a v2 summary and
  aborts with ``FX_PRICE_SCHEMA_TOO_OLD`` for a v1 summary.
* ``_UNUSED_OIS`` is gone from ``rates.app`` (F-305 AC#2, grep-clean).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import rates.app as app
from rates.app import (
    _dom_ois_from_summary,
    _for_ois_from_summary,
    run_fx_price_xccy,
)
from rates.core.curve import OISCurve, Pillar
from rates.core.types import DayCount
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.types import CurrencyPair, QuoteConvention
from rates.io.persistence import write_fx_summary
from rates.io.schemas import FXSummary

_VAL = date(2026, 6, 12)
_SPOT = date(2026, 6, 15)
_MATURITY = date(2027, 6, 14)


# ---------------------------------------------------------------------------
# Hand-built domain objects (no bootstrap; dom/for deliberately distinct).
# ---------------------------------------------------------------------------


def _dom_ois() -> OISCurve:
    """Domestic (TRY) OIS — Act/360, log_linear_df."""
    return OISCurve(
        valuation_date=_VAL,
        day_count=DayCount.ACT_360,
        interp="log_linear_df",
        pillars_tuple=(
            Pillar("TYSO3M", 92, _VAL, date(2026, 9, 14), 0.45, 0.8955),
            Pillar("TYSO1Y", 367, _VAL, _MATURITY, 0.42, 0.7000),
        ),
        forward_ladder_dates=(),
    )


def _for_ois() -> OISCurve:
    """Foreign (USD) OIS — Act/365, linear_zero (differs from dom in every scalar)."""
    return OISCurve(
        valuation_date=_VAL,
        day_count=DayCount.ACT_365,
        interp="linear_zero",
        pillars_tuple=(
            Pillar("USSO3M", 92, _VAL, date(2026, 9, 14), 0.0525, 0.9869),
            Pillar("USSO1Y", 367, _VAL, _MATURITY, 0.0500, 0.9520),
        ),
        forward_ladder_dates=(),
    )


def _pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")


def _conv() -> FXConvention:
    return FXConvention(
        pair_code="USDTRY",
        quote_convention=QuoteConvention.DIRECT,
        spot_lag_days=2,
        settlement_calendars=("TR", "US"),
        forward_point_scale=10000,
    )


def _v2_summary() -> FXSummary:
    forward = FXForwardCurve(
        pair_code="USDTRY",
        spot_date=_SPOT,
        spot_rate=32.45,
        pillars_tuple=(FXForwardPillar("1Y", 364, _MATURITY, 33.50),),
    )
    basis = CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=_SPOT,
        pillars_tuple=(BasisPillar("1Y", 364, _MATURITY, -180.0),),
        quoted_on_foreign=True,
    )
    return FXSummary.from_domain(
        pair=_pair(),
        valuation_date=_VAL,
        forward=forward,
        basis=basis,
        parity_checks=[],
        conv=_conv(),
        diagnostics=[],
        dom_ois=_dom_ois(),
        for_ois=_for_ois(),
        strip_residuals={},
        as_of_timestamp=datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# C-110 reconstruction round-trip
# ---------------------------------------------------------------------------


@pytest.mark.phase9
def test_c110_dom_ois_round_trips_pillars_and_meta() -> None:
    summary = _v2_summary()
    orig = _dom_ois()
    rebuilt = _dom_ois_from_summary(summary)

    assert rebuilt.valuation_date == orig.valuation_date
    assert rebuilt.day_count == orig.day_count
    assert rebuilt.interp == orig.interp
    # The pricing path never uses the forward ladder — reconstructed empty (OQ-502).
    assert rebuilt.forward_ladder_dates == ()
    # DF matches exactly at every pillar (start_date := meta.valuation_date per G10).
    for p in orig.pillars_tuple:
        assert rebuilt.df_at(p.end_date) == pytest.approx(p.discount_factor)
        assert rebuilt.df_at(p.end_date) == pytest.approx(orig.df_at(p.end_date))


@pytest.mark.phase9
def test_c110_for_ois_round_trips_and_not_swapped_with_dom() -> None:
    summary = _v2_summary()
    rebuilt = _for_ois_from_summary(summary)
    # Foreign meta must not be swapped with domestic.
    assert rebuilt.day_count == DayCount.ACT_365
    assert rebuilt.interp == "linear_zero"
    for p in _for_ois().pillars_tuple:
        assert rebuilt.df_at(p.end_date) == pytest.approx(p.discount_factor)


@pytest.mark.phase9
def test_c110_unknown_interpolation_raises_value_error() -> None:
    summary = _v2_summary()
    bad_meta = summary.dom_ois_meta.model_copy(update={"interpolation": "bogus_scheme"})
    bad_summary = summary.model_copy(update={"dom_ois_meta": bad_meta})
    with pytest.raises(ValueError, match="unknown interpolation"):
        _dom_ois_from_summary(bad_summary)


# ---------------------------------------------------------------------------
# run_fx_price_xccy on reconstructed curves (v2) / schema-too-old (v1)
# ---------------------------------------------------------------------------


def _write_summary_json(summary: FXSummary, output_root: Path) -> Path:
    path = output_root / "data" / "latest" / "usdtry_fx_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return path


@pytest.mark.phase9
def test_run_fx_price_xccy_prices_on_reconstructed_curves(
    fx_price_xccy_args, capfd,
) -> None:
    """A v2 summary reconstructs both OIS curves (C-110) and prices the 1Y pillar
    to the stored b_n (round-trip identity through the orchestrator)."""
    write_fx_summary(
        _v2_summary(),
        fx_price_xccy_args.output_root / "data" / "latest" / "usdtry_fx_summary.json",
    )
    code = run_fx_price_xccy(fx_price_xccy_args)
    out, _ = capfd.readouterr()
    assert code == 0
    assert "xccy_basis(USDTRY 1Y)" in out
    assert "-180.0000 bps" in out


@pytest.mark.phase9
def test_run_fx_price_xccy_v1_summary_aborts_schema_too_old(
    fx_price_xccy_args, capfd,
) -> None:
    """A v1 summary lacks embedded OIS pillars ⇒ FX_PRICE_SCHEMA_TOO_OLD + exit 2."""
    summary_v1 = _v2_summary().model_copy(update={"schema_version": 1})
    _write_summary_json(summary_v1, fx_price_xccy_args.output_root)

    code = run_fx_price_xccy(fx_price_xccy_args)
    out, err = capfd.readouterr()
    assert code == 2
    assert "FX_PRICE_SCHEMA_TOO_OLD" in out or "FX_PRICE_SCHEMA_TOO_OLD" in err


# ---------------------------------------------------------------------------
# F-305 AC#2 — _UNUSED_OIS grep-clean
# ---------------------------------------------------------------------------


@pytest.mark.phase9
def test_unused_ois_placeholder_removed_from_app_source() -> None:
    """The _UNUSED_OIS = cast(OISCurve, None) lie must be gone (F-305 AC#2)."""
    source = Path(app.__file__).read_text(encoding="utf-8")
    assert "_UNUSED_OIS" not in source
