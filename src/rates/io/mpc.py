"""rates.io.mpc — MPC policy path CSV reader (M-011).

Reads ``config/mpc_path.csv`` into a frozen :class:`MPCPath`. The CSV layout is
small and stable:

* Required: ``meeting_date`` (ISO YYYY-MM-DD), ``bps_change`` (signed int).
* Optional: ``rationale`` (free text).
* Extra columns ignored.
* Optional first-line ``# schema: v1`` comment skipped.

Rows are sorted by ``meeting_date`` ascending on load. Duplicate
``meeting_date`` values raise :class:`MPCScheduleError` with an
``IO_MPC_DUPLICATE_DATE`` ERROR diagnostic — they cannot be silently merged
because the bps_change semantics are ambiguous (sum vs. last-wins).

Contract: C-007.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from rates.core.diagnostics import DiagnosticsCollector
from rates.core.types import MPCMeeting, MPCPath

REQUIRED_COLS: tuple[str, ...] = ("meeting_date", "bps_change")


class MPCSchemaError(ValueError):
    """Required column missing or row fails Pydantic validation."""


class MPCScheduleError(ValueError):
    """Loaded rows violate uniqueness (duplicate ``meeting_date``)."""


class _RawRow(BaseModel):
    """Boundary row: same locale-strict policy as io.market (default lax mode
    coerces ISO strings; comma decimal / DMY dates rejected)."""

    model_config = ConfigDict(extra="ignore")

    meeting_date: date
    bps_change: int
    rationale: str | None = None


def load_mpc_path(path: Path | str, diagnostics: DiagnosticsCollector) -> MPCPath:
    """Read MPC CSV into a frozen :class:`MPCPath`.

    Args:
        path:         Path to ``mpc_path.csv``.
        diagnostics:  Single mutable collector threaded by the orchestrator.

    Returns:
        :class:`MPCPath` sorted ascending by ``meeting_date``.

    Raises:
        FileNotFoundError:  ``path`` does not exist.
        MPCSchemaError:     Required column missing or row invalid.
        MPCScheduleError:   Duplicate ``meeting_date`` values present.
    """
    path = Path(path)
    if not path.exists():
        diagnostics.error(
            "IO_MPC_FILE_NOT_FOUND",
            f"MPC path CSV not found: {path}",
            {"path": str(path)},
        )
        raise FileNotFoundError(f"MPC path CSV not found: {path}")

    with path.open(encoding="utf-8") as f:
        non_comment = [line for line in f if not line.lstrip().startswith("#")]

    reader = csv.DictReader(non_comment)
    headers = tuple(reader.fieldnames or ())
    missing = [c for c in REQUIRED_COLS if c not in headers]
    if missing:
        msg = f"required column(s) missing from {path.name}: {sorted(missing)}"
        diagnostics.error(
            "IO_MPC_SCHEMA_MISSING_COL",
            msg,
            {"missing": missing, "headers": list(headers)},
        )
        raise MPCSchemaError(msg)

    meetings: list[MPCMeeting] = []
    for line_no, raw in enumerate(reader, start=2):
        cleaned = {k: (v if v not in ("", None) else None) for k, v in raw.items()}
        try:
            r = _RawRow.model_validate(cleaned)
        except ValidationError as e:
            diagnostics.error(
                "IO_MPC_ROW_PARSE_FAIL",
                f"{path.name} line {line_no}: {_first_error(e)}",
                {"line_no": line_no, "row": raw},
            )
            raise MPCSchemaError(f"{path.name} line {line_no}: invalid row data") from e
        meetings.append(
            MPCMeeting(
                meeting_date=r.meeting_date,
                bps_change=r.bps_change,
                rationale=r.rationale,
            )
        )

    meetings.sort(key=lambda m: m.meeting_date)
    seen: dict[date, int] = {}
    for m in meetings:
        seen[m.meeting_date] = seen.get(m.meeting_date, 0) + 1
    duplicates = sorted(d for d, n in seen.items() if n > 1)
    if duplicates:
        msg = f"duplicate meeting_date(s) in {path.name}: {[d.isoformat() for d in duplicates]}"
        diagnostics.error(
            "IO_MPC_DUPLICATE_DATE",
            msg,
            {"duplicates": [d.isoformat() for d in duplicates]},
        )
        raise MPCScheduleError(msg)

    return MPCPath(meetings=tuple(meetings))


def _first_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return str(e)
    first = errs[0]
    loc = ".".join(str(x) for x in first.get("loc", ()))
    return f"{loc}: {first.get('msg', 'invalid value')}"


__all__ = [
    "REQUIRED_COLS",
    "MPCScheduleError",
    "MPCSchemaError",
    "load_mpc_path",
]
