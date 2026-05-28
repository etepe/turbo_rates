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
    fx_basis_to_table,
    fx_forward_to_table,
    scenario_to_table,
    write_fx_curve_partition,
    write_fx_summary,
    write_partition,
    write_summary,
)
from rates.io.schemas import (
    FX_SCHEMA_VERSION,
    SCHEMA_VERSION,
    FXParityCheckRow,
    FXSummary,
    Summary,
)

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


# ---------------------------------------------------------------------------
# FX persistence (M-110, v0.3.0)
# ---------------------------------------------------------------------------


@pytest.mark.phase8
def test_fx_summary_from_domain_happy_path(
    hand_fx_pair,
    hand_fx_conv,
    hand_fx_forward,
    hand_fx_basis,
    fx_valuation_date,
    hand_curve,
    hand_for_curve,
):
    """FXSummary.from_domain wires every domain object into the persisted shape."""
    summary = FXSummary.from_domain(
        pair=hand_fx_pair,
        valuation_date=fx_valuation_date,
        forward=hand_fx_forward,
        basis=hand_fx_basis,
        parity_checks=[
            FXParityCheckRow(
                tenor_code="1M",
                settle_date=date(2026, 7, 1),
                quoted_forward=32.6355,
                parity_forward=32.6354,
                diff_bps_of_spot=0.03,
            )
        ],
        conv=hand_fx_conv,
        diagnostics=[],
        dom_ois=hand_curve,
        for_ois=hand_for_curve,
        strip_residuals={"1Y": 3.0e-12},
        as_of_timestamp=datetime(2026, 5, 28, 16, 30, tzinfo=UTC),
    )

    assert summary.schema_version == FX_SCHEMA_VERSION
    assert summary.pair_code == "USDTRY"
    assert summary.spot_date == hand_fx_forward.spot_date
    assert summary.spot_rate == pytest.approx(hand_fx_forward.spot_rate)
    assert len(summary.forward_pillars) == 3
    assert len(summary.basis_pillars) == 2
    assert all(bp.quoted_on_foreign is True for bp in summary.basis_pillars)
    assert summary.config_snapshot.domestic_currency == "TRY"
    assert summary.config_snapshot.foreign_currency == "USD"
    assert summary.config_snapshot.quote_convention == "direct"
    assert summary.config_snapshot.settlement_calendars == ["TR", "US"]

    # v2: dual OIS pillars + per-ccy meta embedded from the supplied curves.
    assert len(summary.dom_ois_pillars) == len(hand_curve.pillars_tuple)
    assert len(summary.for_ois_pillars) == len(hand_for_curve.pillars_tuple)
    assert summary.dom_ois_meta.day_count == "Act/360"
    assert summary.dom_ois_meta.interpolation == "log_linear_df"
    assert summary.for_ois_meta.day_count == "Act/365"
    assert summary.for_ois_meta.interpolation == "linear_zero"
    assert summary.dom_ois_meta.valuation_date == hand_curve.valuation_date

    # v2: strip residual maps per basis pillar tenor; absent tenors default to 0.0.
    residual_by_tenor = {bp.tenor_code: bp.strip_residual for bp in summary.basis_pillars}
    assert residual_by_tenor["1Y"] == pytest.approx(3.0e-12)
    assert residual_by_tenor["2Y"] == 0.0


@pytest.mark.phase8
def test_fx_summary_from_domain_basis_none_yields_empty_list(
    hand_fx_pair, hand_fx_conv, hand_fx_forward, fx_valuation_date, hand_curve, hand_for_curve
):
    """When no XCCY_BASIS rows are present, basis=None ⇒ basis_pillars == []."""
    summary = FXSummary.from_domain(
        pair=hand_fx_pair,
        valuation_date=fx_valuation_date,
        forward=hand_fx_forward,
        basis=None,
        parity_checks=[],
        conv=hand_fx_conv,
        diagnostics=[],
        dom_ois=hand_curve,
        for_ois=hand_for_curve,
        strip_residuals={},
    )
    assert summary.basis_pillars == []
    assert summary.parity_checks == []


@pytest.mark.phase8
def test_write_fx_summary_round_trip(
    tmp_path,
    hand_fx_pair,
    hand_fx_conv,
    hand_fx_forward,
    hand_fx_basis,
    fx_valuation_date,
    hand_curve,
    hand_for_curve,
):
    summary = FXSummary.from_domain(
        pair=hand_fx_pair,
        valuation_date=fx_valuation_date,
        forward=hand_fx_forward,
        basis=hand_fx_basis,
        parity_checks=[],
        conv=hand_fx_conv,
        diagnostics=[],
        dom_ois=hand_curve,
        for_ois=hand_for_curve,
        strip_residuals={},
        as_of_timestamp=datetime(2026, 5, 28, 16, 30, tzinfo=UTC),
    )
    out = tmp_path / "latest" / "usdtry_fx_summary.json"
    write_fx_summary(summary, out)

    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == FX_SCHEMA_VERSION
    assert payload["pair_code"] == "USDTRY"
    assert payload["spot_rate"] == pytest.approx(32.4520)
    # Indented (pretty) + trailing newline.
    raw = out.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert '\n  "pair_code"' in raw

    # Round-trip equality.
    rebuilt = FXSummary.model_validate_json(raw)
    assert rebuilt == summary


