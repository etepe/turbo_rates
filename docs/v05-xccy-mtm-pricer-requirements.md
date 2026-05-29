# V0.5 — MtM Cross-Currency Basis Swap Pricer — Requirements

**Domain:** quant
**Depth:** derin
**Date:** 2026-05-29
**Status:** Draft — grill-passed 2026-05-29 (Bulgu 1/2 + OQ-601..607 resolved → D-606/D-607); pending user validation (MON-022)
**Ticket:** MON-022
**Based on:** `docs/v04-xccy-calibration-requirements.md` (MON-019, F-301..F-307,
D-1..D-17), `docs/v04-xccy-calibration-architecture.md` (MON-020, §5 strip
mathematics, A-4/A-8/OQ-501, C-108 frozen pricer, C-110 OIS reconstruction),
`docs/fx-architecture.md` (V2 FX math layer), `docs/fx-io-architecture.md`
(v0.3.0 IO/CLI/app wiring).

> **Identifier scheme.** MON-022 uses a fresh **6xx** block to avoid collisions
> with V0.4 (features `F-3xx`, requirements open questions `OQ-4xx`) and the
> V0.4 architecture grill (`OQ-5xx`, decisions `A-1..A-8`). So: features
> `F-6xx`, modelling decisions `D-6xx`, open questions `OQ-6xx`. Interface
> contracts continue the global sequence (`C-108` last in V0.4 + `C-109/C-110`
> added there → MON-022 = `C-111+`); modules continue `M-114+`. These are
> assigned in the architecture phase, not here.

---

## 1. Executive Summary

V0.4 (MON-021) shipped the **calibrated strip** (`build_cross_basis_curve`,
M-106) and an **interpolating pricer** (`price_xccy_basis_swap`, M-107 / C-108):
the pricer is `return basis.basis_at(maturity)` and reports the *fair marginal
stripped basis* at a maturity. Its `dom_ois` / `for_ois` parameters are **kept
but unused** in V0.4 — held purely for MtM-readiness (A-8, OQ-501), with the
real reconstructed curves passed at the call site (C-110) instead of a `None`
placeholder.

V0.5 closes the deliberately-deferred work item: a true **mark-to-market (MtM)
cross-currency basis swap pricer**. Where the V0.4 pricer answers *"what is the
fair basis at tenor T?"*, the MtM pricer answers *"what is this live swap worth
right now?"* — it reprices a **non-par** swap (whose **contract** basis spread
generally **differs** from the fair stripped basis) against the dual OIS curves
**and** the FX forward curve, and returns a **present value (PV) in domestic
units**.

**Scope is spot-starting (Tier A):** the swap's first exchange is at the curve's
spot date, the coupon schedule runs `spot → maturity`, and there are no past
fixings, no mid-period stub, and no accrued interest. The PV is reported **as of
the spot date** (D-607). This reuses the strip's exact PV machinery
(`_coupon_terms` / `_net_pv`, §5/§5.2 of the V0.4 architecture, lifted into a
shared kernel — F-602), so the two surfaces stay arbitrage-consistent by
construction. The PV is linear in the contract spread and zero at the swap's
**par flat spread** `s_par_flat` (the single flat spread that makes a new
spot→`maturity` swap par). For the canonical **RECEIVE_DOMESTIC** base +
foreign-quoted leg the closed form is **`PV = N_for · (s_par_flat −
contract_spread) · A_for / 1e4`**, `A_for = Σ F_i·τ_i·DF_dom_i`; the shared
`net_pv` kernel is **authoritative** for the exact sign under each
(`quoted_on_foreign`, `direction`) combination (grill Bulgu 3). Hence:

- a contract priced **at the par flat spread** `s_par_flat` reprices to
  **PV ≈ 0** (round-trip self-consistency, true at any priceable maturity);
- an **off-market** contract spread produces a **non-zero PV** that scales
  linearly with notional and flips sign with direction.

