# rates_engine v0.3.0 — FX IO / CLI / App Architecture

**Based on:** `docs/architecture.md` (V1 OIS), `docs/fx-architecture.md` (V2 FX math layer), `CHANGELOG.md` v0.2.0 "Not in this release" section.
**Date:** 2026-05-28
**Status:** Draft — Pending Validation
**Ticket:** MON-013
**Scope:** wire the v0.2.0 FX math layer (M-101..M-107) into IO, CLI, and pipeline orchestration so it is consumable end-to-end. No changes to FX math; no V3 xccy stripping.

---

## 1. Architectural Drivers

| Driver | Detail |
|--------|--------|
| **Change driver** | FX snapshot input format. Hidden behind an `FXMarketDataProvider` `Protocol` so a future Bloomberg / Refinitiv adapter implements the same seam (mirrors V1 `MarketDataProvider`). |
| **Risk driver** | Dual-OIS orchestration. The covered-interest-parity check inside `build_fx_forward_curve` (M-105) needs both the domestic (TRY) and foreign (USD/EUR) OIS curves bootstrapped from independent snapshots. One `rates fx bootstrap` invocation reads three CSVs and runs OIS bootstrap twice before the FX layer ever runs. |
| **Compatibility driver** | V1 OIS CLI surface (`rates bootstrap`, `rates diagnose`, `rates forward`) MUST NOT break. New FX verbs are added as a nested `rates fx <verb>` subparser. |
| **Reuse driver** | `conventions.yaml` already carries USD/EUR OIS rows (added in v0.2.0) and `fx_conventions` per pair. The existing `CsvProvider` (M-010) can read foreign-currency OIS snapshots unchanged — no new OIS code in this release. |
| **Scope discipline** | V3 deferred items (full xccy stripping calibration, snapshot freshness, multi-scenario MPC paths) stay deferred. v0.3.0 is plumbing only. |

---

## 2. Tech Stack

No new dependencies. New modules adopt the V1 stack exactly:

| Layer | Choice | Rationale |
|-------|--------|-----------|
| CLI parser | stdlib `argparse` (nested subparsers) | V1 uses argparse; nested subparsers are stdlib-supported. |
| Schema | Pydantic v2 | V1 `rates.io.market` / `rates.io.schemas` already use it. |
| Persistence | JSON + pyarrow Parquet | Mirror V1 `data/latest/*.json` + `data/curves/<curve>/...` layout. |
| Config | YAML (`config/conventions.yaml`) | `fx_conventions` block already wired (v0.2.0). |
| Tests | pytest + Hypothesis | Add `phase8` marker for v0.3.0 IO/CLI/app tests. |

Rejected: Typer / Click (extra dep, no value over argparse for a 3-verb CLI subgroup); a separate `fx_conventions.yaml` file (more files to keep in sync, no benefit over the existing single-file block); a new Pydantic model for foreign-OIS snapshots (the existing `MarketQuote` schema is already correct for any single-currency OIS strip).

---

## 3. Module Decomposition

### Module Overview

| ID | Name | Responsibility | Complexity | Agent |
|----|------|----------------|------------|-------|
| M-108 | `rates.io.fx_market` | Parse single FX snapshot CSV (spot + forward points + xccy basis rows multiplexed by `instrument_type`) into `FXMarketData`. | medium | sonnet |
| M-109 | `rates.io.schemas` (extension) | Pydantic models for `FXConfigSnapshot`, `FXSummary` (JSON persistence shape: forward pillars, basis pillars, parity-check rows, diagnostics envelope). | low | haiku |
| M-110 | `rates.io.persistence` (extension) | Write `FXSummary` JSON; write `FXForwardCurve` + `CrossCurrencyBasisCurve` Parquet partitions under `data/fx_curves/<pair>/`. | low | haiku |
| M-111 | `rates.app` (FX entry points) | Orchestrate `run_fx_bootstrap`, `run_fx_diagnose`, `run_fx_price`. Load FX + dual OIS snapshots, bootstrap both OIS curves, build FX forward curve (with parity check), build basis curve, persist outputs, return Unix exit code. | high | opus |
| M-112 | `rates.cli` (FX subcommand wiring) | Nested `rates fx <verb>` subparser. Each verb body ≤30 LoC; pure argparse → delegate to M-111. | low | sonnet |

