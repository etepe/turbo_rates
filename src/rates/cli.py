"""rates.cli — argparse entry point (M-015, M-112).

V1 OIS subcommands (M-015), each a thin (<30 LoC body) delegate to :mod:`rates.app`:

    rates bootstrap [--snapshot PATH] [--mpc-path PATH] [--band N]
                    [--interp {log_linear_df,linear_zero}] [--quiet]
                    [--convention-override KEY=VALUE]
                    [--conventions-yaml PATH] [--output-root DIR]
                    [--horizon-days N]
    rates diagnose --snapshot PATH [--conventions-yaml PATH]
                   [--convention-override KEY=VALUE]
    rates forward  --start YYYY-MM-DD --end YYYY-MM-DD [--summary-path PATH]

v0.3.0 FX subcommands (M-112) — nested `rates fx <verb>` per architecture D3.
The shared FX shape ``--pair / --as-of / --conventions-yaml / --output-root
/ --quiet / --convention-override`` lives on every verb:

    rates fx diagnose --pair PAIR --as-of YYYY-MM-DD
                      [--fx-snapshot PATH] [--domestic-snapshot PATH]
                      [--foreign-snapshot PATH]
    rates fx bootstrap          (same as diagnose, plus persistence side effect)
    rates fx price-outright --pair PAIR --as-of YYYY-MM-DD
                            (--tenor TENOR | --value-date YYYY-MM-DD)
    rates fx price-swap     --pair PAIR --as-of YYYY-MM-DD
                            (--near-tenor TENOR | --near-value-date YYYY-MM-DD)
                            (--far-tenor TENOR  | --far-value-date YYYY-MM-DD)
    rates fx price-xccy     --pair PAIR --as-of YYYY-MM-DD --tenor TENOR
    rates fx price-xccy-mtm --pair PAIR --as-of YYYY-MM-DD --spread BPS
                            --notional N [--direction receive-domestic|pay-domestic]
                            (--tenor TENOR | --maturity YYYY-MM-DD)

Stdlib argparse — no extra dependency. Entry point declared in pyproject.toml:
``rates = "rates.cli:main"``.

Contract: C-013 (V1 OIS), C-103 (v0.3.0 FX).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from rates.app import (
    run_bootstrap,
    run_diagnose,
    run_forward,
    run_fx_bootstrap,
    run_fx_diagnose,
    run_fx_price_outright,
    run_fx_price_swap,
    run_fx_price_xccy,
    run_fx_price_xccy_mtm,
)

_DEFAULT_SNAPSHOT_DIR: Path = Path("data/snapshots")
_DEFAULT_MPC_PATH: Path = Path("config/mpc_path.csv")
_DEFAULT_CONVENTIONS_YAML: Path = Path("config/conventions.yaml")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a Unix exit code (0 on success, 2 on errors)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rates",
        description="Turkish Lira OIS curve engine (bootstrap, diagnose, forward).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_bootstrap_parser(sub)
    _add_diagnose_parser(sub)
    _add_forward_parser(sub)
    _add_fx_parser(sub)
    return parser


def _add_bootstrap_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("bootstrap", help="Full pipeline: ingest, bootstrap, write outputs.")
    p.add_argument("--snapshot", type=Path, default=None, help="Snapshot CSV (default: latest).")
    p.add_argument("--mpc-path", dest="mpc_path", type=Path, default=_DEFAULT_MPC_PATH)
    p.add_argument("--band", type=int, default=0, help="Band in bps (>=0). 0 = mid only.")
    p.add_argument(
        "--interp",
        choices=["log_linear_df", "linear_zero"],
        default="log_linear_df",
    )
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--convention-override", action="append", default=None)
    p.add_argument("--conventions-yaml", type=Path, default=_DEFAULT_CONVENTIONS_YAML)
    p.add_argument("--output-root", type=Path, default=Path("."))
    p.add_argument("--horizon-days", type=int, default=None)
    p.set_defaults(func=_bootstrap_command)


def _add_diagnose_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("diagnose", help="Run checks only; no output files written.")
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--conventions-yaml", type=Path, default=_DEFAULT_CONVENTIONS_YAML)
    p.add_argument("--convention-override", action="append", default=None)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=_diagnose_command)


def _add_forward_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser("forward", help="Ad-hoc forward rate from the latest summary JSON.")
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--summary-path", dest="summary_path", type=Path, default=None)
    p.add_argument("--output-root", type=Path, default=Path("."))
    p.set_defaults(func=_forward_command)


def _add_fx_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the nested `rates fx <verb>` subcommand tree (M-112)."""
    fx = sub.add_parser("fx", help="FX layer: dual-curve bootstrap + ad-hoc pricing.")
    fx_sub = fx.add_subparsers(dest="fx_command", required=True)

    _add_fx_diagnose_parser(fx_sub)
    _add_fx_bootstrap_parser(fx_sub)
    _add_fx_price_outright_parser(fx_sub)
    _add_fx_price_swap_parser(fx_sub)
    _add_fx_price_xccy_parser(fx_sub)
    _add_fx_price_xccy_mtm_parser(fx_sub)


def _add_fx_shared(p: argparse.ArgumentParser, *, persistence: bool) -> None:
    """Attach the FX-shared args common to every `rates fx <verb>` verb (C-103).

    Args:
        p:            Subparser to mutate.
        persistence:  When True, attaches ``--output-root`` (diagnose verbs
                      that never write outputs still accept it harmlessly,
                      but we keep the surface tight per architecture §4).
    """
    p.add_argument("--pair", required=True, help="6-letter ISO pair, e.g. USDTRY.")
    p.add_argument("--as-of", dest="as_of", type=date.fromisoformat, required=True)
    p.add_argument("--conventions-yaml", type=Path, default=_DEFAULT_CONVENTIONS_YAML)
    p.add_argument("--convention-override", action="append", default=None)
    p.add_argument("--quiet", action="store_true")
    if persistence:
        p.add_argument("--output-root", type=Path, default=Path("."))