> **Marginal vs. par-flat (the round-trip subtlety, grill Bulgu 1).** The
> stripped `b_n` that `price_xccy_basis_swap` (C-108) returns is the curve's
> **marginal** basis (the strip solves a *bucketed* spread `b_1..b_n` so the
> swap to `T_n` is par). A real MtM contract carries **one flat** spread; the
> flat spread that zeroes a swap to `T_n` is `s_par_flat`, a DF-weighted average
> of `b_1..b_n`. **`s_par_flat = b_n` only at the first pillar `T_1` or for a
> flat curve.** So `contract_spread = basis_at(T_n)` does **not** give PV = 0 in
> general — the round-trip anchors are `s_par_flat` (self-consistent) and the
> strip's full bucketed vector `b_1..b_n` fed through the shared kernel (D-606).

The increment is **purely additive in the math + CLI layers**. No schema change
is needed: `FXSummary` v2 already embeds the dom/for OIS pillars + meta, the FX
forward pillars, and the basis pillars, and the reconstruction adapters
(`_dom_ois_from_summary`, `_for_ois_from_summary`, `_fx_forward_from_summary`,
`_fx_basis_from_summary`) **already exist** in `rates.app`. `FX_SCHEMA_VERSION`
stays at 2.

---

## 2. Goals & Success Criteria

- [ ] A new pure function `price_xccy_swap_mtm` reprices a constant-notional,
      spot-starting, float-float xccy basis swap against `dom_ois`, `for_ois`,
      and the FX forward curve, returning **PV in domestic units** for a given
      **contract** basis spread, notional, and direction.
- [ ] **Round-trip consistency (two anchors, D-606):** (a) pricing at the swap's
      par flat spread `s_par_flat` yields `|PV| < 1e-9 · |N_dom|`
      (self-consistent, any maturity); (b) at a calibrated pillar `T_n`, feeding
      the strip's bucketed `b_1..b_n` through the shared kernel reproduces the
      strip's net-PV = 0 (cross-check vs. the strip). `contract_spread = b_n`
      (marginal) is **not** an anchor for `n > 1`.
- [ ] The strip's per-coupon net-PV computation is **factored into a single
      shared, pure kernel** consumed by both `build_cross_basis_curve` (solve
      `b_n` → net PV = 0) and `price_xccy_swap_mtm` (apply `contract_spread` →
      return PV). No duplicated PV math.
- [ ] The strip's existing behaviour is **unchanged** by the refactor: all V0.4
      `phase9` strip tests stay green and the strip still reprices to `1e-9`.
- [ ] A new CLI verb (`rates fx price-xccy-mtm`) reconstructs `dom_ois`,
      `for_ois`, and the FX forward curve from the persisted `FXSummary` v2 and
      prints the PV; a v1 summary aborts with `FX_PRICE_SCHEMA_TOO_OLD`.
- [ ] `price_xccy_basis_swap` (C-108) is **untouched** — the fair-basis
      interpolator and the MtM pricer are separate functions/contracts.
- [ ] All existing gates stay green: `mypy --strict`, `ruff`, full `pytest`
      (baseline + new phase marker), V1 OIS smoke, FX smoke exit 0.

---

## 3. Functional Requirements

### 3.1 Core Features

