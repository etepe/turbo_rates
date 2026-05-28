"""Regenerate ``tests/bootstrap/golden_curve.json`` from the golden inputs.

Run when conventions or input rates change intentionally:

    python scripts/regen_golden_curve.py

The committed JSON locks in deterministic byte-identical bootstrap output for the
22-tenor TYSO-style fixture used by ``tests/bootstrap/test_golden.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a script from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rates.core.bootstrap import bootstrap_curve  # noqa: E402
from rates.core.calendar import HolidayCalendar  # noqa: E402
from rates.core.conventions import Conventions  # noqa: E402
from rates.core.diagnostics import DiagnosticsCollector  # noqa: E402

sys.path.insert(0, str(_REPO_ROOT / "tests"))
from bootstrap._golden_inputs import GOLDEN_PATH, GOLDEN_VAL_DATE, build_golden_market  # noqa: E402


def main() -> None:
    cal = HolidayCalendar.for_currency(
        "TR",
        years=range(GOLDEN_VAL_DATE.year - 1, GOLDEN_VAL_DATE.year + 20),
        override_dir=_REPO_ROOT / "config/holidays",
    )
    conv = Conventions.load(_REPO_ROOT / "config/conventions.yaml")
    dg = DiagnosticsCollector()
    market = build_golden_market(cal)
    curve = bootstrap_curve(market, conv, cal, dg)

    payload = {
        "valuation_date": curve.valuation_date.isoformat(),
        "day_count": str(curve.day_count.value),
        "pillars": [
            {
                "tenor_code": p.tenor_code,
                "tenor_days": p.tenor_days,
                "end_date": p.end_date.isoformat(),
                "rate": p.rate,
                # repr() gives a full-precision round-trippable string for floats.
                "discount_factor_repr": repr(p.discount_factor),
            }
            for p in curve.pillars_tuple
        ],
        "diagnostic_codes": sorted({d.code for d in dg.to_list()}),
    }
    GOLDEN_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN_PATH} ({len(curve.pillars_tuple)} pillars)")


if __name__ == "__main__":
    main()
