"""Phase 9 — basis-aware FX_PARITY_MISMATCH gate (A-6 / OQ-503, M-111).

The relocated gate compares the stripped (forward-implied) pillar spread b_n
against the QUOTED xccy spread at the same tenor and WARNs when they differ by
more than PARITY_MISMATCH_BPS (1.0 bps). M-105 no longer emits the old
pure-CIP WARN. These tests exercise the orchestrator helpers directly.
"""

from __future__ import annotations

from datetime import date

import pytest

from rates.app import _parity_checks_from_diagnostics, _warn_basis_parity_mismatches
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.types import FX_PARITY_MISMATCH, CrossCurrencyBasisQuote, CurrencyPair

_PAIR = CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")
_SPOT = date(2026, 6, 15)
_MAT_1Y = date(2027, 6, 15)
_MAT_2Y = date(2028, 6, 15)


def _basis(*pillars: BasisPillar) -> CrossCurrencyBasisCurve:
    return CrossCurrencyBasisCurve(
        pair_code="USDTRY",
        spot_date=_SPOT,
        pillars_tuple=pillars,
        quoted_on_foreign=True,
    )


def _quote(tenor: str, maturity: date, spread_bps: float) -> CrossCurrencyBasisQuote:
    return CrossCurrencyBasisQuote(
        pair=_PAIR,
        tenor_code=tenor,
        maturity_date=maturity,
        spread_bps=spread_bps,
        quoted_on_foreign=True,
        source="x",
    )


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


@pytest.mark.phase9
def test_gate_warns_when_forward_implied_basis_deviates_from_quoted() -> None:
    """Stripped -180 vs quoted -175 ⇒ 5 bps deviation > 1.0 ⇒ WARN."""
    basis = _basis(BasisPillar("1Y", 365, _MAT_1Y, -180.0))
    quotes = (_quote("1Y", _MAT_1Y, -175.0),)
    dg = DiagnosticsCollector()
    _warn_basis_parity_mismatches(basis, quotes, dg)
    assert FX_PARITY_MISMATCH in _codes(dg)


@pytest.mark.phase9
def test_gate_silent_when_forward_implied_basis_matches_quoted() -> None:
    """Stripped -180.0 vs quoted -180.5 ⇒ 0.5 bps deviation < 1.0 ⇒ no WARN."""
    basis = _basis(BasisPillar("1Y", 365, _MAT_1Y, -180.0))
    quotes = (_quote("1Y", _MAT_1Y, -180.5),)
    dg = DiagnosticsCollector()
    _warn_basis_parity_mismatches(basis, quotes, dg)
    assert FX_PARITY_MISMATCH not in _codes(dg)
    assert not dg.has_errors()


@pytest.mark.phase9
def test_gate_per_pillar_only_offenders_warn() -> None:
    """Multi-pillar: only the deviating tenor fires."""
    basis = _basis(
        BasisPillar("1Y", 365, _MAT_1Y, -180.0),  # matches quote
        BasisPillar("2Y", 730, _MAT_2Y, -200.0),  # 10 bps off quote
    )
    quotes = (_quote("1Y", _MAT_1Y, -180.2), _quote("2Y", _MAT_2Y, -210.0))
    dg = DiagnosticsCollector()
    _warn_basis_parity_mismatches(basis, quotes, dg)
    warns = [d for d in dg.to_list() if d.code == FX_PARITY_MISMATCH]
    assert len(warns) == 1
    assert warns[0].context["tenor_code"] == "2Y"


@pytest.mark.phase9
def test_parity_checks_projection_reinterprets_columns_as_bps() -> None:
    """_parity_checks_from_diagnostics maps the basis WARN onto FXParityCheckRow:
    quoted_forward→quoted bps, parity_forward→b_n bps, diff_bps_of_spot→diff."""
    basis = _basis(BasisPillar("1Y", 365, _MAT_1Y, -180.0))
    quotes = (_quote("1Y", _MAT_1Y, -175.0),)
    dg = DiagnosticsCollector()
    _warn_basis_parity_mismatches(basis, quotes, dg)

    rows = _parity_checks_from_diagnostics(dg)
    assert len(rows) == 1
    row = rows[0]
    assert row.tenor_code == "1Y"
    assert row.settle_date == _MAT_1Y
    assert row.quoted_forward == pytest.approx(-175.0)  # quoted basis (bps)
    assert row.parity_forward == pytest.approx(-180.0)  # forward-implied b_n (bps)
    assert row.diff_bps_of_spot == pytest.approx(-5.0)  # b_n - quoted (bps)


@pytest.mark.phase9
def test_parity_checks_empty_when_no_mismatch() -> None:
    dg = DiagnosticsCollector()
    assert _parity_checks_from_diagnostics(dg) == []
