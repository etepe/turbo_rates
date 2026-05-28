# V1 Smoke Test — Reference Snapshot Bootstrap

**Date:** 2026-05-28
**Branch:** `feature/MON-007-v1-hardening`
**Script:** `scripts/smoke_test.py`
**Inputs:** `ois_calculations.xlsx` (Market_data + User_policy expectation sheets)

The smoke test wires the legacy Excel reference data through the V1 pipeline
end-to-end: read 20 TYSO quotes + the BISTTREF anchor, persist them as a
canonical snapshot CSV, then run `rates bootstrap` and inspect the resulting
summary JSON. This is a manual gate, not part of CI — it depends on a local
copy of the Excel files.

## How to run

```bash
PYTHONPATH=src .venv/bin/python scripts/smoke_test.py
```

By default the script reads `~/Desktop/rates_engine/ois_calculations.xlsx`.
Pass `--ois-xlsx PATH` to override.

## Results

| Metric                       | Value          |
|------------------------------|----------------|
| Valuation date               | 2026-04-14     |
| BISTTREF (initial TLREF)     | 0.399932       |
| Quote rows ingested          | 20             |
| Pillars bootstrapped         | 20             |
| Diagnostics emitted          | 0              |
| Reprice tolerance (F-001)    | 1e-10          |
| Max reprice residual         | within 1e-10   |
| CLI exit code                | 0              |
| Snapshot written             | `data/snapshots/snapshot_20260414.csv` |
| Summary written              | `data/latest/try_ois_summary.json`     |

## Pillar table (CLI output)

```
tenor       end_date     rate         DF
--------------------------------------------------
TYSO2D      2026-04-16  0.401000   0.9977771742
TYSO3D      2026-04-17  0.401000   0.9966694629
TYSO1W      2026-04-21  0.401000   0.9922631041
TYSO2W      2026-04-28  0.402500   0.9845884557
TYSO1M      2026-05-14  0.405000   0.9673518742
TYSO2M      2026-06-15  0.413500   0.9335203832
TYSO3M      2026-07-14  0.416500   0.9047465265
TYSO6M      2026-10-14  0.405000   0.8292733492
TYSO9M      2027-01-14  0.395000   0.7682048546
TYSO1Y      2027-04-14  0.386000   0.7187206772
TYSO18M     2027-10-14  0.372500   0.6125662035
TYSO2Y      2028-04-14  0.361800   0.5383383078
TYSO3Y      2029-04-16  0.345600   0.4133708936
TYSO4Y      2030-04-17  0.332700   0.3252525806
TYSO5Y      2031-04-14  0.322100   0.2619179388
TYSO6Y      2032-04-14  0.313500   0.2135582243
TYSO7Y      2033-04-14  0.306100   0.1771446684
TYSO8Y      2034-04-14  0.299700   0.1490564321
TYSO9Y      2035-04-16  0.294300   0.1264506950
TYSO10Y     2036-04-14  0.289600   0.1086010464
```

## Manual observations

- **Reprice clean:** all 20 pillars satisfy the 1e-10 acceptance per F-001
  without a single diagnostic warning. Bullet (≤ 1Y) and annual-coupon (> 1Y)
  branches both converge closed-form against this curve geometry — no brentq
  fallbacks triggered.
- **TYSO1D dropped:** the source row's bid/ask/mid columns hold formula
  errors (`#VALUE!`); the script treats those as missing and the resulting
  snapshot omits the row. This matches the engine's "no usable quote → skip"
  policy and leaves the curve starting at 2D.
- **Default MPC path used:** the smoke run reads `config/mpc_path.csv` as
  shipped. The Excel-driven MPC path (also encoded on the
  `User_policy expectation` sheet, valuation 2026-04-14) is not yet wired
  in — covered separately by the V1 scenario module. None of the MPC
  meetings on the shipped default path fall in the past relative to
  2026-04-14, so no `SC_MEETING_IN_PAST` warnings fire.
- **Inverted curve shape preserved:** rates decline from ~40% short end
  to ~29% at 10Y, mirroring the deeply-inverted TRY OIS regime in the
  reference workbook. DFs are strictly monotonically decreasing —
  no `BS_NON_MONOTONE_DF` flags, consistent with the input quotes.

The reference workbook holds Excel-computed DFs alongside each tenor; eyeball
comparison vs. the engine output shows agreement to the displayed 4–10
decimal places. The Excel DFs are produced by a different (flawed) chain
documented in the requirements §1; bit-identical parity was an explicit
non-goal (G3 / Phase 1 grill). The smoke test does not assert against the
Excel DFs.
