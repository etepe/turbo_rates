# V0.4 — Cross-Currency Basis Stripping Calibration (V3) — Requirements

**Domain:** quant
**Depth:** derin
**Date:** 2026-05-28
**Status:** Validated + grill-passed (2026-05-28) — all OQ-401..OQ-406 resolved (D-11..D-17)
**Ticket:** MON-019
**Based on:** `docs/fx-architecture.md` (V2 FX math layer, M-101..M-107),
`docs/fx-io-architecture.md` (v0.3.0 IO/CLI/app wiring, §11 V3 deferral),
`docs/architecture.md` (V1 OIS bootstrap reference pattern),
`CHANGELOG.md` 0.3.0 "Not in this release".

---

## 1. Executive Summary

The FX layer ships today (v0.3.0) with the cross-currency basis in **"quotes
verbatim"** mode: `build_cross_basis_curve` (M-106) copies quoted xccy basis
spreads straight onto pillars without any pricing, and `price_xccy_basis_swap`
(M-107) discards its `dom_ois` / `for_ois` arguments and just returns
`basis.basis_at(maturity)`. V0.4 closes the explicitly-deferred V3 work item:
**strip** the basis spread from par cross-currency basis swaps via a net-PV=0
calibration against the dual OIS curves and the FX spot anchor, and make the
pricer consume the full curve set. This converts the FX basis surface from a
pass-through to a calibrated, repriceable object — the same discipline V1 OIS
bootstrap already enforces.

---

## 2. Goals & Success Criteria

- [ ] `build_cross_basis_curve` strips each xccy basis pillar so the par
      cross-currency basis swap reprices to net PV = 0 within `1e-9`.
- [ ] `price_xccy_basis_swap` consumes `dom_ois`, `for_ois`, and the FX spot
      anchor (via the FX forward curve) — no discarded arguments, no
      `_UNUSED_OIS` placeholder.
- [ ] The `_UNUSED_OIS = cast(OISCurve, None)` placeholder is removed from
      `rates.app`; the pricing path obtains both OIS curves from the persisted
      `FXSummary` (schema v2).
- [ ] `FX_SCHEMA_VERSION` bumps 1 → 2; `FXSummary` carries the OIS pillar lists
      and per-pillar strip diagnostics needed to reconstruct curves for pricing.
- [ ] The curated `fixtures/fx_smoke_usdtry/` fixture is regenerated so the FX
      forwards and the xccy basis quotes are **mutually arbitrage-consistent**,
      and the V0.4 smoke gate passes (reprice to `1e-9`, zero
      `FX_PARITY_MISMATCH`).
- [ ] All existing gates stay green: `mypy --strict`, `ruff`, full `pytest`
      (315 baseline + new `phase9` tests), V1 OIS smoke, FX smoke exit 0.

---

## 3. Functional Requirements

### 3.1 Core Features

