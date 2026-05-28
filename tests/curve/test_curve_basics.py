"""OISCurve public API: pillar exactness, interpolation, forward, defensive copy,
summary dict, out-of-range. Aligns with F-002, F-003, C-005.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rates.core.curve import (
    FORWARD_LADDER_LABELS,
    OISCurve,
    Pillar,
)
from rates.core.daycount import year_fraction
from rates.core.types import DayCount

# ---------------------------------------------------------------------------
# Constructor invariants
# ---------------------------------------------------------------------------


@pytest.mark.phase2
def test_anchor_identity(log_curve):
    """df_at(valuation_date) == 1.0."""
    assert log_curve.df_at(log_curve.valuation_date) == 1.0


@pytest.mark.phase2
def test_pillar_exactness_log_linear(log_curve, hand_pillars):
    """At each pillar end_date, df_at returns the bootstrapped DF exactly (F-002)."""
    for p in hand_pillars:
        assert log_curve.df_at(p.end_date) == p.discount_factor


@pytest.mark.phase2
def test_pillar_exactness_linear_zero(zero_curve, hand_pillars):
    """Same invariant under linear_zero interpolation."""
    for p in hand_pillars:
        assert zero_curve.df_at(p.end_date) == p.discount_factor


@pytest.mark.phase2
def test_rejects_unknown_interp(hand_pillars, hand_ladder_dates, val_date):
    with pytest.raises(ValueError, match="unknown interpolation"):
        OISCurve(
            valuation_date=val_date,
            day_count=DayCount.ACT_360,
            interp="cubic_spline",  # type: ignore[arg-type]
            pillars_tuple=hand_pillars,
            forward_ladder_dates=hand_ladder_dates,
        )


@pytest.mark.phase2
def test_rejects_empty_pillars(hand_ladder_dates, val_date):
    with pytest.raises(ValueError, match="at least one pillar"):
        OISCurve(
            valuation_date=val_date,
            day_count=DayCount.ACT_360,
            interp="log_linear_df",
            pillars_tuple=(),
            forward_ladder_dates=hand_ladder_dates,
        )


@pytest.mark.phase2
def test_rejects_out_of_order_pillars(hand_ladder_dates, val_date):
    bad = (
        Pillar("A", 730, val_date, date(2028, 6, 12), 0.3, 0.5),
        Pillar("B", 365, val_date, date(2027, 6, 14), 0.4, 0.6),
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        OISCurve(
            valuation_date=val_date,
            day_count=DayCount.ACT_360,
            interp="log_linear_df",
            pillars_tuple=bad,
            forward_ladder_dates=hand_ladder_dates,
        )


# ---------------------------------------------------------------------------
# df_at, zero_at, forward
# ---------------------------------------------------------------------------


@pytest.mark.phase2
def test_df_monotone_decreasing_log_linear(log_curve):
    """Log-linear DF interpolation preserves monotonicity for positive rates."""
    start = log_curve.valuation_date
    end = log_curve.pillars_tuple[-1].end_date
    days = (end - start).days
    step = timedelta(days=days // 100)
    cur = start
    prev_df = 1.0
    while cur < end:
        cur = cur + step
        if cur > end:
            break
        df = log_curve.df_at(cur)
        assert df <= prev_df + 1e-15, f"DF non-monotone at {cur}: {df} > {prev_df}"
        prev_df = df


@pytest.mark.phase2
def test_zero_at_round_trip(log_curve):
    """zero_at(d, day_count) reconstructs df_at(d) when round-tripped through tau."""
    d = log_curve.pillars_tuple[1].end_date  # 1Y pillar
    z = log_curve.zero_at(d, log_curve.day_count)
    tau = year_fraction(log_curve.valuation_date, d, log_curve.day_count)
    df_round_trip = 1.0 / (1.0 + z * tau)
    assert df_round_trip == pytest.approx(log_curve.df_at(d), abs=1e-15)


@pytest.mark.phase2
def test_zero_at_basis_switch(log_curve):
    """Same DF expressed in two bases differs only by 365/360 ratio in the linear case."""
    d = log_curve.pillars_tuple[1].end_date
    z_360 = log_curve.zero_at(d, DayCount.ACT_360)
    z_365 = log_curve.zero_at(d, DayCount.ACT_365)
    # z_365 / z_360 = 365 / 360 (since DF identical, tau scales linearly).
    assert z_365 / z_360 == pytest.approx(365.0 / 360.0, abs=1e-14)


@pytest.mark.phase2
def test_zero_at_at_valuation_raises(log_curve):
    with pytest.raises(ValueError, match="undefined at valuation_date"):
        log_curve.zero_at(log_curve.valuation_date, log_curve.day_count)


@pytest.mark.phase2
def test_forward_identity(log_curve):
    """forward(val, T) = simple zero rate at T in the curve's native day-count."""
    d = log_curve.pillars_tuple[1].end_date
    f = log_curve.forward(log_curve.valuation_date, d)
    z = log_curve.zero_at(d, log_curve.day_count)
    assert f == pytest.approx(z, abs=1e-15)


