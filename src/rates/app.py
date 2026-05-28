"""rates.app — pipeline orchestrator (M-014, M-111).

V1 entry points consumed by :mod:`rates.cli`, each returning a Unix-style exit
code (0 on WARN-only, 2 on any ERROR):

* :func:`run_bootstrap` — full pipeline: load market + MPC → bootstrap → scenario
  → compare → persist summary JSON + Parquet partition → pretty stdout.
* :func:`run_diagnose`  — load market + bootstrap only; print diagnostics; no
  outputs written.
* :func:`run_forward`   — read latest summary JSON; reconstruct curve; print
  ad-hoc forward rate.

v0.3.0 FX entry points (M-111, Phase 3a):

* :func:`run_fx_diagnose` — full FX orchestration through the parity check:
  load FX + dual OIS snapshots → bootstrap both OIS curves → build FX forward
  curve (with parity check inside M-105) → build basis curve (if XCCY_BASIS
  rows present). No persistence, no pricing. Enforces the
  ``FX_SNAPSHOT_DATE_MISMATCH`` cross-CSV invariant from
  ``docs/fx-io-architecture.md`` §5. Phase 3b adds ``run_fx_bootstrap`` +
  three pricing entry points on top of this surface.

Sequences mirror ``docs/architecture.md`` §6 and ``docs/fx-io-architecture.md`` §6.

Open-question resolutions (this module fixes them):

* **O5 — OISCurve-from-Summary helper location**: lives here as
  :func:`_curve_from_summary` rather than on :class:`OISCurve` as a classmethod.
  Rationale: the conversion needs to read :class:`rates.io.schemas.Summary`,
  which would require ``rates.core.curve`` to import from ``rates.io`` and
  violate the no-cycles layering rule. The orchestrator already knows both
  layers, so the adapter lives here.
* **PipelineResult intentionally omitted.** Architecture lists it in
  ``owns_data`` but the consumer (CLI) only needs the exit code. Bootstrap
  followed the same pattern with ``BootstrapResult``. If a richer return type
  is needed later it can be added without breaking C-013 (which only specifies
  the int return).

Contract: C-013 (consumer: ``rates.cli``).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from rates.core.bootstrap import (
    REPRICE_TOLERANCE,
    ZeroValidQuotesError,
    bootstrap_curve,
)
from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.curve import OISCurve, Pillar
from rates.core.diagnostics import DiagnosticsCollector, Severity
from rates.core.report import build_comparison
from rates.core.scenario import (
    InvalidMPCScheduleError,
    MissingInitialTLREFError,
    ScenarioResult,
    build_scenario,
)
from rates.core.types import DayCount
from rates.fx.bootstrap_basis import build_cross_basis_curve
from rates.fx.bootstrap_forward import build_fx_forward_curve
from rates.fx.conventions import FXConventions
from rates.fx.types import CurrencyPair
from rates.io.fx_market import (
    FxCsvProvider,
    FXCsvSchemaError,
    FXEmptyDataError,
)
from rates.io.market import CsvProvider, CsvSchemaError, EmptyDataError
from rates.io.mpc import MPCScheduleError, MPCSchemaError, load_mpc_path
from rates.io.persistence import scenario_to_table, write_partition, write_summary
from rates.io.schemas import ConfigSnapshot, Summary

_DEFAULT_CURRENCY: str = "TRY"
_SUMMARY_REL_PATH: Path = Path("data/latest/try_ois_summary.json")
_PARTITION_ROOT_REL: Path = Path("data/curves/try_ois")

#: Default root for FX snapshot CSVs when not overridden.
_FX_SNAPSHOT_DIR: Path = Path("data/fx_snapshots")
#: Default root for OIS snapshot CSVs (V1 path).
_OIS_SNAPSHOT_DIR: Path = Path("data/snapshots")
#: Filename suffix pattern carrying YYYYMMDD as_of.
_DATE_SUFFIX_RE = re.compile(r"_(\d{8})$")


# ---------------------------------------------------------------------------
# Subcommand entry points (C-013)
# ---------------------------------------------------------------------------


def run_bootstrap(args: argparse.Namespace) -> int:
    """Run the primary ``rates bootstrap`` pipeline."""
    dg = DiagnosticsCollector()
    try:
        conv = Conventions.load(
            Path(args.conventions_yaml),
            overrides=_parse_overrides(getattr(args, "convention_override", None) or []),
        )
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return _print_exit(dg, args)

    cal = HolidayCalendar.for_currency(conv.ois_conventions[_DEFAULT_CURRENCY].calendar)

    try:
        market = CsvProvider(args.snapshot, dg).load()
        mpc = load_mpc_path(args.mpc_path, dg)
    except (
        FileNotFoundError,
        CsvSchemaError,
        EmptyDataError,
        MPCSchemaError,
        MPCScheduleError,
    ):
        return _print_exit(dg, args)

    try:
        curve = bootstrap_curve(
            market,
            conv,
            cal,
            dg,
            interp=args.interp,
            currency=_DEFAULT_CURRENCY,
        )
    except ZeroValidQuotesError:
        return _print_exit(dg, args)

    try:
        scenario = build_scenario(
            market,
            mpc,
            band_bps=args.band,
            conventions=conv,
            calendar=cal,
            diagnostics=dg,
            horizon_days=getattr(args, "horizon_days", None),
        )
    except (MissingInitialTLREFError, InvalidMPCScheduleError):
        return _print_exit(dg, args)

    comparison = build_comparison(curve, scenario, conv)

    config_snapshot = _build_config_snapshot(conv, args, scenario)
    summary = Summary.from_domain(
        curve=curve,
        scenario=scenario,
        comparison=comparison,
        config_snapshot=config_snapshot,
        diagnostics=dg.to_list(),
    )

    output_root = Path(getattr(args, "output_root", Path(".")))
    write_summary(summary, output_root / _SUMMARY_REL_PATH)
    write_partition(
        scenario_to_table(scenario),
        output_root / _PARTITION_ROOT_REL,
        curve.valuation_date,
    )

    if not getattr(args, "quiet", False):
        print(comparison.pretty())

    return _print_exit(dg, args, summary_msg=f"reprice tolerance = {REPRICE_TOLERANCE:.0e}")


def run_diagnose(args: argparse.Namespace) -> int:
    """Run the ``rates diagnose`` flow: load + bootstrap, no outputs written."""
    dg = DiagnosticsCollector()
    try:
        conv = Conventions.load(
            Path(args.conventions_yaml),
            overrides=_parse_overrides(getattr(args, "convention_override", None) or []),
        )
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return _print_exit(dg, args)

    cal = HolidayCalendar.for_currency(conv.ois_conventions[_DEFAULT_CURRENCY].calendar)

    try:
        market = CsvProvider(args.snapshot, dg).load()
    except (FileNotFoundError, CsvSchemaError, EmptyDataError):
        return _print_exit(dg, args)

    # bootstrap may raise ZeroValidQuotesError after recording the ERROR
    # diagnostic; we fall through to exit-code reporting either way.
    import contextlib

    with contextlib.suppress(ZeroValidQuotesError):
        bootstrap_curve(market, conv, cal, dg, currency=_DEFAULT_CURRENCY)

    return _print_exit(dg, args)


def run_fx_diagnose(args: argparse.Namespace) -> int:
    """Run the ``rates fx diagnose`` flow: full FX pipeline through parity check.

    Loads the FX + dual OIS snapshots, bootstraps both OIS curves, builds the FX
    forward curve (parity check inside M-105 emits ``FX_PARITY_MISMATCH`` WARN
    per pillar), and builds the basis curve when XCCY_BASIS rows are present.
    **Persists nothing.**

    Args:
        args: argparse Namespace with: ``pair`` (str), ``as_of`` (date),
              ``fx_snapshot`` / ``foreign_snapshot`` / ``domestic_snapshot``
              (Path | None — auto-derived from ``as_of`` + ``pair`` when None),
              ``conventions_yaml`` (Path), ``quiet`` (bool),
              ``convention_override`` (list[str] | None).

    Returns:
        Unix exit code — 0 on WARN-only or clean, 2 on any ERROR.
    """
    dg = DiagnosticsCollector()

    # ------------------------------------------------------------------
    # Conventions + pair resolution
    # ------------------------------------------------------------------
    try:
        conv = Conventions.load(
            Path(args.conventions_yaml),
            overrides=_parse_overrides(getattr(args, "convention_override", None) or []),
        )
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return _print_exit(dg, args)

    try:
        fx_convs = FXConventions.load(Path(args.conventions_yaml))
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_FX_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return _print_exit(dg, args)

    pair_code = str(args.pair)
    pair = _parse_currency_pair(pair_code)
    if pair_code not in fx_convs.by_pair:
        dg.error(
            "FX_PAIR_UNKNOWN",
            f"pair {pair_code!r} not in fx_conventions; known: {sorted(fx_convs.by_pair)}",
            {"requested": pair_code, "known": sorted(fx_convs.by_pair)},
        )
        return _print_exit(dg, args)
    fx_conv = fx_convs.by_pair[pair_code]

    if pair.domestic not in conv.ois_conventions or pair.foreign not in conv.ois_conventions:
        dg.error(
            "FX_OIS_CONVENTION_MISSING",
            (
                f"ois_conventions must define both {pair.domestic!r} and {pair.foreign!r}; "
                f"known: {sorted(conv.ois_conventions)}"
            ),
            {"required": [pair.domestic, pair.foreign], "known": sorted(conv.ois_conventions)},
        )
        return _print_exit(dg, args)

    as_of: date = args.as_of

    # ------------------------------------------------------------------
    # Path resolution (auto-derive when caller does not override)
    # ------------------------------------------------------------------
    fx_path, dom_path, for_path = _resolve_fx_paths(args, pair, as_of)

    # ------------------------------------------------------------------
    # Cross-CSV snapshot-date check (FX_SNAPSHOT_DATE_MISMATCH)
    # ------------------------------------------------------------------
    if not _snapshot_dates_consistent(dg, as_of, fx_path, dom_path, for_path):
        return _print_exit(dg, args)

    # ------------------------------------------------------------------
    # Calendar resolution — single per-currency calendars are reused for the
    # OIS bootstraps AND combined for the FX reader's joint-calendar check.
    # ------------------------------------------------------------------
    dom_cal = HolidayCalendar.for_currency(conv.ois_conventions[pair.domestic].calendar)
    for_cal = HolidayCalendar.for_currency(conv.ois_conventions[pair.foreign].calendar)
    joint_calendars: tuple[HolidayCalendar, ...] = (dom_cal, for_cal)

    # ------------------------------------------------------------------
    # Load market data (three CSVs)
    # ------------------------------------------------------------------
    try:
        dom_market = CsvProvider(dom_path, dg).load()
        for_market = CsvProvider(for_path, dg).load()
        fx_market = FxCsvProvider(
            fx_path,
            pair=pair,
            conv=fx_conv,
            calendars=joint_calendars,
            diagnostics=dg,
        ).load(as_of)
    except (
        FileNotFoundError,
        CsvSchemaError,
        EmptyDataError,
        FXCsvSchemaError,
        FXEmptyDataError,
    ):
        return _print_exit(dg, args)

    # ------------------------------------------------------------------
    # Bootstrap both OIS curves — failure on EITHER aborts (grill D9).
    # ------------------------------------------------------------------
    try:
        dom_curve = bootstrap_curve(
            dom_market, conv, dom_cal, dg, currency=pair.domestic
        )
    except ZeroValidQuotesError:
        dg.error(
            "FX_DOMESTIC_OIS_BOOTSTRAP_FAIL",
            f"domestic ({pair.domestic}) OIS bootstrap produced no valid pillars; aborting FX run",
            {"currency": pair.domestic, "snapshot": str(dom_path)},
        )
        return _print_exit(dg, args)

    try:
        for_curve = bootstrap_curve(
            for_market, conv, for_cal, dg, currency=pair.foreign
        )
    except ZeroValidQuotesError:
        dg.error(
            "FX_FOREIGN_OIS_BOOTSTRAP_FAIL",
            f"foreign ({pair.foreign}) OIS bootstrap produced no valid pillars; aborting FX run",
            {"currency": pair.foreign, "snapshot": str(for_path)},
        )
        return _print_exit(dg, args)

    # ------------------------------------------------------------------
    # Build FX forward curve (parity check inside M-105 emits FX_PARITY_MISMATCH).
    # ------------------------------------------------------------------
    try:
        fx_forward = build_fx_forward_curve(
            fx_market, dom_curve, for_curve, fx_conv, dg
        )
    except ValueError as e:
        dg.error(
            "FX_FORWARD_BUILD_FAIL",
            f"FX forward curve construction failed: {e}",
            {"pair": pair.code},
        )
        return _print_exit(dg, args)

    # ------------------------------------------------------------------
    # Build basis curve when XCCY_BASIS quotes are present (optional).
    # ------------------------------------------------------------------
    if fx_market.basis_quotes:
        try:
            build_cross_basis_curve(
                fx_market, dom_curve, for_curve, fx_forward, fx_conv, dg
            )
        except ValueError as e:
            dg.error(
                "FX_BASIS_BUILD_FAIL",
                f"cross-currency basis curve construction failed: {e}",
                {"pair": pair.code},
            )
            return _print_exit(dg, args)

    return _print_exit(dg, args)


def run_forward(args: argparse.Namespace) -> int:
    """Print the forward rate between ``args.start`` and ``args.end`` from the
    most-recent persisted Summary."""
    summary_path = Path(
        getattr(args, "summary_path", None)
        or Path(getattr(args, "output_root", ".")) / _SUMMARY_REL_PATH
    )
    if not summary_path.exists():
        print(f"error: summary not found at {summary_path}", file=sys.stderr)
        return 2
    try:
        summary = Summary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover — Pydantic parses unstructured JSON
        print(f"error: failed to parse {summary_path}: {e}", file=sys.stderr)
        return 2

    curve = _curve_from_summary(summary)
    try:
        rate = curve.forward(args.start, args.end)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"forward({args.start.isoformat()}, {args.end.isoformat()}) = {rate:.6f}")
    return 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_config_snapshot(
    conv: Conventions, args: argparse.Namespace, scenario: ScenarioResult
) -> ConfigSnapshot:
    """Project pipeline configuration into the persisted :class:`ConfigSnapshot`."""
    ccy_conv = conv.ois_conventions[_DEFAULT_CURRENCY]
    return ConfigSnapshot(
        day_count=ccy_conv.day_count.value,
        interpolation=args.interp,
        business_day_convention=ccy_conv.business_day_convention.value,
        bullet_until=ccy_conv.payment.bullet_until,
        calendar=ccy_conv.calendar,
        band_bps=scenario.band_bps,
        horizon_business_days=scenario.horizon_business_days,
        initial_tlref=scenario.initial_tlref,
    )


def _curve_from_summary(summary: Summary) -> OISCurve:
    """Reconstruct an :class:`OISCurve` from a persisted :class:`Summary` (O5).

    Pillar ``start_date`` is set to ``summary.valuation_date`` per G10 (T+0 spot
    convention) since the Summary does not persist it (constant, redundant).
    Interpolation scheme is read from ``summary.config_snapshot.interpolation``.
    """
    val = summary.valuation_date
    interp = summary.config_snapshot.interpolation
    if interp not in ("log_linear_df", "linear_zero"):
        raise ValueError(f"summary has unknown interpolation scheme: {interp!r}")
    pillars = tuple(
        Pillar(
            tenor_code=p.tenor_code,
            tenor_days=p.tenor_days,
            start_date=val,
            end_date=p.end_date,
            rate=p.rate,
            discount_factor=p.discount_factor,
        )
        for p in summary.pillars
    )
    ladder = tuple((f.label, f.start_date, f.end_date) for f in summary.forward_ladder)
    return OISCurve(
        valuation_date=val,
        day_count=DayCount(summary.config_snapshot.day_count),
        interp=interp,  # type: ignore[arg-type]
        pillars_tuple=pillars,
        forward_ladder_dates=ladder,
    )


def _parse_overrides(items: Iterable[str]) -> dict[str, Any]:
    """Parse repeated ``KEY=VALUE`` CLI tokens into a dotted-key override dict."""
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--convention-override must be KEY=VALUE (got {item!r})")
        key, _, value = item.partition("=")
        out[key.strip()] = value.strip()
    return out


def _parse_currency_pair(pair_code: str) -> CurrencyPair:
    """Parse a 6-char ISO pair code into a :class:`CurrencyPair`.

    Convention (street): first 3 chars = foreign (base), last 3 = domestic (term).
    USDTRY ⇒ foreign=USD, domestic=TRY.
    """
    if len(pair_code) != 6 or not pair_code.isalpha():
        raise ValueError(
            f"pair must be a 6-letter ISO code (got {pair_code!r}); examples: USDTRY, EURTRY"
        )
    return CurrencyPair(
        domestic=pair_code[3:].upper(),
        foreign=pair_code[:3].upper(),
        code=pair_code.upper(),
    )


def _resolve_fx_paths(
    args: argparse.Namespace, pair: CurrencyPair, as_of: date
) -> tuple[Path, Path, Path]:
    """Resolve (fx_snapshot, domestic_snapshot, foreign_snapshot) paths.

    Auto-derives from ``as_of`` + ``pair`` when the caller does not override:

    * fx_snapshot       → ``data/fx_snapshots/fx_snapshot_<PAIR>_YYYYMMDD.csv``
    * domestic_snapshot → ``data/snapshots/snapshot_YYYYMMDD.csv`` (TRY default
      keeps V1 layout; non-TRY domestics get the prefixed form).
    * foreign_snapshot  → ``data/snapshots/<ccy_lower>_ois_YYYYMMDD.csv``
    """
    yyyymmdd = as_of.strftime("%Y%m%d")
    fx_path = (
        Path(args.fx_snapshot)
        if getattr(args, "fx_snapshot", None) is not None
        else _FX_SNAPSHOT_DIR / f"fx_snapshot_{pair.code}_{yyyymmdd}.csv"
    )
    dom_path = (
        Path(args.domestic_snapshot)
        if getattr(args, "domestic_snapshot", None) is not None
        else (
            _OIS_SNAPSHOT_DIR / f"snapshot_{yyyymmdd}.csv"
            if pair.domestic == "TRY"
            else _OIS_SNAPSHOT_DIR / f"{pair.domestic.lower()}_ois_{yyyymmdd}.csv"
        )
    )
    for_path = (
        Path(args.foreign_snapshot)
        if getattr(args, "foreign_snapshot", None) is not None
        else _OIS_SNAPSHOT_DIR / f"{pair.foreign.lower()}_ois_{yyyymmdd}.csv"
    )
    return fx_path, dom_path, for_path


def _parse_as_of_from_filename(path: Path) -> date | None:
    """Extract YYYYMMDD from a filename stem suffix; return None on no match."""
    m = _DATE_SUFFIX_RE.search(path.stem)
    if not m:
        return None
    s = m.group(1)
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _snapshot_dates_consistent(
    dg: DiagnosticsCollector, as_of: date, *paths: Path
) -> bool:
    """Check that filename-encoded as_of dates (when parseable) match ``as_of``.

    Files whose names don't encode a date are skipped (trust the caller). Any
    filename-encoded date that disagrees with ``as_of`` emits
    ``FX_SNAPSHOT_DATE_MISMATCH`` ERROR and returns False.
    """
    mismatches: list[tuple[str, str]] = []
    for p in paths:
        parsed = _parse_as_of_from_filename(p)
        if parsed is not None and parsed != as_of:
            mismatches.append((p.name, parsed.isoformat()))
    if not mismatches:
        return True
    dg.error(
        "FX_SNAPSHOT_DATE_MISMATCH",
        (
            f"snapshot filename(s) carry as_of dates that disagree with --as-of "
            f"{as_of.isoformat()}: {mismatches}"
        ),
        {"as_of": as_of.isoformat(), "mismatches": mismatches},
    )
    return False


def _print_exit(
    dg: DiagnosticsCollector,
    args: argparse.Namespace,
    *,
    summary_msg: str | None = None,
) -> int:
    """Emit a compact diagnostics summary to stdout/stderr and return the exit code."""
    quiet = getattr(args, "quiet", False)
    records = dg.to_list()
    warn_count = sum(1 for d in records if d.severity is Severity.WARN)
    err_count = sum(1 for d in records if d.severity is Severity.ERROR)
    code = dg.exit_code()

    if not quiet:
        if records:
            print(f"\ndiagnostics: {warn_count} WARN, {err_count} ERROR")
            for d in records:
                tag = "ERROR" if d.severity is Severity.ERROR else "WARN "
                print(f"  [{tag}] {d.code}: {d.message}")
        if summary_msg:
            print(summary_msg)
        if code == 0:
            print("exit 0 (no errors)")
        else:
            print(f"exit {code} (errors present)", file=sys.stderr)
    return code


__all__ = [
    "run_bootstrap",
    "run_diagnose",
    "run_forward",
    "run_fx_diagnose",
]
