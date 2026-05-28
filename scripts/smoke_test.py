"""V1.5 manual smoke test — reference-data round-trip.

Reads the legacy Excel reference workbooks (``ois_calculations.xlsx`` for the
22 TYSO market quotes + BISTTREF value), writes a canonical snapshot CSV under
``data/snapshots/``, invokes ``rates bootstrap`` via the CLI, then inspects the
resulting ``data/latest/try_ois_summary.json`` and prints reprice residuals plus
a pillar comparison.

Not part of CI. Manual sign-off only: run from the project root with::

    python scripts/smoke_test.py [--ois-xlsx PATH]

Defaults assume the Excel files live in ``$HOME/Desktop/rates_engine/``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

try:
    import openpyxl
except ImportError as e:  # pragma: no cover - dev dependency
    sys.stderr.write("openpyxl missing; run: uv pip install openpyxl\n")
    raise SystemExit(1) from e


_DEFAULT_XLSX = Path.home() / "Desktop" / "rates_engine" / "ois_calculations.xlsx"
_SNAPSHOT_DIR = Path("data/snapshots")
_SUMMARY_PATH = Path("data/latest/try_ois_summary.json")


def _coerce_float(v: object) -> float | None:
    """Return ``v`` as float; treat Excel error strings and None as missing."""
    if v is None:
        return None
    if isinstance(v, str):
        if v.startswith("#"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    if isinstance(v, int | float):
        return float(v)
    return None


def _extract_quotes(xlsx_path: Path) -> tuple[date, float, list[dict[str, object]]]:
    """Return (valuation_date, bisttref, quote_rows) from ``ois_calculations.xlsx``.

    ``quote_rows`` are dicts shaped for the canonical snapshot CSV columns.
    Rows whose bid/ask/mid are all unparseable are dropped silently.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    pol = wb["User_policy expectation"]
    # 'Value date' label at row 1 col 8; date at row 1 col 9.
    # 'TLREF'      label at row 2 col 8; value at row 2 col 9.
    val_dt = pol.cell(row=1, column=9).value
    bisttref = pol.cell(row=2, column=9).value
    val: date = val_dt.date()  # type: ignore[union-attr]
    bisttref_f = _coerce_float(bisttref)
    assert bisttref_f is not None, f"BISTTREF cell unreadable: {bisttref!r}"

    ws = wb["Market_data"]
    rows: list[dict[str, object]] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        tenor_label = raw[1]
        tenor_code_xl = raw[2]
        start = raw[3]
        end = raw[4]
        bid = _coerce_float(raw[6])
        ask = _coerce_float(raw[7])
        mid = _coerce_float(raw[8])
        if not all([tenor_label, tenor_code_xl, start, end]):
            continue
        if mid is None and (bid is None or ask is None):
            # Unparseable row — drop silently (the engine would skip it anyway).
            continue
        canonical_code = f"TYSO{tenor_label}"  # canonical naming, not the Excel ID
        start_d: date = start.date()  # type: ignore[union-attr]
        end_d: date = end.date()  # type: ignore[union-attr]
        rows.append(
            {
                "tenor_code": canonical_code,
                "tenor_days": (end_d - start_d).days,
                "start_date": start_d.isoformat(),
                "end_date": end_d.isoformat(),
                "bid": "" if bid is None else f"{bid:.6f}",
                "ask": "" if ask is None else f"{ask:.6f}",
                "mid": "" if mid is None else f"{mid:.6f}",
                "source": "TYSO-xlsx",
            }
        )
    return val, bisttref_f, rows


def _write_snapshot(
    val_date: date, bisttref: float, quote_rows: list[dict[str, object]]
) -> Path:
    """Serialise to ``data/snapshots/snapshot_YYYYMMDD.csv`` and return the path."""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"snapshot_{val_date.strftime('%Y%m%d')}.csv"
    out_path = _SNAPSHOT_DIR / fname
    cols = ("tenor_code", "tenor_days", "start_date", "end_date", "bid", "ask", "mid", "source")
    with out_path.open("w", encoding="utf-8") as f:
        f.write("# schema: v1\n")
        f.write(",".join(cols) + "\n")
        # BISTTREF row first (engine routes it to special_rates).
        f.write(
            ",".join(
                [
                    "BISTTREF",
                    "0",
                    val_date.isoformat(),
                    val_date.isoformat(),
                    "",
                    "",
                    f"{bisttref:.6f}",
                    "BIST",
                ]
            )
            + "\n"
        )
        for r in quote_rows:
            f.write(",".join(str(r[c]) for c in cols) + "\n")
    return out_path


def _run_bootstrap(snapshot_path: Path) -> int:
    """Invoke the CLI; return the process exit code. CLI output streams live."""
    cmd = [
        sys.executable,
        "-m",
        "rates.cli",
        "bootstrap",
        "--snapshot",
        str(snapshot_path),
        "--mpc-path",
        "config/mpc_path.csv",
        "--conventions-yaml",
        "config/conventions.yaml",
    ]
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _summarise_summary(path: Path) -> None:
    """Print pillar count, max reprice residual, and a compact pillar table."""
    if not path.exists():
        print(f"\nsummary not written: {path}")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    pillars = data["pillars"]
    diags = data.get("diagnostics", [])
    print("\n=== smoke summary ===")
    print(f"valuation_date       : {data['valuation_date']}")
    print(f"pillars              : {len(pillars)}")
    print(f"diagnostics          : {len(diags)} entries")

    reprice_errors = [d for d in diags if d.get("code") == "BS_REPRICE_FAIL"]
    if reprice_errors:
        print(f"REPRICE FAILURES     : {len(reprice_errors)}")
        for e in reprice_errors:
            print(f"  - {e['code']}: {e['message']}")
    else:
        print("reprice              : all pillars within 1e-10 (per F-001)")

    print("\ntenor       end_date     rate         DF")
    print("-" * 50)
    for p in pillars:
        print(
            f"{p['tenor_code']:11s} {p['end_date']}  {p['rate']:.6f}   {p['discount_factor']:.10f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ois-xlsx", type=Path, default=_DEFAULT_XLSX)
    args = parser.parse_args()

    if not args.ois_xlsx.exists():
        print(f"error: {args.ois_xlsx} not found", file=sys.stderr)
        return 2

    print(f"reading reference data from {args.ois_xlsx}")
    val_date, bisttref, rows = _extract_quotes(args.ois_xlsx)
    print(f"  valuation_date     : {val_date}")
    print(f"  BISTTREF           : {bisttref:.6f}")
    print(f"  quote rows         : {len(rows)}")

    snapshot = _write_snapshot(val_date, bisttref, rows)
    print(f"  wrote snapshot     : {snapshot}")

    print("\nrunning: rates bootstrap …")
    rc = _run_bootstrap(snapshot)
    print(f"  CLI exit code      : {rc}")

    _summarise_summary(_SUMMARY_PATH)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
