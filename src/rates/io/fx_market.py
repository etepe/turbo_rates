"""rates.io.fx_market — FX snapshot CSV provider (M-108, v0.3.0).

Defines the :class:`FXMarketDataProvider` ``Protocol`` and the concrete
:class:`FxCsvProvider` that reads
``data/fx_snapshots/fx_snapshot_<PAIR>_YYYYMMDD.csv``.

The CSV is a single file multiplexing three row types via an
``instrument_type`` discriminator: ``SPOT``, ``FORWARD_POINT``, ``XCCY_BASIS``.
Row-level parsing uses a Pydantic v2 discriminated union; file-level
invariants (exactly-one-SPOT, duplicate dedup, pair-not-found ordering) run
after the streaming row pass.

Per ``docs/fx-io-architecture.md`` §3 M-108 / §4 C-101 / §5:

* Required columns: ``pair, instrument_type, tenor_code, tenor_days,
  bid, ask, mid, source``. A missing required column emits
  ``FX_IO_SCHEMA_MISSING_COL`` ERROR and raises :class:`FXCsvSchemaError`.
* Extra columns are silently ignored.
* An optional first-line ``# schema: fx-v1`` comment line is skipped.
* Zero data rows ⇒ ``FX_IO_EMPTY_SNAPSHOT`` ERROR + :class:`FXEmptyDataError`.
* Unknown ``instrument_type`` ⇒ ``FX_IO_UNKNOWN_INSTRUMENT`` ERROR +
  :class:`FXCsvSchemaError`.
* Locale variants (comma decimal, DMY dates) are rejected by Pydantic with
  ``FX_IO_ROW_PARSE_FAIL`` ERROR + :class:`FXCsvSchemaError`.

Pair filtering and cross-row invariants:

* Rows whose ``pair`` ≠ requested pair are skipped with
  ``FX_IO_PAIR_MISMATCH`` WARN (allows a multi-pair master CSV in dev/test).
* After pair filtering, zero rows remain ⇒ ``FX_IO_PAIR_NOT_FOUND`` ERROR
  (checked BEFORE NO_SPOT so a typo in ``pair`` gets the specific error).
* Exactly one ``SPOT`` row required per pair: zero ⇒ ``FX_IO_NO_SPOT``;
  two or more ⇒ ``FX_IO_MULTIPLE_SPOT`` (no silent first-wins).
* Duplicate ``(instrument_type, tenor_code)`` ⇒ ``FX_IO_DUPLICATE_TENOR``
  WARN; keep-first (mirrors M-106 XCCY dedup behavior).

Date computation: the CSV carries only ``tenor_days``; the reader computes
``spot_date = valuation_date + spot_lag_days`` walked over the **joint**
business-day calendar (intersection of all calendars passed by the caller),
then ``settle_date = spot_date + tenor_days`` (calendar arithmetic) for
forward-point and xccy-basis rows.

``CrossCurrencyBasisQuote.quoted_on_foreign`` is hardcoded ``True`` for
v0.3.0: every supported pair (USDTRY, EURTRY) quotes basis on the foreign
leg by market convention. If a future pair flips this, expose
``quoted_on_foreign`` as a field on :class:`rates.fx.conventions.FXConvention`
(deferred per architecture doc D9 follow-up).

V2 ``BloombergFxProvider`` will implement the same Protocol so the
orchestrator code is provider-agnostic.

Contract: C-101.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from rates.core.calendar import HolidayCalendar
from rates.core.diagnostics import DiagnosticsCollector
from rates.fx.conventions import FXConvention
from rates.fx.types import (
    CrossCurrencyBasisQuote,
    CurrencyPair,
    FXForwardPointQuote,
    FXMarketData,
    FXSpotQuote,
)

#: Columns the FX snapshot CSV must contain (forgiving on order, strict on presence).
REQUIRED_COLS: tuple[str, ...] = (
    "pair",
    "instrument_type",
    "tenor_code",
    "tenor_days",
    "bid",
    "ask",
    "mid",
    "source",
)

_VALID_INSTRUMENT_TYPES: frozenset[str] = frozenset({"SPOT", "FORWARD_POINT", "XCCY_BASIS"})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FXCsvSchemaError(ValueError):
    """Required column missing, row failed Pydantic validation, or unknown instrument."""


class FXEmptyDataError(ValueError):
    """CSV parsed successfully but contained zero data rows."""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class FXMarketDataProvider(Protocol):
    """Adapter interface for FX market-data sources.

    Concrete impl today: :class:`FxCsvProvider`. V2 adds ``BloombergFxProvider``
    implementing the same signature.
    """

    def load(self, valuation_date: date) -> FXMarketData:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Pydantic discriminated-union row models
# ---------------------------------------------------------------------------


class _FXSpotRow(BaseModel):
    """Row-level Pydantic model for SPOT quotes."""

    model_config = ConfigDict(extra="ignore")

    instrument_type: Literal["SPOT"]
    pair: str
    tenor_code: str
    tenor_days: int
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    source: str = ""


class _FXForwardPointRow(BaseModel):
    """Row-level Pydantic model for FORWARD_POINT quotes."""

    model_config = ConfigDict(extra="ignore")

    instrument_type: Literal["FORWARD_POINT"]
    pair: str
    tenor_code: str
    tenor_days: int
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    source: str = ""


class _FXBasisRow(BaseModel):
    """Row-level Pydantic model for XCCY_BASIS quotes."""

    model_config = ConfigDict(extra="ignore")

    instrument_type: Literal["XCCY_BASIS"]
    pair: str
    tenor_code: str
    tenor_days: int
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    source: str = ""


_FXRow = Annotated[
    _FXSpotRow | _FXForwardPointRow | _FXBasisRow,
    Field(discriminator="instrument_type"),
]

_FX_ROW_ADAPTER: TypeAdapter[_FXSpotRow | _FXForwardPointRow | _FXBasisRow] = TypeAdapter(_FXRow)

#: Generic bound for the tenor-keyed row types that dedup operates on.
_TenoredRowT = TypeVar("_TenoredRowT", _FXForwardPointRow, _FXBasisRow)


# ---------------------------------------------------------------------------
# Joint calendar helper
# ---------------------------------------------------------------------------


def _is_joint_business_day(d: date, calendars: tuple[HolidayCalendar, ...]) -> bool:
    """True iff ``d`` is a business day in every calendar."""
    return all(cal.is_business_day(d) for cal in calendars)


def _add_joint_business_days(
    start: date, n: int, calendars: tuple[HolidayCalendar, ...]
) -> date:
    """Walk ``n`` business days from ``start`` over the joint calendar."""
    if n == 0:
        # If start itself is non-good-BD, walk forward to the next good BD (forward roll).
        cur = start
        while not _is_joint_business_day(cur, calendars):
            cur = cur + timedelta(days=1)
        return cur
    step = timedelta(days=1 if n > 0 else -1)
    remaining = abs(n)
    cur = start
    while remaining > 0:
        cur = cur + step
        if _is_joint_business_day(cur, calendars):
            remaining -= 1
    return cur


# ---------------------------------------------------------------------------
# CSV provider
# ---------------------------------------------------------------------------


class FxCsvProvider:
    """Read canonical ``fx_snapshot_<PAIR>_YYYYMMDD.csv`` into an :class:`FXMarketData`.

    Args:
        path:         Path to the FX snapshot CSV.
        pair:         The currency pair the caller wants to load. Rows for
                      other pairs are skipped with ``FX_IO_PAIR_MISMATCH`` WARN.
        conv:         FX convention for ``pair`` (spot lag + forward-point scale).
        calendars:    Tuple of holiday calendars whose intersection defines
                      good-business-day status for the spot-date walk.
                      Typically resolved by the caller from
                      ``conv.settlement_calendars``.
        diagnostics:  Single mutable collector threaded by the orchestrator.
                      Errors are recorded **before** the corresponding exception
                      is raised so the caller can inspect state.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        pair: CurrencyPair,
        conv: FXConvention,
        calendars: tuple[HolidayCalendar, ...],
        diagnostics: DiagnosticsCollector,
    ) -> None:
        self.path = Path(path)
        self.pair = pair
        self.conv = conv
        self.calendars = calendars
        self.diagnostics = diagnostics

    def load(self, valuation_date: date) -> FXMarketData:
        """Read the CSV and produce a frozen :class:`FXMarketData`.

        Args:
            valuation_date: As-of date for the snapshot. Spot date is derived as
                            ``valuation_date + spot_lag_days`` walked on the
                            joint calendar.

        Returns:
            Frozen :class:`FXMarketData` instance.

        Raises:
            FileNotFoundError:    ``self.path`` does not exist.
            FXCsvSchemaError:     Required column missing, unknown instrument
                                  type, or row fails validation.
            FXEmptyDataError:     CSV has zero data rows.
        """
        if not self.path.exists():
            self.diagnostics.error(
                "FX_IO_FILE_NOT_FOUND",
                f"FX snapshot CSV not found: {self.path}",
                {"path": str(self.path)},
            )
            raise FileNotFoundError(f"FX snapshot CSV not found: {self.path}")

        with self.path.open(encoding="utf-8") as f:
            non_comment = [line for line in f if not line.lstrip().startswith("#")]

        reader = csv.DictReader(non_comment)
        headers = tuple(reader.fieldnames or ())
        missing = [c for c in REQUIRED_COLS if c not in headers]
        if missing:
            msg = f"required column(s) missing from {self.path.name}: {sorted(missing)}"
            self.diagnostics.error(
                "FX_IO_SCHEMA_MISSING_COL",
                msg,
                {"missing": missing, "headers": list(headers)},
            )
            raise FXCsvSchemaError(msg)

        spot_rows: list[_FXSpotRow] = []
        fwd_rows: list[_FXForwardPointRow] = []
        basis_rows: list[_FXBasisRow] = []
        row_count = 0

        for line_no, raw in enumerate(reader, start=2):  # row 1 is the header
            row_count += 1
            cleaned = self._strip_blanks(raw)

            # Pre-check the discriminator so we can emit a specific code on unknown values.
            inst_type = cleaned.get("instrument_type") or ""
            if inst_type not in _VALID_INSTRUMENT_TYPES:
                msg = (
                    f"{self.path.name} line {line_no}: unknown instrument_type "
                    f"{inst_type!r}; expected one of {sorted(_VALID_INSTRUMENT_TYPES)}"
                )
                self.diagnostics.error(
                    "FX_IO_UNKNOWN_INSTRUMENT",
                    msg,
                    {"line_no": line_no, "instrument_type": inst_type},
                )
                raise FXCsvSchemaError(msg)

            try:
                parsed = _FX_ROW_ADAPTER.validate_python(cleaned)
            except ValidationError as e:
                self.diagnostics.error(
                    "FX_IO_ROW_PARSE_FAIL",
                    f"{self.path.name} line {line_no}: {self._first_error(e)}",
                    {"line_no": line_no, "row": raw},
                )
                raise FXCsvSchemaError(
                    f"{self.path.name} line {line_no}: invalid row data"
                ) from e

            # Pair filter: silently skip rows for other pairs (allows multi-pair files).
            if parsed.pair != self.pair.code:
                self.diagnostics.warn(
                    "FX_IO_PAIR_MISMATCH",
                    (
                        f"{self.path.name} line {line_no}: row pair "
                        f"{parsed.pair!r} ≠ requested {self.pair.code!r}; skipped"
                    ),
                    {"line_no": line_no, "row_pair": parsed.pair, "requested": self.pair.code},
                )
                continue

            # Route by row type.
            if isinstance(parsed, _FXSpotRow):
                spot_rows.append(parsed)
            elif isinstance(parsed, _FXForwardPointRow):
                fwd_rows.append(parsed)
            else:
                basis_rows.append(parsed)

        if row_count == 0:
            msg = f"{self.path.name} contains zero data rows"
            self.diagnostics.error(
                "FX_IO_EMPTY_SNAPSHOT",
                msg,
                {"path": str(self.path)},
            )
            raise FXEmptyDataError(msg)

        # Pair-not-found: file had rows, but none for the requested pair (after filter).
        if not spot_rows and not fwd_rows and not basis_rows:
            msg = (
                f"{self.path.name}: zero rows remain after filtering on "
                f"pair={self.pair.code!r}"
            )
            self.diagnostics.error(
                "FX_IO_PAIR_NOT_FOUND",
                msg,
                {"requested_pair": self.pair.code, "path": str(self.path)},
            )
            raise FXCsvSchemaError(msg)

        # SPOT-row arity.
        if len(spot_rows) == 0:
            msg = (
                f"{self.path.name}: requested pair {self.pair.code!r} present "
                "but zero SPOT rows"
            )
            self.diagnostics.error(
                "FX_IO_NO_SPOT",
                msg,
                {"requested_pair": self.pair.code},
            )
            raise FXCsvSchemaError(msg)
        if len(spot_rows) > 1:
            msg = (
                f"{self.path.name}: {len(spot_rows)} SPOT rows for "
                f"{self.pair.code!r}; expected exactly one"
            )
            self.diagnostics.error(
                "FX_IO_MULTIPLE_SPOT",
                msg,
                {"requested_pair": self.pair.code, "count": len(spot_rows)},
            )
            raise FXCsvSchemaError(msg)

        # Dedupe (instrument_type, tenor_code) — keep-first, WARN on duplicates.
        fwd_rows = self._dedupe("FORWARD_POINT", fwd_rows)
        basis_rows = self._dedupe("XCCY_BASIS", basis_rows)

        # Date computation against the joint calendar.
        spot_date = _add_joint_business_days(
            valuation_date, self.conv.spot_lag_days, self.calendars
        )

        spot_row = spot_rows[0]
        spot_quote = FXSpotQuote(
            pair=self.pair,
            spot_date=spot_date,
            bid=spot_row.bid,
            ask=spot_row.ask,
            mid=spot_row.mid,
            source=spot_row.source,
        )

        forward_quotes = tuple(
            FXForwardPointQuote(
                pair=self.pair,
                tenor_code=r.tenor_code,
                tenor_days=r.tenor_days,
                settle_date=spot_date + timedelta(days=r.tenor_days),
                bid=r.bid,
                ask=r.ask,
                mid=r.mid,
                source=r.source,
            )
            for r in sorted(fwd_rows, key=lambda x: x.tenor_days)
        )

        # quoted_on_foreign hardcoded True for v0.3.0 (USDTRY, EURTRY market convention).
        # If a future pair flips this, surface as a field on FXConvention.
        basis_quotes = tuple(
            CrossCurrencyBasisQuote(
                pair=self.pair,
                tenor_code=r.tenor_code,
                maturity_date=spot_date + timedelta(days=r.tenor_days),
                spread_bps=self._first_present(r.mid, r.ask, r.bid) or 0.0,
                quoted_on_foreign=True,
                source=r.source,
            )
            for r in sorted(basis_rows, key=lambda x: x.tenor_days)
        )

        return FXMarketData(
            spot=spot_quote,
            forward_points=forward_quotes,
            swap_quotes=(),
            basis_quotes=basis_quotes,
            valuation_date=valuation_date,
            source=f"csv:{self.path.name}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dedupe(
        self, instrument_label: str, rows: list[_TenoredRowT]
    ) -> list[_TenoredRowT]:
        """Dedupe by ``tenor_code`` (keep-first); WARN on duplicates.

        Generic over the two tenor-keyed row types (forward-point, xccy-basis).
        """
        seen: set[str] = set()
        kept: list[_TenoredRowT] = []
        duplicates: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.tenor_code in seen:
                duplicates[r.tenor_code] += 1
                continue
            seen.add(r.tenor_code)
            kept.append(r)
        for tenor_code, n_extra in duplicates.items():
            self.diagnostics.warn(
                "FX_IO_DUPLICATE_TENOR",
                (
                    f"{self.path.name}: {instrument_label} tenor {tenor_code!r} "
                    f"appears {n_extra + 1} times; keeping first occurrence"
                ),
                {
                    "instrument_type": instrument_label,
                    "tenor_code": tenor_code,
                    "duplicate_count": n_extra,
                },
            )
        return kept

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
    "FXCsvSchemaError",
    "FXEmptyDataError",
    "FXMarketDataProvider",
    "FxCsvProvider",
]
