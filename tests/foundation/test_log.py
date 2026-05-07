"""Tests for rates.core.log (M-004)."""

from __future__ import annotations

import pytest

from rates.core.log import log


@pytest.mark.phase1
def test_log_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """log() should write to stderr (V1 implementation)."""
    log("info", "hello")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello" in captured.err


@pytest.mark.phase1
def test_log_renders_context_as_key_value_pairs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """log('info', 'msg', foo=1, bar='x') should include 'foo=1 bar=x' in output."""
    log("info", "msg", foo=1, bar="x")
    err = capsys.readouterr().err
    assert "foo=1" in err
    assert "bar=x" in err
    assert "msg" in err
    assert "[info]" in err


@pytest.mark.phase1
def test_log_with_no_context_prints_only_level_and_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty context should not produce trailing whitespace or stray separators."""
    log("warning", "plain")
    err = capsys.readouterr().err
    assert err == "[warning] plain\n"
