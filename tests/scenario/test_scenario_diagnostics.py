"""Diagnostics: invalid MPC schedule, negative rate, beyond-horizon meeting, determinism."""

from __future__ import annotations

from datetime import date

import pytest

from rates.core.diagnostics import DiagnosticsCollector
from rates.core.scenario import InvalidMPCScheduleError, build_scenario
from rates.core.types import MPCMeeting, MPCPath


def _codes(dg):
    return [d.code for d in dg.to_list()]


@pytest.mark.phase3
def test_invalid_mpc_unordered_raises(market_with_tlref, conventions, tr_calendar, diagnostics):
    bad = MPCPath(
        meetings=(
            MPCMeeting(meeting_date=date(2026, 12, 18), bps_change=-250),
            MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=-250),
        )
    )
    with pytest.raises(InvalidMPCScheduleError):
        build_scenario(
            market_with_tlref,
            bad,
            band_bps=0,
            conventions=conventions,
            calendar=tr_calendar,
            diagnostics=diagnostics,
            horizon_days=30,
        )
    assert "SC_INVALID_MPC" in _codes(diagnostics)
    assert diagnostics.has_errors()


@pytest.mark.phase3
def test_invalid_mpc_duplicate_dates_raises(
    market_with_tlref, conventions, tr_calendar, diagnostics
):
    dup = MPCPath(
        meetings=(
            MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=-250),
            MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=100),
        )
    )
    with pytest.raises(InvalidMPCScheduleError):
        build_scenario(
            market_with_tlref,
            dup,
            band_bps=0,
            conventions=conventions,
            calendar=tr_calendar,
            diagnostics=diagnostics,
            horizon_days=30,
        )


@pytest.mark.phase3
def test_negative_policy_rate_warns(market_with_tlref, conventions, tr_calendar, diagnostics):
    """Band shift large enough to push rate negative emits SC_NEGATIVE_POLICY_RATE."""
    # 45% - 5000bps = -5%, definitely negative.
    build_scenario(
        market_with_tlref,
        MPCPath(meetings=()),
        band_bps=5000,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=10,
    )
    assert "SC_NEGATIVE_POLICY_RATE" in _codes(diagnostics)
    # WARN, not ERROR: curve still producible.
    assert not diagnostics.has_errors()


@pytest.mark.phase3
def test_meeting_beyond_horizon_warns_and_is_ignored(
    market_with_tlref, conventions, tr_calendar, diagnostics
):
    """A meeting past horizon end ⇒ SC_MEETING_BEYOND_HORIZON, no impact on index."""
    far_path = MPCPath(meetings=(MPCMeeting(meeting_date=date(2099, 1, 1), bps_change=-1000),))
    res = build_scenario(
        market_with_tlref,
        far_path,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=20,
    )
    assert "SC_MEETING_BEYOND_HORIZON" in _codes(diagnostics)
    # Index equals flat 45% recursion.
    expected = 1.0
    for i in range(1, len(res.mid.dates)):
        delta = (res.mid.dates[i] - res.mid.dates[i - 1]).days
        expected *= 1.0 + 0.45 * delta / 365.0
    assert res.mid.values[-1] == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase3
def test_past_meeting_emits_warn_and_ignored(
    market_with_tlref, conventions, tr_calendar, diagnostics
):
    """A past-dated meeting (already baked into BISTTREF) ⇒ SC_MEETING_IN_PAST
    WARN and is filtered before the forward path is built."""
    val = market_with_tlref.valuation_date
    mixed = MPCPath(
        meetings=(
            MPCMeeting(meeting_date=date(val.year - 1, 6, 1), bps_change=-250, rationale="past"),
            MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=-250, rationale="future"),
        )
    )
    res = build_scenario(
        market_with_tlref,
        mixed,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=120,
    )
    codes = _codes(diagnostics)
    assert "SC_MEETING_IN_PAST" in codes
    assert not diagnostics.has_errors()

    # Confirm filter actually applied: same scenario with only the forward meeting
    # produces the same index (past meeting did not double-count).
    only_future = MPCPath(
        meetings=(MPCMeeting(meeting_date=date(2026, 9, 11), bps_change=-250, rationale="future"),)
    )
    res_future = build_scenario(
        market_with_tlref,
        only_future,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=DiagnosticsCollector(),
        horizon_days=120,
    )
    assert res.mid.values == res_future.mid.values


@pytest.mark.phase3
def test_all_past_meetings_index_equals_flat_initial(
    market_with_tlref, conventions, tr_calendar, diagnostics
):
    """All meetings before val_date ⇒ index recursion runs on flat BISTTREF only."""
    val = market_with_tlref.valuation_date
    past = MPCPath(
        meetings=(
            MPCMeeting(meeting_date=date(val.year - 2, 3, 1), bps_change=200),
            MPCMeeting(meeting_date=date(val.year - 1, 7, 1), bps_change=-150),
        )
    )
    res = build_scenario(
        market_with_tlref,
        past,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=20,
    )
    assert _codes(diagnostics).count("SC_MEETING_IN_PAST") == 2
    # Index equals flat 45% recursion (initial BISTTREF, untouched by past meetings).
    expected = 1.0
    for i in range(1, len(res.mid.dates)):
        delta = (res.mid.dates[i] - res.mid.dates[i - 1]).days
        expected *= 1.0 + 0.45 * delta / 365.0
    assert res.mid.values[-1] == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase3
def test_scenario_determinism(market_with_tlref, mpc_path_three_cuts, conventions, tr_calendar):
    """Same input ⇒ byte-identical mid/low/high values across runs."""
    dg1 = DiagnosticsCollector()
    dg2 = DiagnosticsCollector()
    a = build_scenario(
        market_with_tlref,
        mpc_path_three_cuts,
        band_bps=150,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=dg1,
        horizon_days=252,
    )
    b = build_scenario(
        market_with_tlref,
        mpc_path_three_cuts,
        band_bps=150,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=dg2,
        horizon_days=252,
    )
    assert a.mid.values == b.mid.values
    assert a.low.values == b.low.values
    assert a.high.values == b.high.values
