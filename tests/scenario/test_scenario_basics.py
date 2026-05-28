"""Scenario engine — anchor identity, BISTTREF requirement, band short-circuit.

Aligns with F-004, F-005, C-003 invariants:
* ``index[0] == 1.0`` for every produced index (mid/low/high).
* ``band_bps == 0`` ⇒ only mid is populated; low and high are None (G2).
* Missing BISTTREF ⇒ SC_BISTTREF_MISSING ERROR and exception.
"""

from __future__ import annotations

import pytest

from rates.core.scenario import (
    DailyIndex,
    MissingInitialTLREFError,
    build_scenario,
)


@pytest.mark.phase3
def test_anchor_index_equals_one(
    market_with_tlref, mpc_path_three_cuts, conventions, tr_calendar, diagnostics
):
    res = build_scenario(
        market_with_tlref,
        mpc_path_three_cuts,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=63,
    )
    assert res.mid.values[0] == 1.0
    assert res.mid.dates[0] == res.valuation_date


@pytest.mark.phase3
def test_band_zero_returns_mid_only(
    market_with_tlref, mpc_path_three_cuts, conventions, tr_calendar, diagnostics
):
    res = build_scenario(
        market_with_tlref,
        mpc_path_three_cuts,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=63,
    )
    assert res.low is None
    assert res.high is None
    assert isinstance(res.mid, DailyIndex)
    assert res.band_bps == 0


@pytest.mark.phase3
def test_band_positive_produces_all_three(
    market_with_tlref, mpc_path_three_cuts, conventions, tr_calendar, diagnostics
):
    res = build_scenario(
        market_with_tlref,
        mpc_path_three_cuts,
        band_bps=150,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=63,
    )
    assert res.low is not None
    assert res.high is not None
    assert res.low.values[0] == 1.0
    assert res.high.values[0] == 1.0
    assert res.band_bps == 150


@pytest.mark.phase3
def test_missing_bisttref_raises_and_records_error(
    market_no_tlref, mpc_path_empty, conventions, tr_calendar, diagnostics
):
    with pytest.raises(MissingInitialTLREFError):
        build_scenario(
            market_no_tlref,
            mpc_path_empty,
            band_bps=0,
            conventions=conventions,
            calendar=tr_calendar,
            diagnostics=diagnostics,
            horizon_days=10,
        )
    codes = [d.code for d in diagnostics.to_list()]
    assert "SC_BISTTREF_MISSING" in codes
    assert diagnostics.has_errors()
    assert diagnostics.exit_code() == 2


@pytest.mark.phase3
def test_negative_band_bps_raises(
    market_with_tlref, mpc_path_empty, conventions, tr_calendar, diagnostics
):
    with pytest.raises(ValueError, match="band_bps"):
        build_scenario(
            market_with_tlref,
            mpc_path_empty,
            band_bps=-50,
            conventions=conventions,
            calendar=tr_calendar,
            diagnostics=diagnostics,
            horizon_days=10,
        )
