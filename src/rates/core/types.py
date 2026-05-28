"""rates.core.types — shared input domain types (M-016).

Frozen dataclasses for inputs flowing into the pipeline:

    MarketQuote    — single (tenor, bid, ask, mid, source) row from a snapshot.
    MarketData     — collection of quotes plus special_rates dict (e.g. BISTTREF) and
                     metadata. Produced by any MarketDataProvider (CsvProvider in V1,
                     BloombergProvider in V2).
    MPCMeeting     — single CBRT MPC meeting (date + bps change).
    MPCPath        — ordered collection of MPCMeetings.

Shared enums also live here so that core.daycount, core.conventions, core.calendar can
agree on a single canonical type without circular imports.

This module has NO runtime dependencies (stdlib only) and NO IO. Validation happens at
the io.market / io.mpc boundary; once a value reaches this module's dataclasses it is
trusted.

Contracts: feeds C-001, C-002, C-003, C-007.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

# ---------------------------------------------------------------------------
# Enums (shared)
# ---------------------------------------------------------------------------


class DayCount(StrEnum):
    """Day-count basis. Used by Conventions, daycount.year_fraction, and curve APIs."""

    ACT_360 = "Act/360"
    ACT_365 = "Act/365"
    THIRTY_360 = "30/360"


class BusinessDayConvention(StrEnum):
    """How to roll a payment date that lands on a non-business day."""

    FOLLOWING = "following"
    MODIFIED_FOLLOWING = "modified_following"
    PRECEDING = "preceding"
    MODIFIED_PRECEDING = "modified_preceding"
    NONE = "none"


# ---------------------------------------------------------------------------
# Market snapshot domain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketQuote:
    """A single market quote row from a snapshot.

    Attributes:
        tenor_code:  Provider tenor identifier (e.g. "TYSO1Y", "BISTTREF").
        tenor_days:  Calendar days from valuation_date to end_date.
        start_date:  Effective start of the underlying instrument.
        end_date:    Effective end (maturity) of the underlying instrument.
        bid:         Bid quote, if present.
        ask:         Ask quote, if present.
        mid:         Mid quote, if present.
        source:      Free-text source tag (e.g. "TYSO", "BIST").
    """

    tenor_code: str
    tenor_days: int
    start_date: date
    end_date: date
    bid: float | None
    ask: float | None
    mid: float | None
    source: str


@dataclass(frozen=True, slots=True)
class MarketData:
    """Full market snapshot consumed by core.bootstrap and core.scenario.

    Attributes:
        quotes:         OIS / curve-input quotes.
        special_rates:  Non-pillar rates keyed by ticker (e.g. {"BISTTREF": 0.4275}).
                        core.scenario reads "BISTTREF" as the initial TLREF level.
        valuation_date: As-of date the snapshot pertains to.
        source:         Provider tag (e.g. "csv:snapshot_20260417.csv", "bloomberg:live").
    """

    quotes: tuple[MarketQuote, ...]
    special_rates: dict[str, float] = field(default_factory=dict)
    valuation_date: date | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# MPC policy path domain
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MPCMeeting:
    """A single CBRT MPC meeting on the user's expected policy path.

    Attributes:
        meeting_date: Effective date of the rate change.
        bps_change:   Signed integer change in basis points (e.g. -250, 0, 100).
        rationale:    Optional free-text note (analyst commentary).
    """

    meeting_date: date
    bps_change: int
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class MPCPath:
    """Ordered collection of MPCMeeting entries.

    Invariant: meetings are sorted ascending by meeting_date and contain no duplicates.
    The io.mpc reader is responsible for enforcing this on construction.
    """

    meetings: tuple[MPCMeeting, ...]
