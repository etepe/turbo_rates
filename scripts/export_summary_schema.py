"""Export the Summary JSON Schema for V3 web UI consumption.

Writes ``docs/schemas/summary.schema.json`` from the Pydantic v2 model. Re-run
this script when ``rates.io.schemas`` changes intentionally (schema_version bump).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rates.io.schemas import Summary  # noqa: E402


def main() -> None:
    out_path = _REPO_ROOT / "docs" / "schemas" / "summary.schema.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    schema = Summary.model_json_schema()
    out_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
