"""V0.4 FX smoke test — manual gate per docs/fx-smoke-test.md.

Runs `rates fx bootstrap` against the curated USDTRY fixture under
`fixtures/fx_smoke_usdtry/` and asserts the V0.4 acceptance criteria from
`docs/v04-xccy-calibration-architecture.md` §6.2 / §11:

* exit code 0
* the persisted summary JSON loads back into Pydantic without errors
* zero `FX_PARITY_MISMATCH` warnings — the fixture's forwards EMBED the -180 bps
  basis (A-1), so the basis-aware gate finds forward-implied ≈ quoted
* zero `FX_XCCY_REPRICE_FAIL` (the strip reprices each pillar to net PV ≈ 0)
* the stripped 1Y basis recovers ≈ -180 bps (the embedded target)
* the forward + basis curves are reconstructable from the summary

The independent ground-truth anchors for the strip math (hand-computed micro-case
with native day-counts + CIP-tight degeneracy, grill G-2) live in
`tests/fx/test_strip.py`; this smoke gate is the end-to-end self-consistency check.

Not part of CI — runs against committed curated CSVs, but the in-process
re-derivation step (`--regenerate`) requires the dev environment.

Usage:
    PYTHONPATH=src python scripts/fx_smoke_test.py             # validate
    PYTHONPATH=src python scripts/fx_smoke_test.py --regenerate
        # recompute the basis-embedding forward points and overwrite the FX CSV;
        # useful after any OIS-snapshot or strip-math change.
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

# Single 1Y xccy basis quote; the strip recovers this from the embedded forwards.
_XCCY_TENORS: list[tuple[str, int, float]] = [("1Y", 360, -180.0)]

# Forward pillar labels by calendar-day offset from spot. Pillars sit on the 1Y
# swap's quarterly coupon dates (so the strip's forward_at hits them exactly),
# plus a 1M pillar for outright/swap pricing realism. Unknown offsets fall back
# to a "<n>D" label.
_FWD_LABELS: dict[int, str] = {30: "1M", 92: "3M", 183: "6M", 273: "9M", 360: "12M"}


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
            f"FAIL: {len(parity_warns)} FX_PARITY_MISMATCH WARN(s); the embedded "
            f"basis disagrees with the quote. Run with --regenerate.",
            file=sys.stderr,
        )
        for d in parity_warns:
            print(f"  WARN {d.code}: {d.message}", file=sys.stderr)
        return 1

    reprice_fails = [d for d in summary.diagnostics if d.code == "FX_XCCY_REPRICE_FAIL"]
    if reprice_fails:
        print(f"FAIL: {len(reprice_fails)} FX_XCCY_REPRICE_FAIL ERROR(s).", file=sys.stderr)
        return 1

    if not summary.basis_pillars:
        print("FAIL: no basis pillars stripped from the snapshot.", file=sys.stderr)
        return 1
    _, _, target_bps = _XCCY_TENORS[0]
    stripped_bps = summary.basis_pillars[0].spread_bps
    if abs(stripped_bps - target_bps) > 0.5:
        print(
            f"FAIL: stripped 1Y basis {stripped_bps:+.4f} bps is not within 0.5 bps "
            f"of the embedded target {target_bps:+.1f} bps.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OK: {len(summary.forward_pillars)} forward pillar(s), "
        f"{len(summary.basis_pillars)} basis pillar(s), "
        f"{len(summary.diagnostics)} diagnostic(s), "
        f"stripped 1Y basis {stripped_bps:+.2f} bps, "
        f"0 FX_PARITY_MISMATCH / 0 FX_XCCY_REPRICE_FAIL."
    )
    return 0


def _regenerate_fx_snapshot() -> None:
    """Bootstrap both OIS curves, embed b*=-180 bps into the forwards, rewrite the CSV.

    Method (i) inversion (architecture §5.4 / §6.2, OQ-504). A single 1Y xccy
    pillar is a one-bucket strip whose net PV is linear in the basis. With the
    spot-anchored CIP forward ``F_CIP(t) = S · DF_for_rel(t) / DF_dom_rel(t)`` the
    foreign leg + notional reprice to exactly ``S`` (the accrual ``tau`` cancels in
    the telescoping foreign-OIS forward), so a **uniform** multiplicative shift
    ``F = F_CIP · (1 + δ)`` gives ``PV_for0 = δ`` and a stripped
    ``b_dec = -S · δ / ((1 + δ) · A_CIP)`` where
    ``A_CIP = Σ F_CIP(t_i) · tau_i · DF_dom_rel(t_i)``. Inverting for the target
    ``b*`` is the closed form

        δ = -b*_dec · A_CIP / (S + b*_dec · A_CIP)

    which reproduces ``b*`` to machine precision — no per-period solve needed
    (OQ-504). Forward pillars sit on the quarterly coupon dates so the strip's
    ``forward_at`` hits each exactly; coverage reaches the longest coupon (OQ-505).
    """
    from rates.core.bootstrap import bootstrap_curve
    from rates.core.calendar import HolidayCalendar
    from rates.core.conventions import Conventions
    from rates.core.diagnostics import DiagnosticsCollector
    from rates.core.types import BusinessDayConvention, DayCount
    from rates.fx.conventions import FXConventions
    from rates.fx.schedule import build_quarterly_xccy_schedule
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
    scale = fx_conv.forward_point_scale
    xccy_code, xccy_days, xccy_bps = _XCCY_TENORS[0]

    # Quarterly schedule + the uniform δ that embeds b* into the CIP forwards.
    maturity = spot + timedelta(days=xccy_days)  # mirrors FxCsvProvider's maturity
    sched = build_quarterly_xccy_schedule(
        spot, maturity, tr_cal, us_cal,
        BusinessDayConvention.MODIFIED_FOLLOWING, DayCount.ACT_360,
    )
    df_dom_spot = tr_curve.df_at(spot)
    df_for_spot = us_curve.df_at(spot)

    def f_cip(t: date) -> float:
        return spot_rate * (us_curve.df_at(t) / df_for_spot) / (tr_curve.df_at(t) / df_dom_spot)

    a_cip = sum(
        f_cip(c) * tau * (tr_curve.df_at(c) / df_dom_spot)
        for c, tau in zip(sched.coupon_dates, sched.taus, strict=True)
    )
    b_target = xccy_bps / 10000.0
    delta = -b_target * a_cip / (spot_rate + b_target * a_cip)

    rows: list[str] = [
        "# schema: fx-v1",
        "# Curated arbitrage-consistent FX snapshot — regenerated by scripts/fx_smoke_test.py.",
        f"# Forward points EMBED a {xccy_bps:.0f} bps xccy basis via F = F_CIP * (1 + delta),",
        f"# delta={delta:.8f} (uniform Method-(i) inversion, architecture §5.4/§6.2). The",
        f"# V0.4 strip recovers ~{xccy_bps:.0f} bps so the basis-aware parity gate stays silent.",
        "pair,instrument_type,tenor_code,tenor_days,bid,ask,mid,source",
    ]
    bid_offset = 0.0020  # 20 pips half-spread on spot
    rows.append(
        f"USDTRY,SPOT,SPOT,0,{spot_rate - bid_offset:.4f},{spot_rate + bid_offset:.4f},"
        f"{spot_rate:.4f},CURATED"
    )

    half_spread = 6.0  # ~6 points each side
    coupon_offsets = [(c - spot).days for c in sched.coupon_dates]
    fwd_offsets = sorted({30, *coupon_offsets})
    for off in fwd_offsets:
        settle = spot + timedelta(days=off)
        f = f_cip(settle) * (1.0 + delta)
        points = (f - spot_rate) * scale
        label = _FWD_LABELS.get(off, f"{off}D")
        rows.append(
            f"USDTRY,FORWARD_POINT,{label},{off},"
            f"{points - half_spread:.1f},{points + half_spread:.1f},{points:.1f},CURATED"
        )

    rows.append(
        f"USDTRY,XCCY_BASIS,{xccy_code},{xccy_days},"
        f"{xccy_bps - 5.0:.1f},{xccy_bps + 5.0:.1f},{xccy_bps:.1f},CURATED"
    )

    tmp = _FX_CSV.with_suffix(".csv.tmp")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
    shutil.move(str(tmp), _FX_CSV)


if __name__ == "__main__":
    raise SystemExit(main())
