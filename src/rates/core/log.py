"""rates.core.log — log() indirection helper (M-004).

Library code calls ``log(level, msg, **ctx)`` instead of ``print()`` so that V2 can swap
the implementation to structlog / loguru in a single file without touching call sites.

V1 implementation: print to stderr in a structured but human-readable form.

Contract: C-014.
"""

from __future__ import annotations

import sys
from typing import Any


def log(level: str, msg: str, **ctx: Any) -> None:
    """Emit a structured log line.

    Args:
        level: One of ``"debug"``, ``"info"``, ``"warning"``, ``"error"`` (free string in V1).
        msg:   Human-readable message.
        **ctx: Arbitrary structured context. Will be rendered as ``key=value`` pairs.

    The V1 implementation prints to stderr. V2 may substitute a structured backend
    (loguru/structlog) without changing this signature.
    """
    if ctx:
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        line = f"[{level}] {msg} {ctx_str}"
    else:
        line = f"[{level}] {msg}"
    print(line, file=sys.stderr)