@pytest.mark.phase8
def test_write_fx_summary_rejects_mismatched_schema_version(
    tmp_path,
    hand_fx_pair,
    hand_fx_conv,
    hand_fx_forward,
    fx_valuation_date,
    hand_curve,
    hand_for_curve,
):
    summary = FXSummary.from_domain(
        pair=hand_fx_pair,
        valuation_date=fx_valuation_date,
        forward=hand_fx_forward,
        basis=None,
        parity_checks=[],
        conv=hand_fx_conv,
        diagnostics=[],
        dom_ois=hand_curve,
        for_ois=hand_for_curve,
        strip_residuals={},
    )
    # Re-build with a deliberately wrong schema_version via model_copy.
    bumped = summary.model_copy(update={"schema_version": FX_SCHEMA_VERSION + 1})
    with pytest.raises(ValueError, match="refusing to write FXSummary"):
        write_fx_summary(bumped, tmp_path / "out.json")


@pytest.mark.phase8
def test_write_fx_summary_atomic_overwrite_no_tmp_leftover(
    tmp_path,
    hand_fx_pair,
    hand_fx_conv,
    hand_fx_forward,
    fx_valuation_date,
    hand_curve,
    hand_for_curve,
):
    """Writing twice on the same path leaves no .tmp leftover and overwrites cleanly."""
    summary = FXSummary.from_domain(
        pair=hand_fx_pair,
        valuation_date=fx_valuation_date,
        forward=hand_fx_forward,
        basis=None,
        parity_checks=[],
        conv=hand_fx_conv,
        diagnostics=[],
        dom_ois=hand_curve,
        for_ois=hand_for_curve,
        strip_residuals={},
    )
    out = tmp_path / "usdtry_fx_summary.json"
    write_fx_summary(summary, out)
    write_fx_summary(summary, out)
    assert out.exists()
    assert not out.with_suffix(out.suffix + ".tmp").exists()


@pytest.mark.phase9
def test_fx_summary_v2_round_trip_preserves_ois_and_residuals(
    tmp_path,
    hand_fx_pair,
    hand_fx_conv,
    hand_fx_forward,
    hand_fx_basis,
    fx_valuation_date,
    hand_curve,
    hand_for_curve,
):
    """FXSummary v2 survives from_domain -> write_fx_summary -> deserialize.

    Asserts the new v2 payload (dual OIS pillar lists + per-ccy meta +
    per-pillar strip residual + schema_version==2) round-trips intact. The OIS
    curves themselves are NOT reconstructed here — that adapter is C-110 / Phase
    4; this test only pins the persisted shape.
    """
    strip_residuals = {"1Y": 4.2e-12, "2Y": 0.0}
    summary = FXSummary.from_domain(
        pair=hand_fx_pair,
        valuation_date=fx_valuation_date,
        forward=hand_fx_forward,
        basis=hand_fx_basis,
        parity_checks=[],
        conv=hand_fx_conv,
        diagnostics=[],
        dom_ois=hand_curve,
        for_ois=hand_for_curve,
        strip_residuals=strip_residuals,
        as_of_timestamp=datetime(2026, 5, 28, 16, 30, tzinfo=UTC),
    )

    out = tmp_path / "latest" / "usdtry_fx_summary.json"
    write_fx_summary(summary, out)
    parsed = FXSummary.model_validate_json(out.read_text(encoding="utf-8"))

    assert parsed.schema_version == 2

    # Dual OIS pillar lists survive, in order, with values intact.
    assert [p.tenor_code for p in parsed.dom_ois_pillars] == [
        p.tenor_code for p in hand_curve.pillars_tuple
    ]
    assert [p.tenor_code for p in parsed.for_ois_pillars] == [
        p.tenor_code for p in hand_for_curve.pillars_tuple
    ]
    assert parsed.dom_ois_pillars[0].discount_factor == pytest.approx(
        hand_curve.pillars_tuple[0].discount_factor
    )
    assert parsed.for_ois_pillars[-1].rate == pytest.approx(
        hand_for_curve.pillars_tuple[-1].rate
    )

    # Per-ccy meta survives and is not swapped between dom/for.
    assert parsed.dom_ois_meta.valuation_date == hand_curve.valuation_date
    assert parsed.dom_ois_meta.day_count == hand_curve.day_count.value
    assert parsed.dom_ois_meta.interpolation == hand_curve.interp
    assert parsed.for_ois_meta.day_count == hand_for_curve.day_count.value
    assert parsed.for_ois_meta.interpolation == hand_for_curve.interp

    # Per-pillar strip residual survives (mapped by tenor_code; 0.0 elsewhere).
    residual_by_tenor = {bp.tenor_code: bp.strip_residual for bp in parsed.basis_pillars}
    assert residual_by_tenor["1Y"] == pytest.approx(4.2e-12)
    assert residual_by_tenor["2Y"] == 0.0

    # Full structural round-trip equality.
    assert parsed == summary


