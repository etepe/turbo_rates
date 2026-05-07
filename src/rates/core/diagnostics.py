"""rates.core.diagnostics — DiagnosticsCollector (M-005).

A single ``DiagnosticsCollector`` instance is threaded explicitly through the pipeline
(via function parameters, never a module-level singleton). Modules call ``warn()`` or
``error()`` to record structured diagnostic entries; the orchestrator (rates.app) reads
``has_errors()`` / ``exit_code()`` to decide whether to abort.

Severity rule (per F-006):
    WARN  — curve still producible; flag in summary JSON; exit 0.
    ERROR — curve not producible; abort downstream work; exit 2.

Contract: C-004.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Diagnostic severity. Maps directly to exit-code policy (any ERROR -> 2)."""

    WARN = "WARN"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Single structured diagnostic record.

    Attributes:
        severity: WARN or ERROR.
        code:     Stable machine-readable code (e.g. ``"BS_NON_MONOTONE_DF"``).
        message:  Human-readable description.
        context:  Free-form structured context (must be JSON-serialisable).
    """

    severity: Severity
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


class DiagnosticsCollector:
    """Mutable collector. One instance per pipeline run, passed explicitly."""

    def __init__(self) -> None:
        self._records: list[Diagnostic] = []

    def warn(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        """Record a WARN-severity diagnostic. Curve producible; downstream continues."""
        raise NotImplementedError("M-005: append a Diagnostic(WARN, ...) to self._records.")

    def error(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        """Record an ERROR-severity diagnostic. Caller should abort downstream work."""
        raise NotImplementedError("M-005: append a Diagnostic(ERROR, ...) to self._records.")

    def to_list(self) -> list[Diagnostic]:
        """Return a defensive copy of all diagnostics in insertion order."""
        raise NotImplementedError("M-005: return list(self._records).")

    def has_errors(self) -> bool:
        """True iff at least one ERROR diagnostic has been recorded."""
        raise NotImplementedError("M-005: any(d.severity == ERROR for d in self._records).")

    def exit_code(self) -> int:
        """Map state to process exit code: 2 if any ERROR else 0."""
        raise NotImplementedError("M-005: 2 if self.has_errors() else 0.")
