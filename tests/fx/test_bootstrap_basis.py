"""Phase 7 — FX cross-currency basis bootstrap (M-106) tests."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rates.core.curve import OISCurve, Pillar
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import DayCount
from rates.fx.basis_curve import CrossCurrencyBasisCurve
from rates.fx.bootstrap_basis import (
    MixedQuotedLegError,
    XccyBootstrapError,
    build_cross_basis_curve,
)
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.types import (
    FX_BASIS_INVERTED,
    FX_XCCY_QUOTE_SKIPPED,
    CrossCurrencyBasisQuote,
    CurrencyPair,
    FXMarketData,
    FXSpotQuote,
    QuoteConvention,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_VAL = date(2026, 6, 12)
_SPOT_DATE = date(2026, 6, 15)


def _pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")


def _conv() -> FXConvention:
    return FXConvention(
        pair_code="USDTRY",
        quote_convention=QuoteConvention.DIRECT,
        spot_lag_days=1,
        settlement_calendars=("US", "TR"),
        forward_point_scale=10000,
    )


def _spot(rate: float = 32.5) -> FXSpotQuote:
    return FXSpotQuote(
        pair=_pair(), spot_date=_SPOT_DATE, bid=None, ask=None, mid=rate, source="x"
    )


def _flat_ois(rate: float, days: int = 400, val_date: date = _VAL) -> OISCurve:
    """Bullet-form OISCurve for the few horizons the basis bootstrap touches."""
    pillars: list[Pillar] = []
    for d in (30, 90, 180, 270, days):
        if d <= 0 or d > days:
            continue
        end = val_date + timedelta(days=d)
        tau = d / 360.0
        df = 1.0 / (1.0 + rate * tau)
        pillars.append(
            Pillar(
                tenor_code=f"FLAT{d}D",
                tenor_days=d,
                start_date=val_date,
                end_date=end,
                rate=rate,
                discount_factor=df,
            )
        )
    return OISCurve(
        valuation_date=val_date,
        day_count=DayCount.ACT_360,
        interp="log_linear_df",
        pillars_tuple=tuple(pillars),
        forward_ladder_dates=(),
    )


def _fx_forward(spot: float = 32.5) -> FXForwardCurve:
    """Minimal FXForwardCurve — one pillar, used only as an opaque handle."""
    return FXForwardCurve(
        pair_code="USDTRY",
        spot_date=_SPOT_DATE,
        spot_rate=spot,
        pillars_tuple=(
            FXForwardPillar(
                tenor_code="USDTRY30D",
                tenor_days=30,
                settle_date=_SPOT_DATE + timedelta(days=30),
                forward_rate=spot * 1.01,
            ),
        ),
    )


def _basis_quote(
    days: int,
    bps: float,
    *,
    code: str | None = None,
    quoted_on_foreign: bool = True,
) -> CrossCurrencyBasisQuote:
    return CrossCurrencyBasisQuote(
        pair=_pair(),
        tenor_code=code or f"USDTRY{days}D-XCCY",
        maturity_date=_SPOT_DATE + timedelta(days=days),
        spread_bps=bps,
        quoted_on_foreign=quoted_on_foreign,
        source="x",
    )


def _market(
    *basis_quotes: CrossCurrencyBasisQuote,
    spot: float = 32.5,
) -> FXMarketData:
    return FXMarketData(
        spot=_spot(spot),
        basis_quotes=tuple(basis_quotes),
        valuation_date=_VAL,
    )


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.phase7
def test_bootstrap_emits_one_pillar_per_basis_quote() -> None:
    market = _market(
        _basis_quote(30, -10.0),
        _basis_quote(90, -25.0),
        _basis_quote(180, -30.0),
    )
    dg = DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert len(curve.pillars_tuple) == 3
    assert curve.pillars_tuple[0].spread_bps == -10.0
    assert curve.pillars_tuple[1].spread_bps == -25.0
    assert curve.pillars_tuple[2].spread_bps == -30.0
    assert curve.quoted_on_foreign is True
    assert not dg.has_errors()


@pytest.mark.phase7
def test_returned_curve_basis_at_matches_pillar_spread() -> None:
    """End-to-end sanity: the bootstrap output integrates with M-104 basis_at."""
    market = _market(_basis_quote(90, -25.0), _basis_quote(180, -30.0))
    dg = DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert isinstance(curve, CrossCurrencyBasisCurve)
    assert curve.basis_at(_SPOT_DATE) == 0.0
    assert curve.basis_at(_SPOT_DATE + timedelta(days=90)) == -25.0
    assert curve.basis_at(_SPOT_DATE + timedelta(days=180)) == -30.0


@pytest.mark.phase7
def test_basis_inverted_warns_on_sign_flip_between_pillars() -> None:
    market = _market(_basis_quote(30, -20.0), _basis_quote(90, +15.0))
    dg = DiagnosticsCollector()
    build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert FX_BASIS_INVERTED in _codes(dg)


@pytest.mark.phase7
def test_basis_inverted_silent_when_all_pillars_same_sign() -> None:
    market = _market(_basis_quote(30, -10.0), _basis_quote(90, -20.0))
    dg = DiagnosticsCollector()
    build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert FX_BASIS_INVERTED not in _codes(dg)


@pytest.mark.phase7
def test_zero_spread_does_not_trigger_inversion_warn() -> None:
    """Zero-spread pillar is treated as same-sign as either neighbour."""
    market = _market(
        _basis_quote(30, -15.0),
        _basis_quote(90, 0.0),
        _basis_quote(180, -25.0),
    )
    dg = DiagnosticsCollector()
    build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert FX_BASIS_INVERTED not in _codes(dg)


@pytest.mark.phase7
def test_xccy_quote_skipped_warns_on_unusable_legs() -> None:
    """Non-finite spread_bps (NaN/inf) is skipped with FX_XCCY_QUOTE_SKIPPED."""
    nan_quote = _basis_quote(30, float("nan"))
    good_quote = _basis_quote(90, -25.0)
    market = _market(nan_quote, good_quote)
    dg = DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert FX_XCCY_QUOTE_SKIPPED in _codes(dg)
    assert len(curve.pillars_tuple) == 1
    assert curve.pillars_tuple[0].spread_bps == -25.0


@pytest.mark.phase7
def test_maturity_collision_keeps_first_drops_later() -> None:
    """Two quotes at the same maturity: keep the first, drop the second + WARN."""
    market = _market(
        _basis_quote(90, -25.0, code="USDTRY90D-XCCY"),
        _basis_quote(90, -30.0, code="USDTRY90D-DUP"),
    )
    dg = DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert FX_XCCY_QUOTE_SKIPPED in _codes(dg)
    assert len(curve.pillars_tuple) == 1
    assert curve.pillars_tuple[0].tenor_code == "USDTRY90D-XCCY"


@pytest.mark.phase7
def test_mixed_quoted_on_foreign_raises_and_records_error() -> None:
    market = _market(
        _basis_quote(30, -10.0, quoted_on_foreign=True),
        _basis_quote(90, -25.0, quoted_on_foreign=False),
    )
    dg = DiagnosticsCollector()
    with pytest.raises(MixedQuotedLegError):
        build_cross_basis_curve(
            market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
        )
    assert "FX_BASIS_MIXED_QUOTED_LEG" in _codes(dg)
    assert dg.has_errors()


@pytest.mark.phase7
def test_empty_basis_quotes_raises_bootstrap_error() -> None:
    market = _market()  # no basis quotes
    dg = DiagnosticsCollector()
    with pytest.raises(XccyBootstrapError):
        build_cross_basis_curve(
            market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
        )
    assert dg.has_errors()


@pytest.mark.phase7
def test_all_quotes_unusable_raises_after_filtering() -> None:
    """Even with quotes present, if all are unusable the bootstrap errors out."""
    market = _market(_basis_quote(30, float("nan")), _basis_quote(90, float("inf")))
    dg = DiagnosticsCollector()
    with pytest.raises(XccyBootstrapError):
        build_cross_basis_curve(
            market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
        )
    assert dg.has_errors()


@pytest.mark.phase7
def test_quoted_on_foreign_false_propagates_to_curve() -> None:
    market = _market(
        _basis_quote(30, -10.0, quoted_on_foreign=False),
        _basis_quote(90, -25.0, quoted_on_foreign=False),
    )
    dg = DiagnosticsCollector()
    curve = build_cross_basis_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _fx_forward(), _conv(), dg
    )
    assert curve.quoted_on_foreign is False
