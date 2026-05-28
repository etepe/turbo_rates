"""rates.fx.types — frozen FX domain dataclasses (M-101, V2 scaffold).

Pure stdlib. Mirrors :mod:`rates.core.types` for the FX layer. Validation
lives at the IO boundary (V2 ``rates.io.fx_market``); once a value reaches
this module it is trusted.

Diagnostic code constants live here so any module that emits them gets a
single canonical reference. See ``docs/fx-architecture.md`` §6.

Contract: feeds C-101, C-102, C-104, C-106..C-108.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

# ---------------------------------------------------------------------------
# Diagnostic codes (FX_*) — see docs/fx-architecture.md §6.
# ---------------------------------------------------------------------------

FX_SPOT_MISSING: str = "FX_SPOT_MISSING"
FX_FOR_CCY_CURVE_MISSING: str = "FX_FOR_CCY_CURVE_MISSING"
FX_FWD_POINTS_NON_MONOTONE: str = "FX_FWD_POINTS_NON_MONOTONE"
FX_PARITY_MISMATCH: str = "FX_PARITY_MISMATCH"
FX_BASIS_INVERTED: str = "FX_BASIS_INVERTED"
FX_XCCY_QUOTE_SKIPPED: str = "FX_XCCY_QUOTE_SKIPPED"
FX_BOOTSTRAP_NON_CONVERGENT: str = "FX_BOOTSTRAP_NON_CONVERGENT"
FX_XCCY_REPRICE_FAIL: str = "FX_XCCY_REPRICE_FAIL"
FX_XCCY_FORWARD_COVERAGE: str = "FX_XCCY_FORWARD_COVERAGE"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class QuoteConvention(StrEnum):
    """How an FX pair is quoted.

    ``DIRECT`` — units of domestic currency per one unit of foreign currency
    (e.g. USDTRY direct = TRY per USD). ``INDIRECT`` — the inverse.
    """

    DIRECT = "direct"
    INDIRECT = "indirect"


# ---------------------------------------------------------------------------
# CurrencyPair
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CurrencyPair:
    """An ordered FX pair.

    Attributes:
        domestic:  ISO 4217 code of the "term" currency (e.g. ``"TRY"``).
        foreign:   ISO 4217 code of the "base" currency (e.g. ``"USD"``).
        code:      Canonical concatenated code, e.g. ``"USDTRY"`` (foreign then
                   domestic, per street convention).
    """

    domestic: str
    foreign: str
    code: str


# ---------------------------------------------------------------------------
# Market quote rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FXSpotQuote:
    """A single FX spot quote.

    Attributes:
        pair:        Currency pair.
        spot_date:   Settlement date implied by the pair's spot lag and joint
                     business-day calendar.
        bid:         Bid quote (term per base), if present.
        ask:         Ask quote, if present.
        mid:         Mid quote, if present.
        source:      Free-text source tag.
    """

    pair: CurrencyPair
    spot_date: date
    bid: float | None
    ask: float | None
    mid: float | None
    source: str


@dataclass(frozen=True, slots=True)
class FXForwardPointQuote:
    """A single forward-point quote for a tenor.

    Forward points are quoted in pips: ``F = S + points / forward_point_scale``
    under the pair's quote convention. ``forward_point_scale`` is read from
    the FX conventions block (typically 10000).

    Attributes:
        pair:        Currency pair.
        tenor_code:  Provider tenor identifier (e.g. ``"USDTRY1M"``).
        tenor_days:  Calendar days from spot to settlement.
        settle_date: Forward settlement date.
        bid:         Bid quote (pips).
        ask:         Ask quote (pips).
        mid:         Mid quote (pips).
        source:      Free-text source tag.
    """

    pair: CurrencyPair
    tenor_code: str
    tenor_days: int
    settle_date: date
    bid: float | None
    ask: float | None
    mid: float | None
    source: str


@dataclass(frozen=True, slots=True)
class FXSwapQuote:
    """A par FX swap quote (simultaneous near + far legs at common notional).

    The quote represents the par price differential between the two legs in
    forward points; the near leg is typically spot.

    Attributes:
        pair:            Currency pair.
        tenor_code:      Provider tenor identifier.
        near_date:       Near leg settlement date.
        far_date:        Far leg settlement date.
        swap_points_mid: Far-minus-near forward points at par (mid).
        source:          Free-text source tag.
    """

    pair: CurrencyPair
    tenor_code: str
    near_date: date
    far_date: date
    swap_points_mid: float
    source: str


@dataclass(frozen=True, slots=True)
class CrossCurrencyBasisQuote:
    """A par cross-currency basis swap quote.

    Quoted as a spread (bps) added to one leg of a notional-aligned xccy swap.
    The quoted leg is determined by ``quoted_on_foreign`` per the pair's market
    convention (USD-leg in USDTRY xccy, etc.); see FX-O5 in
    ``docs/fx-architecture.md`` §9.

    Attributes:
        pair:               Currency pair.
        tenor_code:         Provider tenor identifier.
        maturity_date:      Maturity (final settlement).
        spread_bps:         Quoted basis spread in basis points.
        quoted_on_foreign:  True iff the spread is added to the foreign-currency
                            leg (USD-leg convention).
        source:             Free-text source tag.
    """

    pair: CurrencyPair
    tenor_code: str
    maturity_date: date
    spread_bps: float
    quoted_on_foreign: bool
    source: str


# ---------------------------------------------------------------------------
# Aggregate market snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FXMarketData:
    """FX snapshot consumed by the FX bootstrap and pricer.

    Attributes:
        spot:            Spot quote for the pair.
        forward_points:  Forward-point quotes ordered ascending by settle_date.
        swap_quotes:     Par FX swap quotes.
        basis_quotes:    Cross-currency par-swap basis quotes.
        valuation_date:  As-of date of the snapshot.
        source:          Provider tag.
    """

    spot: FXSpotQuote
    forward_points: tuple[FXForwardPointQuote, ...] = ()
    swap_quotes: tuple[FXSwapQuote, ...] = ()
    basis_quotes: tuple[CrossCurrencyBasisQuote, ...] = ()
    valuation_date: date | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# Pricing output
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FXSwapPricing:
    """Output of :func:`rates.fx.pricer.price_fx_swap`.

    Attributes:
        near_rate:    Fair FX rate at the near leg.
        far_rate:     Fair FX rate at the far leg.
        swap_points:  Fair far-minus-near forward points (under pair scale).
        context:      Free-form provenance dict for diagnostics.
    """

    near_rate: float
    far_rate: float
    swap_points: float
    context: dict[str, float] = field(default_factory=dict)


__all__ = [
    "FX_BASIS_INVERTED",
    "FX_BOOTSTRAP_NON_CONVERGENT",
    "FX_FOR_CCY_CURVE_MISSING",
    "FX_FWD_POINTS_NON_MONOTONE",
    "FX_PARITY_MISMATCH",
    "FX_SPOT_MISSING",
    "FX_XCCY_FORWARD_COVERAGE",
    "FX_XCCY_QUOTE_SKIPPED",
    "FX_XCCY_REPRICE_FAIL",
    "CrossCurrencyBasisQuote",
    "CurrencyPair",
    "FXForwardPointQuote",
    "FXMarketData",
    "FXSpotQuote",
    "FXSwapPricing",
    "FXSwapQuote",
    "QuoteConvention",
]
