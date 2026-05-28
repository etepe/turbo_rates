"""Collision handling (G4) + abort conditions (F-006 ERROR cases).

* Direct quote wins on tenor_days collision; WARN reports implied-vs-direct spread.
* All-unusable-quotes -> BS_NO_VALID_QUOTES ERROR + ZeroValidQuotesError.
* Empty quotes -> ZeroValidQuotesError (via ValueError on val_date resolution if None).
"""

from __future__ import annotations

import pytest

from rates.core.bootstrap import (
    REPRICE_TOLERANCE,
    ZeroValidQuotesError,
    bootstrap_curve,
)
from rates.core.types import MarketData, MarketQuote


def _codes(dg):
    return [d.code for d in dg.to_list()]


@pytest.mark.phase2
def test_tenor_collision_direct_wins(val_date, yearly_date, conventions, tr_calendar, diagnostics):
    """Two quotes for the same maturity: first (lower tenor_code) kept, second shadowed.

    With identical tenor_days, the secondary sort key is tenor_code (alphabetical), so
    ``TYSO1Y_DIRECT`` precedes ``TYSO1Y_DUP`` and becomes the canonical quote.
    """
    end = yearly_date(1)
    days = (end - val_date).days
    q1 = MarketQuote("TYSO1Y_DIRECT", days, val_date, end, None, None, 0.45, "src")
    q2 = MarketQuote("TYSO1Y_DUP", days, val_date, end, None, None, 0.46, "src")
    market = MarketData(quotes=(q2, q1), valuation_date=val_date)

    curve = bootstrap_curve(market, conventions, tr_calendar, diagnostics)
    assert len(curve.pillars_tuple) == 1
    assert curve.pillars_tuple[0].tenor_code == "TYSO1Y_DIRECT"
    assert curve.pillars_tuple[0].rate == 0.45
    assert "BS_TENOR_COLLISION" in _codes(diagnostics)
    # The WARN context carries spread_bps.
    collision = next(d for d in diagnostics.to_list() if d.code == "BS_TENOR_COLLISION")
    assert collision.context["spread_bps"] == pytest.approx(100.0, abs=1e-9)


@pytest.mark.phase2
def test_no_valid_quotes_raises(val_date, yearly_date, conventions, tr_calendar, diagnostics):
    """All quotes unusable -> ZeroValidQuotesError + BS_NO_VALID_QUOTES ERROR."""
    end = yearly_date(1)
    q_bad = MarketQuote("TYSO1Y", (end - val_date).days, val_date, end, None, None, None, "src")
    market = MarketData(quotes=(q_bad,), valuation_date=val_date)
    with pytest.raises(ZeroValidQuotesError):
        bootstrap_curve(market, conventions, tr_calendar, diagnostics)
    assert "BS_NO_VALID_QUOTES" in _codes(diagnostics)
    assert diagnostics.has_errors()
    assert diagnostics.exit_code() == 2


@pytest.mark.phase2
def test_empty_quotes_raises(val_date, conventions, tr_calendar, diagnostics):
    """Zero quotes -> ZeroValidQuotesError. ERROR diagnostic also recorded."""
    market = MarketData(quotes=(), valuation_date=val_date)
    with pytest.raises(ZeroValidQuotesError):
        bootstrap_curve(market, conventions, tr_calendar, diagnostics)
    assert diagnostics.has_errors()


@pytest.mark.phase2
def test_bisttref_like_zero_tenor_filtered(
    val_date, yearly_date, conventions, tr_calendar, diagnostics
):
    """A non-pillar row (tenor_days <= 0) is silently filtered, not skipped-with-WARN."""
    end = yearly_date(1)
    q_special = MarketQuote("BISTTREF", 0, val_date, val_date, None, None, 0.4275, "BIST")
    q_pillar = MarketQuote("TYSO1Y", (end - val_date).days, val_date, end, None, None, 0.45, "src")
    market = MarketData(
        quotes=(q_special, q_pillar),
        valuation_date=val_date,
        special_rates={"BISTTREF": 0.4275},
    )
    curve = bootstrap_curve(market, conventions, tr_calendar, diagnostics)
    assert len(curve.pillars_tuple) == 1
    # No skipped-WARN for the BISTTREF-like row.
    assert "BS_QUOTE_SKIPPED" not in _codes(diagnostics)


@pytest.mark.phase2
def test_reprice_tolerance_constant():
    """REPRICE_TOLERANCE is exactly 1e-10 per F-001 / C-002 invariants."""
    assert REPRICE_TOLERANCE == 1e-10
