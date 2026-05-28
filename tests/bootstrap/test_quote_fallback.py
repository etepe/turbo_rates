"""Quote fallback ladder per F-006 / G13.

Order: mid -> avg(bid,ask) -> ask -> bid -> skip (WARN). Each fallback emits a
specific WARN code.
"""

from __future__ import annotations

import pytest

from rates.core.bootstrap import bootstrap_curve
from rates.core.diagnostics import Severity
from rates.core.types import MarketData, MarketQuote


def _codes(dg):
    return [d.code for d in dg.to_list()]


@pytest.mark.phase2
def test_avg_bid_ask_used_when_mid_missing(
    val_date, yearly_date, conventions, tr_calendar, diagnostics
):
    """mid=None, bid+ask present -> BS_QUOTE_FALLBACK_AVG, rate = (bid+ask)/2."""
    end = yearly_date(1)
    q = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=(end - val_date).days,
        start_date=val_date,
        end_date=end,
        bid=0.4490,
        ask=0.4510,
        mid=None,
        source="synthetic",
    )
    market = MarketData(quotes=(q,), valuation_date=val_date)
    curve = bootstrap_curve(market, conventions, tr_calendar, diagnostics)

    assert "BS_QUOTE_FALLBACK_AVG" in _codes(diagnostics)
    assert curve.pillars_tuple[0].rate == pytest.approx(0.45, abs=1e-15)


@pytest.mark.phase2
def test_ask_used_when_mid_and_bid_missing(
    val_date, yearly_date, conventions, tr_calendar, diagnostics
):
    end = yearly_date(1)
    q = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=(end - val_date).days,
        start_date=val_date,
        end_date=end,
        bid=None,
        ask=0.4501,
        mid=None,
        source="synthetic",
    )
    market = MarketData(quotes=(q,), valuation_date=val_date)
    curve = bootstrap_curve(market, conventions, tr_calendar, diagnostics)

    assert "BS_QUOTE_FALLBACK_ASK" in _codes(diagnostics)
    assert curve.pillars_tuple[0].rate == 0.4501


@pytest.mark.phase2
def test_bid_used_when_only_bid_present(
    val_date, yearly_date, conventions, tr_calendar, diagnostics
):
    end = yearly_date(1)
    q = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=(end - val_date).days,
        start_date=val_date,
        end_date=end,
        bid=0.4499,
        ask=None,
        mid=None,
        source="synthetic",
    )
    market = MarketData(quotes=(q,), valuation_date=val_date)
    curve = bootstrap_curve(market, conventions, tr_calendar, diagnostics)

    assert "BS_QUOTE_FALLBACK_BID" in _codes(diagnostics)
    assert curve.pillars_tuple[0].rate == 0.4499


@pytest.mark.phase2
def test_bid_greater_than_ask_warns(val_date, yearly_date, conventions, tr_calendar, diagnostics):
    """Inverted bid/ask -> BS_BID_GT_ASK WARN, but avg still used."""
    end = yearly_date(1)
    q = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=(end - val_date).days,
        start_date=val_date,
        end_date=end,
        bid=0.4510,
        ask=0.4490,
        mid=None,
        source="synthetic",
    )
    market = MarketData(quotes=(q,), valuation_date=val_date)
    bootstrap_curve(market, conventions, tr_calendar, diagnostics)

    codes = _codes(diagnostics)
    assert "BS_BID_GT_ASK" in codes


@pytest.mark.phase2
def test_pillar_skipped_when_no_quote(val_date, yearly_date, conventions, tr_calendar, diagnostics):
    """All three quote fields None -> BS_QUOTE_SKIPPED, pillar dropped."""
    q_bad = MarketQuote(
        tenor_code="TYSO6M",
        tenor_days=180,
        start_date=val_date,
        end_date=yearly_date(1),  # only the field; will be filtered before use
        bid=None,
        ask=None,
        mid=None,
        source="synthetic",
    )
    q_good = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=(yearly_date(1) - val_date).days,
        start_date=val_date,
        end_date=yearly_date(1),
        bid=None,
        ask=None,
        mid=0.45,
        source="synthetic",
    )
    market = MarketData(quotes=(q_bad, q_good), valuation_date=val_date)
    curve = bootstrap_curve(market, conventions, tr_calendar, diagnostics)

    assert "BS_QUOTE_SKIPPED" in _codes(diagnostics)
    assert len(curve.pillars_tuple) == 1
    assert curve.pillars_tuple[0].tenor_code == "TYSO1Y"
    # Skipped pillar does NOT promote to error since others are usable.
    assert not diagnostics.has_errors()


@pytest.mark.phase2
def test_all_warns_are_warn_severity(val_date, yearly_date, conventions, tr_calendar, diagnostics):
    """Fallback ladder never produces ERROR diagnostics on usable quotes."""
    end = yearly_date(1)
    q = MarketQuote(
        tenor_code="TYSO1Y",
        tenor_days=(end - val_date).days,
        start_date=val_date,
        end_date=end,
        bid=0.4490,
        ask=0.4510,
        mid=None,
        source="synthetic",
    )
    market = MarketData(quotes=(q,), valuation_date=val_date)
    bootstrap_curve(market, conventions, tr_calendar, diagnostics)
    for d in diagnostics.to_list():
        assert d.severity is Severity.WARN
