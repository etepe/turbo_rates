"""rates.fx.conventions — per-pair FX conventions (M-102, V2 scaffold).

Reads the ``fx_conventions`` block from ``config/conventions.yaml``.
:class:`rates.core.conventions.Conventions` does not consume this block; it
is parsed here so the FX layer stays out of ``rates.core``.

Contract: feeds C-102, C-104, C-106..C-108.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rates.fx.types import QuoteConvention


@dataclass(frozen=True, slots=True)
class FXConvention:
    """Per-pair FX market convention.

    Attributes:
        pair_code:            Canonical concatenated code (e.g. ``"USDTRY"``).
        quote_convention:     Direct vs indirect quotation.
        spot_lag_days:        Business days from valuation to spot settlement.
        settlement_calendars: Calendars that must all admit the spot date.
        forward_point_scale:  Quoted pip multiplier (typically 10000).
    """

    pair_code: str
    quote_convention: QuoteConvention
    spot_lag_days: int
    settlement_calendars: tuple[str, ...]
    forward_point_scale: int


@dataclass(frozen=True, slots=True)
class FXConventions:
    """Top-level container for all per-pair FX conventions.

    Attributes:
        by_pair: Mapping of pair_code (e.g. ``"USDTRY"``) to :class:`FXConvention`.
    """

    by_pair: dict[str, FXConvention]

    @classmethod
    def load(cls, yaml_path: Path) -> FXConventions:
        """Parse the ``fx_conventions`` block from a conventions YAML file.

        Args:
            yaml_path: Path to ``config/conventions.yaml``.

        Returns:
            Frozen :class:`FXConventions` instance. Empty ``by_pair`` is
            permitted if the YAML omits ``fx_conventions`` entirely.

        Raises:
            FileNotFoundError: When ``yaml_path`` does not exist.
            ValueError:        When the ``fx_conventions`` block is malformed.
        """
        if not yaml_path.exists():
            raise FileNotFoundError(f"FX conventions YAML not found: {yaml_path}")

        with yaml_path.open(encoding="utf-8") as f:
            raw: Any = yaml.safe_load(f)

        if not isinstance(raw, dict):
            raise ValueError(
                f"conventions YAML must be a mapping at top level "
                f"(got {type(raw).__name__})"
            )

        fx_block = raw.get("fx_conventions", {})
        if not isinstance(fx_block, dict):
            raise ValueError("fx_conventions must be a mapping when present")

        by_pair: dict[str, FXConvention] = {}
        for pair_code, body in fx_block.items():
            if not isinstance(body, dict):
                raise ValueError(f"fx_conventions.{pair_code} must be a mapping")
            by_pair[str(pair_code)] = _parse_pair(str(pair_code), body)

        return cls(by_pair=by_pair)


def _parse_pair(pair_code: str, body: dict[str, Any]) -> FXConvention:
    """Build a single :class:`FXConvention` from a parsed YAML mapping."""
    quote_raw = body.get("quote_convention")
    if not isinstance(quote_raw, str):
        raise ValueError(f"fx_conventions.{pair_code}.quote_convention must be a string")
    try:
        quote = QuoteConvention(quote_raw)
    except ValueError as e:
        valid = [m.value for m in QuoteConvention]
        raise ValueError(
            f"fx_conventions.{pair_code}.quote_convention {quote_raw!r} not in {valid}"
        ) from e

    spot_lag = body.get("spot_lag_days")
    if not isinstance(spot_lag, int) or spot_lag < 0:
        raise ValueError(
            f"fx_conventions.{pair_code}.spot_lag_days must be a non-negative integer"
        )

    cals_raw = body.get("settlement_calendars", [])
    if not isinstance(cals_raw, list) or not all(isinstance(c, str) for c in cals_raw):
        raise ValueError(
            f"fx_conventions.{pair_code}.settlement_calendars must be a list of strings"
        )

    scale = body.get("forward_point_scale", 10000)
    if not isinstance(scale, int) or scale <= 0:
        raise ValueError(
            f"fx_conventions.{pair_code}.forward_point_scale must be a positive integer"
        )

    return FXConvention(
        pair_code=pair_code,
        quote_convention=quote,
        spot_lag_days=spot_lag,
        settlement_calendars=tuple(cals_raw),
        forward_point_scale=scale,
    )


__all__ = ["FXConvention", "FXConventions"]
