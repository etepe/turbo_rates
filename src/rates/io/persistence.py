"""rates.io.persistence — Summary JSON + Parquet partition writers (M-012).

Two functions, per C-006:

* :func:`write_summary` — pretty-printed JSON to ``data/latest/try_ois_summary.json``.
* :func:`write_partition` — long-format daily-grid Parquet to
  ``data/curves/try_ois/date=YYYY-MM-DD/data.parquet`` (Hive-style partitioning).

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

Contract: C-006.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from rates.core.scenario import DailyIndex, ScenarioResult
from rates.io.schemas import SCHEMA_VERSION, Summary

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


__all__ = [
    "PARTITION_FILENAME",
    "scenario_to_table",
    "write_partition",
    "write_summary",
]
