# V0.4 — Cross-Currency Basis Stripping Calibration (V3) — Architecture

**Based on:** `docs/v04-xccy-calibration-requirements.md` (MON-019, validated +
grill-passed 2026-05-28 — F-301..F-307, D-1..D-17, OQ-401..OQ-406),
`docs/fx-architecture.md` (V2 FX math layer, M-101..M-107, C-101..C-108, §6
diagnostics, §9 FX-O* decisions), `docs/fx-io-architecture.md` (v0.3.0 IO/CLI/app
wiring, M-108..M-112), `docs/architecture.md` (V1 OIS bootstrap reference).
**Date:** 2026-05-28
**Status:** Draft — Pending Validation (grill-me + human)
**Ticket:** MON-020

This document turns the locked V0.4 requirements into an implementable design.
It freezes module boundaries, the exact strip mathematics, the FXSummary v2
schema diff, the diagnostic-code changes, and a module-by-module build order so
the implementation (MON-021-*) is traceable to documented decisions before any
math is written. **No new modelling decisions are introduced here** — every
choice traces to a locked D-1..D-17 / OQ-401..OQ-406 resolution, plus two
architect-level decisions confirmed with the user (A-1, A-2 in §8).

---

## 1. Architectural Drivers (delta from v0.3.0)

The FX layer's macro-drivers (D1..D7 in `docs/fx-architecture.md` §2) are
unchanged. V0.4 adds four delta-drivers specific to closing the V3 strip:

| # | Driver | Consequence for this design |
|---|--------|------------------------------|
| **DV-1** (complexity / risk) | The strip equation is the hard, novel part. A par xccy basis swap net-PV=0 calibration over the dual OIS curves **and** the full FX forward curve (Method (i), D-11) is the highest-risk numeric in V0.4. | Strip math (M-106) + pricer (M-107) get **opus**; everything else (schedule helper, schema, app wiring, fixture) is mechanical. Reprice assert (`FX_XCCY_REPRICE_FAIL`, D-13) gates correctness exactly as V1 `BS_REPRICE_FAIL` does. |
| **DV-2** (change / coupling) | Pricing must reconstruct **both** OIS curves from the persisted summary — the pricing path can no longer pass `None` (`_UNUSED_OIS`). | `FXSummary` v2 embeds dom/for OIS pillar lists + per-ccy meta (D-16); reconstruction adapters live in `rates.app` (no `rates.io` → `rates.fx` cycle, no `rates.fx` → `rates.io`). |
| **DV-3** (correctness / consistency) | The FX forward surface and the xccy basis surface are no longer decoupled: a non-zero basis ⟺ forwards deviating from CIP. | `FX_PARITY_MISMATCH` becomes a **basis-aware** gate (D-12): it compares the market forwards against `CIP + stripped basis`, not pure CIP. Fixture forwards are regenerated to embed the −180 bps basis (A-1) so the gate is meaningful, not vacuous. |
| **DV-4** (reuse / boundary discipline) | xccy legs are **quarterly (3M)**; the V1 annual/bullet schedule cannot be reused (requirements §5 constraint). | A dedicated quarterly schedule generator is added as a **new module** `rates.fx.schedule` (M-113, A-2) rather than a private helper, so it is independently unit-testable and reusable by a future MtM/pricer path. |

**No new external dependencies.** No `rates.core` edits. Layering (`rates.fx` →
`rates.core`, never reverse; IO/schema reconstruction in `rates.app`) is
preserved.

---

## 2. Tech Stack

Identical to v0.3.0 — V0.4 is a math + schema change inside the existing stack.

| Layer | Choice | Rationale | Rejected |
|-------|--------|-----------|----------|
| Root solver | `scipy.optimize.brentq` | D-6/D-13: matches V1 `bootstrap_curve`; bracketing solver, MtM-ready even though each step is closed-form-linear. | Closed-form-only (considered, rejected D-13 — loses V1-pattern consistency + MtM readiness). |
| Reprice tol | `1e-9` absolute on NPV | D-6: matches `FX_FORWARD_REPRICE_TOLERANCE`; looser than OIS `1e-10` because spot/forward quotes carry larger precision noise. | `1e-10` (OIS-tight; unjustified for FX-noise inputs). |
| Schema | Pydantic v2 | Existing `rates.io.schemas`; `FXSummary` already lives there. | New schema lib (no value). |
| Schedule day-count | single common `Act/360` + back stub | D-14; TRY-leg Act/360 vs native OIS day-count is a documented simplification (OQ-403). | Per-leg native day-count (deferred — out of scope; adds a basis-of-basis nuance V0.4 does not need). |
| FX-forward interp to coupon dates | M-103 log-linear in `log(F/S)` (`FXForwardCurve.forward_at`) | D-15 (OQ-405): reuse the existing accessor; no new interpolation surface. | Linear-on-points / cubic (new surface, no requirement). |
| Tests | pytest, new `phase9` marker | Mirrors `phase6`/`phase7`/`phase8` boundary discipline. | Reuse `phase8` (loses selective re-run). |

---

## 3. Module Decomposition

### 3.1 Module Overview

Modules continue the V2/v0.3.0 numbering (which ended at M-112). One **new**
module (M-113); five **modified** modules. Verbatim mode (D-1) is **removed**,
not flagged — single code path.

