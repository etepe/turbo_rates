"""Phase 7 — FX types (M-101) scaffold tests."""

from __future__ import annotations

from datetime import date

import pytest

from rates.fx.types import (
    CurrencyPair,
    FXMarketData,
    FXSpotQuote,
    QuoteConvention,
)


@pytest.mark.phase7
def test_currency_pair_holds_domestic_foreign_code() -> None:
    pair = CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")
    assert pair.domestic == "TRY"
    assert pair.foreign == "USD"
    assert pair.code == "USDTRY"


@pytest.mark.phase7
def test_fx_market_data_defaults_empty_tuples() -> None:
    pair = CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")
    spot = FXSpotQuote(
        pair=pair, spot_date=date(2026, 6, 15), bid=None, ask=None, mid=32.5, source="x"
    )
    market = FXMarketData(spot=spot)
    assert market.forward_points == ()
    assert market.swap_quotes == ()
    assert market.basis_quotes == ()


@pytest.mark.phase7
def test_quote_convention_enum_values() -> None:
    assert QuoteConvention("direct") is QuoteConvention.DIRECT
    assert QuoteConvention("indirect") is QuoteConvention.INDIRECT


@pytest.mark.phase7
@pytest.mark.skip(reason="V2 — provider-level validation not yet implemented")
def test_fx_market_data_rejects_inconsistent_pair_across_quotes() -> None:
    """When forward_points or basis_quotes carry a different pair than spot,
    the IO boundary will reject. Skipped until rates.io.fx_market lands."""
    raise NotImplementedError