```json
{
  "features": [
    {
      "id": "F-601",
      "name": "Spot-starting MtM xccy basis swap PV pricer",
      "description": "A new pure function price_xccy_swap_mtm that values a constant-notional, float-float cross-currency basis swap STARTING at the curve spot date and maturing at maturity_date. It converts the foreign leg's cashflows to domestic units at the full FX forward curve and discounts on the domestic OIS curve (Method (i), same as the strip), applies a single flat CONTRACT basis spread (NOT the fair stripped basis) across the whole schedule, and returns the net PV in domestic units, AS OF THE SPOT DATE (D-607). PV = N_for*(contract_spread - s_par_flat)*A_total (foreign-quoted, closed form). Direction controls the PV sign. The function does NOT consume the stripped CrossCurrencyBasisCurve — PV comes from cashflows + curves directly. The public signature takes a SCALAR contract_spread; the shared kernel (F-602) takes a per-coupon spread vector and the pricer fills it constant.",
      "priority": "must-have",
      "acceptance_criteria": [
        "ROUND-TRIP (a) — par-flat self-consistency: for any priceable maturity, when price_xccy_swap_mtm is called with contract_spread_bps = s_par_flat (the par flat spread, computed via the shared kernel / closed form), then |PV| < 1e-9 * max(1, |N_dom|) (the unit-notional NPV is < 1e-9 internally, then scaled by N_dom — OQ-604).",
        "ROUND-TRIP (b) — bucketed cross-check vs. strip: at a calibrated pillar T_n, when the strip's stripped spreads b_1..b_n are fed as the shared kernel's per-coupon spread vector, then the resulting net PV reproduces the strip's net-PV = 0 to < 1e-9 (this validates the shared kernel against M-106).",
        "Given contract_spread_bps != s_par_flat, when priced, then PV is non-zero, equals N_for*(contract_spread - s_par_flat)*A_total, scales linearly with N_dom (PV(2N) == 2*PV(N) to machine precision), and flips sign with direction.",
        "NOTE (not an AC, documented expectation): contract_spread_bps = basis_at(T_n) (the MARGINAL stripped basis) gives PV ~ 0 ONLY at the first pillar T_1 or for a flat curve; for n>1 it is non-zero by construction (grill Bulgu 1). Tests must assert this, not PV=0.",
        "Given quoted_on_foreign True vs False (spread on the foreign vs domestic leg), when priced, then the correct leg carries the spread (the same sign branch the strip's _net_pv uses), exercised for both USDTRY and EURTRY.",
        "Given an FX forward curve whose last pillar precedes the swap's longest coupon date, when priced, then FX_XCCY_FORWARD_COVERAGE ERROR is emitted and no PV is returned (no silent extrapolation — reuses the strip's OQ-505 rule).",
        "Given maturity_date <= spot_date (or not strictly after spot), when priced, then a ValueError / diagnostic is raised (degenerate swap)."
      ]
    },
    {
      "id": "F-602",
      "name": "Shared xccy net-PV kernel",
      "description": "Refactor the strip's per-coupon present-value computation (currently _coupon_terms + _net_pv inside bootstrap_basis.py) into a single shared, pure function consumed by BOTH the strip (M-106, which solves b_n so net PV = 0 per pillar) and the new MtM pricer (F-601, which applies a single flat contract spread across the whole schedule and returns the PV). The kernel takes the per-coupon terms, the spread-free foreign base PV, the spot rate, the quoted_on_foreign flag, and a PER-COUPON SPREAD VECTOR (length = #coupons), and returns the unit-notional net PV in domestic units. The strip fills the vector from its buckets (b_1..b_n); the MtM pricer fills it with a constant contract_spread (its public signature stays a scalar). OQ-601 RESOLVED to the vector shape (grill Bulgu 1 requires it); the exact module location is an architecture decision (lean: a dedicated internal rates.fx module, so neither bootstrap_basis nor pricer imports the other).",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given the refactor, when the V0.4 phase9 strip tests run, then they pass unchanged and the strip still reprices each pillar to |net PV| < 1e-9.",
        "Given the codebase after the refactor, when inspected, then the net-PV arithmetic exists in exactly one place (the shared kernel); the strip and the MtM pricer both call it (no copied formula).",
        "Given the MtM pricer at a calibrated pillar with contract_spread = b_n, when it calls the shared kernel, then it reproduces the same net PV the strip drove to zero (this is the mechanism behind F-601 AC#1)."
      ]
    },
    {
      "id": "F-603",
      "name": "CLI verb: rates fx price-xccy-mtm",
      "description": "A new pricing subcommand that reconstructs dom_ois, for_ois, and the FX forward curve from the persisted FXSummary v2 (reusing the existing C-110 / _fx_forward_from_summary adapters) and prices an MtM xccy basis swap. Contract terms (basis spread, notional, direction) are CLI flags; the maturity is resolved from --tenor against the persisted basis pillars (mirroring run_fx_price_xccy) or supplied as --maturity (value-date). Stdout prints the PV.",
      "priority": "should-have",
      "acceptance_criteria": [
        "Given a persisted v2 summary, when `rates fx price-xccy-mtm --pair USDTRY --tenor 1Y --spread <bps> --notional <N> [--direction ...]` runs, then it prints the PV in domestic units and exits 0.",
        "Given a v1 summary (no embedded OIS pillars), when the verb runs, then FX_PRICE_SCHEMA_TOO_OLD ERROR is emitted and pricing aborts (reuses the C-110 guard).",
        "Given a tenor not present in the persisted basis pillars, when the verb runs, then FX_PRICE_UNKNOWN_TENOR ERROR is emitted with the available tenors."
      ]
    },
    {
      "id": "F-604",
      "name": "Correctness tests: round-trip + independent PV anchor",
      "description": "A new pytest phase marker for V0.5. Tests cover: (a) round-trip par-flat self-consistency (contract_spread = s_par_flat -> PV ~ 0, D-606a); (b) round-trip bucketed cross-check (strip's b_1..b_n vector through the shared kernel -> net PV ~ 0 at each pillar, D-606b); (c) an INDEPENDENT hand-computed PV micro-case (1-2 periods, expected PV worked out by hand, NOT generated by the production kernel) so a sign/day-count error cannot pass undetected; (d) notional linearity PV(2N)=2PV(N); (e) the quoted_on_foreign sign branch via EURTRY (toggle, mirrors D-17); (f) FX_XCCY_FORWARD_COVERAGE abort; (g) negative assertion: contract_spread = basis_at(T_n) gives PV != 0 for a non-flat curve at n>1 (documents Bulgu 1, guards against the regression of re-introducing the wrong identity).",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given the new phase marker, when pytest runs, then the V0.5 tests are selectable in isolation and pass.",
        "Given the hand-computed micro-case, when the MtM pricer runs, then its PV matches the hand value within a documented tolerance (the independent anchor — analogous to the strip's grill G-2 check)."
      ]
    }
  ]
}
```