### Module Details

```json
{
  "modules": [
    {
      "module_id": "M-108",
      "name": "rates.io.fx_market",
      "responsibility": "Parse FX snapshot CSV (multiplexed by instrument_type) into FXMarketData.",
      "owns_data": ["FXMarketData", "FX snapshot CSV schema"],
      "depends_on": ["M-101", "M-102"],
      "external_deps": ["filesystem"],
      "features_served": ["F-FX-IO-CSV"],
      "complexity": "medium",
      "suggested_agent": "sonnet",
      "new_file": "src/rates/io/fx_market.py"
    },
    {
      "module_id": "M-109",
      "name": "rates.io.schemas (FX extension)",
      "responsibility": "Add FXConfigSnapshot and FXSummary Pydantic models to the existing schemas module.",
      "owns_data": ["FXSummary JSON schema", "FXConfigSnapshot"],
      "depends_on": ["M-101"],
      "external_deps": [],
      "features_served": ["F-FX-IO-SUMMARY"],
      "complexity": "low",
      "suggested_agent": "haiku",
      "modified_file": "src/rates/io/schemas.py",
      "new_schema_file": "docs/schemas/fx_summary.schema.json"
    },
    {
      "module_id": "M-110",
      "name": "rates.io.persistence (FX extension)",
      "responsibility": "Persist FXSummary JSON and partitioned Parquet curves for FX outputs.",
      "owns_data": ["data/latest/<pair>_fx_summary.json layout", "data/fx_curves/<pair>/ partition layout"],
      "depends_on": ["M-109", "M-101", "M-103", "M-104"],
      "external_deps": ["pyarrow", "filesystem"],
      "features_served": ["F-FX-PERSIST"],
      "complexity": "low",
      "suggested_agent": "haiku",
      "modified_file": "src/rates/io/persistence.py"
    },
    {
      "module_id": "M-111",
      "name": "rates.app (FX orchestration)",
      "responsibility": "FX pipeline entry points consumed by rates.cli.",
      "owns_data": ["FX pipeline sequence", "exit code semantics"],
      "depends_on": ["M-010", "M-108", "M-109", "M-110", "M-102", "M-103", "M-104", "M-105", "M-106", "M-107"],
      "external_deps": [],
      "features_served": ["F-FX-ORCH-BOOTSTRAP", "F-FX-ORCH-DIAGNOSE", "F-FX-ORCH-PRICE"],
      "complexity": "high",
      "suggested_agent": "opus",
      "modified_file": "src/rates/app.py"
    },
    {
      "module_id": "M-112",
      "name": "rates.cli (FX wiring)",
      "responsibility": "Add nested `rates fx <verb>` subparser; delegate to rates.app FX entry points.",
      "owns_data": ["CLI surface for FX"],
      "depends_on": ["M-111"],
      "external_deps": [],
      "features_served": ["F-FX-CLI"],
      "complexity": "low",
      "suggested_agent": "sonnet",
      "modified_file": "src/rates/cli.py"
    }
  ]
}
```

---

## 4. Interface Contracts

