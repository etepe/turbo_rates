"""Phase 2 curve test fixtures.

Curve tests construct ``OISCurve`` directly from hand-built pillars so they remain
isolated from bootstrap correctness — a regression in bootstrap should not turn into
silent failures of curve interpolation tests.
"""

from __future__ import annotations

from datetime import date

import pytest

from rates.core.curve import OISCurve, Pillar
from rates.core.types import DayCount

_VAL = date(2026, 6, 12)


@pytest.fixture()
def val_date() -> date:
    return _VAL


@pytest.fixture()
def hand_pillars() -> tuple[Pillar, ...]:
    """Four hand-built pillars with arithmetically clean DFs.

    Maturities: 6M, 1Y, 2Y, 5Y. DFs chosen monotonically decreasing.
    """
    return (
        Pillar("M-6M", 183, _VAL, date(2026, 12, 12), 0.45, 0.80),
        Pillar("M-1Y", 365, _VAL, date(2027, 6, 14), 0.42, 0.65),
        Pillar("M-2Y", 730, _VAL, date(2028, 6, 12), 0.39, 0.48),
        Pillar("M-5Y", 1827, _VAL, date(2031, 6, 12), 0.35, 0.22),
    )


@pytest.fixture()
def hand_ladder_dates() -> tuple[tuple[str, date, date], ...]:
    """Single-entry stub ladder used for forward_ladder() tests."""
    return (
        ("1x2", date(2026, 7, 13), date(2026, 8, 12)),
        ("3x6", date(2026, 9, 14), date(2026, 12, 14)),
    )


@pytest.fixture()
def log_curve(hand_pillars, hand_ladder_dates, val_date) -> OISCurve:
    return OISCurve(
        valuation_date=val_date,
        day_count=DayCount.ACT_360,
        interp="log_linear_df",
        pillars_tuple=hand_pillars,
        forward_ladder_dates=hand_ladder_dates,
    )


@pytest.fixture()
def zero_curve(hand_pillars, hand_ladder_dates, val_date) -> OISCurve:
    return OISCurve(
        valuation_date=val_date,
        day_count=DayCount.ACT_360,
        interp="linear_zero",
        pillars_tuple=hand_pillars,
        forward_ladder_dates=hand_ladder_dates,
    )