| ID | Module | Change | Responsibility (V0.4) | Complexity | Agent | Features |
|----|--------|--------|------------------------|------------|-------|----------|
| **M-113** | `rates.fx.schedule` | **new** | Quarterly (3M) coupon-schedule generator for xccy legs: joint-calendar-rolled, single Act/360, back stub ending exactly on maturity. | medium | **opus** | F-303 |
| **M-106** | `rates.fx.bootstrap_basis` | **rewrite body** | Strip each xccy basis pillar via sequential par→pillar net-PV=0 calibration (Method (i)): foreign leg converted at the full FX forward curve, discounted on dom OIS. brentq + reprice assert. Removes `_ = (...)` discard. | **high** | **opus** | F-301 |
| **M-107** | `rates.fx.pricer` | **rewrite `price_xccy_basis_swap`** | Price a par xccy basis swap at an arbitrary maturity by re-running the same net-PV=0 solve against dom/for OIS + FX forward + stripped basis term-structure. No discarded args. | **high** | **opus** | F-302 |
| **M-109** | `rates.io.schemas` (FX) | **extend** | `FX_SCHEMA_VERSION` 1→2; add `dom_ois_pillars` / `for_ois_pillars` (`PillarOut` ×2) + per-ccy `FXOisMeta` blocks + per-basis-pillar `strip_residual`. | low | sonnet | F-304 |
| **M-111** | `rates.app` (FX) | **extend** | Persist OIS pillars into FXSummary v2; add `_dom_ois_from_summary` / `_for_ois_from_summary` reconstruction adapters; delete `_UNUSED_OIS`; `run_fx_price_xccy` reconstructs both OIS curves and passes them to the calibrated pricer. | medium | sonnet | F-304, F-305 |
| **M-110** | `rates.io.persistence` (FX) | **touch (mapper)** | `FXSummary.from_domain` call site gains the two OIS curves + strip residuals; Parquet partition shape unchanged. | low | haiku | F-304 |
| — | `scripts/fx_smoke_test.py` + fixture | **extend** | Regenerate forwards to embed −180 bps basis (A-1); assert reprice `1e-9` + zero basis-aware `FX_PARITY_MISMATCH` + zero `FX_XCCY_REPRICE_FAIL`. | medium | sonnet | F-306, F-307 |

**Feature → module trace (every F-3xx hits ≥1 module):**

| Feature | Modules |
|---------|---------|
| F-301 Par xccy strip (net PV=0) | M-106 (+ M-113 schedule, consumes M-103/M-105 forward, dual OIS) |
| F-302 Calibrated pricer | M-107 (+ M-113, M-106 shared core) |
| F-303 Quarterly schedule | M-113 |
| F-304 FXSummary v2 + OIS pillars + strip diag | M-109, M-110, M-111 |
| F-305 Pricing-side OIS reconstruction (remove `_UNUSED_OIS`) | M-111 |
| F-306 Arbitrage-consistent fixture + reprice smoke gate | `fx_smoke_test.py`, fixture |
| F-307 Parity-check semantics under non-zero basis | M-105 (gate semantics) + `fx_smoke_test.py` |

### 3.2 Module Details

