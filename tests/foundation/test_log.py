"""Tests for rates.core.log (M-004)."""

from __future__ import annotations

import pytest


@pytest.mark.phase1
def test_log_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """log() should write to stderr (V1 implementation)."""
    pytest.skip("not yet implemented — assert message lands in capsys.readouterr().err")


@pytest.mark.phase1
def test_log_renders_context_as_key_value_pairs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """log('info', 'msg', foo=1, bar='x') should include 'foo=1 bar=x' in output."""
    pytest.skip("not yet implemented")


@pytest.mark.phase1
def test_log_with_no_context_prints_only_level_and_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty context should not produce trailing whitespace or stray separators."""
    pytest.skip("not yet implemented")
