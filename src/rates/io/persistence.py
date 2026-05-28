"""rates.io.persistence — Summary JSON + Parquet partition writers (M-012, M-110).

V1 (M-012, contract C-006):

* :func:`write_summary` — pretty-printed JSON to ``data/latest/try_ois_summary.json``.
* :func:`write_partition` — long-format daily-grid Parquet to
  ``data/curves/try_ois/date=YYYY-MM-DD/data.parquet`` (Hive-style partitioning).

v0.3.0 FX (M-110, contract C-102):

* :func:`write_fx_summary` — pretty-printed JSON to
  ``data/latest/<pair_lower>_fx_summary.json``. Mirrors :func:`write_summary`.
* :func:`write_fx_curve_partition` — two Hive-style Parquet partitions per
  run: ``{root}/{pair_lower}/forward/date=YYYY-MM-DD/data.parquet`` and (when
  the basis curve is present) ``{root}/{pair_lower}/basis/date=YYYY-MM-DD/data.parquet``.
  Forward and basis live in separate sub-datasets because they have different
  schemas (forward carries ``forward_rate``; basis carries ``spread_bps`` +
  ``quoted_on_foreign``); merging into one file would force column nullability
  for the row-type discrimination.

Open-question resolution (O4): daily-grid Parquet schema is **long format**:

    index_date (date32)   scenario_label (string)    index_value (float64)

The ``valuation_date`` lives in the Hive partition path
(``date=YYYY-MM-DD``); it is **not** also written as a column inside the file
(no redundancy). The internal column is named ``index_date`` (not ``date``)
deliberately: a file-level column named ``date`` would collide with the
partition-key column also named ``date`` that pyarrow re-exposes on read.

Long-format choice rationale (vs wide ``mid/low/high`` columns): future scenario
counts (e.g. hawkish/base/dovish in V2) cost zero schema churn — just more
``scenario_label`` values. Wide format would require a column-per-scenario
schema bump and break backwards compatibility with V1 readers.

Re-run policy: writing on the same valuation_date overwrites the partition
directory entirely (per F-008). The Summary JSON is overwritten in place.

Contracts: C-006 (V1 OIS), C-102 (v0.3.0 FX).
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from rates.core.scenario import DailyIndex, ScenarioResult
from rates.fx.basis_curve import CrossCurrencyBasisCurve
from rates.fx.forward_curve import FXForwardCurve
from rates.fx.types import CurrencyPair
from rates.io.schemas import FX_SCHEMA_VERSION, SCHEMA_VERSION, FXSummary, Summary

#: Canonical filename inside each ``date=YYYY-MM-DD/`` partition directory.
PARTITION_FILENAME: str = "data.parquet"

#: Long-format schema columns (kept stable; reorder = schema_version bump).
_DAILY_GRID_COLUMNS: tuple[str, ...] = ("index_date", "scenario_label", "index_value")


# ---------------------------------------------------------------------------
# Summary JSON
# ---------------------------------------------------------------------------


def write_summary(summary: Summary, path: Path | str) -> None:
    """Write Summary to a pretty-printed JSON file (indent=2, UTF-8, trailing newline).

    Parent directories are created on demand. Re-runs overwrite atomically via a
    temp file rename so a crashed write never leaves a half-written summary.

    Args:
        summary: Pydantic :class:`Summary` (M-013).
        path:    Destination file path; parents auto-created.

    Raises:
        ValueError: If ``summary.schema_version`` does not match
                    :data:`rates.io.schemas.SCHEMA_VERSION` (defence against
                    accidental mismatched writes during a migration window).
    """
    path = Path(path)
    if summary.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"refusing to write Summary with schema_version={summary.schema_version}; "
            f"writer is at SCHEMA_VERSION={SCHEMA_VERSION}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = summary.model_dump_json(indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Daily-grid Parquet partition
# ---------------------------------------------------------------------------


def scenario_to_table(scenario: ScenarioResult) -> pa.Table:
    """Convert a :class:`ScenarioResult` to a long-format :class:`pyarrow.Table`.

    Output schema: ``index_date (date32), scenario_label (string),
    index_value (float64)``. Rows are emitted in (label, ascending date)
    order — deterministic.
    """
    dates: list[date] = []
    labels: list[str] = []
    values: list[float] = []

    def _extend(idx: DailyIndex) -> None:
        for d, v in zip(idx.dates, idx.values, strict=True):
            dates.append(d)
            labels.append(idx.label)
            values.append(v)

    _extend(scenario.mid)
    if scenario.low is not None:
        _extend(scenario.low)
    if scenario.high is not None:
        _extend(scenario.high)

    return pa.table(
        {
            "index_date": pa.array(dates, type=pa.date32()),
            "scenario_label": pa.array(labels, type=pa.string()),
            "index_value": pa.array(values, type=pa.float64()),
        }
    )


def write_partition(
    daily_grid: pa.Table,
    root: Path | str,
    valuation_date: date,
) -> Path:
    """Write the daily-grid Parquet under a Hive-style partition directory.

    Output path: ``{root}/date={valuation_date.isoformat()}/data.parquet``. If
    the partition directory exists, it is removed entirely (per F-008 re-run
    semantics) before the new file is written.

    Args:
        daily_grid:     pyarrow Table; must contain
                        ``date, scenario_label, index_value`` columns (extras
                        ignored).
        root:           Dataset root, e.g. ``data/curves/try_ois``.
        valuation_date: Partition key.

    Returns:
        Absolute path to the written ``data.parquet``.

    Raises:
        ValueError: When the table is missing one of the required columns.
    """
    missing = [c for c in _DAILY_GRID_COLUMNS if c not in daily_grid.column_names]
    if missing:
        raise ValueError(
            f"daily_grid missing required columns: {missing}; got {daily_grid.column_names}"
        )

    root = Path(root)
    partition_dir = root / f"date={valuation_date.isoformat()}"
    if partition_dir.exists():
        shutil.rmtree(partition_dir)
    partition_dir.mkdir(parents=True)

    out_path = partition_dir / PARTITION_FILENAME
    pq.write_table(  # type: ignore[no-untyped-call]
        daily_grid.select(list(_DAILY_GRID_COLUMNS)),
        out_path,
    )
    return out_path


# ---------------------------------------------------------------------------
# FX summary JSON (M-110, v0.3.0)
# ---------------------------------------------------------------------------


def write_fx_summary(summary: FXSummary, path: Path | str) -> None:
    """Write FXSummary to a pretty-printed JSON file (indent=2, UTF-8, trailing newline).

    Mirrors :func:`write_summary`. Parent directories are created on demand;
    overwrite is atomic via temp-file rename so a crashed write never leaves a
    half-written summary.

    Args:
        summary: Pydantic :class:`FXSummary` (M-109).
        path:    Destination file path; parents auto-created.

    Raises:
        ValueError: If ``summary.schema_version`` does not match
                    :data:`rates.io.schemas.FX_SCHEMA_VERSION`.
    """
    path = Path(path)
    if summary.schema_version != FX_SCHEMA_VERSION:
        raise ValueError(
            f"refusing to write FXSummary with schema_version={summary.schema_version}; "
            f"writer is at FX_SCHEMA_VERSION={FX_SCHEMA_VERSION}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = summary.model_dump_json(indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# FX curve Parquet partitions (M-110, v0.3.0)
# ---------------------------------------------------------------------------


#: Forward-curve pillar columns (kept stable; reorder = schema_version bump).
_FX_FORWARD_COLUMNS: tuple[str, ...] = ("tenor_code", "tenor_days", "settle_date", "forward_rate")

#: Basis-curve pillar columns.
_FX_BASIS_COLUMNS: tuple[str, ...] = (
    "tenor_code",
    "tenor_days",
    "maturity_date",
    "spread_bps",
    "quoted_on_foreign",
)


def fx_forward_to_table(forward: FXForwardCurve) -> pa.Table:
    """Convert an :class:`FXForwardCurve` to a pyarrow Table.

    Output schema: ``tenor_code (string), tenor_days (int32),
    settle_date (date32), forward_rate (float64)``. Rows in pillar order
    (ascending ``settle_date`` by curve invariant).
    """
    rows = forward.pillars_tuple
    return pa.table(
        {
            "tenor_code": pa.array([p.tenor_code for p in rows], type=pa.string()),
            "tenor_days": pa.array([p.tenor_days for p in rows], type=pa.int32()),
            "settle_date": pa.array([p.settle_date for p in rows], type=pa.date32()),
            "forward_rate": pa.array([p.forward_rate for p in rows], type=pa.float64()),
        }
    )


def fx_basis_to_table(basis: CrossCurrencyBasisCurve) -> pa.Table:
    """Convert a :class:`CrossCurrencyBasisCurve` to a pyarrow Table.

    ``quoted_on_foreign`` is a curve-level scalar that is broadcast onto every
    row so a Parquet reader can recover it without needing the summary JSON.
    """
    rows = basis.pillars_tuple
    return pa.table(
        {
            "tenor_code": pa.array([p.tenor_code for p in rows], type=pa.string()),
            "tenor_days": pa.array([p.tenor_days for p in rows], type=pa.int32()),
            "maturity_date": pa.array(
                [p.maturity_date for p in rows], type=pa.date32()
            ),
            "spread_bps": pa.array([p.spread_bps for p in rows], type=pa.float64()),
            "quoted_on_foreign": pa.array(
                [basis.quoted_on_foreign] * len(rows), type=pa.bool_()
            ),
        }
    )


def write_fx_curve_partition(
    forward: FXForwardCurve,
    basis: CrossCurrencyBasisCurve | None,
    root: Path | str,
    valuation_date: date,
    pair: CurrencyPair,
) -> dict[str, Path]:
    """Write FX forward (and optional basis) curve Parquet partitions.

    Output layout (Hive-style ``date=YYYY-MM-DD`` partition key):

    * ``{root}/{pair_lower}/forward/date=YYYY-MM-DD/data.parquet`` — always.
    * ``{root}/{pair_lower}/basis/date=YYYY-MM-DD/data.parquet`` — only when
      ``basis is not None``.

    Re-run semantics: each partition directory is removed entirely and
    rewritten (per F-008, same as :func:`write_partition`).

    Args:
        forward:         Bootstrapped FX forward curve (M-103).
        basis:           Bootstrapped cross-currency basis curve (M-104), or
                         ``None`` when the snapshot carries no XCCY_BASIS rows.
        root:            Root of the FX curve dataset, e.g. ``data/fx_curves``.
        valuation_date:  Partition key.
        pair:            Currency pair (drives the ``{pair_lower}`` subdirectory).

    Returns:
        Dict mapping curve names to written paths: ``{"forward": Path}`` or
        ``{"forward": Path, "basis": Path}`` when basis was provided.
    """
    root_path = Path(root)
    pair_dir = root_path / pair.code.lower()
    paths: dict[str, Path] = {}

    forward_dir = pair_dir / "forward" / f"date={valuation_date.isoformat()}"
    if forward_dir.exists():
        shutil.rmtree(forward_dir)
    forward_dir.mkdir(parents=True)
    forward_out = forward_dir / PARTITION_FILENAME
    pq.write_table(  # type: ignore[no-untyped-call]
        fx_forward_to_table(forward).select(list(_FX_FORWARD_COLUMNS)),
        forward_out,
    )
    paths["forward"] = forward_out

    if basis is not None:
        basis_dir = pair_dir / "basis" / f"date={valuation_date.isoformat()}"
        if basis_dir.exists():
            shutil.rmtree(basis_dir)
        basis_dir.mkdir(parents=True)
        basis_out = basis_dir / PARTITION_FILENAME
        pq.write_table(  # type: ignore[no-untyped-call]
            fx_basis_to_table(basis).select(list(_FX_BASIS_COLUMNS)),
            basis_out,
        )
        paths["basis"] = basis_out

    return paths


__all__ = [
    "PARTITION_FILENAME",
    "fx_basis_to_table",
    "fx_forward_to_table",
    "scenario_to_table",
    "write_fx_curve_partition",
    "write_fx_summary",
    "write_partition",
    "write_summary",
]