```json
{
  "modules": [
    {
      "module_id": "M-113",
      "name": "rates.fx.schedule",
      "responsibility": "Generate the quarterly (3M) xccy coupon schedule: joint-calendar-rolled coupon dates, single Act/360 accrual, back stub ending exactly on maturity.",
      "owns_data": ["XccySchedule (coupon dates + year-fractions)"],
      "depends_on": ["M-101 (rates.fx.types)", "rates.core.calendar", "rates.core.daycount", "rates.core.types (BusinessDayConvention, DayCount)"],
      "external_deps": [],
      "features_served": ["F-303"],
      "complexity": "medium",
      "suggested_agent": "opus",
      "new_file": "src/rates/fx/schedule.py",
      "implementation_note": "Reuse rates.core.bootstrap._add_months/_roll idioms (do NOT import the private helpers — re-implement the 3M-step version locally to avoid coupling to core internals; the _roll logic is small). Modified-following BDC comes from the currency's OIS convention. Final coupon date == maturity exactly (back stub); the stub period's tau is the actual Act/360 fraction (may be < 0.25)."
    },
    {
      "module_id": "M-106",
      "name": "rates.fx.bootstrap_basis (strip rewrite)",
      "responsibility": "Strip a CrossCurrencyBasisCurve from xccy par-swap quotes via sequential par->pillar net-PV=0 calibration (Method (i)).",
      "owns_data": ["CrossCurrencyBasisCurve (stripped term-structure)", "per-pillar strip residual"],
      "depends_on": ["M-101", "M-104 (basis_curve)", "M-103 (forward_curve.forward_at)", "M-113 (schedule)", "rates.core.curve (OISCurve.df_at)"],
      "external_deps": ["scipy.optimize.brentq"],
      "features_served": ["F-301"],
      "complexity": "high",
      "suggested_agent": "opus",
      "modified_file": "src/rates/fx/bootstrap_basis.py",
      "implementation_note": "Signature is already stable (C-104). Remove the `_ = (dom_ois, for_ois, fx_forward, fx_convention)` discard. The strip core (net-PV residual fn + sequential solve) is shared with M-107 via a private module-level helper so the pricer round-trips identically (F-302)."
    },
    {
      "module_id": "M-107",
      "name": "rates.fx.pricer (price_xccy_basis_swap rewrite)",
      "responsibility": "Price a par xccy basis swap at an arbitrary maturity by solving net-PV=0 for the fair flat spread, consistent with the stripped term-structure.",
      "owns_data": [],
      "depends_on": ["M-104", "M-103", "M-113", "rates.core.curve", "M-106 (shared strip core)"],
      "external_deps": ["scipy.optimize.brentq"],
      "features_served": ["F-302"],
      "complexity": "high",
      "suggested_agent": "opus",
      "modified_file": "src/rates/fx/pricer.py",
      "implementation_note": "Signature C-108 unchanged; the FX forward curve is reachable via the basis curve? NO — C-108 has no fx_forward arg. See C-108 note in section 4: the pricer at a CALIBRATED pillar returns the stored stripped spread (round-trip identity, F-302 AC#1); between pillars it interpolates under the curve's documented convention (FX-O2 piecewise-linear bps, F-302 AC#2). A full re-solve at arbitrary maturity needs the FX forward curve, which is NOT in C-108 today — handled by the contract-extension decision in section 4 (C-108')."
    },
    {
      "module_id": "M-109",
      "name": "rates.io.schemas (FXSummary v2)",
      "responsibility": "Bump FX_SCHEMA_VERSION 1->2; embed dom/for OIS pillar lists + per-ccy meta + per-basis-pillar strip residual.",
      "owns_data": ["FXSummary v2 JSON schema", "FXOisMeta"],
      "depends_on": ["M-101"],
      "external_deps": [],
      "features_served": ["F-304"],
      "complexity": "low",
      "suggested_agent": "sonnet",
      "modified_file": "src/rates/io/schemas.py",
      "new_schema_file": "docs/schemas/fx_summary.schema.json (regenerated)"
    },
    {
      "module_id": "M-111",
      "name": "rates.app (FX reconstruction + pricing)",
      "responsibility": "Persist OIS pillars into FXSummary v2; reconstruct dom/for OIS for pricing; delete _UNUSED_OIS; pass real curves to the calibrated pricer.",
      "owns_data": ["FXSummary v2 write call site", "dom/for OIS reconstruction adapters"],
      "depends_on": ["M-109", "M-110", "M-106", "M-107", "M-005 (bootstrap, indirectly via persisted pillars)", "rates.core.curve"],
      "external_deps": [],
      "features_served": ["F-304", "F-305"],
      "complexity": "medium",
      "suggested_agent": "sonnet",
      "modified_file": "src/rates/app.py"
    },
    {
      "module_id": "M-110",
      "name": "rates.io.persistence (FX mapper)",
      "responsibility": "Thread dom/for OIS curves + strip residuals into FXSummary.from_domain; Parquet partition layout unchanged.",
      "owns_data": [],
      "depends_on": ["M-109", "M-103", "M-104"],
      "external_deps": ["pyarrow", "filesystem"],
      "features_served": ["F-304"],
      "complexity": "low",
      "suggested_agent": "haiku",
      "modified_file": "src/rates/io/persistence.py (or the from_domain call site in rates.app — see C-102)"
    }
  ]
}
```

---

## 4. Interface Contracts

