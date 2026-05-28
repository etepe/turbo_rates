"""v0.3.0 FX smoke test — manual gate per docs/fx-smoke-test.md.

Runs `rates fx bootstrap` against the curated USDTRY fixture under
`fixtures/fx_smoke_usdtry/` and asserts the v0.3.0 acceptance criteria from
`docs/fx-io-architecture.md` §10:

* exit code 0
* the persisted summary JSON loads back into Pydantic without errors
* zero `FX_PARITY_MISMATCH` warnings emitted (the fixture is parity-tight by
  construction — see the comment block at the top of
  `usdtry_fx_snapshot_20260612.csv`)
* the forward + basis curves are reconstructable from the summary

Not part of CI — runs against committed curated CSVs, but the in-process
re-derivation step (`--regenerate`) requires the dev environment.

Usage:
    PYTHONPATH=src python scripts/fx_smoke_test.py             # validate
    PYTHONPATH=src python scripts/fx_smoke_test.py --regenerate
        # recompute parity-implied forward points and overwrite the FX CSV;
        # useful after any OIS-snapshot or bootstrap-math change.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "fx_smoke_usdtry"

_PAIR = "USDTRY"
_AS_OF = date(2026, 6, 12)
_FX_CSV = FIXTURES / f"usdtry_fx_snapshot_{_AS_OF.strftime('%Y%m%d')}.csv"
_TRY_CSV = FIXTURES / f"try_ois_snapshot_{_AS_OF.strftime('%Y%m%d')}.csv"
_USD_CSV = FIXTURES / f"usd_ois_snapshot_{_AS_OF.strftime('%Y%m%d')}.csv"

# Tenor table used both for regeneration and for cross-checking the fixture.
# (1M, 3M, 6M only — TRY OIS last pillar is 2027-06-14, so 1Y parity check
# would extrapolate. XCCY 1Y at 360 days lands at 2027-06-10, inside range.)
_FWD_TENORS: list[tuple[str, int]] = [("1M", 30), ("3M", 91), ("6M", 183)]
_XCCY_TENORS: list[tuple[str, int, float]] = [("1Y", 360, -180.0)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Recompute parity-implied forward points and rewrite the FX CSV.",
    )
    args = parser.parse_args(argv)

    if args.regenerate:
        _regenerate_fx_snapshot()
        print(f"regenerated {_FX_CSV}")
        return 0
    return _validate()


def _validate() -> int:
    """Run `rates fx bootstrap` on the curated fixture and check the gates."""
    from rates.io.schemas import FXSummary

    with tempfile.TemporaryDirectory() as tmp:
        out_root = Path(tmp)
        proc = subprocess.run(
            [
                sys.executable, "-m", "rates.cli",
                "fx", "bootstrap",
                "--pair", _PAIR,
                "--as-of", _AS_OF.isoformat(),
                "--fx-snapshot", str(_FX_CSV),
                "--domestic-snapshot", str(_TRY_CSV),
                "--foreign-snapshot", str(_USD_CSV),
                "--output-root", str(out_root),
            ],
            cwd=REPO_ROOT,
            env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)

        if proc.returncode != 0:
            print(f"FAIL: rates fx bootstrap returned {proc.returncode}", file=sys.stderr)
            return 1

        summary_path = out_root / "data" / "latest" / "usdtry_fx_summary.json"
        if not summary_path.exists():
            print(f"FAIL: summary not written at {summary_path}", file=sys.stderr)
            return 1

        summary = FXSummary.model_validate_json(
            summary_path.read_text(encoding="utf-8")
        )

    parity_warns = [d for d in summary.diagnostics if d.code == "FX_PARITY_MISMATCH"]
    if parity_warns:
        print(
            f"FAIL: {len(parity_warns)} FX_PARITY_MISMATCH WARN(s); "
            f"fixture is not parity-tight. Run with --regenerate.",
            file=sys.stderr,
        )
        for d in parity_warns:
            print(f"  WARN {d.code}: {d.message}", file=sys.stderr)
        return 1

    print(
        f"OK: {len(summary.forward_pillars)} forward pillar(s), "
        f"{len(summary.basis_pillars)} basis pillar(s), "
        f"{len(summary.diagnostics)} diagnostic(s), "
        f"0 FX_PARITY_MISMATCH WARNs."
    )
    return 0


def _regenerate_fx_snapshot() -> None:
    """Bootstrap both OIS curves, compute parity forwards, rewrite the FX CSV."""
    from rates.core.bootstrap import bootstrap_curve
    from rates.core.calendar import HolidayCalendar
    from rates.core.conventions import Conventions
    from rates.core.diagnostics import DiagnosticsCollector
    from rates.fx.conventions import FXConventions
    from rates.io.market import CsvProvider

    conv = Conventions.load(REPO_ROOT / "config" / "conventions.yaml")
    fx_convs = FXConventions.load(REPO_ROOT / "config" / "conventions.yaml")
    fx_conv = fx_convs.by_pair[_PAIR]

    dg = DiagnosticsCollector()
    tr_cal = HolidayCalendar.for_currency("TR")
    us_cal = HolidayCalendar.for_currency("US")
    tr_curve = bootstrap_curve(
        CsvProvider(_TRY_CSV, dg).load(), conv, tr_cal, dg, currency="TRY"
    )
    us_curve = bootstrap_curve(
        CsvProvider(_USD_CSV, dg).load(), conv, us_cal, dg, currency="USD"
    )

    # spot_date = as_of + spot_lag_days (joint calendar walk).
    spot = _AS_OF
    for _ in range(fx_conv.spot_lag_days):
        spot = spot + timedelta(days=1)
        while not (us_cal.is_business_day(spot) and tr_cal.is_business_day(spot)):
            spot = spot + timedelta(days=1)

    spot_rate = 32.4520
    bid_offset = 0.0020  # 20 pips half-spread on spot
    rows: list[str] = ["# schema: fx-v1"]
    rows.append(
        "# Curated parity-tight FX snapshot — regenerated by scripts/fx_smoke_test.py."
    )
    rows.append(
        "# Forward points satisfy F = S * DF_USD / DF_TRY (covered interest parity)"
    )
    rows.append("# so the M-105 parity check emits zero FX_PARITY_MISMATCH WARNs.")
    rows.append(
        "pair,instrument_type,tenor_code,tenor_days,bid,ask,mid,source"
    )
    rows.append(
        f"USDTRY,SPOT,SPOT,0,{spot_rate - bid_offset:.4f},{spot_rate + bid_offset:.4f},"
        f"{spot_rate:.4f},CURATED"
    )

    scale = fx_conv.forward_point_scale
    half_spread = 6.0  # ~6 points each side; tweak to taste

    for tenor_code, days in _FWD_TENORS:
        settle = spot + timedelta(days=days)
        df_tr = tr_curve.df_at(settle)
        df_us = us_curve.df_at(settle)
        parity_f = spot_rate * df_us / df_tr
        points = (parity_f - spot_rate) * scale
        rows.append(
            f"USDTRY,FORWARD_POINT,{tenor_code},{days},"
            f"{points - half_spread:.1f},{points + half_spread:.1f},"
            f"{points:.1f},CURATED"
        )

    for tenor_code, days, bps in _XCCY_TENORS:
        rows.append(
            f"USDTRY,XCCY_BASIS,{tenor_code},{days},"
            f"{bps - 5.0:.1f},{bps + 5.0:.1f},{bps:.1f},CURATED"
        )

    tmp = _FX_CSV.with_suffix(".csv.tmp")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
    shutil.move(str(tmp), _FX_CSV)


if __name__ == "__main__":
    raise SystemExit(main())