### 3.2 User Flows

The bootstrap flow is **unchanged** (V0.5 adds no schema/persistence change).
The new flow is a pricing read:

1. (Unchanged) `rates fx bootstrap --pair USDTRY --as-of YYYY-MM-DD` writes
   `FXSummary` v2 (dom/for OIS pillars + meta, forward pillars, basis pillars).
2. **New:** `rates fx price-xccy-mtm --pair USDTRY --tenor 1Y --spread -150
   --notional 1e7 [--direction receive]` reads the v2 summary, reconstructs
   `dom_ois` / `for_ois` / `fx_forward` (existing adapters), resolves the tenor
   to a maturity against the basis pillars, and calls `price_xccy_swap_mtm`.
3. Stdout prints the PV in domestic units (e.g. `mtm_xccy(USDTRY 1Y, spread=-150.00 bps, N=10,000,000) PV = <...> TRY`).

### 3.3 Data Requirements

```json
{
  "data_sources": [
    {
      "name": "Persisted FXSummary v2 (curve source — UNCHANGED)",
      "type": "file",
      "format": "JSON (Pydantic, schema_version=2)",
      "frequency": "on-demand",
      "auth_required": false,
      "note": "Already carries dom_ois_pillars/for_ois_pillars + meta, forward_pillars, basis_pillars. No new fields needed for V0.5."
    },
    {
      "name": "Contract terms (CLI flags, NOT a file)",
      "type": "cli",
      "format": "scalars: contract basis spread (bps), domestic notional, direction, tenor|maturity",
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
  "performance": "Pricing is one pass over an O(N) quarterly schedule (no root solve); negligible. No latency budget. Pure read of persisted curves; no re-bootstrap.",
  "security": "Not applicable — local CLI, no network, no secrets.",
  "deployment": { "target": "local CLI (rates fx ...)", "provider": "n/a" },
  "scale": "Single swap per run; O(10) coupons. No scale concerns.",
  "monitoring": "Diagnostics envelope + stdout; reuse FX_XCCY_FORWARD_COVERAGE (ERROR) and FX_PRICE_SCHEMA_TOO_OLD (ERROR). No new diagnostic codes expected (confirm in architecture)."
}
```