```json
{
  "features": [
    {
      "id": "F-301",
      "name": "Par xccy basis stripping (net PV = 0)",
      "description": "Replace M-106 verbatim mode with a tenor-by-tenor strip: for each par cross-currency basis swap quote, solve for the basis spread that drives the constant-notional swap's net PV to zero, using the dual OIS curves (single-curve per ccy: OIS = projection + discount) and the FX spot anchor.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given a par xccy basis swap quote at tenor T, when the curve is built, then the stripped spread reprices that swap to |net PV| < 1e-9 (FX_XCCY_REPRICE_FAIL ERROR otherwise).",
        "Given a quote vector, when stripping runs, then pillars are produced in strictly ascending maturity order with the quoted_on_foreign sign convention preserved.",
        "Given a brentq solver that cannot bracket/converge, when stripping a pillar, then FX_BOOTSTRAP_NON_CONVERGENT ERROR is emitted and the curve is not produced."
      ]
    },
    {
      "id": "F-302",
      "name": "Calibrated xccy pricer",
      "description": "price_xccy_basis_swap consumes dom_ois, for_ois, and the FX spot anchor to price a par cross-currency basis swap at an arbitrary maturity, returning the fair basis spread consistent with the stripped curve. No discarded arguments.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given the stripped curve and the same dual OIS curves, when pricing at a calibrated pillar maturity, then the returned spread equals the stripped pillar spread within 1e-9 (round-trip identity).",
        "Given a maturity between pillars, when pricing, then the returned spread is interpolated under the curve's documented convention."
      ]
    },
    {
      "id": "F-303",
      "name": "Quarterly (3M) xccy coupon schedule",
      "description": "A dedicated quarterly coupon-schedule generator for both xccy legs (3M frequency, joint-calendar rolled), distinct from the V1 annual/bullet OIS schedule. Drives the basis annuity term Sum(DF*tau).",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given spot_date and maturity, when generating the schedule, then coupon dates are 3M-spaced, business-day rolled on each leg's calendar, with the final stub ending exactly on maturity.",
        "Given a leap/holiday boundary, when rolling a coupon date, then the modified-following convention from the currency's OIS conventions is applied."
      ]
    },
    {
      "id": "F-304",
      "name": "FXSummary schema v2 with OIS pillars + strip diagnostics",
      "description": "Bump FX_SCHEMA_VERSION 1 -> 2. FXSummary embeds the domestic and foreign OIS pillar lists (so the pricing path reconstructs both curves) plus a per-basis-pillar strip-quality field (par_spread_pv_check_diff or residual).",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given a v0.4 bootstrap run, when the summary is written, then schema_version == 2 and both OIS pillar lists round-trip into reconstructable OISCurve objects.",
        "Given a persisted v2 summary, when re-read, then dom/for OIS curves + forward curve + basis curve reconstruct without re-running any bootstrap."
      ]
    },
    {
      "id": "F-305",
      "name": "Pricing-side OIS reconstruction (remove _UNUSED_OIS)",
      "description": "rates.app pricing entry points (run_fx_price_xccy) reconstruct dom_ois / for_ois from the persisted FXSummary v2 OIS pillars and pass them to the pricer. The _UNUSED_OIS placeholder is deleted.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given a persisted v2 summary, when run_fx_price_xccy runs, then dom_ois and for_ois are real reconstructed curves, not None.",
        "Given the codebase after this change, when grepping, then _UNUSED_OIS no longer appears."
      ]
    },
    {
      "id": "F-306",
      "name": "Arbitrage-consistent fixture regeneration + reprice smoke gate",
      "description": "Regenerate fixtures/fx_smoke_usdtry/ so FX forward points and xccy basis quotes are mutually consistent under the chosen model. Extend scripts/fx_smoke_test.py to assert the reprice gate (net PV=0 within 1e-9) and zero FX_PARITY_MISMATCH.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given the regenerated fixture, when fx_smoke_test runs, then exit 0 with zero FX_PARITY_MISMATCH and zero FX_XCCY_REPRICE_FAIL.",
        "Given --regenerate, when run after a math change, then the fixture's forwards and basis quotes are recomputed to stay mutually consistent."
      ]
    },
    {
      "id": "F-307",
      "name": "Parity-check semantics under non-zero basis",
      "description": "Resolve whether M-105's FX_PARITY_MISMATCH check (pure CIP today) must become basis-aware once a non-zero stripped basis exists, or whether the FX forward and xccy basis surfaces stay decoupled with documented meaning. (Drives whether the fixture keeps -180 basis or moves to ~0.)",
      "priority": "must-have",
      "acceptance_criteria": [
        "A documented decision exists (architecture doc) stating the relationship between the FX forward parity check and the stripped basis.",
        "The fixture and the parity check are mutually consistent under that decision."
      ]
    }
  ]
}
```

### 3.2 User Flows

Primary flow is unchanged in shape from v0.3.0; the change is in what the math
layer does internally.

1. `rates fx bootstrap --pair USDTRY --as-of YYYY-MM-DD`
2. Orchestrator loads FX snapshot + dual OIS snapshots, bootstraps both OIS
   curves (unchanged), builds the FX forward curve (M-105, unchanged).
3. **New:** `build_cross_basis_curve` (M-106) **strips** each xccy basis pillar
   via net-PV=0 calibration against `dom_ois`, `for_ois`, the FX spot anchor,
   and the F-303 quarterly schedule, instead of copying quotes verbatim.
