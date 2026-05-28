"""rates.cli — argparse entry point (M-015).

Three subcommands, each a thin (<30 LoC body) delegate to :mod:`rates.app`:

    rates bootstrap [--snapshot PATH] [--mpc-path PATH] [--band N]
                    [--interp {log_linear_df,linear_zero}] [--quiet]
                    [--convention-override KEY=VALUE]
                    [--conventions-yaml PATH] [--output-root DIR]
                    [--horizon-days N]
    rates diagnose --snapshot PATH [--conventions-yaml PATH]
                   [--convention-override KEY=VALUE]
    rates forward  --start YYYY-MM-DD --end YYYY-MM-DD [--summary-path PATH]

Stdlib argparse — no extra dependency. Entry point declared in pyproject.toml:
``rates = "rates.cli:main"``.

Contract: C-013.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from rates.app import run_bootstrap, run_diagnose, run_forward

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
