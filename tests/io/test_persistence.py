"""Summary JSON + Parquet partition writers (M-012, contract C-006).

Covers atomic JSON write + round-trip, schema_version guard, overwrite
semantics, parquet long-format layout, and the partition path convention.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from rates.io.persistence import (
    PARTITION_FILENAME,
    scenario_to_table,
    write_partition,
    write_summary,
)
from rates.io.schemas import SCHEMA_VERSION, Summary

# ---------------------------------------------------------------------------
# Summary JSON
# ---------------------------------------------------------------------------


@pytest.mark.phase4
def test_write_summary_round_trip(
    tmp_path,
    hand_curve,
    hand_scenario_with_band,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_with_band,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
        as_of_timestamp=datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC),
    )
    out = tmp_path / "latest" / "try_ois_summary.json"
    write_summary(summary, out)

    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # Pretty-printed (indent=2) → contains the two-space indent for sub-objects.
    assert "  " in text
    # Trailing newline.
    assert text.endswith("\n")
    parsed = json.loads(text)
    assert parsed["schema_version"] == 1
    assert parsed["valuation_date"] == "2026-06-12"

    # Round-trip via Pydantic gives an equal Summary.
    rebuilt = Summary.model_validate_json(text)
    assert rebuilt == summary


@pytest.mark.phase4
def test_write_summary_creates_parent_dirs(
    tmp_path,
    hand_curve,
    hand_scenario_mid_only,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
    )
    out = tmp_path / "deep" / "nested" / "summary.json"
    write_summary(summary, out)
    assert out.exists()


@pytest.mark.phase4
def test_write_summary_overwrites_existing(
    tmp_path,
    hand_curve,
    hand_scenario_mid_only,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
):
    out = tmp_path / "s.json"
    out.write_text("stale-content", encoding="utf-8")
    summary = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
    )
    write_summary(summary, out)
    text = out.read_text(encoding="utf-8")
    assert "stale-content" not in text
    assert "schema_version" in text


@pytest.mark.phase4
def test_write_summary_rejects_schema_version_mismatch(
    hand_curve,
    hand_scenario_mid_only,
    hand_comparison,
    hand_config_snapshot,
    hand_diagnostics,
    tmp_path,
):
    base = Summary.from_domain(
        curve=hand_curve,
        scenario=hand_scenario_mid_only,
        comparison=hand_comparison,
        config_snapshot=hand_config_snapshot,
        diagnostics=hand_diagnostics,
    )
    # Force a mismatched schema_version via Pydantic copy semantics.
    mutated = Summary.model_validate({**base.model_dump(), "schema_version": SCHEMA_VERSION + 1})
    with pytest.raises(ValueError, match="schema_version"):
        write_summary(mutated, tmp_path / "x.json")


# ---------------------------------------------------------------------------
# Parquet partition
# ---------------------------------------------------------------------------


@pytest.mark.phase4
def test_scenario_to_table_mid_only(hand_scenario_mid_only):
    tbl = scenario_to_table(hand_scenario_mid_only)
    assert tbl.column_names == ["index_date", "scenario_label", "index_value"]
    assert tbl.num_rows == len(hand_scenario_mid_only.mid.dates)
    labels = tbl.column("scenario_label").to_pylist()
    assert set(labels) == {"mid"}


@pytest.mark.phase4
def test_scenario_to_table_with_band(hand_scenario_with_band):
    tbl = scenario_to_table(hand_scenario_with_band)
    labels = tbl.column("scenario_label").to_pylist()
    # Three labels times the per-label point count.
    assert set(labels) == {"mid", "low", "high"}
    per_label = len(hand_scenario_with_band.mid.dates)
    assert tbl.num_rows == 3 * per_label
    # Ordering invariant: mid first, then low, then high.
    assert labels[:per_label] == ["mid"] * per_label
    assert labels[per_label : 2 * per_label] == ["low"] * per_label
    assert labels[2 * per_label :] == ["high"] * per_label


@pytest.mark.phase4
def test_write_partition_writes_parquet_in_hive_dir(tmp_path, hand_scenario_with_band):
    val = date(2026, 6, 12)
    tbl = scenario_to_table(hand_scenario_with_band)
    root = tmp_path / "curves" / "try_ois"
    out = write_partition(tbl, root, val)

    expected_dir = root / "date=2026-06-12"
    assert out == expected_dir / PARTITION_FILENAME
    assert out.exists()

    # Read back via pyarrow.parquet and check row count + schema. pq.read_table
    # auto-infers the hive partition and synthesises a ``date`` column from the
    # path; the file's stored schema is asserted via ParquetFile below.
    rt = pq.read_table(out)
    assert rt.num_rows == tbl.num_rows
    pf = pq.ParquetFile(out)
    assert pf.schema_arrow.names == ["index_date", "scenario_label", "index_value"]


@pytest.mark.phase4
def test_write_partition_overwrites_existing_directory(
    tmp_path, hand_scenario_with_band, hand_scenario_mid_only
):
    val = date(2026, 6, 12)
    root = tmp_path / "curves"

    # Initial write with band (larger row count).
    write_partition(scenario_to_table(hand_scenario_with_band), root, val)
    first_path = root / "date=2026-06-12" / PARTITION_FILENAME
    first_rows = pq.read_table(first_path).num_rows

    # Plant a sentinel file that overwrite must wipe.
    sentinel = root / "date=2026-06-12" / "stale.txt"
    sentinel.write_text("stale", encoding="utf-8")
    assert sentinel.exists()

    # Re-write with mid-only (smaller row count).
    write_partition(scenario_to_table(hand_scenario_mid_only), root, val)
    second_rows = pq.read_table(first_path).num_rows

    assert second_rows < first_rows
    assert not sentinel.exists()  # partition dir wiped.


@pytest.mark.phase4
def test_write_partition_readable_via_dataset(tmp_path, hand_scenario_with_band):
    val = date(2026, 6, 12)
    root = tmp_path / "curves" / "try_ois"
    write_partition(scenario_to_table(hand_scenario_with_band), root, val)

    dataset = ds.dataset(root, format="parquet", partitioning="hive")
    table = dataset.to_table()
    # Hive partitioning exposes the partition key as a column ``date``; the
    # internal column is named ``index_date`` so there's no collision.
    assert "date" in table.column_names
    assert "index_date" in table.column_names
    assert "scenario_label" in table.column_names
    assert "index_value" in table.column_names
    # Partition value matches what we wrote.
    assert set(table.column("date").to_pylist()) == {"2026-06-12"}


@pytest.mark.phase4
def test_write_partition_rejects_missing_columns(tmp_path):
    import pyarrow as pa

    bad = pa.table({"index_date": pa.array([date(2026, 1, 1)], type=pa.date32())})
    with pytest.raises(ValueError, match="missing required columns"):
        write_partition(bad, tmp_path, date(2026, 1, 1))