4. Orchestrator persists `FXSummary` v2 — now including the dom/for OIS pillar
   lists and per-pillar strip residuals.
5. `rates fx price-xccy --pair USDTRY --tenor 1Y` reads the v2 summary,
   reconstructs dom/for OIS + basis curve, and prices via the **calibrated**
   `price_xccy_basis_swap` (F-302, F-305).

### 3.3 Data Requirements

```json
{
  "data_sources": [
    {
      "name": "FX snapshot CSV (spot + forward points + xccy basis rows)",
      "type": "file",
      "format": "CSV (multiplexed by instrument_type)",
      "frequency": "on-demand",
      "auth_required": false
    },
    {
      "name": "Domestic OIS snapshot CSV (TRY)",
      "type": "file",
      "format": "CSV (V1 MarketQuote schema)",
      "frequency": "on-demand",
      "auth_required": false
    },
    {
      "name": "Foreign OIS snapshot CSV (USD/EUR)",
      "type": "file",
      "format": "CSV (V1 MarketQuote schema)",
      "frequency": "on-demand",
      "auth_required": false
    },
    {
      "name": "conventions.yaml (ois_conventions + fx_conventions)",
      "type": "file",
      "format": "YAML",
      "frequency": "on-demand",
      "auth_required": false
    },
    {
      "name": "Persisted FXSummary v2 (pricing-side input)",
      "type": "file",
      "format": "JSON (Pydantic, schema_version=2)",
      "frequency": "on-demand",
      "auth_required": false
    }
  ]
}
```

---

## 4. Non-Functional Requirements

```json
{
  "performance": "Stripping is tenor-by-tenor brentq, one root-solve per pillar; negligible vs OIS bootstrap. No latency budget. Pricing stays a pure read of persisted curves (no re-bootstrap).",
  "security": "Not applicable — local CLI, no network, no secrets.",
  "deployment": {
    "target": "local CLI (rates fx ...)",
    "provider": "n/a"
  },
  "scale": "Single pair per run; pillar counts O(10). No scale concerns.",
  "monitoring": "Diagnostics envelope in FXSummary + stdout pretty-print; new FX_XCCY_REPRICE_FAIL / reuse of FX_BOOTSTRAP_NON_CONVERGENT."
}
```

Engineering NFRs (project-standard gates):

- `mypy --strict src/rates` clean.
- `ruff check src tests scripts` clean.
- `pytest -q` green; new `phase9` marker for V0.4 (mirrors phase6/7/8).
- V1 OIS regression smoke unchanged (`scripts/smoke_test.py` exit 0, reprice 1e-10).
- FX smoke (`scripts/fx_smoke_test.py`) exit 0, zero `FX_PARITY_MISMATCH`,
  zero `FX_XCCY_REPRICE_FAIL`.

---

## 5. Constraints & Assumptions

**Constraints**
- Reuse V1 idioms: brentq solver, `DiagnosticsCollector` threading, frozen
  dataclasses, hexagonal layering (`rates.fx` → `rates.core`, never reverse;
  IO/schema reconstruction adapters live in `rates.app`).
- `rates.core` is not modified for FX. Adding/extending `rates.fx` schedule
  helpers is permitted; the V1 `_annual_coupon_schedule` is **not** reused for
  xccy (F-303 requires a dedicated quarterly generator).
- GitFlow per CLAUDE.md: feature branches off `develop`, PR per discovery stage
  and per impl module; no direct main/develop commits; no merge without user
  approval.

