"""rates.app — pipeline orchestrator (M-014, M-111).

V1 entry points consumed by :mod:`rates.cli`, each returning a Unix-style exit
code (0 on WARN-only, 2 on any ERROR):

* :func:`run_bootstrap` — full pipeline: load market + MPC → bootstrap → scenario
  → compare → persist summary JSON + Parquet partition → pretty stdout.
* :func:`run_diagnose`  — load market + bootstrap only; print diagnostics; no
  outputs written.
* :func:`run_forward`   — read latest summary JSON; reconstruct curve; print
  ad-hoc forward rate.

v0.3.0 FX entry points (M-111):

* :func:`run_fx_diagnose` — full FX orchestration through the parity check:
  load FX + dual OIS snapshots → bootstrap both OIS curves → build FX forward
  curve (with parity check inside M-105) → build basis curve (if XCCY_BASIS
  rows present). No persistence, no pricing. Enforces the
  ``FX_SNAPSHOT_DATE_MISMATCH`` cross-CSV invariant from
  ``docs/fx-io-architecture.md`` §5.
* :func:`run_fx_bootstrap` — same pipeline as ``run_fx_diagnose`` plus
  persistence: writes ``<pair>_fx_summary.json`` under ``data/latest/`` and
  Hive-partitioned forward + basis Parquet under ``data/fx_curves/``.
* :func:`run_fx_price_outright` / :func:`run_fx_price_swap` /
  :func:`run_fx_price_xccy` — ad-hoc pricing from persisted curves. Each verb
  loads the latest FX summary, reconstructs the curves, validates value-date
  inputs against the joint calendar, and delegates to ``rates.fx.pricer``.
  Per architecture D11, ``--tenor`` is resolved to a value-date first so both
  flags share a single pricing code path.

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
import math
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
from rates.fx.basis_curve import BasisPillar, CrossCurrencyBasisCurve
from rates.fx.bootstrap_basis import (
    FXBootstrapNonConvergentError,
    XccyBootstrapError,
    build_cross_basis_curve,
)
from rates.fx.bootstrap_forward import PARITY_MISMATCH_BPS, build_fx_forward_curve
from rates.fx.conventions import FXConvention, FXConventions
from rates.fx.forward_curve import FXForwardCurve, FXForwardPillar
from rates.fx.pricer import (
    price_fx_swap,
    price_outright_forward,
    price_xccy_basis_swap,
)
from rates.fx.types import (
    FX_PARITY_MISMATCH,
    FX_PRICE_SCHEMA_TOO_OLD,
    CrossCurrencyBasisQuote,
    CurrencyPair,
)
from rates.io.fx_market import (
    FxCsvProvider,
    FXCsvSchemaError,
    FXEmptyDataError,
)
from rates.io.market import CsvProvider, CsvSchemaError, EmptyDataError
from rates.io.mpc import MPCScheduleError, MPCSchemaError, load_mpc_path
from rates.io.persistence import (
    scenario_to_table,
    write_fx_curve_partition,
    write_fx_summary,
    write_partition,
    write_summary,
)
from rates.io.schemas import (
    FX_SCHEMA_VERSION,
    ConfigSnapshot,
    FXOisMeta,
    FXParityCheckRow,
    FXSummary,
    PillarOut,
    Summary,
)

_DEFAULT_CURRENCY: str = "TRY"
_SUMMARY_REL_PATH: Path = Path("data/latest/try_ois_summary.json")
_PARTITION_ROOT_REL: Path = Path("data/curves/try_ois")

#: Default root for FX snapshot CSVs when not overridden.
_FX_SNAPSHOT_DIR: Path = Path("data/fx_snapshots")
#: Default root for OIS snapshot CSVs (V1 path).
_OIS_SNAPSHOT_DIR: Path = Path("data/snapshots")
#: Filename suffix pattern carrying YYYYMMDD as_of.
_DATE_SUFFIX_RE = re.compile(r"_(\d{8})$")

#: FX persistence layout (consumed by both writers and pricing-side readers).
_FX_LATEST_DIR: Path = Path("data/latest")
_FX_CURVES_DIR: Path = Path("data/fx_curves")

#: Tenor → calendar-day offset for CLI tenor convenience (C-103 D11).
#: Anything outside this set must use --value-date. Richer tenor calculus
#: (e.g. "3M+2D", IMM dates) is a v0.4 work item.
_TENOR_DAYS: dict[str, int] = {
    "1M": 30,
    "3M": 91,
    "6M": 183,
    "1Y": 365,
    "2Y": 730,
}


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
    _ = _run_fx_pipeline(args, dg)
    return _print_exit(dg, args)


def run_fx_bootstrap(args: argparse.Namespace) -> int:
    """Run the ``rates fx bootstrap`` flow: full FX pipeline + persistence.

    Same orchestration as :func:`run_fx_diagnose`, but on success writes:

    * ``{output_root}/data/latest/{pair_lower}_fx_summary.json`` — FXSummary JSON
      (M-110), atomic temp-file rename, includes pillar tables, parity-check
      rows (derived from M-105 diagnostics), and the full diagnostics envelope.
    * ``{output_root}/data/fx_curves/{pair_lower}/forward/date=YYYY-MM-DD/data.parquet``
      and (when basis quotes present) ``.../basis/date=YYYY-MM-DD/data.parquet``
      — Hive-partitioned Parquet curve dumps.

    Args:
        args: argparse Namespace — same fields as :func:`run_fx_diagnose` plus
              ``output_root`` (Path).

    Returns:
        Unix exit code — 0 on WARN-only or clean, 2 on any ERROR.
    """
    dg = DiagnosticsCollector()
    result = _run_fx_pipeline(args, dg)
    if result is None:
        return _print_exit(dg, args)

    pair, fx_conv, fx_forward, basis, as_of, dom_curve, for_curve = result
    parity_checks = _parity_checks_from_diagnostics(dg)
    summary = FXSummary.from_domain(
        pair=pair,
        valuation_date=as_of,
        forward=fx_forward,
        basis=basis,
        parity_checks=parity_checks,
        conv=fx_conv,
        diagnostics=dg.to_list(),
        dom_ois=dom_curve,
        for_ois=for_curve,
        # The strip's reprice assert pins |NPV(b_n*)| < 1e-9, so each persisted
        # residual is ~0 by construction; the 0.0 default is faithful. Surfacing
        # the exact per-pillar value would require extending the strip's return
        # (C-104' freezes it to CrossCurrencyBasisCurve), so it stays a no-op here.
        strip_residuals={},
    )

    output_root = Path(getattr(args, "output_root", Path(".")))
    write_fx_summary(summary, output_root / _FX_LATEST_DIR / _fx_summary_filename(pair))
    write_fx_curve_partition(fx_forward, basis, output_root / _FX_CURVES_DIR, as_of, pair)

    return _print_exit(dg, args)


# ---------------------------------------------------------------------------
# FX pricing entry points (M-111, C-103)
# ---------------------------------------------------------------------------


def run_fx_price_outright(args: argparse.Namespace) -> int:
    """Price an outright forward at ``args.tenor_code`` or ``args.value_date``.

    Reads the latest persisted FXSummary, reconstructs the forward curve, and
    delegates to :func:`rates.fx.pricer.price_outright_forward`. Per C-103
    ``--tenor`` resolves to a value-date first so both flags share one pricing
    code path (D11). Stdout format::

        outright(<pair> <ISO date>) = <rate:.6f>

    Args:
        args: Namespace — shared pricing fields (see
              :func:`_load_fx_pricing_context`) plus exactly one of
              ``tenor_code`` / ``value_date`` (mutex enforced at the argparse
              layer; defensively re-checked here).

    Returns:
        Unix exit code — 0 on success, 2 on any ERROR.
    """
    dg = DiagnosticsCollector()
    ctx = _load_fx_pricing_context(args, dg, require_basis=False)
    if ctx is None:
        return _print_exit(dg, args)
    pair, _fx_conv, calendars, fx_forward, _basis, _summary = ctx

    value_date = _resolve_outright_value_date(args, fx_forward.spot_date, calendars, dg)
    if value_date is None:
        return _print_exit(dg, args)

    last = fx_forward.pillars_tuple[-1].settle_date
    if not _validate_pricing_value_date(
        value_date, fx_forward.spot_date, last, calendars, dg,
    ):
        return _print_exit(dg, args)

    try:
        rate = price_outright_forward(fx_forward, value_date)
    except ValueError:
        # Past last pillar — M-103 hard cap; WARN already recorded in
        # validation. Clip to last-pillar forward per architecture D11
        # ("price still returned"). V3 calibration will lift this.
        rate = fx_forward.pillars_tuple[-1].forward_rate
    if not getattr(args, "quiet", False):
        print(f"outright({pair.code} {value_date.isoformat()}) = {rate:.6f}")
    return _print_exit(dg, args)


def run_fx_price_swap(args: argparse.Namespace) -> int:
    """Price an FX swap with near + far legs (each tenor- or value-date-driven).

    Stdout format::

        fx_swap(<pair>) near=<rate:.6f>@<ISO date>  far=<rate:.6f>@<ISO date>  points=<diff:.6f>

    Args:
        args: Namespace — shared pricing fields plus each leg's mutex pair:
              ``(near_tenor | near_value_date)`` AND
              ``(far_tenor | far_value_date)``.

    Returns:
        Unix exit code — 0 on success, 2 on any ERROR.
    """
    dg = DiagnosticsCollector()
    ctx = _load_fx_pricing_context(args, dg, require_basis=False)
    if ctx is None:
        return _print_exit(dg, args)
    pair, _fx_conv, calendars, fx_forward, _basis, _summary = ctx

    near_date = _resolve_swap_leg_value_date(args, "near", fx_forward.spot_date, calendars, dg)
    far_date = _resolve_swap_leg_value_date(args, "far", fx_forward.spot_date, calendars, dg)
    if near_date is None or far_date is None:
        return _print_exit(dg, args)

    last = fx_forward.pillars_tuple[-1].settle_date
    if not _validate_pricing_value_date(
        near_date, fx_forward.spot_date, last, calendars, dg
    ):
        return _print_exit(dg, args)
    if not _validate_pricing_value_date(
        far_date, fx_forward.spot_date, last, calendars, dg
    ):
        return _print_exit(dg, args)

    if near_date >= far_date:
        dg.error(
            "FX_PRICE_SWAP_LEG_ORDER",
            f"swap requires near < far (got near={near_date} far={far_date})",
            {"near_date": near_date.isoformat(), "far_date": far_date.isoformat()},
        )
        return _print_exit(dg, args)

    try:
        pricing = price_fx_swap(fx_forward, near_date, far_date)
        near_rate, far_rate, swap_points = (
            pricing.near_rate, pricing.far_rate, pricing.swap_points,
        )
    except ValueError:
        # Either leg past last pillar — M-103 hard cap; WARN already recorded.
        # Clip each leg to the last pillar's rate per architecture D11.
        last_rate = fx_forward.pillars_tuple[-1].forward_rate
        near_rate = (
            fx_forward.forward_at(near_date) if near_date <= last else last_rate
        )
        far_rate = (
            fx_forward.forward_at(far_date) if far_date <= last else last_rate
        )
        swap_points = far_rate - near_rate
    if not getattr(args, "quiet", False):
        print(
            f"fx_swap({pair.code}) "
            f"near={near_rate:.6f}@{near_date.isoformat()}  "
            f"far={far_rate:.6f}@{far_date.isoformat()}  "
            f"points={swap_points:.6f}"
        )
    return _print_exit(dg, args)


def run_fx_price_xccy(args: argparse.Namespace) -> int:
    """Price a cross-currency basis swap at ``args.tenor_code``.

    XCCY basis swaps are tenor-keyed in v0.3.0 (value-date variant deferred).
    Requires a persisted basis curve — aborts with ``FX_PRICE_BASIS_MISSING``
    if the snapshot did not include XCCY_BASIS rows.

    Stdout format::

        xccy_basis(<pair> <tenor>) = <spread:+.4f> bps (maturity <ISO date>)

    Args:
        args: Namespace — shared pricing fields plus ``tenor_code`` (str).

    Returns:
        Unix exit code — 0 on success, 2 on any ERROR.
    """
    dg = DiagnosticsCollector()
    ctx = _load_fx_pricing_context(args, dg, require_basis=True)
    if ctx is None:
        return _print_exit(dg, args)
    pair, _fx_conv, _calendars, _fx_forward, basis, summary = ctx
    assert basis is not None  # require_basis=True guarantees this

    tenor_code = getattr(args, "tenor_code", None)
    if not isinstance(tenor_code, str) or not tenor_code:
        dg.error(
            "FX_PRICE_TENOR_REQUIRED",
            "price-xccy requires --tenor TENOR_CODE",
            {"tenor_code": repr(tenor_code)},
        )
        return _print_exit(dg, args)

    # XCCY basis swaps are tenor-keyed (C-103) — resolve the tenor against the
    # basis curve's persisted pillar table rather than the CLI calendar-day
    # table, because basis pillars carry the market-convention maturity dates
    # (e.g. USDTRY 1Y = 360 days) that may not match the generic CLI offsets.
    maturity: date | None = next(
        (p.maturity_date for p in basis.pillars_tuple if p.tenor_code == tenor_code),
        None,
    )
    if maturity is None:
        dg.error(
            "FX_PRICE_UNKNOWN_TENOR",
            (
                f"tenor {tenor_code!r} not in persisted basis pillars; available: "
                f"{[p.tenor_code for p in basis.pillars_tuple]}"
            ),
            {
                "tenor_code": tenor_code,
                "available": [p.tenor_code for p in basis.pillars_tuple],
            },
        )
        return _print_exit(dg, args)

    # C-110: reconstruct the dual OIS curves from the persisted summary and pass
    # the real curves to the pricer. V0.4's interpolating pricer does not consume
    # them (OQ-501), but they replace the former typed-None placeholder and keep
    # the call MtM-ready (A-8). A v1 summary lacks embedded OIS pillars → abort.
    if summary.schema_version != FX_SCHEMA_VERSION:
        dg.error(
            FX_PRICE_SCHEMA_TOO_OLD,
            (
                f"persisted FX summary is schema_version {summary.schema_version}; "
                f"xccy pricing needs v{FX_SCHEMA_VERSION} (embedded OIS pillars). "
                "Re-run `rates fx bootstrap` to regenerate."
            ),
            {"schema_version": summary.schema_version, "required": FX_SCHEMA_VERSION},
        )
        return _print_exit(dg, args)
    try:
        dom_ois = _dom_ois_from_summary(summary)
        for_ois = _for_ois_from_summary(summary)
    except ValueError as e:
        dg.error(
            "FX_PRICE_OIS_RECONSTRUCT_FAIL",
            f"failed to reconstruct OIS curves from persisted summary: {e}",
            {"pair": pair.code},
        )
        return _print_exit(dg, args)

    spread = price_xccy_basis_swap(basis, dom_ois, for_ois, maturity)

    if not getattr(args, "quiet", False):
        print(
            f"xccy_basis({pair.code} {tenor_code}) = {spread:+.4f} bps "
            f"(maturity {maturity.isoformat()})"
        )
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


def _ois_from_summary_pillars(pillars: list[PillarOut], meta: FXOisMeta) -> OISCurve:
    """Reconstruct an :class:`OISCurve` from persisted OIS pillars + meta (C-110).

    Mirrors :func:`_curve_from_summary` for the FX layer: ``PillarOut`` carries no
    ``start_date`` (constant per G10), so each pillar's ``start_date`` is set to
    ``meta.valuation_date``. ``forward_ladder_dates`` is reconstructed empty —
    the FX pricing path only calls ``df_at`` and never the forward ladder, and
    :meth:`OISCurve.__post_init__` imposes no ladder invariant (OQ-502).

    Raises:
        ValueError: When ``meta.interpolation`` is not a known scheme (mirrors
            :func:`_curve_from_summary`).
    """
    interp = meta.interpolation
    if interp not in ("log_linear_df", "linear_zero"):
        raise ValueError(
            f"FXSummary OIS meta has unknown interpolation scheme: {interp!r}"
        )
    val = meta.valuation_date
    pillars_tuple = tuple(
        Pillar(
            tenor_code=p.tenor_code,
            tenor_days=p.tenor_days,
            start_date=val,
            end_date=p.end_date,
            rate=p.rate,
            discount_factor=p.discount_factor,
        )
        for p in pillars
    )
    return OISCurve(
        valuation_date=val,
        day_count=DayCount(meta.day_count),
        interp=interp,  # type: ignore[arg-type]
        pillars_tuple=pillars_tuple,
        forward_ladder_dates=(),
    )


def _dom_ois_from_summary(summary: FXSummary) -> OISCurve:
    """Reconstruct the domestic OIS curve from an FXSummary v2 (C-110).

    Callers must verify ``summary.schema_version == FX_SCHEMA_VERSION`` first
    (a v1 summary lacks ``dom_ois_pillars`` — emit ``FX_PRICE_SCHEMA_TOO_OLD``).
    """
    return _ois_from_summary_pillars(summary.dom_ois_pillars, summary.dom_ois_meta)


def _for_ois_from_summary(summary: FXSummary) -> OISCurve:
    """Reconstruct the foreign OIS curve from an FXSummary v2 (C-110)."""
    return _ois_from_summary_pillars(summary.for_ois_pillars, summary.for_ois_meta)


# ---------------------------------------------------------------------------
# FX pipeline + pricing helpers (M-111, v0.3.0)
# ---------------------------------------------------------------------------


def _run_fx_pipeline(
    args: argparse.Namespace,
    dg: DiagnosticsCollector,
) -> (
    tuple[
        CurrencyPair,
        FXConvention,
        FXForwardCurve,
        CrossCurrencyBasisCurve | None,
        date,
        OISCurve,
        OISCurve,
    ]
    | None
):
    """Shared FX orchestration: conventions → snapshots → bootstrap → curves.

    Backs both :func:`run_fx_diagnose` (which discards the result) and
    :func:`run_fx_bootstrap` (which persists it). On any abort path the caller
    receives ``None``; the relevant ERROR diagnostic is already on ``dg`` so
    the caller's ``_print_exit`` produces the correct stdout + exit code.

    Returns:
        ``(pair, fx_convention, fx_forward_curve, basis_curve_or_None,
        as_of_date, dom_ois_curve, for_ois_curve)`` on success; ``None`` on any
        failure. The two OIS curves are surfaced so the persistence call site can
        embed them into FXSummary v2 (D-16, A-8).
    """
    try:
        conv = Conventions.load(
            Path(args.conventions_yaml),
            overrides=_parse_overrides(getattr(args, "convention_override", None) or []),
        )
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return None

    try:
        fx_convs = FXConventions.load(Path(args.conventions_yaml))
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_FX_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return None

    pair_code = str(args.pair)
    pair = _parse_currency_pair(pair_code)
    if pair_code not in fx_convs.by_pair:
        dg.error(
            "FX_PAIR_UNKNOWN",
            f"pair {pair_code!r} not in fx_conventions; known: {sorted(fx_convs.by_pair)}",
            {"requested": pair_code, "known": sorted(fx_convs.by_pair)},
        )
        return None
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
        return None

    as_of: date = args.as_of
    fx_path, dom_path, for_path = _resolve_fx_paths(args, pair, as_of)
    if not _snapshot_dates_consistent(dg, as_of, fx_path, dom_path, for_path):
        return None

    dom_cal = HolidayCalendar.for_currency(conv.ois_conventions[pair.domestic].calendar)
    for_cal = HolidayCalendar.for_currency(conv.ois_conventions[pair.foreign].calendar)
    joint_calendars: tuple[HolidayCalendar, ...] = (dom_cal, for_cal)

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
        return None

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
        return None

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
        return None

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
        return None

    basis: CrossCurrencyBasisCurve | None = None
    if fx_market.basis_quotes:
        try:
            basis = build_cross_basis_curve(
                fx_market, dom_curve, for_curve, fx_forward, fx_conv, dom_cal, for_cal, dg
            )
        except (ValueError, XccyBootstrapError, FXBootstrapNonConvergentError) as e:
            # The strip emits its own specific ERROR (e.g. FX_XCCY_FORWARD_COVERAGE,
            # FX_XCCY_REPRICE_FAIL); add build-fail context and abort cleanly (exit 2)
            # rather than letting the RuntimeError escape the orchestrator.
            dg.error(
                "FX_BASIS_BUILD_FAIL",
                f"cross-currency basis curve construction failed: {e}",
                {"pair": pair.code},
            )
            return None

    if basis is not None:
        # A-6 / OQ-503: the basis-aware FX_PARITY_MISMATCH gate runs here, once
        # both the forward curve and the stripped basis exist. M-105 no longer
        # emits the old pure-CIP WARN (no double-reporting).
        _warn_basis_parity_mismatches(basis, fx_market.basis_quotes, dg)

    return pair, fx_conv, fx_forward, basis, as_of, dom_curve, for_curve


def _fx_summary_filename(pair: CurrencyPair) -> str:
    """Canonical FX summary filename for ``pair`` (e.g. ``usdtry_fx_summary.json``)."""
    return f"{pair.code.lower()}_fx_summary.json"


def _warn_basis_parity_mismatches(
    basis: CrossCurrencyBasisCurve,
    quotes: tuple[CrossCurrencyBasisQuote, ...],
    dg: DiagnosticsCollector,
) -> None:
    """Basis-aware ``FX_PARITY_MISMATCH`` gate (A-6 / OQ-503, §7).

    Reconciles the two market sources once both the forward curve and the
    stripped basis exist: for each calibrated pillar it compares the
    **forward-implied** basis ``b_n`` (the spread the strip derived from the FX
    forwards, §5.4) against the **quoted** XCCY_BASIS spread at the same tenor,
    and emits one WARN per pillar whose ``|b_n - quoted|`` exceeds
    :data:`PARITY_MISMATCH_BPS` (1.0 bps). The comparison is in bps directly
    (not bps-of-spot): both sides are basis spreads.

    This is **not** a forward-vs-(CIP+stripped) check — that would be vacuous,
    since the stripped basis is *defined* from the forwards. Pillars are matched
    to quotes by ``tenor_code`` (the strip seeds the pillar grid from the quote
    tenors, so they align); a quote with no surviving pillar (skipped as
    non-finite/duplicate) is simply never looked up.
    """
    quoted_by_tenor: dict[str, float] = {q.tenor_code: q.spread_bps for q in quotes}
    for p in basis.pillars_tuple:
        quoted = quoted_by_tenor.get(p.tenor_code)
        if quoted is None or not math.isfinite(quoted):
            continue
        diff = p.spread_bps - quoted
        if abs(diff) > PARITY_MISMATCH_BPS:
            dg.warn(
                FX_PARITY_MISMATCH,
                (
                    f"{p.tenor_code}: forward-implied basis {p.spread_bps:+.2f} bps "
                    f"vs quoted {quoted:+.2f} bps (diff {diff:+.2f} bps)"
                ),
                {
                    "tenor_code": p.tenor_code,
                    "settle_date": p.maturity_date.isoformat(),
                    "quoted_bps": quoted,
                    "forward_implied_bps": p.spread_bps,
                    "diff_bps": diff,
                },
            )


def _parity_checks_from_diagnostics(
    dg: DiagnosticsCollector,
) -> list[FXParityCheckRow]:
    """Project basis-aware ``FX_PARITY_MISMATCH`` WARNs into FXParityCheckRow rows.

    The relocated gate (:func:`_warn_basis_parity_mismatches`) emits one WARN per
    basis pillar with context ``{tenor_code, settle_date, quoted_bps,
    forward_implied_bps, diff_bps}``.

    The persisted :class:`FXParityCheckRow` (v2, frozen in Phase 3) keeps its
    forward-named float columns; rather than bump the schema, they are reused and
    **reinterpreted** for the basis-aware reconciliation (decision: keep columns,
    document the meaning):

    * ``quoted_forward``    → quoted XCCY_BASIS spread (bps)
    * ``parity_forward``    → forward-implied basis ``b_n`` (bps)
    * ``diff_bps_of_spot``  → ``b_n - quoted`` (bps)

    ``settle_date`` is the basis pillar maturity, carried in the WARN context.
    The authoritative human-readable record remains the WARN in
    ``summary.diagnostics``; this is its typed projection.
    """
    rows: list[FXParityCheckRow] = []
    for d in dg.to_list():
        if d.code != FX_PARITY_MISMATCH:
            continue
        ctx = d.context
        tenor_code = ctx.get("tenor_code")
        settle_raw = ctx.get("settle_date")
        if not isinstance(tenor_code, str) or not isinstance(settle_raw, str):
            continue
        try:
            rows.append(
                FXParityCheckRow(
                    tenor_code=tenor_code,
                    settle_date=date.fromisoformat(settle_raw),
                    quoted_forward=float(ctx["quoted_bps"]),
                    parity_forward=float(ctx["forward_implied_bps"]),
                    diff_bps_of_spot=float(ctx["diff_bps"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def _fx_forward_from_summary(summary: FXSummary) -> FXForwardCurve:
    """Reconstruct an :class:`FXForwardCurve` from a persisted :class:`FXSummary`.

    Mirrors :func:`_curve_from_summary` for V1 OIS — the conversion lives here
    rather than on :class:`FXForwardCurve` to avoid pulling ``rates.io`` into
    ``rates.fx`` and violating the no-cycles layering rule.
    """
    pillars = tuple(
        FXForwardPillar(
            tenor_code=p.tenor_code,
            tenor_days=p.tenor_days,
            settle_date=p.settle_date,
            forward_rate=p.forward_rate,
        )
        for p in summary.forward_pillars
    )
    return FXForwardCurve(
        pair_code=summary.pair_code,
        spot_date=summary.spot_date,
        spot_rate=summary.spot_rate,
        pillars_tuple=pillars,
    )


def _fx_basis_from_summary(summary: FXSummary) -> CrossCurrencyBasisCurve | None:
    """Reconstruct a :class:`CrossCurrencyBasisCurve` from a persisted FXSummary.

    Returns ``None`` when the snapshot carried no XCCY_BASIS rows (basis_pillars
    is empty). ``quoted_on_foreign`` is constant across pillars per the curve
    invariant — read it off the first row.
    """
    if not summary.basis_pillars:
        return None
    pillars = tuple(
        BasisPillar(
            tenor_code=p.tenor_code,
            tenor_days=p.tenor_days,
            maturity_date=p.maturity_date,
            spread_bps=p.spread_bps,
        )
        for p in summary.basis_pillars
    )
    return CrossCurrencyBasisCurve(
        pair_code=summary.pair_code,
        spot_date=summary.spot_date,
        pillars_tuple=pillars,
        quoted_on_foreign=summary.basis_pillars[0].quoted_on_foreign,
    )


def _load_fx_pricing_context(
    args: argparse.Namespace,
    dg: DiagnosticsCollector,
    *,
    require_basis: bool,
) -> (
    tuple[
        CurrencyPair,
        FXConvention,
        tuple[HolidayCalendar, ...],
        FXForwardCurve,
        CrossCurrencyBasisCurve | None,
        FXSummary,
    ]
    | None
):
    """Load conventions, joint calendars, and curves from the persisted FXSummary.

    Args:
        args:           Namespace carrying ``conventions_yaml``, ``pair``,
                        ``output_root``, ``convention_override``.
        dg:             Diagnostics collector — receives ERROR rows on every
                        abort path so the caller's ``_print_exit`` produces
                        the right exit code.
        require_basis:  When True, abort with ``FX_PRICE_BASIS_MISSING`` if
                        the persisted summary contains no basis pillars.

    Returns:
        ``(pair, fx_convention, joint_calendars, fx_forward_curve, basis_or_None,
        summary)`` on success; ``None`` on any failure. The raw ``summary`` is
        surfaced so the xccy verb can check ``schema_version`` and reconstruct
        the dual OIS curves (C-110).
    """
    try:
        conv = Conventions.load(
            Path(args.conventions_yaml),
            overrides=_parse_overrides(getattr(args, "convention_override", None) or []),
        )
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return None
    try:
        fx_convs = FXConventions.load(Path(args.conventions_yaml))
    except (FileNotFoundError, ValueError) as e:
        dg.error("APP_FX_CONVENTIONS_LOAD_FAIL", str(e), {"path": str(args.conventions_yaml)})
        return None

    pair_code = str(args.pair)
    pair = _parse_currency_pair(pair_code)
    if pair_code not in fx_convs.by_pair:
        dg.error(
            "FX_PAIR_UNKNOWN",
            f"pair {pair_code!r} not in fx_conventions; known: {sorted(fx_convs.by_pair)}",
            {"requested": pair_code, "known": sorted(fx_convs.by_pair)},
        )
        return None
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
        return None

    dom_cal = HolidayCalendar.for_currency(conv.ois_conventions[pair.domestic].calendar)
    for_cal = HolidayCalendar.for_currency(conv.ois_conventions[pair.foreign].calendar)
    calendars: tuple[HolidayCalendar, ...] = (dom_cal, for_cal)

    output_root = Path(getattr(args, "output_root", Path(".")))
    summary_path = output_root / _FX_LATEST_DIR / _fx_summary_filename(pair)
    if not summary_path.exists():
        dg.error(
            "FX_PRICE_SUMMARY_NOT_FOUND",
            f"persisted FX summary not found at {summary_path}; run `rates fx bootstrap` first",
            {"path": str(summary_path), "pair": pair.code},
        )
        return None
    try:
        summary = FXSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    except ValueError as e:
        dg.error(
            "FX_PRICE_SUMMARY_PARSE_FAIL",
            f"failed to parse {summary_path}: {e}",
            {"path": str(summary_path)},
        )
        return None

    if summary.pair_code != pair.code:
        dg.error(
            "FX_PRICE_SUMMARY_PAIR_MISMATCH",
            (
                f"summary at {summary_path} is for {summary.pair_code!r}, "
                f"but --pair requested {pair.code!r}"
            ),
            {"summary_pair": summary.pair_code, "requested": pair.code},
        )
        return None

    fx_forward = _fx_forward_from_summary(summary)
    basis = _fx_basis_from_summary(summary)
    if require_basis and basis is None:
        dg.error(
            "FX_PRICE_BASIS_MISSING",
            f"price-xccy requires basis pillars in {summary_path}; snapshot had none",
            {"path": str(summary_path), "pair": pair.code},
        )
        return None

    return pair, fx_conv, calendars, fx_forward, basis, summary


def _resolve_tenor_to_value_date(
    tenor_code: str,
    spot_date: date,
    calendars: tuple[HolidayCalendar, ...],
    dg: DiagnosticsCollector,
) -> date | None:
    """Resolve a CLI tenor code (e.g. ``"3M"``) to a settlement value-date.

    Algorithm: look up the tenor in :data:`_TENOR_DAYS`, add that many calendar
    days to ``spot_date``, then roll forward to the next joint-calendar good
    business day. Rich tenor calculus is deferred to v0.4; unknown tenors
    surface as ``FX_PRICE_UNKNOWN_TENOR`` ERROR.
    """
    offset = _TENOR_DAYS.get(tenor_code.upper())
    if offset is None:
        dg.error(
            "FX_PRICE_UNKNOWN_TENOR",
            f"tenor {tenor_code!r} not in CLI tenor table {sorted(_TENOR_DAYS)}",
            {"tenor_code": tenor_code, "supported": sorted(_TENOR_DAYS)},
        )
        return None
    from datetime import timedelta as _td

    target = spot_date + _td(days=offset)
    while not _is_joint_business_day(target, calendars):
        target = target + _td(days=1)
    return target


def _is_joint_business_day(d: date, calendars: tuple[HolidayCalendar, ...]) -> bool:
    """True iff ``d`` is a business day in every calendar (joint intersection).

    Mirrors :func:`rates.io.fx_market._is_joint_business_day` (private to that
    module); duplicating the 1-liner here keeps the cross-module boundary
    clean.
    """
    return all(cal.is_business_day(d) for cal in calendars)


def _resolve_outright_value_date(
    args: argparse.Namespace,
    spot_date: date,
    calendars: tuple[HolidayCalendar, ...],
    dg: DiagnosticsCollector,
) -> date | None:
    """Apply the ``--tenor / --value-date`` mutex for ``price-outright``.

    Argparse enforces the mutex at the CLI layer (``add_mutually_exclusive_group(
    required=True)``); we re-check defensively so a caller building the
    Namespace by hand still gets a clean diagnostic.
    """
    tenor_code = getattr(args, "tenor_code", None)
    value_date = getattr(args, "value_date", None)
    return _resolve_tenor_or_value_date(tenor_code, value_date, spot_date, calendars, dg)


def _resolve_swap_leg_value_date(
    args: argparse.Namespace,
    leg: str,
    spot_date: date,
    calendars: tuple[HolidayCalendar, ...],
    dg: DiagnosticsCollector,
) -> date | None:
    """Apply the per-leg mutex for ``price-swap`` (``leg`` ∈ ``"near" | "far"``)."""
    tenor_code = getattr(args, f"{leg}_tenor", None)
    value_date = getattr(args, f"{leg}_value_date", None)
    return _resolve_tenor_or_value_date(tenor_code, value_date, spot_date, calendars, dg)


def _resolve_tenor_or_value_date(
    tenor_code: str | None,
    value_date: date | None,
    spot_date: date,
    calendars: tuple[HolidayCalendar, ...],
    dg: DiagnosticsCollector,
) -> date | None:
    """Core mutex resolver: exactly one of tenor / value_date must be set."""
    if (tenor_code is None) == (value_date is None):
        dg.error(
            "FX_PRICE_TENOR_VALUE_DATE_MUTEX",
            (
                "exactly one of --tenor / --value-date is required "
                f"(got tenor={tenor_code!r}, value_date={value_date!r})"
            ),
            {"tenor_code": repr(tenor_code), "value_date": repr(value_date)},
        )
        return None
    if value_date is not None:
        return value_date
    assert tenor_code is not None  # mutex above
    return _resolve_tenor_to_value_date(tenor_code, spot_date, calendars, dg)


def _validate_pricing_value_date(
    value_date: date,
    spot_date: date,
    last_pillar_date: date,
    calendars: tuple[HolidayCalendar, ...],
    dg: DiagnosticsCollector,
) -> bool:
    """Apply the three pricing-side value-date validations (C-103 D11).

    * ``FX_PRICE_VALUE_DATE_BEFORE_SPOT`` (ERROR) when ``value_date <= spot_date``.
      Equal-to-spot is rejected too — the curve's spot rate is a quoted anchor,
      not a forward fix, and CLI pricing on the spot leg has no operational
      meaning.
    * ``FX_PRICE_INVALID_VALUE_DATE`` (ERROR) when the date is not a good
      business day on the joint calendar (no auto-roll — explicit input).
    * ``FX_PRICE_EXTRAPOLATION`` (WARN) when the date is past the last pillar;
      the price still returns (trader's responsibility — curve invariant
      blocks dates strictly beyond the last pillar so we WARN at exactly the
      boundary).
    """
    if value_date <= spot_date:
        dg.error(
            "FX_PRICE_VALUE_DATE_BEFORE_SPOT",
            f"value_date {value_date} must be strictly after spot_date {spot_date}",
            {"value_date": value_date.isoformat(), "spot_date": spot_date.isoformat()},
        )
        return False
    if not _is_joint_business_day(value_date, calendars):
        dg.error(
            "FX_PRICE_INVALID_VALUE_DATE",
            f"value_date {value_date} is not a good business day on the joint calendar",
            {"value_date": value_date.isoformat()},
        )
        return False
    if value_date > last_pillar_date:
        dg.warn(
            "FX_PRICE_EXTRAPOLATION",
            (
                f"value_date {value_date} is beyond the last curve pillar "
                f"{last_pillar_date}; quoting last-pillar forward as a "
                f"constant-extrapolation"
            ),
            {
                "value_date": value_date.isoformat(),
                "last_pillar": last_pillar_date.isoformat(),
            },
        )
        # Validation still passes per architecture D11 ("price still returned").
        # The math layer's curve invariant (forward_at raises past last pillar)
        # is V2 hard cap; pricing call sites catch the ValueError and clip to
        # the last pillar's rate. V3 calibration can lift this to true
        # extrapolation in the curve itself.
    return True


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
    "run_fx_bootstrap",
    "run_fx_diagnose",
    "run_fx_price_outright",
    "run_fx_price_swap",
    "run_fx_price_xccy",
]
