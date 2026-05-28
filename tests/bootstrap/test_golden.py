"""Golden-curve regression test (22 TYSO-style pillars).

The committed ``golden_curve.json`` captures the deterministic output of
:func:`bootstrap_curve` for a deeply-inverted TRY OIS fixture. This test re-bootstraps
the same inputs and asserts byte-identical DFs (per F-001 determinism criterion).

Regenerate the golden file intentionally via ``scripts/regen_golden_curve.py`` when
conventions or fixture rates change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rates.core.bootstrap import REPRICE_TOLERANCE, bootstrap_curve
from rates.core.calendar import HolidayCalendar
from rates.core.conventions import Conventions
from rates.core.diagnostics import DiagnosticsCollector

from ._golden_inputs import GOLDEN_PATH, GOLDEN_VAL_DATE, build_golden_market


@pytest.fixture(scope="module")
def golden_calendar() -> HolidayCalendar:
    return HolidayCalendar.for_currency(
        "TR",
        years=range(GOLDEN_VAL_DATE.year - 1, GOLDEN_VAL_DATE.year + 20),
        override_dir=Path("config/holidays"),
    )


@pytest.fixture(scope="module")
def golden_payload() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.mark.phase2
def test_golden_curve_byte_identical(golden_calendar, conventions_yaml, golden_payload):
    """Bootstrap output matches the committed golden DFs bit-for-bit."""
    conv = Conventions.load(conventions_yaml)
    dg = DiagnosticsCollector()
    market = build_golden_market(golden_calendar)
    curve = bootstrap_curve(market, conv, golden_calendar, dg)

    expected = golden_payload["pillars"]
    assert len(curve.pillars_tuple) == len(expected)

    for actual, gold in zip(curve.pillars_tuple, expected, strict=True):
        assert actual.tenor_code == gold["tenor_code"]
        assert actual.tenor_days == gold["tenor_days"]
        assert actual.end_date.isoformat() == gold["end_date"]
        assert actual.rate == gold["rate"]
        # repr() preserves full float precision; eval(repr(f)) == f exactly.
        assert repr(actual.discount_factor) == gold["discount_factor_repr"]


@pytest.mark.phase2
def test_golden_curve_reprices_within_tolerance(golden_calendar, conventions_yaml, golden_payload):
    """Every pillar reprices to within 1e-10 absolute (F-001 acceptance)."""
    from rates.core.bootstrap import _annual_coupon_schedule
    from rates.core.daycount import year_fraction
    from rates.core.types import BusinessDayConvention

    conv = Conventions.load(conventions_yaml)
    dg = DiagnosticsCollector()
    market = build_golden_market(golden_calendar)
    curve = bootstrap_curve(market, conv, golden_calendar, dg)

    for p in curve.pillars_tuple:
        if p.tenor_days <= 366:
            tau = year_fraction(curve.valuation_date, p.end_date, curve.day_count)
            residual = abs(p.rate * tau * p.discount_factor - (1.0 - p.discount_factor))
        else:
            schedule = _annual_coupon_schedule(
                curve.valuation_date,
                p.end_date,
                golden_calendar,
                BusinessDayConvention.MODIFIED_FOLLOWING,
            )
            s = 0.0
            prev = curve.valuation_date
            for c in schedule:
                tau_i = year_fraction(prev, c, curve.day_count)
                s += curve.df_at(c) * tau_i
                prev = c
            residual = abs(p.rate * s - (1.0 - p.discount_factor))

        assert residual < REPRICE_TOLERANCE, (
            f"{p.tenor_code} residual {residual:.3e} exceeds {REPRICE_TOLERANCE}"
        )

    assert not dg.has_errors()


@pytest.mark.phase2
def test_golden_curve_summary_dict_serialisable(golden_calendar, conventions_yaml):
    """Golden curve's to_summary_dict() is JSON-serialisable end-to-end."""
    conv = Conventions.load(conventions_yaml)
    dg = DiagnosticsCollector()
    market = build_golden_market(golden_calendar)
    curve = bootstrap_curve(market, conv, golden_calendar, dg)

    summary = curve.to_summary_dict()
    encoded = json.dumps(summary)
    decoded = json.loads(encoded)
    assert decoded["valuation_date"] == GOLDEN_VAL_DATE.isoformat()
    assert decoded["day_count"] == "Act/360"
    assert len(decoded["pillars"]) == 22
    assert len(decoded["forward_ladder"]) == 15