```json
{
  "contracts": [
    {
      "contract_id": "C-101",
      "from": "M-111 (rates.app)",
      "to": "M-108 (rates.io.fx_market)",
      "type": "function_call",
      "interface": {
        "name": "FXMarketDataProvider.load",
        "input": {
          "snapshot_path": "Path",
          "pair": "CurrencyPair",
          "diagnostics": "DiagnosticsCollector"
        },
        "output": "FXMarketData",
        "error_cases": [
          "FXCsvSchemaError (required column missing or row parse failure)",
          "FXEmptyDataError (CSV parsed but zero data rows)",
          "FileNotFoundError"
        ],
        "diagnostic_codes_emitted": [
          "FX_IO_SCHEMA_MISSING_COL (ERROR)",
          "FX_IO_EMPTY_SNAPSHOT (ERROR)",
          "FX_IO_ROW_PARSE_FAIL (ERROR)",
          "FX_IO_UNKNOWN_INSTRUMENT (ERROR, instrument_type not in {SPOT, FORWARD_POINT, XCCY_BASIS})",
          "FX_IO_PAIR_MISMATCH (WARN, row pair != requested pair; row skipped)",
          "FX_IO_NO_SPOT (ERROR, zero SPOT rows after filtering)"
        ]
      }
    },
    {
      "contract_id": "C-102",
      "from": "M-111 (rates.app)",
      "to": "M-110 (rates.io.persistence FX side)",
      "type": "function_call",
      "interface": {
        "name": "write_fx_summary / write_fx_curve_partition",
        "signatures": [
          "write_fx_summary(summary: FXSummary, path: Path) -> None",
          "write_fx_curve_partition(forward: FXForwardCurve, basis: CrossCurrencyBasisCurve | None, root: Path, as_of: date, pair: CurrencyPair) -> None"
        ],
        "input": {
          "summary": "FXSummary (Pydantic, M-109)",
          "forward": "FXForwardCurve (M-103)",
          "basis": "CrossCurrencyBasisCurve | None (M-104) — None when basis quotes absent",
          "path": "Path (target JSON path; parent dirs created)",
          "root": "Path (partition root, e.g. data/fx_curves)",
          "as_of": "date",
          "pair": "CurrencyPair"
        },
        "output": "None (idempotent overwrite; atomic via temp-file + rename for JSON)",
        "error_cases": [
          "OSError (permission / disk full) — propagated, not caught"
        ]
      }
    },
    {
      "contract_id": "C-103",
      "from": "M-112 (rates.cli)",
      "to": "M-111 (rates.app FX entry points)",
      "type": "function_call",
      "interface": {
        "signatures": [
          "run_fx_bootstrap(args: argparse.Namespace) -> int",
          "run_fx_diagnose(args: argparse.Namespace) -> int",
          "run_fx_price(args: argparse.Namespace) -> int"
        ],
        "input": "argparse.Namespace with: pair (str), as_of (date|None), fx_snapshot (Path|None), foreign_snapshot (Path|None), domestic_snapshot (Path|None), conventions_yaml (Path), output_root (Path), quiet (bool), convention_override (list[str]|None). run_fx_price adds: tenor_code (str) or value_date (date), instrument (outright|swap|xccy).",
        "output": "Unix exit code — 0 on WARN-only, 2 on any ERROR. Mirrors run_bootstrap exit semantics.",
        "error_cases": [
          "All caught internally; converted to diagnostics + exit 2. No exceptions escape to argparse."
        ]
      }
    },
    {
      "contract_id": "C-104",
      "from": "rates.app (within M-111)",
      "to": "rates.core.bootstrap.bootstrap_curve (existing M-005)",
      "type": "function_call",
      "interface": {
        "name": "bootstrap_curve (called twice per FX bootstrap)",
        "input": "Existing signature unchanged — `MarketData`, `Conventions`, `HolidayCalendar`, `DayCount`, `DiagnosticsCollector`, currency selector.",
        "output": "OISCurve (one for TRY domestic, one for foreign).",
        "error_cases": [
          "ZeroValidQuotesError (per currency) — caught by orchestrator; emits FX_OIS_BOOTSTRAP_FAIL ERROR scoped to the currency that failed and decides whether the FX run can continue (see OQ-303)."
        ]
      }
    }
  ]
}
```

### Contract Notes

- **All contracts are consumer-owned.** M-108's `FXMarketDataProvider` signature is dictated by what M-111 needs, not by what's convenient to implement.
- **No untyped dicts cross any seam.** `FXMarketData`, `FXSummary`, `CurrencyPair` are concrete Pydantic / dataclass types from M-101 (FX types).
- **Diagnostic codes are namespaced `FX_*` and pair-agnostic.** The `pair` is carried as a structured field on each `Diagnostic`, not encoded into the code string (see OQ-304).
- **Exit code policy is identical to V1.** `0` = WARN-only or clean, `2` = at least one ERROR.

