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
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
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
        self._records.append(
            Diagnostic(severity=Severity.WARN, code=code, message=message, context=context or {})
        )

    def error(self, code: str, message: str, context: dict[str, Any] | None = None) -> None:
        """Record an ERROR-severity diagnostic. Caller should abort downstream work."""
        self._records.append(
            Diagnostic(severity=Severity.ERROR, code=code, message=message, context=context or {})
        )

    def to_list(self) -> list[Diagnostic]:
        """Return a defensive copy of all diagnostics in insertion order."""
        return list(self._records)

    def has_errors(self) -> bool:
        """True iff at least one ERROR diagnostic has been recorded."""
        return any(d.severity is Severity.ERROR for d in self._records)

    def exit_code(self) -> int:
        """Map state to process exit code: 2 if any ERROR else 0."""
        return 2 if self.has_errors() else 0
