"""rates.app — pipeline orchestrator (M-014).

Three entry points consumed by :mod:`rates.cli`, each returning a Unix-style exit
code (0 on WARN-only, 2 on any ERROR):

* :func:`run_bootstrap` — full pipeline: load market + MPC → bootstrap → scenario
  → compare → persist summary JSON + Parquet partition → pretty stdout.
* :func:`run_diagnose`  — load market + bootstrap only; print diagnostics; no
  outputs written.
* :func:`run_forward`   — read latest summary JSON; reconstruct curve; print
  ad-hoc forward rate.

Sequences mirror ``docs/architecture.md`` §6.

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
import sys
from collections.abc import Iterable
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
from rates.io.market import CsvProvider, CsvSchemaError, EmptyDataError
from rates.io.mpc import MPCScheduleError, MPCSchemaError, load_mpc_path
from rates.io.persistence import scenario_to_table, write_partition, write_summary
from rates.io.schemas import ConfigSnapshot, Summary

_DEFAULT_CURRENCY: str = "TRY"
_SUMMARY_REL_PATH: Path = Path("data/latest/try_ois_summary.json")
_PARTITION_ROOT_REL: Path = Path("data/curves/try_ois")


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
]