Engineering NFRs (project-standard gates):

- `mypy --strict src/rates` clean (incl. refactored kernel + new pricer + CLI).
- `ruff check src tests scripts` clean.
- `pytest -q` green: existing baseline + new V0.5 phase marker; **all V0.4
  `phase9` strip tests still green** (the shared-kernel refactor must not
  regress the strip).
- V1 OIS regression smoke unchanged (`scripts/smoke_test.py` exit 0, reprice 1e-10).
- FX smoke (`scripts/fx_smoke_test.py`) exit 0, unchanged.

---

## 5. Constraints & Assumptions

**Constraints**
- Reuse V1/V2 idioms: frozen dataclasses, `DiagnosticsCollector` threading,
  hexagonal layering (`rates.fx` → `rates.core`, never reverse; IO/schema
  reconstruction adapters live in `rates.app`).
- `rates.core` is **not** modified. The quarterly schedule generator
  (`rates.fx.schedule`, M-113) and the FX forward / OIS accessors are reused
  as-is.
- **No schema change.** `FX_SCHEMA_VERSION` stays 2; `FXSummary` is untouched.
- **C-108 stays frozen.** `price_xccy_basis_swap` is not modified; the MtM
  pricer is a separate function/contract.
- GitFlow per CLAUDE.md: feature branch off `develop`, PR per discovery stage
  and per impl unit; no direct main/develop commits; no merge without user
  approval; never `--no-verify`.

