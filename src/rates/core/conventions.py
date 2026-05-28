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

import yaml

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


# YAML root-key aliases accepted on the left side of dotted overrides.
# CLI users can write ``ois.TRY.day_count`` instead of the verbose YAML key.
_OVERRIDE_KEY_ALIASES: dict[str, str] = {
    "ois": "ois_conventions",
    "tlref": "tlref_convention",
    "spread": "spread_reporting",
}


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
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"conventions YAML not found: {path}")

        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict):
            raise ValueError(
                f"conventions YAML must be a mapping at top level (got {type(raw).__name__})"
            )

        if overrides:
            _apply_overrides(raw, overrides)

        return _build_from_mapping(raw)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _apply_overrides(data: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Mutate ``data`` in place, applying each dotted-key override."""
    for dotted_key, value in overrides.items():
        parts = dotted_key.split(".")
        if not parts or any(p == "" for p in parts):
            raise ValueError(f"invalid override key: {dotted_key!r}")

        head = _OVERRIDE_KEY_ALIASES.get(parts[0], parts[0])
        keys = [head, *parts[1:]]

        cursor: Any = data
        for k in keys[:-1]:
            if not isinstance(cursor, dict) or k not in cursor:
                raise ValueError(f"override path invalid at {k!r} in {dotted_key!r}")
            cursor = cursor[k]
        if not isinstance(cursor, dict):
            raise ValueError(f"override path invalid at leaf in {dotted_key!r}")
        cursor[keys[-1]] = value


def _build_from_mapping(data: dict[str, Any]) -> Conventions:
    """Convert a parsed YAML mapping into a frozen ``Conventions`` object."""
    ois_raw = _require_mapping(data, "ois_conventions")
    tlref_raw = _require_mapping(data, "tlref_convention")
    spread_raw = _require_mapping(data, "spread_reporting")

    ois: dict[str, CurrencyConvention] = {}
    for ccy, body in ois_raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"ois_conventions.{ccy} must be a mapping")
        payment_raw = _require_mapping(body, "payment", parent=f"ois_conventions.{ccy}")
        ois[ccy] = CurrencyConvention(
            day_count=_parse_day_count(
                body.get("day_count"), where=f"ois_conventions.{ccy}.day_count"
            ),
            business_day_convention=_parse_bdc(
                body.get("business_day_convention"),
                where=f"ois_conventions.{ccy}.business_day_convention",
            ),
            calendar=_require_str(body, "calendar", parent=f"ois_conventions.{ccy}"),
            payment=PaymentConvention(
                default_frequency=_require_str(
                    payment_raw, "default_frequency", parent=f"ois_conventions.{ccy}.payment"
                ),
                bullet_until=_require_str(
                    payment_raw, "bullet_until", parent=f"ois_conventions.{ccy}.payment"
                ),
            ),
        )

    return Conventions(
        ois_conventions=ois,
        tlref_convention=TLREFConvention(
            day_count=_parse_day_count(
                tlref_raw.get("day_count"), where="tlref_convention.day_count"
            )
        ),
        spread_reporting=SpreadReportingConvention(
            day_count=_parse_day_count(
                spread_raw.get("day_count"), where="spread_reporting.day_count"
            )
        ),
    )


def _require_mapping(data: dict[str, Any], key: str, *, parent: str = "") -> dict[str, Any]:
    if key not in data:
        loc = f"{parent}.{key}" if parent else key
        raise ValueError(f"missing required key: {loc}")
    value = data[key]
    if not isinstance(value, dict):
        loc = f"{parent}.{key}" if parent else key
        raise ValueError(f"{loc} must be a mapping")
    return value


def _require_str(data: dict[str, Any], key: str, *, parent: str) -> str:
    if key not in data:
        raise ValueError(f"missing required key: {parent}.{key}")
    value = data[key]
    if not isinstance(value, str):
        raise ValueError(f"{parent}.{key} must be a string")
    return value


def _parse_day_count(raw: Any, *, where: str) -> DayCount:
    if not isinstance(raw, str):
        raise ValueError(f"{where} must be a string day-count value")
    try:
        return DayCount(raw)
    except ValueError as e:
        valid = [m.value for m in DayCount]
        raise ValueError(f"{where}: unknown day_count {raw!r}; expected one of {valid}") from e


def _parse_bdc(raw: Any, *, where: str) -> BusinessDayConvention:
    if not isinstance(raw, str):
        raise ValueError(f"{where} must be a string business_day_convention value")
    try:
        return BusinessDayConvention(raw)
    except ValueError as e:
        valid = [m.value for m in BusinessDayConvention]
        raise ValueError(
            f"{where}: unknown business_day_convention {raw!r}; expected one of {valid}"
        ) from e