**Modeling decisions (locked in interview 2026-05-28)**
- **D-1** Verbatim mode is **removed**, replaced by V3 par-xccy stripping (single code path).
- **D-2** `FX_SCHEMA_VERSION` bumps **1 → 2**.
- **D-3** Pricing-side OIS reconstruction: **embed OIS pillar lists in FXSummary** (option a); FXSummary stays self-contained, pricing is a pure read.
- **D-4** Swap structure: **constant-notional (non-MtM)**. MtM-resetting notional deferred.
- **D-5** Curve model: **single-curve per currency** — each OIS curve is both projection and discount. Separate xccy-adjusted discount / IBOR projection curves deferred.
- **D-6** Solver: **scipy brentq** (bracketing), reprice tolerance **1e-9** (matches `FX_FORWARD_REPRICE_TOLERANCE`, looser than OIS 1e-10).
- **D-7** Spread placement: **`quoted_on_foreign`** (foreign/USD leg, FX-O5) — unchanged.
- **D-8** Coupon schedule: **quarterly (3M)** for both xccy legs — new generator, deviation from V1 annual/bullet reuse.
- **D-9** *(superseded by D-11 — grill 2026-05-28)* ~~FX forward curve consumed for spot anchor only.~~ The spot-only anchor degenerates the strip to `s = 0`; D-11 now consumes the **full** FX forward curve.
- **D-10** Smoke gate: **reprice to 1e-9 + zero `FX_PARITY_MISMATCH`** (now basis-aware per D-12), fixture `--regenerate`d to V3-consistency.

**Modeling decisions (added by grill 2026-05-28 — resolve OQ-401..OQ-406)**
- **D-11** Strip equation = **Method (i)**: foreign-leg cashflows are converted to domestic at the **full FX forward curve** (not spot). The xccy basis ≡ the market forwards' deviation from CIP. Resolves OQ-401; supersedes D-9. D-4/D-5 stay intact.
- **D-12** `FX_PARITY_MISMATCH` becomes a **basis-aware reprice gate**: parity target is `S·DF_for/DF_dom` adjusted by the stripped basis; it fires only on genuine forward-vs-(CIP+basis) inconsistency. Resolves OQ-402 and F-307.
- **D-13** Strip = **sequential par→pillar bootstrap** (`b_n` solved with `b_1..b_{n-1}` fixed). Each step is closed-form linear, but **brentq is retained** (D-6 unchanged) for V1-pattern consistency and MtM-readiness; closed-form was considered and rejected. Reprice assert `|NPV(s*)| < 1e-9` → `FX_XCCY_REPRICE_FAIL` on breach. Resolves F-301 mechanics.
- **D-14** Quarterly schedule: **single common Act/360** day-count on both legs + **back stub**. The TRY-leg Act/360 (vs its native OIS day-count) is a **documented simplification**. Resolves OQ-403.
- **D-15** FX forwards interpolated to quarterly coupon dates via **M-103 log-linear in log(F/S)** (reuse `FXForwardCurve.forward_at`). Resolves OQ-405.
- **D-16** FXSummary v2 OIS persistence = **V1 `PillarOut` embedded twice** (`dom_ois_pillars` / `for_ois_pillars`) + a per-ccy meta block (valuation_date, day_count, interpolation) for exact `_curve_from_summary`-style reconstruction. Resolves OQ-404.
- **D-17** EURTRY uses the **same code path** (no special-casing); V0.4 fixture/smoke is **USDTRY-only**, EURTRY exercised by unit tests. Resolves OQ-406.

**Assumptions**
- The xccy basis swap is floating-floating, par notional, with notional
  exchange at start (spot) and maturity.
- Joint calendar (domestic + foreign) governs coupon/settlement rolling, as in
  the existing FX layer.
- Only USDTRY (and EURTRY by the same code path) are in scope; no new pairs.

---

## 6. Out of Scope

- **MtM-resetting (mark-to-market) cross-currency swaps** — notional reset at
  each coupon. Deferred (would add FX-forward reset terms; constant-notional only).
- **Separate xccy-adjusted discount curve / IBOR projection curve** — single-
  curve-per-ccy only.
- **DV01 / risk decomposition** for xccy (FX-O3 remains deferred).
- **New currency pairs** beyond USDTRY/EURTRY.
- **FX options / vol surface**, NDF/offshore TRY, real-time streaming.
- **Bloomberg / Refinitiv adapters** (Protocol exists; impls later).
- **Market-noise integration smoke** against real-world snapshots (separate gate).

---

## 7. Open Questions — all RESOLVED (grill-me, 2026-05-28)

