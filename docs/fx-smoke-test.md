# v0.3.0 FX Smoke Test — Curated USDTRY Bootstrap

**Date:** 2026-05-28
**Branch:** `release/0.3.0`
**Script:** `scripts/fx_smoke_test.py`
**Fixture root:** `fixtures/fx_smoke_usdtry/`

This smoke test wires the v0.3.0 FX layer through `rates fx bootstrap`
end-to-end on a curated USDTRY snapshot. It is the manual gate behind the
last bullet of `docs/fx-io-architecture.md` §10 — not part of CI.

## What it checks

Per architecture §10:

1. `rates fx bootstrap` returns exit code 0 on the curated fixture.
2. The persisted `data/latest/usdtry_fx_summary.json` round-trips through
   `FXSummary.model_validate_json` (Pydantic v2).
3. **Zero `FX_PARITY_MISMATCH` warnings.** The fixture's forward points
   are constructed *from* the OIS DFs of the sibling TRY + USD snapshots
   under covered interest parity, so by construction the diff is ≤ 1e-4
   of spot — well inside M-105's 1 bp threshold.

The fixture's XCCY 1Y row carries a market-realistic −180 bps spread
purely so the basis-curve path is exercised; the parity check is on the
FX forward leg only and is independent of basis.

## How to run

```bash
PYTHONPATH=src .venv/bin/python scripts/fx_smoke_test.py
```

Expected output:

```
exit 0 (no errors)
OK: 3 forward pillar(s), 1 basis pillar(s), 0 diagnostic(s), 0 FX_PARITY_MISMATCH WARNs.
```

The script shells out to `python -m rates.cli fx bootstrap ...` against a
disposable `tmp` `--output-root`, then re-parses the resulting summary
JSON in-process. A non-zero exit, a missing summary, or any
`FX_PARITY_MISMATCH` diagnostic surfaces as a `FAIL:` line and exit 1.

## How to regenerate (after an OIS or bootstrap-math change)

The committed FX CSV is parity-implied off the committed OIS snapshots. If
either OIS file changes — or if `bootstrap_curve`'s interpolation behaviour
changes in a way that shifts DFs — the forward points must be recomputed:

```bash
PYTHONPATH=src .venv/bin/python scripts/fx_smoke_test.py --regenerate
```

This bootstraps both OIS curves, recomputes `F_parity(t) = S * DF_USD / DF_TRY`
for each smoke tenor (1M / 3M / 6M), converts to forward points using the
pair's `forward_point_scale = 10000`, and rewrites
`fixtures/fx_smoke_usdtry/usdtry_fx_snapshot_20260612.csv` in place with
the standard 6-point half-spread.

Then re-run without `--regenerate` to confirm the new fixture passes the
zero-WARN gate. Commit both the regenerated CSV and any OIS changes that
prompted it in the same PR.

## Why no integration with real market snapshots

This is a *correctness* gate — it answers "does our pipeline strip a
parity-tight snapshot cleanly". A *market-noise* gate (real snapshots
where parity diffs of a few bps are normal) is a separate, deferred
exercise (see `docs/fx-io-architecture.md` §10 closing paragraph). The
two gates would lie at different tolerances and confound each other if
merged.

## What this does NOT cover

- Pricing verbs (`rates fx price-outright / swap / xccy`) — exercised by
  `tests/app/test_run_fx_price.py` and `tests/cli/test_fx_cli.py`.
- V1 OIS regression — see `docs/v1-smoke-test.md`.
- Multi-pair (EURTRY) bootstrap — fixture for v0.4 if needed.
