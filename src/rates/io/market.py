"""rates.io.market — CSV market-data provider (M-010).

Defines the :class:`MarketDataProvider` ``Protocol`` and one concrete impl,
:class:`CsvProvider`, that reads ``data/snapshots/snapshot_YYYYMMDD.csv``.

Per F-007 / G14 the reader is forgiving:

* Required columns: ``tenor_code, tenor_days, start_date, end_date, bid, ask,
  mid, source``. A missing required column emits ``IO_SCHEMA_MISSING_COL``
  ERROR and raises :class:`CsvSchemaError`.
* Extra columns are silently ignored.
* An optional first-line ``# schema: v1`` comment is skipped (decorative).
* Zero data rows ⇒ ``IO_EMPTY_SNAPSHOT`` ERROR + :class:`EmptyDataError`.
* Locale variants (comma decimal, DMY dates) are rejected by Pydantic v2 with
  ``IO_ROW_PARSE_FAIL`` ERROR + :class:`CsvSchemaError`.

Special-row routing: any row whose ``tenor_code`` is in
:data:`SPECIAL_TICKERS` (currently ``{"BISTTREF"}``) is routed to
``MarketData.special_rates`` rather than ``quotes``. A missing ``BISTTREF``
row is **not** an error at this layer — the scenario module (M-008) raises
``SC_BISTTREF_MISSING`` at use time. The orchestrator can also short-circuit
via diagnostics state.

V2 ``BloombergProvider`` will implement the same Protocol, so the orchestrator
code is provider-agnostic.

Contract: C-001.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import MarketData, MarketQuote

#: Columns the snapshot CSV must contain (forgiving on order, strict on presence).
REQUIRED_COLS: tuple[str, ...] = (
    "tenor_code",
    "tenor_days",
    "start_date",
    "end_date",
    "bid",
    "ask",
    "mid",
    "source",
)

#: tenor_code values routed to ``MarketData.special_rates`` instead of ``quotes``.
SPECIAL_TICKERS: frozenset[str] = frozenset({"BISTTREF"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CsvSchemaError(ValueError):
    """Required column missing or row failed Pydantic validation."""


class EmptyDataError(ValueError):
    """CSV parsed successfully but contained zero data rows."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MarketDataProvider(Protocol):
    """Adapter interface for market-data sources.

    Concrete impls today: :class:`CsvProvider`. V2 adds a ``BloombergProvider``
    implementing the same signature.
    """

    def load(self, valuation_date: date | None = None) -> MarketData:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Pydantic boundary model
# ---------------------------------------------------------------------------


class _RawRow(BaseModel):
    """Boundary-validated CSV row. Extra cols ignored; ISO date/period decimals
    coerced from string, locale variants (comma decimal, DMY) rejected by
    Pydantic's default lax-but-format-strict mode."""

    model_config = ConfigDict(extra="ignore")

    tenor_code: str
    tenor_days: int
    start_date: date
    end_date: date
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# CSV provider
# ---------------------------------------------------------------------------


class CsvProvider:
    """Read canonical ``snapshot_YYYYMMDD.csv`` into a :class:`MarketData`.

    Args:
        path:         Path to the snapshot CSV.
        diagnostics:  Single mutable collector threaded by the orchestrator.
                      Errors are recorded **before** the corresponding exception
                      is raised so the caller can inspect state via ``has_errors()``.
    """

    def __init__(self, path: Path | str, diagnostics: DiagnosticsCollector) -> None:
        self.path = Path(path)
        self.diagnostics = diagnostics

    def load(self, valuation_date: date | None = None) -> MarketData:
        """Read the CSV and produce a frozen :class:`MarketData`.

        Args:
            valuation_date: Override; when None, inferred from the earliest
                            ``start_date`` across quote rows.

        Returns:
            Frozen :class:`MarketData` instance.

        Raises:
            FileNotFoundError:  ``self.path`` does not exist.
            CsvSchemaError:     Required column missing or row fails validation.
            EmptyDataError:     CSV has zero data rows.
        """
        if not self.path.exists():
            self.diagnostics.error(
                "IO_FILE_NOT_FOUND",
                f"snapshot CSV not found: {self.path}",
                {"path": str(self.path)},
            )
            raise FileNotFoundError(f"snapshot CSV not found: {self.path}")

        with self.path.open(encoding="utf-8") as f:
            non_comment = [line for line in f if not line.lstrip().startswith("#")]

        reader = csv.DictReader(non_comment)
        headers = tuple(reader.fieldnames or ())
        missing = [c for c in REQUIRED_COLS if c not in headers]
        if missing:
            msg = f"required column(s) missing from {self.path.name}: {sorted(missing)}"
            self.diagnostics.error(
                "IO_SCHEMA_MISSING_COL",
                msg,
                {"missing": missing, "headers": list(headers)},
            )
            raise CsvSchemaError(msg)

        quotes: list[MarketQuote] = []
        special: dict[str, float] = {}
        row_count = 0

        for line_no, raw in enumerate(reader, start=2):  # row 1 is the header
            row_count += 1
            try:
                r = _RawRow.model_validate(self._strip_blanks(raw))
            except ValidationError as e:
                self.diagnostics.error(
                    "IO_ROW_PARSE_FAIL",
                    f"{self.path.name} line {line_no}: {self._first_error(e)}",
                    {"line_no": line_no, "row": raw},
                )
                raise CsvSchemaError(f"{self.path.name} line {line_no}: invalid row data") from e

            if r.tenor_code in SPECIAL_TICKERS:
                rate = self._first_present(r.mid, r.ask, r.bid)
                if rate is None:
                    self.diagnostics.warn(
                        "IO_SPECIAL_RATE_MISSING_VALUE",
                        (
                            f"{self.path.name}: special row {r.tenor_code} has "
                            "no usable bid/ask/mid; skipped"
                        ),
                        {"tenor_code": r.tenor_code, "line_no": line_no},
                    )
                    continue
                special[r.tenor_code] = rate
                continue

            quotes.append(
                MarketQuote(
                    tenor_code=r.tenor_code,
                    tenor_days=r.tenor_days,
                    start_date=r.start_date,
                    end_date=r.end_date,
                    bid=r.bid,
                    ask=r.ask,
                    mid=r.mid,
                    source=r.source,
                )
            )

        if row_count == 0:
            msg = f"{self.path.name} contains zero data rows"
            self.diagnostics.error(
                "IO_EMPTY_SNAPSHOT",
                msg,
                {"path": str(self.path)},
            )
            raise EmptyDataError(msg)

        val = valuation_date
        if val is None and quotes:
            val = min(q.start_date for q in quotes)

        return MarketData(
            quotes=tuple(quotes),
            special_rates=special,
            valuation_date=val,
            source=f"csv:{self.path.name}",
        )

    @staticmethod
    def _strip_blanks(row: dict[str, str | None]) -> dict[str, str | None]:
        """Normalise blank strings to None so Pydantic treats them as missing."""
        return {k: (v if v not in ("", None) else None) for k, v in row.items()}

    @staticmethod
    def _first_present(*values: float | None) -> float | None:
        for v in values:
            if v is not None:
                return v
        return None

    @staticmethod
    def _first_error(e: ValidationError) -> str:
        errs = e.errors()
        if not errs:
            return str(e)
        first = errs[0]
        loc = ".".join(str(x) for x in first.get("loc", ()))
        return f"{loc}: {first.get('msg', 'invalid value')}"


__all__ = [
    "REQUIRED_COLS",
    "SPECIAL_TICKERS",
    "CsvProvider",
    "CsvSchemaError",
    "EmptyDataError",
    "MarketDataProvider",
]