Contract IDs continue from V2 (which ended at C-108). **Two existing contracts
change semantics without changing signature** (C-104, C-108); one is **extended**
(C-108'); two are **new** (C-109, C-110).

```json
{
  "contracts": [
    {
      "contract_id": "C-109",
      "from": "M-106 (bootstrap_basis), M-107 (pricer)",
      "to": "M-113 (rates.fx.schedule)",
      "type": "function_call",
      "interface": {
        "name": "build_quarterly_xccy_schedule",
        "input": {
          "spot_date": "date  (curve spot, t_0)",
          "maturity": "date  (final settlement, t_N)",
          "dom_calendar": "HolidayCalendar",
          "for_calendar": "HolidayCalendar",
          "bdc": "BusinessDayConvention  (modified_following, from dom OIS convention per D-14)",
          "day_count": "DayCount  (Act/360, common to both legs per D-14)"
        },
        "output": "XccySchedule { coupon_dates: tuple[date, ...]  (ascending, last == maturity), taus: tuple[float, ...]  (Act/360 accrual per period; len == len(coupon_dates)), period_starts: tuple[date, ...]  (prev coupon date, period_starts[0] == spot_date) }",
        "error_cases": [
          "ValueError: maturity <= spot_date",
          "ValueError: no whole 3M period fits before maturity (sub-quarter swap) -> single back-stub period spot..maturity"
        ],
        "diagnostic_codes_emitted": []
      }
    },
    {
      "contract_id": "C-104'",
      "from": "rates.app (M-111)",
      "to": "M-106 (build_cross_basis_curve)",
      "type": "function_call",
      "interface": {
        "name": "build_cross_basis_curve  (SIGNATURE UNCHANGED — body now consumes all args)",
        "input": "(market: FXMarketData, dom_ois: OISCurve, for_ois: OISCurve, fx_forward: FXForwardCurve, fx_convention: FXConvention, diagnostics: DiagnosticsCollector)  + joint calendars reachable via the orchestrator (see note)",
        "output": "CrossCurrencyBasisCurve  (pillars now carry STRIPPED term-structure spreads, not verbatim quotes; ascending maturity; quoted_on_foreign preserved)",
        "error_cases": [
          "XccyBootstrapError (no usable quotes — unchanged)",
          "MixedQuotedLegError (mixed quoted_on_foreign — unchanged)",
          "FXBootstrapNonConvergentError (brentq cannot bracket/converge a pillar) -> FX_BOOTSTRAP_NON_CONVERGENT ERROR, curve not produced"
        ],
        "diagnostic_codes_emitted": [
          "FX_XCCY_REPRICE_FAIL (ERROR, |NPV(s*)| >= 1e-9 after solve — D-13)",
          "FX_BOOTSTRAP_NON_CONVERGENT (ERROR, brentq failure — already declared in rates.fx.types)",
          "FX_BASIS_INVERTED (WARN, adjacent stripped pillars opposing sign — unchanged)",
          "FX_XCCY_QUOTE_SKIPPED (WARN, non-finite/duplicate quote — unchanged)"
        ],
        "note": "build_cross_basis_curve needs the joint calendars + day-count to build the schedule (C-109). The v0.3.0 signature does NOT pass calendars. RESOLUTION: pass the two HolidayCalendars via fx_convention is NOT possible (FXConvention holds calendar CODES, not objects). Add dom_calendar/for_calendar parameters to build_cross_basis_curve — this is the one signature change required and it stays consumer-owned (M-111 already has both calendars in _run_fx_pipeline). The day-count (Act/360) is read from the foreign OIS convention by the orchestrator and passed in. See C-110 for how M-111 supplies them."
      }
    },
    {
      "contract_id": "C-108'",
      "from": "rates.app (M-111), rates.cli",
      "to": "M-107 (price_xccy_basis_swap)",
      "type": "function_call",
      "interface": {
        "name": "price_xccy_basis_swap  (SIGNATURE EXTENDED)",
        "input_v2_was": "(basis: CrossCurrencyBasisCurve, dom_ois: OISCurve, for_ois: OISCurve, maturity_date: date)",
        "input_v04": "(basis: CrossCurrencyBasisCurve, dom_ois: OISCurve, for_ois: OISCurve, fx_forward: FXForwardCurve, maturity_date: date, *, dom_calendar: HolidayCalendar, for_calendar: HolidayCalendar, day_count: DayCount)",
        "output": "float  (fair basis spread in bps, signed per basis.quoted_on_foreign)",
        "error_cases": [
          "ValueError: maturity_date out of range on basis / fx_forward",
          "FXBootstrapNonConvergentError: solve fails at the requested maturity"
        ],
        "rationale": "F-302 AC#2 (arbitrary maturity between pillars) requires re-running the net-PV=0 solve, which needs the FX forward curve + schedule inputs. The V2 signature could not do this (no fx_forward). C-108 is therefore EXTENDED, not merely de-stubbed. At a CALIBRATED pillar maturity the solve reproduces the stored stripped spread within 1e-9 (round-trip identity, AC#1). The persisted FXSummary v2 already carries forward_pillars, so M-111 reconstructs fx_forward via the existing _fx_forward_from_summary adapter — no extra persistence."
      }
    },
    {
      "contract_id": "C-110",
      "from": "rates.app (M-111) internal",
      "to": "rates.app reconstruction adapters",
      "type": "function_call",
      "interface": {
        "name": "_dom_ois_from_summary / _for_ois_from_summary",
        "input": "summary: FXSummary (v2)",
        "output": "OISCurve  (reconstructed from dom_ois_pillars / for_ois_pillars + the per-ccy FXOisMeta block: valuation_date, day_count, interpolation)",
        "error_cases": [
          "ValueError: unknown interpolation scheme in meta (mirrors _curve_from_summary)",
          "ValueError: schema_version != 2 (a v1 summary lacks OIS pillars — emit FX_PRICE_SCHEMA_TOO_OLD ERROR, abort pricing)"
        ],
        "note": "Mirrors the V1 _curve_from_summary adapter exactly (start_date := meta.valuation_date per G10; forward_ladder_dates reconstructed empty or omitted — the pricer's strip does not use the forward ladder, only df_at). Lives in rates.app to avoid rates.io->rates.fx and rates.fx->rates.io cycles. Replaces the _UNUSED_OIS placeholder."
      }
    },
    {
      "contract_id": "C-102'",
      "from": "rates.app (M-111)",
      "to": "M-110 / M-109 (FXSummary.from_domain)",
      "type": "function_call",
      "interface": {
        "name": "FXSummary.from_domain  (EXTENDED)",
        "added_input": "dom_ois: OISCurve, for_ois: OISCurve, strip_residuals: dict[str, float]  (tenor_code -> |NPV(s*)| at strip time)",
        "output": "FXSummary (schema_version=2)",
        "error_cases": ["unchanged"],
        "note": "from_domain maps both OISCurve.pillars_tuple into PillarOut lists (reusing the V1 PillarOut model, D-16) and stamps each ccy's FXOisMeta. strip_residuals are attached per basis pillar as FXBasisPillarOut.strip_residual."
      }
    }
  ]
}
```

### Contract notes

- **One genuine signature change** (`build_cross_basis_curve` gains
  `dom_calendar` / `for_calendar`) and **one extension**
  (`price_xccy_basis_swap` gains `fx_forward` + keyword schedule inputs). Both
  remain consumer-owned: `rates.app` already holds the calendars and the FX
  forward curve at every call site. The requirements' "signatures are stable —
  V0.4 fills the bodies" (Implementation Hints §8) holds for the **strip core**;
  the pricer's arbitrary-maturity re-solve (F-302 AC#2) genuinely needs the
  forward curve, so the extension is unavoidable and is called out here rather
  than smuggled in. *(Flag for grill: is extending C-108 acceptable, or should
  AC#2 be narrowed to "interpolate stored pillars only", keeping C-108 as-is?)*
- **No untyped dicts cross seams.** `XccySchedule` is a frozen dataclass;
  `FXOisMeta` is a Pydantic model.
- **Exit-code policy unchanged** (0 = WARN-only, 2 = any ERROR).

---

## 5. The Strip Mathematics (precise)

This section is the load-bearing specification for M-106/M-107. It implements
**Method (i)** (D-11): foreign-leg cashflows converted to domestic at the **full
FX forward curve**, discounted on the domestic OIS curve.

### 5.1 Instrument

Constant-notional (D-4), float-float par xccy basis swap, USDTRY, spread quoted
on the foreign (USD) leg (D-7, `quoted_on_foreign=True`):

- **Domestic (TRY) leg:** receive TRY OIS flat on notional `N_dom`, quarterly.
- **Foreign (USD) leg:** pay USD OIS **+ basis `b`** on notional `N_for = N_dom / S`, quarterly.
- **Notional exchange** at start `t_0` (spot, at rate `S`) and maturity `t_N`.

Single-curve per ccy (D-5): each OIS curve is both projection and discount.
Coupon dates `t_0 < t_1 < ... < t_N` and accruals `tau_i` come from M-113
(quarterly, Act/360 both legs, back stub; D-14). FX forwards `F(t_i)` come from
`fx_forward.forward_at(t_i)` (M-103 log-linear in `log(F/S)`; D-15).

### 5.2 Net PV in domestic units

Discount factors: `DF_dom(t) = dom_ois.df_at(t)`, `DF_for(t) = for_ois.df_at(t)`,
with `DF(t_0)=1`, `F(t_0)=S`.

The **domestic floating leg with notional exchange** prices to par in its own
currency (single-curve identity): `PV_dom = 0`.

The **foreign leg** (in USD), split into its OIS-flat-plus-notional base and the
basis-spread part:

```
PV_for→dom = PV_for0→dom  +  N_for · Σ_{i=1}^{N} F(t_i) · b_i · tau_i · DF_dom(t_i)
```

where the **base** (no spread) converted-and-domestic-discounted PV is

```
PV_for0→dom = Σ_{i=0}^{N} F(t_i) · CF0_i · DF_dom(t_i)
            = -S·N_for·DF_dom(t_0)                                  (initial notional, t_0)
            + Σ_{i=1}^{N} F(t_i) · N_for · f_for,i · tau_i · DF_dom(t_i)   (USD OIS coupons)
            + F(t_N) · N_for · DF_dom(t_N)                          (final notional, t_N)
```

and `f_for,i = (DF_for(t_{i-1})/DF_for(t_i) − 1) / tau_i` is the foreign OIS
forward over period `i`.

**Net PV** (receive domestic, pay foreign): `NPV = PV_dom − PV_for→dom = −PV_for→dom`.

### 5.3 CIP degeneracy check (why Method (i), not spot-only)

If `F(t_i) = F_CIP(t_i) := S · DF_for(t_i)/DF_dom(t_i)` for all `i`, then
`F(t_i)·DF_dom(t_i) = S·DF_for(t_i)`, so `PV_for0→dom = S · [foreign leg priced
on for_ois] = S · 0 = 0`, and the net-PV=0 condition gives `b ≡ 0`. **Non-zero
basis ⟺ market forwards deviate from CIP** — exactly D-11 / OQ-401. The spot-only
anchor (superseded D-9) sets every `F(t_i)=S` and degenerates to `b=0`
regardless; that is the bug grill-me caught.

### 5.4 Sequential par→pillar strip (D-13)

Pillars are the quoted xccy tenors `T_1 < ... < T_M` (the quote vector supplies
the maturity grid + sign). The stripped term-structure `b(t)` is piecewise-flat:
`b(t) = b_k` for `t ∈ (T_{k-1}, T_k]`. For pillar `n`, the par swap maturing at
`T_n` must reprice to zero with `b_1..b_{n-1}` fixed. Since `NPV` is **linear in
`b_n`**, define the period-`n` **basis annuity** (domestic units):

```
A_k = Σ_{i : t_i ∈ (T_{k-1}, T_k]}  F(t_i) · tau_i · DF_dom(t_i)
```

Then the closed-form solution is

```
b_n = [ −PV_for0→dom(T_n)  −  N_for · Σ_{k=1}^{n-1} b_k · A_k ]  /  ( N_for · A_n )
```

**brentq is retained** (D-6/D-13): the implementation wraps `NPV(b_n)` and solves
with `brentq` over a bps bracket (e.g. `[−10000, +10000]` bps), then a reprice
assert `|NPV(b_n*)| < 1e-9` → else `FX_XCCY_REPRICE_FAIL` ERROR (curve not
produced). The closed-form above is the bracketing seed / sanity reference, not
the production path — this mirrors V1's closed-form-then-brentq structure and
keeps the path MtM-ready.

`N_dom` cancels out of `b_n` (it scales `PV_for0→dom` and `N_for·A` identically
since `N_for = N_dom/S`); the implementation uses `N_dom = 1` (or `N_for = 1`)
for numerical conditioning. **Document this normalization in the docstring.**

### 5.5 Pricer (F-302)

`price_xccy_basis_swap` at maturity `m`:
- **`m` equals a calibrated pillar `T_n`:** return the stored `b_n` (round-trip
  identity, AC#1 — the solve reproduces it to `1e-9`).
- **`m` between pillars:** §5.4 with `T_n := m` and the term-structure `b_1..b_{n-1}`
  from the curve over `(T_0, m)`, solving for the single flat fair spread that
  zeroes the par swap to `m`. The returned value is interpolated under the
  curve's documented convention (FX-O2 piecewise-linear bps) **only** when the
  contract-extension (C-108') is declined; otherwise it is the freshly solved
  fair spread. *(Grill decision point — see §4 note and §9 OQ-501.)*

---

## 6. FXSummary v2 — Schema Diff & Fixture Strategy

### 6.1 Field-by-field diff (M-109)

`FX_SCHEMA_VERSION: 1 → 2`. New top-level fields and a new sub-model; existing
fields unchanged and in stable order (additions appended → reordering would be a
further bump, but appends within a version bump are the migration).

| Field | v1 | v2 | Type | Source |
|-------|----|----|------|--------|
| `schema_version` | `1` | `2` | int | constant |
| `dom_ois_pillars` | — | **new** | `list[PillarOut]` | `dom_curve.pillars_tuple` (reuse V1 `PillarOut`, D-16) |
| `for_ois_pillars` | — | **new** | `list[PillarOut]` | `for_curve.pillars_tuple` |
| `dom_ois_meta` | — | **new** | `FXOisMeta` | `{valuation_date, day_count, interpolation}` of dom curve |
| `for_ois_meta` | — | **new** | `FXOisMeta` | same for foreign curve |
| `basis_pillars[].strip_residual` | — | **new** | `float` | `|NPV(b_n*)|` at strip time (per-pillar) |
| all other fields | ✓ | ✓ unchanged | | |

New sub-model:

```python
class FXOisMeta(BaseModel):
    model_config = _FROZEN
    valuation_date: date
    day_count: str          # OISCurve.day_count.value
    interpolation: str      # OISCurve.interp  ("log_linear_df" | "linear_zero")
```

`FXBasisPillarOut` gains `strip_residual: float`.

**Reconstruction (C-110):** `_dom_ois_from_summary` / `_for_ois_from_summary`
rebuild `OISCurve` from `*_ois_pillars` + `*_ois_meta`, mirroring
`_curve_from_summary` (pillar `start_date := meta.valuation_date` per G10).
`forward_ladder_dates` is reconstructed empty — the strip uses only `df_at`, and
the FX pricing path never calls `OISCurve.forward`. *(Grill: confirm empty ladder
does not trip an `OISCurve.__post_init__` invariant — if it does, persist a
minimal ladder or relax the check; flagged as OQ-502.)*

### 6.2 Fixture regeneration (F-306, A-1: keep −180 bps)

Decision A-1 (user-confirmed): **keep the realistic −180 bps basis; regenerate
the FX forwards to embed it** so the strip recovers −180 and the basis-aware
parity gate (D-12) passes meaningfully (not vacuously).

`scripts/fx_smoke_test._regenerate_fx_snapshot` changes:
1. Bootstrap TRY + USD OIS (unchanged).
2. Build the quarterly schedule to the xccy maturity (M-113).
3. **Choose the basis** `b* = −180 bps` (the target).
4. **Invert §5.4** to compute the FX forwards that embed `b*`: for the forward
   pillars (1M/3M/6M) and the implied 9M/12M points needed by the 1Y swap,
   solve for `F(t_i)` such that the Method-(i) net-PV=0 strip yields exactly
   `b* = −180`. Concretely: perturb each `F_CIP(t_i)` by the basis carry so that
   `PV_for0→dom + N_for·b*·Σ F(t_i)·tau_i·DF_dom(t_i) = 0`. A simple, sufficient
   construction: distribute the basis uniformly as a forward-points shift
   `ΔF(t_i)` that reproduces `b*` on the strip (document the exact closed form in
   the script).
5. Write forward points (embedding `b*`) + the −180 XCCY_BASIS quote.
6. Smoke assertions: exit 0; summary loads as schema_version=2; **zero
   basis-aware `FX_PARITY_MISMATCH`**; **zero `FX_XCCY_REPRICE_FAIL`**; stripped
   1Y pillar ≈ −180 within tolerance; OIS pillars round-trip into curves.

The committed CSV header comment is updated: forwards now embed −180 bps basis
(not CIP-tight). The `--regenerate` flow recomputes both forwards and the basis
quote to stay mutually consistent after any math change.

---

## 7. Diagnostic Codes

| Code | Severity | Change | Trigger |
|------|----------|--------|---------|
| `FX_XCCY_REPRICE_FAIL` | ERROR | **new** (add to `rates.fx.types`, `__all__`) | Strip/price reprice residual `|NPV(s*)| >= 1e-9` (D-13). Mirrors V1 `BS_REPRICE_FAIL`. |
| `FX_PARITY_MISMATCH` | WARN | **semantics change** (D-12) | Now **basis-aware**: fires when market forward `F(t)` deviates from `CIP(t) · (1 + basis adjustment)` — i.e. genuine forward-vs-(CIP+stripped-basis) inconsistency. Was: forward vs pure CIP. Threshold `PARITY_MISMATCH_BPS = 1.0` unchanged. |
| `FX_BOOTSTRAP_NON_CONVERGENT` | ERROR | **reused** (already declared) | brentq cannot bracket/converge a pillar. |
| `FX_PRICE_SCHEMA_TOO_OLD` | ERROR | **new** | Pricing reads a v1 summary (no OIS pillars); cannot reconstruct dom/for OIS — abort (C-110). |
| `FX_BASIS_INVERTED`, `FX_XCCY_QUOTE_SKIPPED` | WARN | unchanged | as v0.3.0. |

**`FX_PARITY_MISMATCH` relocation:** the basis-aware check still lives in M-105
(`bootstrap_forward._warn_parity_mismatches`) but must now receive the stripped
basis curve. Ordering problem: M-105 (forward) runs **before** M-106 (basis) in
`_run_fx_pipeline`. **Resolution:** move the parity check to **after** the basis
strip — either (a) call `_warn_parity_mismatches(...)` from the orchestrator
once both `fx_forward` and `basis` exist, passing the basis curve in; or (b) keep
M-105's pure-CIP WARN as an early signal and add the authoritative basis-aware
check post-strip in M-106/orchestrator. **This design chooses (a):** M-105 stops
emitting the parity WARN; the orchestrator runs the basis-aware check after the
strip. *(Grill: confirm (a) over (b); (b) double-reports. Flagged OQ-503.)*

---

## 8. Decisions & Trade-offs

| # | Decision | Chosen | Rejected | Rationale / Trace |
|---|----------|--------|----------|-------------------|
| A-1 | Fixture basis direction | **Keep −180 bps; embed in FX forwards** | Move basis to ~0 (parity-tight forwards) | User-confirmed. Exercises the real V0.4 numeric; keeps the basis-aware gate meaningful. Maps F-307/D-12. |
| A-2 | Quarterly schedule placement | **New module `rates.fx.schedule` (M-113)** | Private helper in `bootstrap_basis.py` | User-confirmed. Independent unit tests; reused by both M-106 and M-107; MtM-ready. Maps F-303/DV-4. |
| A-3 | Strip solver | brentq + closed-form seed | Closed-form only | D-6/D-13 (locked). MtM-readiness + V1-pattern consistency. |
| A-4 | Pricer contract | **Extend C-108** (add `fx_forward` + schedule kwargs) | Keep C-108 as-is, narrow F-302 AC#2 | F-302 AC#2 (arbitrary maturity) needs a re-solve, which needs the forward curve. Surfaced for grill (OQ-501). |
| A-5 | OIS persistence shape | `PillarOut` ×2 + `FXOisMeta` ×2 | New dedicated OIS-curve schema | D-16 (locked). Reuse the V1 model; minimal surface. |
| A-6 | Parity-check placement | Post-strip, orchestrator-driven, M-105 stops emitting | Keep M-105 pure-CIP WARN + add second check | Avoids double-reporting; single authoritative basis-aware gate. (OQ-503.) |
| A-7 | Reconstruction adapter location | `rates.app` (`_dom/_for_ois_from_summary`) | Classmethod on `OISCurve` / in `rates.io` | No `rates.io`→`rates.fx` or `rates.fx`→`rates.io` cycle; mirrors V1 `_curve_from_summary`. |

---

## 9. Open Architecture Questions (grill-fodder)

| ID | Question | Provisional resolution | Risk if wrong |
|----|----------|------------------------|---------------|
| **OQ-501** | Should `price_xccy_basis_swap` (C-108) be **extended** with `fx_forward` for a true arbitrary-maturity re-solve (F-302 AC#2), or kept at its v0.3.0 signature with AC#2 narrowed to "interpolate stored pillars under FX-O2"? | **Extend (A-4)** — honour AC#2 literally. | Medium. Extension touches the CLI call site (`run_fx_price_xccy`) and any future `rates.cli` wiring. Narrowing is simpler but makes the pricer not a true par-swap pricer between pillars. |
| **OQ-502** | Does reconstructing `OISCurve` with an **empty** `forward_ladder_dates` trip `OISCurve.__post_init__`? | Assume no (ladder is decorative for `df_at`); verify in M-111. | Low — if it trips, persist a minimal ladder or relax the invariant. |
| **OQ-503** | Parity-check placement: orchestrator-driven post-strip (A-6) vs. keep M-105 early WARN + second authoritative check? | **Orchestrator post-strip (A-6).** | Low — both are correct; A-6 avoids double WARNs. |
| **OQ-504** | Fixture forward-embedding closed form (§6.2 step 4): uniform forward-points shift vs. exact per-period inversion of §5.4? | Uniform shift if it reproduces `b*` within `1e-9`; else exact per-period. Document the chosen form in the script. | Low — internal to the smoke fixture; does not affect production math. |
| **OQ-505** | 9M/12M FX forwards for the 1Y xccy swap are **beyond** the 6M forward-curve last pillar → `forward_at` raises (DateOutOfRange). Extrapolate, or require forward pillars out to the longest xccy maturity? | **Require forward pillars ≥ longest xccy maturity** (add 9M/12M forward points to the fixture) — no silent extrapolation, consistent with v0.3.0's no-extrapolation stance (`FX_PRICE_EXTRAPOLATION` is WARN-only for pricing, but the strip must not extrapolate silently). | **Medium-high.** If the forward curve does not cover the xccy schedule, the strip cannot run. Must be resolved before M-106 implementation. |

---

## 10. Build Order (module-by-module PR plan)

GitFlow per CLAUDE.md: each module ships as its own PR to `develop`
(`feature/MON-021-v04-xccy-impl-<module>`); no direct main/develop commits; no
merge without user approval. New `phase9` pytest marker throughout.

```json
{
  "build_phases": [
    {
      "phase": 1,
      "name": "Quarterly schedule (M-113)",
      "branch": "feature/MON-021-v04-xccy-schedule",
      "modules": ["M-113"],
      "rationale": "Pure, dependency-free generator; unlocks both strip and pricer. Highest reuse, lowest risk-to-build-first.",
      "deliverable": "build_quarterly_xccy_schedule produces correct 3M dates + Act/360 taus + back stub on USDTRY 1Y; phase9 unit tests incl. leap/holiday boundary + sub-quarter stub.",
      "estimated_effort": "small",
      "suggested_agent": "opus"
    },
    {
      "phase": 2,
      "name": "Strip core (M-106)",
      "branch": "feature/MON-021-v04-xccy-strip",
      "modules": ["M-106"],
      "rationale": "The hard numeric. Lands on M-113 + existing forward/OIS curves. Reprice assert (FX_XCCY_REPRICE_FAIL) is the correctness gate. Resolve OQ-505 (forward coverage) here.",
      "deliverable": "build_cross_basis_curve strips USDTRY pillars to net-PV=0 within 1e-9; EURTRY same code path covered by unit test (D-17); CIP-tight input -> b≈0 test; non-CIP input -> b matches target.",
      "estimated_effort": "large",
      "suggested_agent": "opus"
    },
    {
      "phase": 3,
      "name": "Schema v2 + persistence (M-109, M-110)",
      "branch": "feature/MON-021-v04-xccy-schema",
      "modules": ["M-109", "M-110"],
      "rationale": "Independent of pricing; round-trippable standalone. Bumps FX_SCHEMA_VERSION; regenerates docs/schemas/fx_summary.schema.json.",
      "deliverable": "FXSummary v2 round-trips dom/for OIS pillars + meta + strip residuals; schema export regenerated; phase9 round-trip test.",
      "estimated_effort": "medium",
      "suggested_agent": "sonnet"
    },
    {
      "phase": 4,
      "name": "Pricer + app wiring (M-107, M-111)",
      "branch": "feature/MON-021-v04-xccy-pricer-app",
      "modules": ["M-107", "M-111"],
      "rationale": "Pricer shares the strip core (M-106). M-111 deletes _UNUSED_OIS, adds C-110 reconstruction, wires real OIS into the calibrated pricer. Resolve OQ-501/OQ-502/OQ-503 here.",
      "deliverable": "round-trip identity (price at pillar == stored b within 1e-9); _UNUSED_OIS gone (grep clean, F-305 AC#2); run_fx_price_xccy prices on reconstructed curves; parity-aware gate post-strip.",
      "estimated_effort": "large",
      "suggested_agent": "opus"
    },
    {
      "phase": 5,
      "name": "Fixture regen + smoke gate (scripts + fixture)",
      "branch": "feature/MON-021-v04-xccy-fixture",
      "modules": ["scripts/fx_smoke_test.py", "fixtures/fx_smoke_usdtry/"],
      "rationale": "End-to-end gate. Regenerate forwards to embed -180 bps (A-1); assert reprice 1e-9 + zero basis-aware FX_PARITY_MISMATCH + zero FX_XCCY_REPRICE_FAIL. Resolve OQ-504/OQ-505 fixture details.",
      "deliverable": "fx_smoke_test exit 0 on regenerated fixture; V1 OIS smoke unchanged (1e-10); mypy --strict + ruff clean; phase9 >= baseline new tests; 315 baseline pytest green.",
      "estimated_effort": "medium",
      "suggested_agent": "sonnet"
    }
  ]
}
```

### Ordering principles applied
- **Dependencies first:** M-113 → M-106 → (M-109/M-110) → (M-107/M-111) → fixture.
- **Risk early:** the strip numeric (M-106) and its forward-coverage question
  (OQ-505) land in Phase 2, before schema/wiring cosmetics.
- **Value early:** M-113 alone is reusable; M-106 alone makes the curve
  calibrated even before persistence changes.
- **Stub-free:** every contract in §4 is locked, so phases can proceed against
  the documented signatures.

---

## 11. Quality Gates (NFR, project-standard)

All must hold before the V0.4 release PR:

- `mypy --strict src/rates` clean (incl. M-113, rewritten M-106/M-107, v2 schema).
- `ruff check src tests scripts` clean.
- `pytest -q` green: 315 baseline + new `phase9` tests (strip net-PV=0, round-trip
  identity, schedule boundaries, schema v2 round-trip, EURTRY unit path D-17).
- V1 OIS regression smoke unchanged: `scripts/smoke_test.py` exit 0, reprice `1e-10`.
- FX smoke: `scripts/fx_smoke_test.py` exit 0, zero `FX_PARITY_MISMATCH`
  (basis-aware), zero `FX_XCCY_REPRICE_FAIL`.
- `_UNUSED_OIS` absent from the codebase (`grep` clean — F-305 AC#2).
- `docs/v04-xccy-calibration-architecture.md` (this file) + grill notes merged to
  `develop` before any MON-021 implementation PR opens.

---

## 12. Validation Checklist (review before implementation)

1. **Strip math (§5):** is Method (i) + the sequential `b_n` formula correct, and
   is the `N_dom`-cancels normalization sound?
2. **Contract changes (§4):** are the `build_cross_basis_curve` calendar params
   and the `price_xccy_basis_swap` `fx_forward` extension (OQ-501) acceptable, or
   should AC#2 be narrowed to keep C-108 frozen?
3. **OQ-505:** require forward pillars out to the longest xccy maturity (no silent
   extrapolation)? This blocks M-106.
4. **Schema v2 (§6):** is `PillarOut ×2 + FXOisMeta ×2 + per-pillar strip_residual`
   the right shape, and is empty `forward_ladder_dates` on reconstruction safe
   (OQ-502)?
5. **Parity-check placement (§7, OQ-503):** orchestrator-driven post-strip (A-6)
   vs. M-105 early + second check?
6. **Build order (§10):** comfortable with M-113 → M-106 first, schema/wiring
   after?

Once confirmed, this document advances to **grill-me** (stress-test), then
**Status: Validated**, then quant-developer implementation (MON-021-*) per §10.