def _add_fx_snapshot_args(p: argparse.ArgumentParser) -> None:
    """Attach `--fx-snapshot / --domestic-snapshot / --foreign-snapshot` overrides."""
    p.add_argument("--fx-snapshot", dest="fx_snapshot", type=Path, default=None)
    p.add_argument(
        "--domestic-snapshot", dest="domestic_snapshot", type=Path, default=None
    )
    p.add_argument(
        "--foreign-snapshot", dest="foreign_snapshot", type=Path, default=None
    )


def _add_fx_diagnose_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "diagnose",
        help="Full FX pipeline through parity check; no outputs written.",
    )
    _add_fx_shared(p, persistence=False)
    _add_fx_snapshot_args(p)
    p.set_defaults(func=_fx_diagnose_command)


def _add_fx_bootstrap_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "bootstrap",
        help="Full FX pipeline + persist summary JSON and curve Parquet partitions.",
    )
    _add_fx_shared(p, persistence=True)
    _add_fx_snapshot_args(p)
    p.set_defaults(func=_fx_bootstrap_command)


def _add_fx_price_outright_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = sub.add_parser(
        "price-outright", help="Price an outright forward from the latest FX summary."
    )
    _add_fx_shared(p, persistence=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenor", dest="tenor_code", default=None)
    group.add_argument("--value-date", dest="value_date", type=date.fromisoformat, default=None)
    p.set_defaults(func=_fx_price_outright_command)


def _add_fx_price_swap_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "price-swap", help="Price an FX swap (near + far) from the latest FX summary."
    )
    _add_fx_shared(p, persistence=True)
    near = p.add_mutually_exclusive_group(required=True)
    near.add_argument("--near-tenor", dest="near_tenor", default=None)
    near.add_argument(
        "--near-value-date", dest="near_value_date", type=date.fromisoformat, default=None
    )
    far = p.add_mutually_exclusive_group(required=True)
    far.add_argument("--far-tenor", dest="far_tenor", default=None)
    far.add_argument(
        "--far-value-date", dest="far_value_date", type=date.fromisoformat, default=None
    )
    p.set_defaults(func=_fx_price_swap_command)


def _add_fx_price_xccy_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = sub.add_parser(
        "price-xccy", help="Price a cross-currency basis swap (tenor-keyed)."
    )
    _add_fx_shared(p, persistence=True)
    p.add_argument("--tenor", dest="tenor_code", required=True)
    p.set_defaults(func=_fx_price_xccy_command)


def _add_fx_price_xccy_mtm_parser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = sub.add_parser(
        "price-xccy-mtm",
        help="Mark-to-market PV of a cross-currency basis swap at an off-market spread.",
    )
    _add_fx_shared(p, persistence=True)
    p.add_argument("--spread", type=float, required=True, help="Contract basis spread (bps).")
    p.add_argument(
        "--notional",
        type=float,
        required=True,
        help="Domestic notional (>= 0; use --direction for the side).",
    )
    p.add_argument(
        "--direction",
        choices=["receive-domestic", "pay-domestic"],
        default="receive-domestic",
        help="Which side the PV is reported for (default: receive-domestic).",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenor", dest="tenor_code", default=None)
    group.add_argument(
        "--maturity", dest="maturity_date", type=date.fromisoformat, default=None
    )
    p.set_defaults(func=_fx_price_xccy_mtm_command)


# ---------------------------------------------------------------------------
# Subcommand bodies (<30 LoC each per F-009)
# ---------------------------------------------------------------------------


def _bootstrap_command(args: argparse.Namespace) -> int:
    if args.snapshot is None:
        latest = _latest_snapshot(_DEFAULT_SNAPSHOT_DIR)
        if latest is None:
            print(
                f"error: no snapshot found under {_DEFAULT_SNAPSHOT_DIR}; pass --snapshot PATH",
                file=sys.stderr,
            )
            return 2
        args.snapshot = latest
    return run_bootstrap(args)


def _diagnose_command(args: argparse.Namespace) -> int:
    return run_diagnose(args)


def _forward_command(args: argparse.Namespace) -> int:
    return run_forward(args)


def _fx_diagnose_command(args: argparse.Namespace) -> int:
    return run_fx_diagnose(args)


def _fx_bootstrap_command(args: argparse.Namespace) -> int:
    return run_fx_bootstrap(args)


def _fx_price_outright_command(args: argparse.Namespace) -> int:
    return run_fx_price_outright(args)


def _fx_price_swap_command(args: argparse.Namespace) -> int:
    return run_fx_price_swap(args)


def _fx_price_xccy_command(args: argparse.Namespace) -> int:
    return run_fx_price_xccy(args)


def _fx_price_xccy_mtm_command(args: argparse.Namespace) -> int:
    return run_fx_price_xccy_mtm(args)


# ---------------------------------------------------------------------------
# Default-path helpers
# ---------------------------------------------------------------------------


def _latest_snapshot(directory: Path) -> Path | None:
    """Return the lexicographically-latest ``snapshot_*.csv`` under ``directory``.

    Snapshots follow the ``snapshot_YYYYMMDD.csv`` convention so lexical max
    coincides with chronological max.
    """
    if not directory.exists():
        return None
    candidates = sorted(directory.glob("snapshot_*.csv"))
    return candidates[-1] if candidates else None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