**Modelling decisions (locked with user, 2026-05-29)**
- **D-601 — Scope = spot-starting (Tier A).** The MtM pricer values a swap whose
  first exchange is at the **curve spot date** (`t_0 = spot`); the schedule is
  `spot → maturity`. There are no past fixings, no valuation-date stub, no
  accrued interest, and `clean == dirty`. The swap-start date and the PV-as-of
  date are **both** the spot date (D-607). `valuation_date` therefore collapses
  to the curve spot date and is **not a free input** (OQ-603). Seasoned/mid-life
  valuation (Tier B), where the PV-as-of date is a free "today" earlier than
  spot and the start date is in the past, is **deferred** (§6).
  - *Rationale:* smallest meaningful increment; directly reuses the strip's
    `spot → maturity` PV machine; the non-par PV behaviour (contract spread ≠
    fair ⇒ PV ≠ 0) is fully exercised at spot-start. (CLAUDE.md "Simplicity
    First".)
- **D-602 — New function, C-108 frozen.** The MtM pricer is a **new** function
  `price_xccy_swap_mtm` (new contract C-111) in `rates.fx.pricer`. It adds the
  `fx_forward` curve (which C-108 lacks) and the contract terms, and returns a
  PV. `price_xccy_basis_swap` (the fair-basis interpolator) is untouched.
  - *Rationale:* fair-basis and PV are two different operations; folding them
    into one signature would repeat the OQ-501 representation-mixing mistake.
- **D-603 — Output = PV only (domestic).** The pricer returns a single `float`:
  the net PV in domestic units. No leg-by-leg breakdown, no implied fair basis,
  no clean/dirty split (the latter is moot at spot-start).
- **D-604 — Shared PV kernel.** The strip's net-PV arithmetic is factored into
  one shared pure function used by both the strip and the MtM pricer (F-602).
  The strip solves a spread per bucket so net PV = 0; the MtM pricer applies one
  flat contract spread and returns the PV. The exact extraction boundary is an
  architecture decision (OQ-601).
- **D-605 — Day-count: keep Act/360 both legs (OQ-403 carried forward).** The
  MtM pricer uses the **same** conventions as the strip — quarterly schedule,
  single common Act/360 on both legs, modified-following, joint calendar — so
  the round-trip identity (D-601 / F-601 AC#1) holds exactly. Per-leg native
  day-counts remain a documented simplification deferred to a later increment
  (§6), consistent with V0.4 D-14/OQ-403.
- **D-606 — Round-trip semantics + vector kernel (grill Bulgu 1).** The MtM
  round-trip correctness anchors are (a) the **par flat spread** `s_par_flat`
  (`PV(s_par_flat) = 0`, self-consistent, any maturity) and (b) the strip's
  **bucketed** spread vector `b_1..b_n` fed through the shared kernel
  (`net PV = 0` at calibrated pillar `T_n`, cross-checking the strip). The
  **marginal** `basis_at(T_n)` is **not** a round-trip anchor for `n > 1` (it
  equals `s_par_flat` only at `T_1` / flat curve). Consequence: the shared
  kernel (D-604) takes a **per-coupon spread vector**; the public
  `price_xccy_swap_mtm` takes a **scalar flat** `contract_spread` and fills the
  vector constant. Closed form (foreign-quoted):
  `PV = N_for·(contract_spread − s_par_flat)·A_total`,
  `A_total = Σ F_i·τ_i·DF_dom_i`.
  - *Rationale:* a real xccy basis contract carries one flat spread; the strip
    produces a marginal/bucketed term-structure. Conflating them re-introduces
    the OQ-501/A-4 representation error. Resolves OQ-601 (→ vector) + OQ-604.
- **D-607 — PV reported as of the spot date (grill Bulgu 2).** Domestic
  discounting is anchored at spot (`DF_dom(spot) = 1`, via the existing
  `_coupon_terms` re-anchoring), so the returned PV is **as of the swap's
  inception = spot date**. This matches the strip's net-PV definition exactly
  and falls out of the reused kernel for free. As-of-today discounting (multiply
  by `DF_dom(as_of → spot)`) is **deferred** to Tier B.

**Assumptions**
- The swap is float-float, constant-notional, with notional exchange at the
  start (spot) and at maturity — identical to the par swap the strip calibrates.
- The domestic notional `N_dom` is the input; `N_for = N_dom / S_spot` (the same
  normalization the strip uses with `N_dom = 1`). PV scales linearly with
  `N_dom`.
- Joint calendar (domestic + foreign) governs coupon/settlement rolling, as in
  the existing FX layer.
- Only USDTRY (and EURTRY by the same code path) are in scope; no new pairs.
- Round-trip behaviour (D-606), precisely: (i) `PV(s_par_flat) = 0` holds at
  **any** priceable maturity (`s_par_flat` is the closed-form par flat spread
  from the kernel); (ii) feeding the strip's bucketed `b_1..b_n` through the
  kernel gives `net PV = 0` **at calibrated pillars `T_n`** (cross-check vs. the
  strip); (iii) `PV(basis_at(m)) ≠ 0` in general (= 0 only at `T_1` / flat
  curve) — this must be **asserted non-zero** in the tests, never as zero.

---

## 6. Out of Scope

- **Seasoned / mid-life valuation (Tier B).** `valuation_date` strictly between
  start and maturity; past floating fixings as inputs; the current-period stub
  from valuation date to the next coupon; accrued interest / clean-vs-dirty PV;
  notional exchange only at remaining dates (initial exchange already settled).
- **Per-leg native day-counts** (TRY Act/365 vs USD Act/360) — Act/360-both-legs
  retained (D-605 / OQ-403).
- **MtM-resetting (notional-reset) cross-currency swaps** — still
  constant-notional only.
- **Leg-by-leg PV breakdown, implied fair basis, clean/dirty split** in the
  output (D-603).
- **DV01 / risk decomposition** for the MtM PV (FX-O3 still deferred).
- **FX-forward interpolation upgrade**, separate xccy-adjusted discount curve.
- **New currency pairs** beyond USDTRY/EURTRY; NDF/offshore TRY; FX options/vol.

---

## 7. Open Questions — all RESOLVED (grill-me, 2026-05-29)

> All seven were resolved in the MON-022 grill. Two **critical** findings —
> **Bulgu 1** (marginal `b_n` vs. par-flat `s_par_flat` round-trip; corrected
> F-601, §1 callout) and **Bulgu 2** (PV as-of date) — produced **D-606 /
> D-607**. OQ-601..607 are locked at the leanings below (now the resolutions);
> OQ-601 → vector kernel and OQ-604 → unit-notional tolerance are subsumed by
> D-606.

| ID | Question | Why it matters | Resolution (locked) |
|----|----------|----------------|---------------------|
| **OQ-601** | **Shared-kernel boundary.** Where does the shared net-PV kernel live and what is its exact shape? Options: (a) keep `_coupon_terms` / `_net_pv` in `bootstrap_basis.py` and import them into the pricer; (b) extract a new internal module (e.g. `rates.fx._xccy_pv` / `rates.fx.xccy_pv`); (c) a small frozen "swap leg terms" object + a free `net_pv(terms, spread_spec)` function. How is the spread parametrized so the strip's **bucketed** spread and MtM's **flat** spread both fit one signature? | Determines coupling (does the pricer import from the bootstrap module?), testability, and whether the strip's private helpers become a public-ish contract. | (b)/(c): extract a dedicated module with a spread *vector* (length = #coupons) so the strip passes bucketed values and MtM passes a constant-filled vector. Keeps `bootstrap_basis` from importing the pricer and vice-versa. |
| **OQ-602** | **Direction / PV sign convention.** How is "which side of the swap" expressed — a signed notional, a `direction: Literal["pay","receive"]`, a boolean, or a fixed base perspective the caller negates? And the base perspective: PV from the receiver of the domestic leg (the strip's `_net_pv` sign)? | A single PV float's sign is meaningless without a pinned convention; the CLI verb needs an unambiguous flag. | Fixed base perspective = strip's `_net_pv` ("receive domestic, pay foreign+basis"); expose an explicit `direction` (enum) that multiplies by ±1. Document the sign in the docstring + CLI help. |
| **OQ-603** | **`valuation_date` parameter.** Under Tier A it equals the curve spot date. Does the pricer take it (and ERROR/validate if ≠ spot), or omit it entirely and implicitly start at spot? | API ergonomics + forward-compat with Tier B (which needs a free valuation date). | Omit from the core function (derive `t_0` from the curve spot); the CLI takes no valuation-date flag. Tier B can add it later without breaking Tier A call sites. |
| **OQ-604** | **Reprice tolerance for round-trip.** Is `|PV| < 1e-9 · |N_dom|` the right tolerance, or absolute `1e-9` on a unit-notional PV computed internally then scaled? | Test stability across notional magnitudes; consistency with the strip's `1e-9` absolute on unit-notional NPV. | Compute the unit-notional NPV internally (the strip's `N_dom = 1` convention), assert `< 1e-9` there, then multiply by `N_dom` for the returned PV — so the tolerance is notional-independent. |
| **OQ-605** | **Notional input currency.** Is the CLI/function notional `N_dom` (domestic) only, or also allow `N_for` (foreign)? | Avoids ambiguity; the strip normalizes on `N_dom`. | `N_dom` only (domestic). `N_for = N_dom / S_spot` derived internally. Document; reject negative notional (use `direction` for side). |
| **OQ-606** | **FX-forward coverage check reuse.** Should the MtM pricer reuse the strip's `FX_XCCY_FORWARD_COVERAGE` ERROR, or assume coverage (curves came from a successful bootstrap)? | A persisted summary's forward pillars might not reach a user-supplied `--maturity` beyond the curve. | Reuse the same coverage check (the user can request a maturity the persisted forward curve doesn't cover). No silent extrapolation, same as the strip. |
| **OQ-607** | **CLI maturity input.** `--tenor` (resolved against persisted basis pillars, like `run_fx_price_xccy`) only, or also a free `--value-date`/`--maturity`? | The interesting MtM cases are often off-pillar (a seasoned-but-spot-started book). | Support both: `--tenor` (pillar-keyed) **and** `--maturity` (explicit date), mutually exclusive — mirrors the outright/swap verbs' tenor/value-date mutex. |

---

## 8. Implementation Hints

- **Reuse the strip's PV core verbatim.** `bootstrap_basis._coupon_terms`
  already returns the per-coupon `_CouponTerm` list (`tau`, `df_dom`, `fwd`,
  `bucket`) and `pv_for0` (the spread-free foreign base PV in domestic units);
  `_net_pv` already computes the net PV for a trial spread with the
  `quoted_on_foreign` sign branch. The MtM pricer is the **same arithmetic** with
  (i) a single flat `contract_spread` instead of a bucketed solve, and (ii)
  returning the PV instead of driving it to zero. F-602 is about lifting these
  two into a shared home.
- **Curve reconstruction already exists.** `rates.app` has
  `_dom_ois_from_summary`, `_for_ois_from_summary`, `_fx_forward_from_summary`,
  `_fx_basis_from_summary`, plus the `schema_version != 2 → FX_PRICE_SCHEMA_TOO_OLD`
  guard inside `run_fx_price_xccy`. The new verb composes these.
- **Schedule.** `rates.fx.schedule.build_quarterly_xccy_schedule(spot, maturity,
  dom_cal, for_cal, MODIFIED_FOLLOWING, ACT_360)` is the same call the strip
  makes — reuse it; do not re-implement.
- **Coverage check.** `bootstrap_basis._check_forward_coverage` raises on a short
  forward curve; the MtM path needs the equivalent guard (decide reuse vs. local
  in architecture per OQ-606).
- **CLI.** Model the new verb on `run_fx_price_xccy` (tenor→maturity via basis
  pillars, schema-version guard, OIS reconstruction) plus the outright/swap
  verbs' tenor/value-date mutex for `--maturity` (OQ-607).
- **Tests.** New phase marker for V0.5 (mirrors the `phase9` boundary
  discipline); register it in `pyproject.toml` / `pytest.ini` markers.
- **Suggested module agents:** shared PV kernel + MtM pricer = **opus** (core
  numerics, sign branches, round-trip correctness); CLI verb = **sonnet**;
  marker/config = **haiku**.

---

## 9. Build / PR Plan (GitFlow, per CLAUDE.md)

Each discovery doc and the implementation ship as PRs to `develop` off
`feature/MON-022-mtm-xccy-pricer` (already created from `origin/develop`):

1. **This requirements doc** (+ grill notes) — validated before architecture.
2. **Architecture doc** (`docs/v05-xccy-mtm-pricer-architecture.md`, post-grill)
   — modules (`M-114+`), contract `C-111`, the shared-kernel boundary (OQ-601),
   diagnostic-code audit, build order. Validated before code.
3. **Implementation** — likely 2–3 commits/units on the feature branch:
   kernel refactor (F-602, strip stays green) → MtM pricer (F-601) + tests
   (F-604) → CLI verb (F-603). Final PR to `develop`; **no merge without user
   approval.**

No code is written until this requirements document **and** the architecture
document are validated.
