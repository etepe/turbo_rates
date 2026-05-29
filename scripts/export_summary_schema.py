"""Export the persisted JSON Schemas for V3 web UI consumption.

Writes ``docs/schemas/summary.schema.json`` (V1 OIS :class:`Summary`) and
``docs/schemas/fx_summary.schema.json`` (FX :class:`FXSummary`) from the
Pydantic v2 models. Re-run this script when ``rates.io.schemas`` changes
intentionally (schema_version bump).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rates.io.schemas import FXSummary, Summary  # noqa: E402


def _export(model: type[BaseModel], filename: str) -> None:
    out_path = _REPO_ROOT / "docs" / "schemas" / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    schema = model.model_json_schema()
    out_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")


def main() -> None:
    _export(Summary, "summary.schema.json")
    _export(FXSummary, "fx_summary.schema.json")


if __name__ == "__main__":
    main()