@pytest.mark.phase2
def test_forward_consistent_with_dfs(log_curve):
    """forward(s, e) = (DF_s/DF_e - 1) / tau matches by construction."""
    s = log_curve.pillars_tuple[0].end_date
    e = log_curve.pillars_tuple[2].end_date
    df_s = log_curve.df_at(s)
    df_e = log_curve.df_at(e)
    tau = year_fraction(s, e, log_curve.day_count)
    expected = (df_s / df_e - 1.0) / tau
    assert log_curve.forward(s, e) == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase2
def test_forward_requires_start_before_end(log_curve):
    d = log_curve.pillars_tuple[1].end_date
    with pytest.raises(ValueError, match="start < end"):
        log_curve.forward(d, d)


# ---------------------------------------------------------------------------
# Out-of-range
# ---------------------------------------------------------------------------


@pytest.mark.phase2
def test_df_before_valuation_raises(log_curve):
    with pytest.raises(ValueError, match="DateOutOfRange"):
        log_curve.df_at(log_curve.valuation_date - timedelta(days=1))


@pytest.mark.phase2
def test_df_after_last_pillar_raises(log_curve):
    last = log_curve.pillars_tuple[-1].end_date
    with pytest.raises(ValueError, match="DateOutOfRange"):
        log_curve.df_at(last + timedelta(days=1))


# ---------------------------------------------------------------------------
# forward_ladder, pillars, to_summary_dict
# ---------------------------------------------------------------------------


@pytest.mark.phase2
def test_forward_ladder_labels():
    """The canonical ladder is exactly 15 labels in the documented order."""
    assert len(FORWARD_LADDER_LABELS) == 15
    assert FORWARD_LADDER_LABELS[0] == "1x2"
    assert FORWARD_LADDER_LABELS[10] == "11x12"
    assert FORWARD_LADDER_LABELS[-1] == "9x12"


@pytest.mark.phase2
def test_forward_ladder_returns_dataframe(log_curve):
    df = log_curve.forward_ladder()
    assert list(df.columns) == ["label", "start_date", "end_date", "rate"]
    assert len(df) == len(log_curve.forward_ladder_dates)


@pytest.mark.phase2
def test_pillars_defensive_copy(log_curve):
    """Mutating the DataFrame returned by pillars() does not affect curve state."""
    df = log_curve.pillars()
    original_dfs = [p.discount_factor for p in log_curve.pillars_tuple]
    df.iloc[0, df.columns.get_loc("discount_factor")] = -999.0
    # Second call returns clean DataFrame.
    df2 = log_curve.pillars()
    assert df2.iloc[0]["discount_factor"] == original_dfs[0]
    # Internal state unaltered.
    assert [p.discount_factor for p in log_curve.pillars_tuple] == original_dfs


@pytest.mark.phase2
def test_to_summary_dict_keys(log_curve):
    """Summary dict contains the documented top-level keys and is JSON-serialisable."""
    import json

    summary = log_curve.to_summary_dict()
    assert set(summary.keys()) == {
        "valuation_date",
        "day_count",
        "interpolation",
        "pillars",
        "forward_ladder",
    }
    # Round-trip through JSON without errors.
    encoded = json.dumps(summary)
    decoded = json.loads(encoded)
    assert decoded["interpolation"] == "log_linear_df"
    assert decoded["day_count"] == "Act/360"
    assert decoded["valuation_date"] == log_curve.valuation_date.isoformat()
    assert len(decoded["pillars"]) == len(log_curve.pillars_tuple)
    assert len(decoded["forward_ladder"]) == len(log_curve.forward_ladder_dates)


@pytest.mark.phase2
def test_linear_zero_anchor_segment_consistency(zero_curve):
    """Between val_date and first pillar, linear_zero uses flat-extrapolated zero.

    The endpoint values match (anchor=1.0, first pillar=bootstrapped DF) by construction.
    """
    first = zero_curve.pillars_tuple[0]
    mid = zero_curve.valuation_date + (first.end_date - zero_curve.valuation_date) / 2
    df_mid = zero_curve.df_at(mid)
    # Sanity: 0 < df_mid < 1, and df_mid is between anchor and first pillar's DF.
    assert first.discount_factor < df_mid < 1.0