> All six open questions were resolved in the grill stress-test. The central
> risk (OQ-401) drove decisions D-11..D-12; the rest map to D-13..D-17.

| ID | Question | Resolution | Decision |
|----|----------|-----------|----------|
| **OQ-401** | **CIP-consistency of the strip.** Under D-4/D-5/D-9 the par xccy swap net PV reduces to `-N_dom·s·A_for`, so the strip yields **s = 0** — yet a non-zero arbitrage-free basis ⟺ FX forwards deviating from CIP, conflicting with the parity-tight −180 bps fixture. | **Method (i)**: convert foreign-leg cashflows at the **full FX forward curve**; basis ≡ forward-vs-CIP deviation. D-9 superseded. | **D-11** |
| OQ-402 | **Parity-check semantics (F-307).** Should `FX_PARITY_MISMATCH` compare against basis-adjusted parity or pure CIP? | **Basis-aware reprice gate**: parity target adjusted by stripped basis; fires only on genuine inconsistency. | **D-12** |
| OQ-403 | **Quarterly schedule day-count / stub.** | **Single common Act/360 + back stub**; TRY-leg Act/360 is a documented simplification. | **D-14** |
| OQ-404 | **OIS pillar persistence shape in FXSummary v2.** | **Reuse V1 `PillarOut` ×2 + per-ccy meta block** (valuation_date, day_count, interpolation). | **D-16** |
| OQ-405 | **Interpolation of FX forwards to quarterly coupon dates.** | **M-103 log-linear in log(F/S)** (reuse `forward_at`). | **D-15** |
| OQ-406 | **EURTRY coverage.** | **Same code path; USDTRY-only fixture/smoke**, EURTRY by unit tests. | **D-17** |

**Strip mechanics (OQ-401 follow-ups, resolved):** the strip is a **sequential
par→pillar bootstrap**; each step is closed-form linear in `s`, but **brentq is
retained** (D-6/D-13) for V1-pattern consistency and MtM-readiness, guarded by a
reprice assert (`|NPV(s*)| < 1e-9` → `FX_XCCY_REPRICE_FAIL`).

---

## 8. Implementation Hints

- **Reference pattern:** `src/rates/core/bootstrap.py` — `REPRICE_TOLERANCE`,
  `brentq` bounds (`_BRENTQ_DF_LO/HI`, `BRENTQ_XTOL/RTOL/MAXITER`),
  `_reprice_pillar`, and the closed-form-then-brentq structure. Mirror the
  reprice-validation loop with an FX-side `FX_XCCY_REPRICE_FAIL` ERROR.
- **Stripping seam already exists:** M-106 already accepts `dom_ois`, `for_ois`,
  `fx_forward`, `fx_convention` (currently `_ = (...)`-discarded); M-107 already
  accepts `dom_ois`, `for_ois`. The signatures are stable — V0.4 fills the bodies.
- **Schema mapper:** follow `FXSummary.from_domain` (M-109) and the V1
  `_curve_from_summary` / `_fx_forward_from_summary` reconstruction adapters in
  `rates.app` for the new OIS-pillar embedding (F-304/F-305).
- **Diagnostics:** `FX_BOOTSTRAP_NON_CONVERGENT` already declared in
  `rates.fx.types`; add `FX_XCCY_REPRICE_FAIL` there.
- **Tests:** new `phase9` marker (mirrors v0.2.0 `phase6/7`, v0.3.0 `phase8`).
- **Suggested module agents:** M-106 strip math + M-107 pricer = **opus**
  (core numerics); schema/persistence/app wiring = **sonnet/haiku**; fixture
  regeneration script = **sonnet**.

---

## 9. Build / PR Plan (GitFlow, per CLAUDE.md)

Each discovery doc and each impl module ships as its own PR to `develop`:

1. `feature/MON-019-v04-xccy-requirements` — **this document**.
2. `feature/MON-020-v04-xccy-architecture` — architecture doc (post-grill).
3. `feature/MON-021-v04-xccy-impl-<module>` — implementation, module by module
   (schedule → strip → schema/persist → pricer/app → fixture/smoke).

No code is written until this requirements document and the architecture
document are validated.
