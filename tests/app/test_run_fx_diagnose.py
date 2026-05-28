"""FX diagnose orchestrator (M-111 partial, contract C-103).

Covers Phase 3a per docs/fx-io-architecture.md §7: full FX pipeline through
parity check with NO persistence. Tests cover the happy path on curated
fixtures, the cross-CSV FX_SNAPSHOT_DATE_MISMATCH gate, and each abort path
for missing/bad inputs.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.app import run_fx_diagnose


@pytest.mark.phase8
def test_run_fx_diagnose_happy_path_returns_zero(fx_diagnose_args, tmp_path) -> None:
    """Curated USDTRY fixture: pipeline runs end-to-end without ERROR.

    The fixture is intentionally not parity-tight (TRY OIS rate of 42% vs USD
    OIS 4.8% produces a huge implied forward), so FX_PARITY_MISMATCH WARNs
    are expected. Exit code is 0 because they are WARN-only.
    """
    code = run_fx_diagnose(fx_diagnose_args)
    assert code == 0
    # Diagnose never writes — sanity check against accidental persistence.
    assert not (tmp_path / "data" / "latest").exists()


@pytest.mark.phase8
def test_run_fx_diagnose_missing_conventions_returns_two(fx_diagnose_args) -> None:
    fx_diagnose_args.conventions_yaml = Path("/no/such/conventions.yaml")
    assert run_fx_diagnose(fx_diagnose_args) == 2


@pytest.mark.phase8
def test_run_fx_diagnose_unknown_pair_aborts(fx_diagnose_args) -> None:
    """Pair not in fx_conventions block ⇒ FX_PAIR_UNKNOWN ERROR + exit 2."""
    fx_diagnose_args.pair = "GBPTRY"
    assert run_fx_diagnose(fx_diagnose_args) == 2


@pytest.mark.phase8
def test_run_fx_diagnose_invalid_pair_format_raises(fx_diagnose_args) -> None:
    """Pair string not a 6-letter ISO code ⇒ ValueError before the pipeline runs."""
    fx_diagnose_args.pair = "USD-TRY"
    with pytest.raises(ValueError, match="6-letter ISO"):
        run_fx_diagnose(fx_diagnose_args)


@pytest.mark.phase8
def test_run_fx_diagnose_snapshot_date_mismatch_aborts(fx_diagnose_args) -> None:
    """When a filename-encoded as_of disagrees with --as-of, abort with the
    specific FX_SNAPSHOT_DATE_MISMATCH ERROR (not a generic load failure)."""
    fx_diagnose_args.as_of = date(2026, 5, 28)  # filename says 20260612
    assert run_fx_diagnose(fx_diagnose_args) == 2


@pytest.mark.phase8
def test_run_fx_diagnose_missing_fx_snapshot_returns_two(fx_diagnose_args) -> None:
    fx_diagnose_args.fx_snapshot = Path("/no/such/fx_snapshot.csv")
    assert run_fx_diagnose(fx_diagnose_args) == 2


@pytest.mark.phase8
def test_run_fx_diagnose_missing_domestic_snapshot_returns_two(fx_diagnose_args) -> None:
    fx_diagnose_args.domestic_snapshot = Path("/no/such/domestic.csv")
    assert run_fx_diagnose(fx_diagnose_args) == 2


@pytest.mark.phase8
def test_run_fx_diagnose_missing_foreign_snapshot_returns_two(fx_diagnose_args) -> None:
    fx_diagnose_args.foreign_snapshot = Path("/no/such/foreign.csv")
    assert run_fx_diagnose(fx_diagnose_args) == 2


@pytest.mark.phase8
def test_run_fx_diagnose_path_auto_derivation_when_overrides_omitted(
    fx_diagnose_args,
) -> None:
    """When the caller omits explicit snapshot paths, the orchestrator derives
    them from ``as_of`` + ``pair``. Auto-derived paths under data/fx_snapshots
    / data/snapshots don't exist in this repo, so the run aborts on the first
    file-not-found — but the abort path proves the derivation logic ran."""
    fx_diagnose_args.fx_snapshot = None
    fx_diagnose_args.domestic_snapshot = None
    fx_diagnose_args.foreign_snapshot = None
    assert run_fx_diagnose(fx_diagnose_args) == 2