---

## 5. Data Flow

```mermaid
graph LR
    A1[fx_snapshots/<br/>fx_snapshot_USDTRY_YYYYMMDD.csv] --> P1[M-108<br/>FXCsvProvider]
    A2[snapshots/<br/>snapshot_YYYYMMDD.csv<br/>TRY OIS] --> P2[M-010<br/>CsvProvider]
    A3[snapshots/<br/>usd_ois_YYYYMMDD.csv<br/>USD OIS] --> P3[M-010<br/>CsvProvider]
    P1 --> O[M-111<br/>run_fx_bootstrap]
    P2 --> O
    P3 --> O
    O --> B1[bootstrap_curve<br/>TRY OIS]
    O --> B2[bootstrap_curve<br/>USD OIS]
    B1 --> F[build_fx_forward_curve<br/>M-105 — parity check]
    B2 --> F
    P1 --> F
    P1 --> G[build_cross_basis_curve<br/>M-106]
    F --> W1[write_fx_summary<br/>M-110]
    G --> W1
    F --> W2[write_fx_curve_partition<br/>M-110]
    G --> W2
    W1 --> O1[data/latest/<br/>usdtry_fx_summary.json]
    W2 --> O2[data/fx_curves/usdtry/...]
```

### CSV Schema (single-file, multiplexed)

`data/fx_snapshots/fx_snapshot_<PAIR>_YYYYMMDD.csv`:

```
# schema: fx-v1   (optional decorative comment line, ignored)
pair,instrument_type,tenor_code,tenor_days,bid,ask,mid,source
USDTRY,SPOT,SPOT,0,,, 32.4521,REUTERS
USDTRY,FORWARD_POINT,1M,30,1820,1850,1835,REUTERS
USDTRY,FORWARD_POINT,3M,91,5400,5460,5430,REUTERS
USDTRY,XCCY_BASIS,1Y,365,-185.0,-175.0,-180.0,BLOOMBERG
```

**Required columns:** `pair, instrument_type, tenor_code, tenor_days, bid, ask, mid, source`.

**Row-type semantics:**
- `SPOT` — exactly one row required per file. `tenor_days = 0`. `mid` is the spot rate.
- `FORWARD_POINT` — zero or more rows. `mid` is forward points in the convention's scale; pricer converts via `S + mid / scale`.
- `XCCY_BASIS` — zero or more rows. `mid` is bps (sign per the `quoted_on_foreign` convention).
- Rows with `instrument_type` outside this set emit `FX_IO_UNKNOWN_INSTRUMENT` ERROR.
- Rows where `pair` ≠ requested pair are skipped with `FX_IO_PAIR_MISMATCH` WARN (allows a multi-pair master CSV in dev/test).

**Per-row resolution** of bid/ask/mid follows the V1 OIS rule: `mid → avg(bid,ask) → ask → bid → skip-with-WARN`.

---

## 6. Key Sequences

### 6.1 `rates fx bootstrap` (happy path)

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as rates.cli (M-112)
    participant APP as rates.app.run_fx_bootstrap (M-111)
    participant CONV as Conventions (M-102)
    participant CSV as CsvProvider (M-010)
    participant FXCSV as FXCsvProvider (M-108)
    participant BOOT as bootstrap_curve (M-005)
    participant FWD as build_fx_forward_curve (M-105)
    participant BAS as build_cross_basis_curve (M-106)
    participant PERS as persistence (M-110)

    U->>CLI: rates fx bootstrap --pair USDTRY --as-of 2026-05-28
    CLI->>APP: run_fx_bootstrap(args)
    APP->>CONV: load(conventions.yaml) → TRY/USD OIS + fx_conventions[USDTRY]
    APP->>CSV: load(snapshot_TRY) → TRY MarketData
    APP->>CSV: load(usd_ois_snapshot) → USD MarketData
    APP->>FXCSV: load(fx_snapshot, USDTRY) → FXMarketData
    APP->>BOOT: bootstrap_curve(TRY) → TRY OISCurve
    APP->>BOOT: bootstrap_curve(USD) → USD OISCurve
    APP->>FWD: (FXMarketData, TRY, USD) → FXForwardCurve (parity check inside, may WARN)
    APP->>BAS: (FXMarketData) → CrossCurrencyBasisCurve
    APP->>PERS: write_fx_summary(...)
    APP->>PERS: write_fx_curve_partition(...)
    APP-->>CLI: exit code (0 if WARN-only)
    CLI-->>U: stdout pretty-printed diagnostics + exit code
