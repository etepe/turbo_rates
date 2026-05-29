"""Phase 7 — FX forward bootstrap (M-105) tests."""

from __future__ import annotations

from datetime import date, timedelta
from math import isclose

import pytest

from rates.core.curve import OISCurve, Pillar
from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import DayCount
from rates.fx.bootstrap_forward import (
    FXBootstrapError,
    FXSpotMissingError,
    build_fx_forward_curve,
)
from rates.fx.conventions import FXConvention
from rates.fx.types import (
    FX_FWD_POINTS_NON_MONOTONE,
    FX_PARITY_MISMATCH,
    FX_SPOT_MISSING,
    CurrencyPair,
    FXForwardPointQuote,
    FXMarketData,
    FXSpotQuote,
    QuoteConvention,
)

# ---------------------------------------------------------------------------
# Synthetic OIS curve helpers — direct construction, no bootstrap_curve.
# ---------------------------------------------------------------------------


_VAL = date(2026, 6, 12)
_SPOT_DATE = date(2026, 6, 15)  # T+1 US, T+1 TR; both biz days


def _flat_ois(rate: float, days: int = 400, val_date: date = _VAL) -> OISCurve:
    """Build an OISCurve with a few pillars all at the same simple rate ``r``.

    DFs are bullet-form: ``DF(d) = 1 / (1 + r * d/360)`` (Act/360).
    """
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


def _fwd(days: int, points: float, code: str | None = None) -> FXForwardPointQuote:
    return FXForwardPointQuote(
        pair=_pair(),
        tenor_code=code or f"USDTRY{days}D",
        tenor_days=days,
        settle_date=_SPOT_DATE + timedelta(days=days),
        bid=None,
        ask=None,
        mid=points,
        source="x",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


@pytest.mark.phase7
def test_bootstrap_emits_one_pillar_per_forward_point() -> None:
    spot = 32.5
    market = FXMarketData(
        spot=_spot(spot),
        forward_points=(_fwd(30, 2000.0), _fwd(90, 6000.0), _fwd(180, 12000.0)),
        valuation_date=_VAL,
    )
    dg = DiagnosticsCollector()
    # Domestic rate (TRY) high, foreign (USD) low → forward > spot ↗ matches parity.
    dom = _flat_ois(0.45)
    for_ = _flat_ois(0.05)
    curve = build_fx_forward_curve(market, dom, for_, _conv(), dg)
    assert len(curve.pillars_tuple) == 3
    # Quoted outright = spot + points/scale.
    assert curve.pillars_tuple[0].forward_rate == pytest.approx(spot + 0.2)
    assert curve.pillars_tuple[1].forward_rate == pytest.approx(spot + 0.6)
    assert curve.pillars_tuple[2].forward_rate == pytest.approx(spot + 1.2)
    assert not dg.has_errors()


# NOTE: M-105's pure-CIP FX_PARITY_MISMATCH WARN was removed in V0.4 (A-6 /
# OQ-503). The parity reconciliation is now the basis-aware gate in rates.app,
# unit-tested in tests/app/test_fx_parity_gate.py; M-105 no longer emits it.


@pytest.mark.phase9
def test_m105_no_longer_emits_parity_mismatch() -> None:
    """A-6: even wildly off-parity forwards produce no FX_PARITY_MISMATCH from
    M-105 — the basis-aware gate in rates.app is now the single authority."""
    spot = 32.5
    market = FXMarketData(
        spot=_spot(spot),
        forward_points=(_fwd(180, 12000.0),),  # far above CIP for these rates
        valuation_date=_VAL,
    )
    dg = DiagnosticsCollector()
    build_fx_forward_curve(market, _flat_ois(0.10), _flat_ois(0.05), _conv(), dg)
    assert FX_PARITY_MISMATCH not in _codes(dg)
    assert not dg.has_errors()


@pytest.mark.phase7
def test_missing_spot_raises_fx_spot_missing() -> None:
    bad_spot = FXSpotQuote(
        pair=_pair(), spot_date=_SPOT_DATE, bid=None, ask=None, mid=None, source="x"
    )
    market = FXMarketData(spot=bad_spot, forward_points=(_fwd(30, 100.0),))
    dg = DiagnosticsCollector()
    with pytest.raises(FXSpotMissingError):
        build_fx_forward_curve(
            market, _flat_ois(0.45), _flat_ois(0.05), _conv(), dg
        )
    assert FX_SPOT_MISSING in _codes(dg)
    assert dg.has_errors()


@pytest.mark.phase7
def test_non_monotone_forward_points_warns() -> None:
    """Two quotes with overlapping tenor_days emit FX_FWD_POINTS_NON_MONOTONE."""
    spot = 32.5
    quotes = (_fwd(30, 200.0), _fwd(30, 250.0, code="USDTRY30D-DUP"))
    market = FXMarketData(spot=_spot(spot), forward_points=quotes, valuation_date=_VAL)
    dg = DiagnosticsCollector()
    build_fx_forward_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _conv(), dg
    )
    assert FX_FWD_POINTS_NON_MONOTONE in _codes(dg)


@pytest.mark.phase7
def test_empty_forward_points_raises_bootstrap_error() -> None:
    market = FXMarketData(spot=_spot(), forward_points=(), valuation_date=_VAL)
    dg = DiagnosticsCollector()
    with pytest.raises(FXBootstrapError):
        build_fx_forward_curve(
            market, _flat_ois(0.45), _flat_ois(0.05), _conv(), dg
        )
    assert dg.has_errors()


@pytest.mark.phase7
def test_quote_resolution_falls_back_through_ladder() -> None:
    """mid → avg → ask → bid fallback; bid-only quote still produces a pillar."""
    spot = 32.5
    bid_only = FXForwardPointQuote(
        pair=_pair(),
        tenor_code="USDTRY30D",
        tenor_days=30,
        settle_date=_SPOT_DATE + timedelta(days=30),
        bid=180.0,
        ask=None,
        mid=None,
        source="x",
    )
    market = FXMarketData(
        spot=_spot(spot), forward_points=(bid_only,), valuation_date=_VAL
    )
    dg = DiagnosticsCollector()
    curve = build_fx_forward_curve(
        market, _flat_ois(0.45), _flat_ois(0.05), _conv(), dg
    )
    assert len(curve.pillars_tuple) == 1
    assert isclose(curve.pillars_tuple[0].forward_rate, spot + 0.018, abs_tol=1e-12)
