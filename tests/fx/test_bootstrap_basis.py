"""Phase 7 — FX cross-currency basis bootstrap (M-106): contract + grid behaviour.

These tests pin the *contract* surface of ``build_cross_basis_curve`` — quote
sign resolution, the usable-quote grid (skip/dedup), error paths, and the
sign-flag passthrough — under the V0.4 strip. The numerical strip semantics
(net-PV=0, CIP degeneracy, sign branch, day-count bias) live in
``tests/fx/test_strip.py`` (phase9). Forwards here are parity-tight, so the strip
succeeds and yields b ≈ 0; the value itself is not asserted in this file.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.fx._strip_helpers import (
    SPOT_DATE,
    TR_CALENDAR,
    US_CALENDAR,
    basis_quote,
    cip_forward_curve,
    convention,
    flat_ois,
    market,
    quarterly_coupons,
)

from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.basis_curve import CrossCurrencyBasisCurve
from rates.fx.bootstrap_basis import (
    MixedQuotedLegError,
    XccyBootstrapError,
    build_cross_basis_curve,
)
from rates.fx.types import FX_BASIS_INVERTED, FX_XCCY_QUOTE_SKIPPED

pytestmark = pytest.mark.phase7

_DOM_RATE = 0.45
_FOR_RATE = 0.05


def _codes(dg: DiagnosticsCollector) -> list[str]:
    return [d.code for d in dg.to_list()]


def _curves():
    """Dom/for OIS + parity-tight forward curve covering a 1Y quarterly grid."""
    dom, fer = flat_ois(_DOM_RATE), flat_ois(_FOR_RATE)
    coupons = quarterly_coupons(SPOT_DATE + timedelta(days=360))
    fwd = cip_forward_curve(dom, fer, coupons)
    return dom, fer, fwd, coupons


def _build(market_data, dom, fer, fwd, dg):
    return build_cross_basis_curve(
        market_data, dom, fer, fwd, convention(), TR_CALENDAR, US_CALENDAR, dg
    )


# ---------------------------------------------------------------------------
# Grid: one stripped pillar per usable quote, ascending
# ---------------------------------------------------------------------------


def test_one_pillar_per_usable_quote() -> None:
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    curve = _build(
        market(basis_quote(c[1]), basis_quote(c[2]), basis_quote(c[3])), dom, fer, fwd, dg
    )
    assert isinstance(curve, CrossCurrencyBasisCurve)
    assert len(curve.pillars_tuple) == 3
    maturities = [p.maturity_date for p in curve.pillars_tuple]
    assert maturities == sorted(maturities)
    assert curve.quoted_on_foreign is True
    assert not dg.has_errors()


def test_parity_tight_forwards_no_inversion_warn() -> None:
    """CIP-tight forwards ⇒ b ≈ 0 across pillars ⇒ no FX_BASIS_INVERTED WARN."""
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    _build(market(basis_quote(c[1]), basis_quote(c[2]), basis_quote(c[3])), dom, fer, fwd, dg)
    assert FX_BASIS_INVERTED not in _codes(dg)


# ---------------------------------------------------------------------------
# Quote skip / dedup (grid only — values are not strip inputs)
# ---------------------------------------------------------------------------


def test_non_finite_quote_skipped() -> None:
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    curve = _build(
        market(basis_quote(c[1], bps=float("nan")), basis_quote(c[3])), dom, fer, fwd, dg
    )
    assert FX_XCCY_QUOTE_SKIPPED in _codes(dg)
    assert len(curve.pillars_tuple) == 1
    assert curve.pillars_tuple[0].maturity_date == c[3]


def test_maturity_collision_keeps_first_drops_later() -> None:
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    curve = _build(
        market(
            basis_quote(c[3], code="USDTRY1Y-XCCY"),
            basis_quote(c[3], code="USDTRY1Y-DUP"),
        ),
        dom,
        fer,
        fwd,
        dg,
    )
    assert FX_XCCY_QUOTE_SKIPPED in _codes(dg)
    assert len(curve.pillars_tuple) == 1
    assert curve.pillars_tuple[0].tenor_code == "USDTRY1Y-XCCY"


def test_quoted_on_foreign_false_propagates_to_curve() -> None:
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    curve = _build(
        market(
            basis_quote(c[1], quoted_on_foreign=False),
            basis_quote(c[3], quoted_on_foreign=False),
        ),
        dom,
        fer,
        fwd,
        dg,
    )
    assert curve.quoted_on_foreign is False


# ---------------------------------------------------------------------------
# Error paths (raise before the strip)
# ---------------------------------------------------------------------------


def test_mixed_quoted_on_foreign_raises_and_records_error() -> None:
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    with pytest.raises(MixedQuotedLegError):
        _build(
            market(
                basis_quote(c[1], quoted_on_foreign=True),
                basis_quote(c[3], quoted_on_foreign=False),
            ),
            dom,
            fer,
            fwd,
            dg,
        )
    assert "FX_BASIS_MIXED_QUOTED_LEG" in _codes(dg)
    assert dg.has_errors()


def test_empty_basis_quotes_raises_bootstrap_error() -> None:
    dom, fer, fwd, _ = _curves()
    dg = DiagnosticsCollector()
    with pytest.raises(XccyBootstrapError):
        _build(market(), dom, fer, fwd, dg)
    assert dg.has_errors()


def test_all_quotes_unusable_raises_after_filtering() -> None:
    dom, fer, fwd, c = _curves()
    dg = DiagnosticsCollector()
    with pytest.raises(XccyBootstrapError):
        _build(
            market(basis_quote(c[1], bps=float("nan")), basis_quote(c[3], bps=float("inf"))),
            dom,
            fer,
            fwd,
            dg,
        )
    assert dg.has_errors()