```

### 6.2 `rates fx price` (ad-hoc pricing from persisted curves)

```mermaid
sequenceDiagram
    participant U as User
    participant CLI as rates.cli (M-112)
    participant APP as rates.app.run_fx_price (M-111)
    participant PERS as persistence (M-110)
    participant PRC as pricer (M-107)

    U->>CLI: rates fx price --pair USDTRY --tenor 3M --instrument outright
    CLI->>APP: run_fx_price(args)
    APP->>PERS: read latest <pair>_fx_summary.json + curves
    APP->>APP: reconstruct FXForwardCurve / CrossCurrencyBasisCurve
    APP->>PRC: price_outright_forward(curve, tenor) → quote
    APP-->>CLI: stdout (quote) + exit 0
```

### 6.3 `rates fx diagnose` (no outputs written)

Identical to 6.1 through the `FXMarketData` load + parity check, but **does not** call `bootstrap_curve` for foreign OIS, **does not** persist anything. Purpose: validate the FX snapshot CSV alone (schema, instrument coverage, spot presence, sign conventions on basis). Equivalent to V1 `rates diagnose`.

---

## 7. Build Order

```json
{
  "build_phases": [
    {
      "phase": 1,
      "name": "IO foundation",
      "modules": ["M-109", "M-108"],
      "rationale": "Schemas first (pure types, no deps beyond rates.fx.types); fx_market reader second (consumes the schemas).",
      "deliverable": "Loading a sample fx_snapshot CSV produces a valid FXMarketData. Pre-existing 1 skipped pytest awaiting rates.io.fx_market turns green.",
      "estimated_effort": "small"
    },
    {
      "phase": 2,
      "name": "Persistence",
      "modules": ["M-110"],
      "rationale": "Independent of orchestration; can be built and unit-tested standalone with hand-constructed FXSummary objects.",
      "deliverable": "Round-trip: build_fx_forward_curve output → write_fx_summary → JSON → re-read → equal curves (within 1e-12).",
      "estimated_effort": "small"
    },
    {
      "phase": 3,
      "name": "Orchestration",
      "modules": ["M-111"],
      "rationale": "Ties M-108, M-110, and existing OIS bootstrap together. Highest complexity; opus-assigned.",
      "deliverable": "rates.app.run_fx_bootstrap end-to-end on a fixture FX snapshot pair (USDTRY) produces both summary JSON and Parquet partitions with parity check WARN counts inside tolerance.",
      "estimated_effort": "large"
    },
    {
      "phase": 4,
      "name": "CLI wiring",
      "modules": ["M-112"],
      "rationale": "Thin shell on top of M-111. Built last so we can iterate on M-111 API without churning argparse code.",
      "deliverable": "`rates fx bootstrap` / `rates fx diagnose` / `rates fx price` end-to-end CLI tests pass. V1 `rates bootstrap` regression test still green.",
      "estimated_effort": "small"
    },
    {
      "phase": 5,
      "name": "Release prep",
      "modules": [],
      "rationale": "Documentation + smoke + CHANGELOG. Not a code module.",
      "deliverable": "CHANGELOG.md 0.3.0 section; docs/v1-smoke-test.md updated or new docs/fx-smoke-test.md; pyproject.toml version bump.",
      "estimated_effort": "small"
    }
  ]
}
```

### Ordering Principles Applied

- **Dependencies first.** M-109 → M-108 → M-110 → M-111 → M-112 follows the dependency DAG strictly.
- **Risk early.** Multi-curve orchestration (M-111) is the highest-risk piece; it lands in Phase 3 with two foundation phases under it, but earlier than the cosmetic CLI work.
- **Value early.** Phase 1 alone unblocks downstream consumers (the FX math layer is already usable as a library; once M-108 lands, callers can read CSVs without the orchestrator).
- **Stubbable contracts.** M-112 can be drafted in parallel with M-111 because C-103 is locked in this document.

---

## 8. Decisions & Trade-offs

| # | Decision | Chosen | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| D1 | FX snapshot format | Single CSV with `instrument_type` discriminator column | Three CSVs (spot, fwd-pts, xccy-basis) ; one wide CSV with nullable columns | Atomic snapshot, single Provider, mirrors V1 OIS `SPECIAL_TICKERS` row-routing pattern. Wide-with-nulls produces unreadable headers and ambiguous validation; three-file forces atomic snapshot management on the caller. |
| D2 | Foreign OIS source | Reuse existing `CsvProvider` against `data/snapshots/usd_ois_YYYYMMDD.csv` | New foreign-OIS provider; embedding USD rows inside the FX CSV | `conventions.yaml` already supports USD/EUR OIS rows; the existing schema is the right shape for any OIS strip. Embedding OIS rows in FX CSV would violate layering (FX → core). |
| D3 | CLI shape | Nested `rates fx <verb>` | Hyphenated `rates fx-bootstrap`; single-word `rates fxbootstrap` | Symmetric scaling for future products (`rates fi`, `rates eq`). argparse nested subparsers cost ~10 LoC. |
| D4 | Outputs from `rates fx bootstrap` | Summary JSON + Parquet partition | JSON-only; stdout-only | Mirrors V1 OIS persistence; analytics consumers downstream (V0.4 reporting layer) expect Parquet. |
| D5 | Pricer in `bootstrap` | Not orchestrated by `rates fx bootstrap`; its own `rates fx price` verb | Inline pricing of standard tenors during bootstrap | Bootstrap stays a pure stripping operation (idempotent over a snapshot). Pricing reads persisted curves, can be repeated with different tenors at zero re-bootstrap cost. |
| D6 | xccy stripping | Keep M-106 verbatim (no calibration) | Light calibration pass in v0.3.0 | Out of scope per CHANGELOG; M-106 already returns curves consumable by M-107. Full calibration is the V3 work item. |
| D7 | Diagnostic code namespace | `FX_*` codes are pair-agnostic; `pair` carried as a structured `Diagnostic.context` field | Per-pair codes (`FX_USDTRY_PARITY_MISMATCH`) | Pair-agnostic codes keep the grep-able code surface small; structured context preserves the pair for downstream filtering. Mirrors V1 (no per-currency code suffix). |
| D8 | Tests marker | New `phase8` pytest marker for v0.3.0 IO/CLI/app tests | Reuse `phase7` (FX) | Same boundary discipline V1 used between hardening (`phase5`) and properties (`phase6`); makes selective re-runs easy. |

---

## 9. Open Architecture Questions

| ID | Question | Provisional Resolution | Risk if Wrong |
|----|----------|------------------------|---------------|
| OQ-301 | Foreign-OIS snapshot discovery: explicit `--foreign-snapshot` flag, or auto-derived from `--pair USDTRY` → `data/snapshots/usd_ois_<as_of>.csv`? | Auto-derive with `--foreign-snapshot PATH` as explicit override. Same auto-derivation logic for `--domestic-snapshot`. | Low — single CLI surface change; can revisit before v0.3.0 cut. |
| OQ-302 | Should `FXSummary` JSON include pre-computed pricing snapshots (1M/3M/6M outright forwards) or stay strictly curve-only? | Curve-only in v0.3.0. Pricing is the dedicated `rates fx price` verb. Reporting layer (v0.4) can read curves and render. | Low — additive. |
| OQ-303 | Failure isolation: if foreign-OIS `bootstrap_curve` fails, can the FX run continue? | TRY (domestic) bootstrap failure ⇒ abort with `FX_OIS_BOOTSTRAP_FAIL` ERROR (no FX forward parity possible, no usable persistence). Foreign-OIS failure ⇒ skip parity check inside M-105 (emit `FX_PARITY_CHECK_SKIPPED` WARN), proceed with naked covered-interest forwards from quotes; basis curve still builds. | Medium — affects exit-code semantics and CHANGELOG promise. Validate during M-111 implementation. |
| OQ-304 | Currency-pair scoping of `FX_*` diagnostic codes. | Codes are pair-agnostic; pair lives on `Diagnostic.context["pair"]`. CLI pretty-printer formats `[FX_PARITY_MISMATCH USDTRY 3M] ...`. | Low — structured context already exists in V1 diagnostics. |
| OQ-305 | Should `Diagnostics.context` schema be formalized in Pydantic for FX rows (vs. the V1 free-form `dict[str, Any]`)? | Keep V1 free-form for v0.3.0 to avoid disturbing the diagnostics module. Revisit in v0.4 if reporting layer needs a typed view. | Low. |

These resolutions are the design's default. The Developer skill can proceed against them; any deviation during implementation must update this section.

---

## 10. Acceptance Gates for v0.3.0

The release is cuttable when ALL hold:

- `mypy --strict src/rates` clean (now includes new IO/CLI/app modules).
- `ruff check src tests scripts` clean.
- `pytest -q` — green; the 1 previously-skipped test (`rates.io.fx_market`) is now active and passing. New `phase8` marker has ≥ 20 tests covering IO schema validation, CLI argparse coverage, app pipeline orchestration on fixture data, persistence round-trip.
- V1 OIS regression: `rates bootstrap` / `rates diagnose` / `rates forward` smoke unchanged. `scripts/smoke_test.py` exit 0, reprice within `1e-10` (same as v0.2.0).
- New `scripts/fx_smoke_test.py` or extension to existing script: `rates fx bootstrap --pair USDTRY` on a fixture snapshot exits 0, summary JSON loads back into Pydantic without errors, parity-check diagnostics within 5 bps tolerance.
- `docs/fx-io-architecture.md` (this file) merged to develop before any implementation PR opens.
- `CHANGELOG.md` 0.3.0 section drafted and referenced in the release PR.

---

## 11. Out of Scope (explicit)

The following items are NOT addressed in v0.3.0 and remain deferred to later releases:

- **Full V3 xccy stripping calibration.** `build_cross_basis_curve` continues to hold quotes verbatim.
- **Snapshot freshness check.** V1.5 grill follow-up; still deferred.
- **Multi-scenario MPC paths (hawkish/base/dovish).** Single-path only.
- **FX scenario module.** No FX equivalent of `rates.core.scenario` in this release.
- **Bloomberg / Refinitiv FX adapters.** `FXMarketDataProvider` Protocol is designed for them; concrete impls land later.
- **GUI / web frontend.** CLI-only.

---

## 12. Validation Checklist (review with user before implementation)

When reviewing this document, confirm:

1. **Module split** — is M-108 + M-109 + M-110 + M-111 + M-112 the right granularity, or should anything merge/split?
2. **C-101 schema** — is the multiplexed FX CSV with `instrument_type` column the right shape, or do quote-vendors push you toward separate files?
3. **C-103 surface** — are the three verbs (`bootstrap`, `diagnose`, `price`) the right set? Missing `forward`-equivalent? Missing `report`?
4. **Build order** — comfortable with M-111 (the hard module) landing in Phase 3 rather than earlier prototyping?
5. **OQ-303** — does the proposed failure-isolation policy (TRY fail = abort, foreign fail = skip parity) match operational expectations?
6. **No deviation from V1 idioms** — anything in this design that breaks consistency with `docs/architecture.md` patterns you'd want surfaced?
