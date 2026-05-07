"""rates.core.conventions — market convention loader (M-001).

Loads ``config/conventions.yaml`` into a frozen ``Conventions`` object that is passed
explicitly through the pipeline (no global state, no module-level singleton). The single
file is the authoritative source for: per-currency OIS day-count, payment frequency and
"bullet up to N years" boundary, business-day convention, calendar code; plus separate
TLREF and spread-reporting conventions.

Round-trip invariant (per F-011 acceptance): rate -> DF -> rate reproduces input to
1e-12 absolute under the declared day-count.

Open question O1 (resolved 2026-05-07): ``business_day_convention`` is a per-currency
field. Default for TRY is ``modified_following``.

Contracts: C-008.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rates.core.types import BusinessDayConvention, DayCount


# ---------------------------------------------------------------------------
# Component conventions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PaymentConvention:
    """OIS payment schedule for a currency.

    Attributes:
        default_frequency: ``"annual"`` for TRY in V1. May extend to semi-annual /
                           quarterly when other currencies are added.
        bullet_until:      ISO-8601 period string (``"1Y"``) below which the swap
                           degenerates to a single bullet payment at maturity.
    """

    default_frequency: str
    bullet_until: str


@dataclass(frozen=True, slots=True)
class CurrencyConvention:
    """OIS conventions for a single currency."""

    day_count: DayCount
    business_day_convention: BusinessDayConvention
    calendar: str  # e.g. "TR", "US", "EU" — keyed by HolidayCalendar.for_currency.
    payment: PaymentConvention


@dataclass(frozen=True, slots=True)
class TLREFConvention:
    """TLREF accrual basis (separate from OIS day-count)."""

    day_count: DayCount


@dataclass(frozen=True, slots=True)
class SpreadReportingConvention:
    """Common basis used to express market vs scenario spread (per F-012)."""

    day_count: DayCount


# ---------------------------------------------------------------------------
# Top-level container
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Conventions:
    """Complete set of market conventions loaded from conventions.yaml."""

    ois_conventions: dict[str, CurrencyConvention]
    tlref_convention: TLREFConvention
    spread_reporting: SpreadReportingConvention

    @classmethod
    def load(
        cls,
        yaml_path: Path,
        overrides: dict[str, Any] | None = None,
    ) -> Conventions:
        """Load conventions from YAML and merge any CLI overrides.

        Args:
            yaml_path: Path to ``config/conventions.yaml``.
            overrides: Optional dotted-key overrides such as
                       ``{"ois.TRY.day_count": "Act/365"}``. Each key navigates the YAML
                       tree; the leaf is replaced.

        Returns:
            Frozen ``Conventions`` instance.

        Raises:
            FileNotFoundError: When ``yaml_path`` does not exist.
            ValueError:        When required keys are missing or override path is invalid.
        """
        raise NotImplementedError(
            "M-001: yaml.safe_load -> apply overrides via dotted keys -> validate -> "
            "construct Conventions. Reject unknown enum values clearly."
        )
