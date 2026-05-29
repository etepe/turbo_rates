"""Shared builders for the V0.4 xccy strip tests (M-106).

Constructs self-consistent OIS curves and an FX forward curve so the strip's
CIP degeneracy (§5.3) and reprice identity (§5.4) can be exercised against
independently-derived expectations. Not a test module — imported by
``test_strip.py`` and ``test_bootstrap_basis.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path

from rates.core.calendar import HolidayCalendar
from rates.core.curve import OISCurve, Pillar
from rates.core.types import BusinessDayConvention, DayCount
from rates.fx.conventions import FXConvention
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.schedule import build_quarterly_xccy_schedule
from rates.fx.types import (
    CrossCurrencyBasisQuote,
    CurrencyPair,
    FXMarketData,
    FXSpotQuote,
    QuoteConvention,
)

VAL_DATE = date(2026, 6, 12)
SPOT_DATE = date(2026, 6, 15)
SPOT_RATE = 32.5

MODFOL = BusinessDayConvention.MODIFIED_FOLLOWING
ACT360 = DayCount.ACT_360

# Module-level joint calendars for the USDTRY pair (built once).
TR_CALENDAR = HolidayCalendar.for_currency(
    "TR", years=range(2025, 2035), override_dir=Path("config/holidays")
)
US_CALENDAR = HolidayCalendar.for_currency(
    "US", years=range(2025, 2035), override_dir=Path("config/holidays")
)


def pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")


def convention() -> FXConvention:
    return FXConvention(
        pair_code="USDTRY",
        quote_convention=QuoteConvention.DIRECT,
        spot_lag_days=1,
        settlement_calendars=("US", "TR"),
        forward_point_scale=10000,
    )


def spot_quote(rate: float = SPOT_RATE) -> FXSpotQuote:
    return FXSpotQuote(
        pair=pair(), spot_date=SPOT_DATE, bid=None, ask=None, mid=rate, source="test"
    )


def flat_ois(
    rate: float,
    day_count: DayCount = ACT360,
    *,
    val_date: date = VAL_DATE,
    horizon_days: Iterable[int] = (30, 90, 180, 270, 365, 450),
) -> OISCurve:
    """Simple-compounded flat OIS curve covering the given horizons."""
    pillars: list[Pillar] = []
    for d in horizon_days:
        end = val_date + timedelta(days=d)
        tau = d / 360.0
        df = 1.0 / (1.0 + rate * tau)
        pillars.append(
            Pillar(
                tenor_code=f"OIS{d}D",
                tenor_days=d,
                start_date=val_date,
                end_date=end,
                rate=rate,
                discount_factor=df,
            )
        )
    return OISCurve(
        valuation_date=val_date,
        day_count=day_count,
        interp="log_linear_df",
        pillars_tuple=tuple(pillars),
        forward_ladder_dates=(),
    )


def quarterly_coupons(maturity: date) -> tuple[date, ...]:
    """Coupon dates of the quarterly schedule SPOT_DATE -> maturity (M-113)."""
    return build_quarterly_xccy_schedule(
        SPOT_DATE, maturity, TR_CALENDAR, US_CALENDAR, MODFOL, ACT360
    ).coupon_dates


def cip_forward_curve(
    dom_ois: OISCurve,
    for_ois: OISCurve,
    settle_dates: Iterable[date],
    *,
    spot_rate: float = SPOT_RATE,
    deviation: float | Sequence[float] = 0.0,
) -> FXForwardCurve:
    """FX forward curve with pillars at ``settle_dates``.

    Each pillar is the **spot-anchored** CIP forward
    ``S · (DF_for(t)/DF_for(spot)) / (DF_dom(t)/DF_dom(spot))`` scaled by
    ``(1 + deviation)``. ``deviation == 0`` reproduces CIP exactly (strip ⇒
    b ≈ 0). A scalar embeds a uniform (multiplicative) deviation; a per-pillar
    sequence embeds a deviation term-structure so each stripped pillar carries a
    distinct basis.
    """
    df_dom_spot = dom_ois.df_at(SPOT_DATE)
    df_for_spot = for_ois.df_at(SPOT_DATE)
    settles = list(settle_dates)
    if isinstance(deviation, (int, float)):
        devs = [float(deviation)] * len(settles)
    else:
        devs = list(deviation)
        if len(devs) != len(settles):
            raise ValueError("deviation sequence length must match settle_dates")
    pillars: list[FXForwardPillar] = []
    for t, dev in zip(settles, devs, strict=True):
        df_dom_rel = dom_ois.df_at(t) / df_dom_spot
        df_for_rel = for_ois.df_at(t) / df_for_spot
        f_cip = spot_rate * df_for_rel / df_dom_rel
        pillars.append(
            FXForwardPillar(
                tenor_code=f"FWD{(t - SPOT_DATE).days}D",
                tenor_days=(t - SPOT_DATE).days,
                settle_date=t,
                forward_rate=f_cip * (1.0 + dev),
            )
        )
    return FXForwardCurve(
        pair_code="USDTRY",
        spot_date=SPOT_DATE,
        spot_rate=spot_rate,
        pillars_tuple=tuple(pillars),
    )


def basis_quote(
    maturity: date,
    *,
    bps: float = 0.0,
    quoted_on_foreign: bool = True,
    code: str | None = None,
) -> CrossCurrencyBasisQuote:
    """An xccy basis quote. The value is *not* a strip input (§5.4) — only its
    maturity + sign matter — but a finite value is needed to clear the grid."""
    return CrossCurrencyBasisQuote(
        pair=pair(),
        tenor_code=code or f"USDTRY-XCCY-{(maturity - SPOT_DATE).days}D",
        maturity_date=maturity,
        spread_bps=bps,
        quoted_on_foreign=quoted_on_foreign,
        source="test",
    )


def market(*basis_quotes: CrossCurrencyBasisQuote, spot: float = SPOT_RATE) -> FXMarketData:
    return FXMarketData(
        spot=spot_quote(spot),
        basis_quotes=tuple(basis_quotes),
        valuation_date=VAL_DATE,
    )
