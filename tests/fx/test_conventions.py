"""Phase 7 — FX conventions (M-102) scaffold tests."""

from __future__ import annotations

import pytest

from rates.fx.conventions import FXConventions
from rates.fx.types import QuoteConvention


@pytest.mark.phase7
def test_load_returns_known_pairs(fx_conventions: FXConventions) -> None:
    assert "USDTRY" in fx_conventions.by_pair
    assert "EURTRY" in fx_conventions.by_pair


@pytest.mark.phase7
def test_usdtry_convention_fields(fx_conventions: FXConventions) -> None:
    c = fx_conventions.by_pair["USDTRY"]
    assert c.pair_code == "USDTRY"
    assert c.quote_convention is QuoteConvention.DIRECT
    assert c.spot_lag_days == 1
    assert c.settlement_calendars == ("US", "TR")
    assert c.forward_point_scale == 10000


@pytest.mark.phase7
def test_eurtry_spot_lag_is_two(fx_conventions: FXConventions) -> None:
    assert fx_conventions.by_pair["EURTRY"].spot_lag_days == 2