@pytest.mark.phase8
def test_write_fx_curve_partition_forward_only(
    tmp_path, hand_fx_pair, hand_fx_forward, fx_valuation_date
):
    """basis=None ⇒ only the forward partition is written."""
    root = tmp_path / "fx_curves"
    written = write_fx_curve_partition(
        forward=hand_fx_forward,
        basis=None,
        root=root,
        valuation_date=fx_valuation_date,
        pair=hand_fx_pair,
    )

    assert "forward" in written
    assert "basis" not in written
    expected = (
        root / "usdtry" / "forward" / f"date={fx_valuation_date.isoformat()}" / PARTITION_FILENAME
    )
    assert written["forward"] == expected
    assert expected.exists()
    table = pq.read_table(expected)  # type: ignore[no-untyped-call]
    assert table.num_rows == 3
    # pyarrow ≥15 auto-detects Hive partition keys (``date``) when reading a
    # file inside a partition dir, so they appear as an extra column alongside
    # the file's own schema. Assert the latter as a subset.
    assert {"tenor_code", "tenor_days", "settle_date", "forward_rate"}.issubset(
        set(table.column_names)
    )


@pytest.mark.phase8
def test_write_fx_curve_partition_with_basis_writes_both(
    tmp_path, hand_fx_pair, hand_fx_forward, hand_fx_basis, fx_valuation_date
):
    root = tmp_path / "fx_curves"
    written = write_fx_curve_partition(
        forward=hand_fx_forward,
        basis=hand_fx_basis,
        root=root,
        valuation_date=fx_valuation_date,
        pair=hand_fx_pair,
    )
    assert {"forward", "basis"} == set(written.keys())
    fwd_table = pq.read_table(written["forward"])  # type: ignore[no-untyped-call]
    bas_table = pq.read_table(written["basis"])  # type: ignore[no-untyped-call]
    assert fwd_table.num_rows == 3
    assert bas_table.num_rows == 2
    # quoted_on_foreign broadcast across every basis row.
    assert set(bas_table.column("quoted_on_foreign").to_pylist()) == {True}


@pytest.mark.phase8
def test_write_fx_curve_partition_overwrites_existing(
    tmp_path, hand_fx_pair, hand_fx_forward, fx_valuation_date
):
    """Re-running on the same valuation_date wipes the partition directory."""
    root = tmp_path / "fx_curves"
    write_fx_curve_partition(
        forward=hand_fx_forward,
        basis=None,
        root=root,
        valuation_date=fx_valuation_date,
        pair=hand_fx_pair,
    )
    # Plant a sentinel inside the partition dir to detect the wipe.
    partition_dir = root / "usdtry" / "forward" / f"date={fx_valuation_date.isoformat()}"
    sentinel = partition_dir / "stale.txt"
    sentinel.write_text("stale")

    write_fx_curve_partition(
        forward=hand_fx_forward,
        basis=None,
        root=root,
        valuation_date=fx_valuation_date,
        pair=hand_fx_pair,
    )
    assert not sentinel.exists()
    assert (partition_dir / PARTITION_FILENAME).exists()


@pytest.mark.phase8
def test_write_fx_curve_partition_readable_via_dataset(
    tmp_path, hand_fx_pair, hand_fx_forward, hand_fx_basis, fx_valuation_date
):
    """Hive partitioning exposes the ``date`` partition key as a readable column."""
    root = tmp_path / "fx_curves"
    write_fx_curve_partition(
        forward=hand_fx_forward,
        basis=hand_fx_basis,
        root=root,
        valuation_date=fx_valuation_date,
        pair=hand_fx_pair,
    )
    forward_root = root / "usdtry" / "forward"
    dataset = ds.dataset(forward_root, format="parquet", partitioning="hive")
    table = dataset.to_table()
    assert "date" in table.column_names
    assert set(table.column("date").to_pylist()) == {fx_valuation_date.isoformat()}


@pytest.mark.phase8
def test_fx_forward_to_table_schema(hand_fx_forward):
    table = fx_forward_to_table(hand_fx_forward)
    assert table.column_names == ["tenor_code", "tenor_days", "settle_date", "forward_rate"]
    assert table.num_rows == 3


@pytest.mark.phase8
def test_fx_basis_to_table_schema(hand_fx_basis):
    table = fx_basis_to_table(hand_fx_basis)
    assert table.column_names == [
        "tenor_code",
        "tenor_days",
        "maturity_date",
        "spread_bps",
        "quoted_on_foreign",
    ]
    assert table.num_rows == 2
