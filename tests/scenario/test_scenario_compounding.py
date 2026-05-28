"""Compounding identity tests.

* Empty MPC path with flat rate r: ``index[N] = product(1 + r*delta_t/365)``.
* Single-meeting cut: rate flips on meeting_date and forward (Convention B).
"""

from __future__ import annotations

import pytest

from rates.core.scenario import build_scenario


@pytest.mark.phase3
def test_flat_rate_matches_closed_form(
    market_with_tlref, mpc_path_empty, conventions, tr_calendar, diagnostics
):
    """No MPC events ⇒ piecewise-flat 45% compounding via Act/365."""
    horizon = 60
    res = build_scenario(
        market_with_tlref,
        mpc_path_empty,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=horizon,
    )
    r = 0.45
    expected = 1.0
    for i in range(1, len(res.mid.dates)):
        delta = (res.mid.dates[i] - res.mid.dates[i - 1]).days
        expected *= 1.0 + r * delta / 365.0
    assert res.mid.values[-1] == pytest.approx(expected, abs=1e-15)


@pytest.mark.phase3
def test_mpc_step_changes_rate_on_meeting_date(
    market_with_tlref, mpc_path_single_cut, conventions, tr_calendar, diagnostics
):
    """Before meeting_date (2026-09-11) the daily ratio uses 45%; from meeting_date
    forward it uses 40% (45% - 500bps).

    We test the *daily growth ratio* index[i]/index[i-1] equals
    ``1 + rate(dates[i-1]) * delta_t / 365`` with rate switching at the meeting.
    """
    res = build_scenario(
        market_with_tlref,
        mpc_path_single_cut,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=120,
    )

    from datetime import date as _d

    meeting = _d(2026, 9, 11)
    saw_pre = False
    saw_post = False
    for i in range(1, len(res.mid.dates)):
        prev = res.mid.dates[i - 1]
        delta = (res.mid.dates[i] - prev).days
        rate = 0.45 if prev < meeting else 0.40
        expected_ratio = 1.0 + rate * delta / 365.0
        actual_ratio = res.mid.values[i] / res.mid.values[i - 1]
        assert actual_ratio == pytest.approx(expected_ratio, abs=1e-14)
        if prev < meeting:
            saw_pre = True
        else:
            saw_post = True
    assert saw_pre and saw_post


@pytest.mark.phase3
def test_band_shift_direction(
    market_with_tlref, mpc_path_three_cuts, conventions, tr_calendar, diagnostics
):
    """band=150 ⇒ high values >= mid >= low at every grid point (positive rates)."""
    res = build_scenario(
        market_with_tlref,
        mpc_path_three_cuts,
        band_bps=150,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=200,
    )
    assert res.low is not None and res.high is not None
    for i in range(len(res.mid.values)):
        # Anchor identity at i=0; later strictly ordered.
        assert res.low.values[i] <= res.mid.values[i] <= res.high.values[i]
        if i > 0:
            assert res.low.values[i] < res.mid.values[i] < res.high.values[i]


@pytest.mark.phase3
def test_band_shift_magnitude(
    market_with_tlref, mpc_path_empty, conventions, tr_calendar, diagnostics
):
    """band=N flat-path: rate(high) - rate(mid) = N/10000 exactly per day."""
    res = build_scenario(
        market_with_tlref,
        mpc_path_empty,
        band_bps=150,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=30,
    )
    assert res.high is not None and res.low is not None
    # Solve the per-step rate from index ratio.
    for i in range(1, len(res.mid.dates)):
        delta = (res.mid.dates[i] - res.mid.dates[i - 1]).days
        r_mid = (res.mid.values[i] / res.mid.values[i - 1] - 1.0) * 365.0 / delta
        r_high = (res.high.values[i] / res.high.values[i - 1] - 1.0) * 365.0 / delta
        r_low = (res.low.values[i] / res.low.values[i - 1] - 1.0) * 365.0 / delta
        assert r_high - r_mid == pytest.approx(0.0150, abs=1e-12)
        assert r_mid - r_low == pytest.approx(0.0150, abs=1e-12)


@pytest.mark.phase3
def test_horizon_days_zero_returns_anchor_only(
    market_with_tlref, mpc_path_empty, conventions, tr_calendar, diagnostics
):
    res = build_scenario(
        market_with_tlref,
        mpc_path_empty,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
        horizon_days=0,
    )
    assert len(res.mid.dates) == 1
    assert res.mid.values == (1.0,)


@pytest.mark.phase3
def test_horizon_auto_default_produces_long_grid(
    market_with_tlref, mpc_path_empty, conventions, tr_calendar, diagnostics
):
    """horizon_days=None ⇒ ~10y business-day grid (2500+ entries)."""
    res = build_scenario(
        market_with_tlref,
        mpc_path_empty,
        band_bps=0,
        conventions=conventions,
        calendar=tr_calendar,
        diagnostics=diagnostics,
    )
    # 10y of business days: ~252 BD/year * 10 ≈ 2520; allow generous bounds.
    assert 2400 < len(res.mid.dates) < 2700
