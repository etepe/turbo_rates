"""Comparison report — row coverage, rate conversion, spread formula, pretty().

Aligns with F-012 acceptance and G6 (Act/365 common basis).
"""

from __future__ import annotations

import pytest

from rates.core.report import ComparisonRow, build_comparison


def _split_rows(table):
    pillars = [r for r in table.rows if r.kind == "pillar"]
    forwards = [r for r in table.rows if r.kind == "forward"]
    return pillars, forwards


@pytest.mark.phase3
def test_table_covers_pillars_and_ladder(hand_curve, scenario_short, conventions):
    table = build_comparison(hand_curve, scenario_short, conventions)
    pillars, forwards = _split_rows(table)
    assert len(pillars) == len(hand_curve.pillars_tuple)
    assert len(forwards) == len(hand_curve.forward_ladder_dates)
    assert all(isinstance(r, ComparisonRow) for r in table.rows)


@pytest.mark.phase3
def test_market_rate_converted_to_act365(hand_curve, scenario_short, conventions):
    """For a pillar with DF=0.7 over 367 calendar days, market_act365 must equal
    ``(1/0.7 - 1) * 365 / 367``."""
    table = build_comparison(hand_curve, scenario_short, conventions)
    one_year = next(r for r in table.rows if r.tenor == "M-1Y")
    expected = (1.0 / 0.7000 - 1.0) * 365.0 / 367.0
    assert one_year.market_rate_act365 == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase3
def test_scenario_rate_uses_act365_basis(hand_curve, scenario_short, conventions):
    """Scenario rate for a pillar = (index(end) - 1) * 365 / days."""
    table = build_comparison(hand_curve, scenario_short, conventions)
    pillars, _ = _split_rows(table)
    lut = dict(zip(scenario_short.mid.dates, scenario_short.mid.values, strict=True))
    for r in pillars:
        if r.scenario_rate_act365 is None:
            continue
        idx = lut[r.end_date]
        expected = (idx - 1.0) * 365.0 / r.days_to_maturity
        assert r.scenario_rate_act365 == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase3
def test_spread_bps_formula(hand_curve, scenario_short, conventions):
    table = build_comparison(hand_curve, scenario_short, conventions)
    for r in table.rows:
        if r.scenario_rate_act365 is None:
            assert r.spread_bps is None
        else:
            assert r.spread_bps == pytest.approx(
                (r.market_rate_act365 - r.scenario_rate_act365) * 1e4, abs=1e-12
            )


@pytest.mark.phase3
def test_out_of_horizon_rows_emit_none(hand_curve, scenario_truncated, conventions):
    """Pillars / forwards past horizon=30 BD ⇒ scenario_rate_act365 is None."""
    table = build_comparison(hand_curve, scenario_truncated, conventions)
    out_rows = [r for r in table.rows if r.scenario_rate_act365 is None]
    assert out_rows, "expected at least one out-of-horizon row"
    for r in out_rows:
        assert r.spread_bps is None
        # Market rate is still computed.
        assert r.market_rate_act365 > 0


@pytest.mark.phase3
def test_forward_rate_uses_span_days(hand_curve, scenario_short, conventions):
    """Forward Act/365 rate = (DF_s/DF_e - 1) * 365 / (e-s).days."""
    table = build_comparison(hand_curve, scenario_short, conventions)
    fwd_1x2 = next(r for r in table.rows if r.tenor == "1x2")
    df_s = hand_curve.df_at(fwd_1x2.start_date)
    df_e = hand_curve.df_at(fwd_1x2.end_date)
    span = (fwd_1x2.end_date - fwd_1x2.start_date).days
    expected = (df_s / df_e - 1.0) * 365.0 / span
    assert fwd_1x2.market_rate_act365 == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase3
def test_to_dataframe_columns(hand_curve, scenario_short, conventions):
    table = build_comparison(hand_curve, scenario_short, conventions)
    df = table.to_dataframe()
    assert list(df.columns) == [
        "tenor",
        "kind",
        "start_date",
        "end_date",
        "days_to_maturity",
        "market_rate_act365",
        "scenario_rate_act365",
        "spread_bps",
    ]
    assert len(df) == len(table.rows)


@pytest.mark.phase3
def test_pretty_renders_all_rows(hand_curve, scenario_short, conventions):
    table = build_comparison(hand_curve, scenario_short, conventions)
    rendered = table.pretty()
    # Header present.
    assert "tenor" in rendered
    assert "kind" in rendered
    assert "spread(bp)" in rendered
    # Every tenor surfaces.
    for r in table.rows:
        assert r.tenor in rendered
    # Multiple lines: header + separator + one per row.
    assert rendered.count("\n") == len(table.rows) + 1


@pytest.mark.phase3
def test_pretty_renders_n_a_for_missing(hand_curve, scenario_truncated, conventions):
    table = build_comparison(hand_curve, scenario_truncated, conventions)
    rendered = table.pretty()
    assert "n/a" in rendered
