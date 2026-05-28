# Rates Engine — Requirements Document

**Domain:** quant (foundation for future frontend dashboard)
**Depth:** standart
**Date:** 2026-04-17
**Status:** Validated (Grill pass complete)
**Revision:** 2 (post-grill)

---

## 1. Executive Summary

Rates Engine is a Python library and CLI that builds Turkish Lira OIS (Overnight Index Swap) curves from market quotes and a user-defined CBRT policy rate path, replacing a current Excel-based workflow. V1 delivers curve bootstrapping, interpolation, forward-starting OIS computation, and market-vs-scenario comparison — consumed via library API and thin CLI wrapper. The same core is designed to be consumed later by an FX pricing module (V2), a Bloomberg blpapi data adapter (V2), and a web dashboard (V3).

The design sacrifices Excel bit-identical compatibility in favor of market convention correctness. Several known defects in the Excel source (empty band columns, broken holiday reference, buggy cumulative DF chain, zero-defaulted spread cell) are explicitly replaced by correct implementations here.

## 2. Goals & Success Criteria

- [ ] Given a CSV snapshot of TYSO market quotes + BISTTREF row, produce a bootstrapped TRY OIS curve (spot rates + DFs + forward rates) that reprices each market pillar to within 1e-10 absolute error.
- [ ] Given a CBRT MPC policy path (CSV), produce a daily TLREF-equivalent index (Act/365 accrual) and a scenario-implied DF curve comparable tenor-by-tenor to market.
- [ ] Support both log-linear DF interpolation (default) and piecewise-linear on zero rates (opt-in via parameter).
- [ ] Produce forward-starting OIS (1x2m, 2x3m, …, 9x10y) from the bootstrapped curve.
- [ ] Produce a market-vs-scenario comparison report in a single common day-count convention (Act/365, TLREF basis).
- [ ] Run diagnostics and report all flags under a warn+continue policy. Distinguish curve-producible anomalies (WARN, exit 0) from curve-blocking errors (ERROR, exit 2).
- [ ] Persist each run: one summary JSON (`data/latest/try_ois_summary.json`) + one partition row in a PyArrow dataset (`data/curves/try_ois/date=YYYY-MM-DD/`).
- [ ] Sever the Excel dependency entirely: no runtime Excel reading, no VBA, no workbook bridge. Excel used only for one-time reference data export during V1 dev.
- [ ] Design adapter and output interfaces so that a Bloomberg blpapi provider (V2) and a web UI consumer (V3) can plug in without touching curve math.

## 3. Functional Requirements

### 3.1 Core Features

```json
{
  "features": [
    {
      "id": "F-001",
      "name": "OIS curve bootstrap",
      "description": "Unified OIS swap bootstrap for all pillars. Each tenor is treated as an OIS swap; for tenors <= 1Y the swap degenerates to a single bullet payment at maturity (DF = 1/(1 + r*tau)), for tenors > 1Y annual coupons are used. All pillars start at T+0 spot (market standard). Day-count Act/360 per conventions.yaml.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given 22 TYSO tenors from 1D to 10Y, bootstrap returns DFs that reprice each pillar to within 1e-10 absolute error.",
        "For tenors > 1Y, annual coupon schedule is used; closed-form solution where possible, scipy.optimize.brentq fallback for irregular payment grids.",
        "Bootstrap is deterministic: same input -> same output, byte-identical across runs.",
        "When the same tenor has both a direct quote and an implied value from a longer pillar's interpolation, the direct quote always wins (a diagnostic flag reports the implied-vs-direct spread)."
      ]
    },
    {
      "id": "F-002",
      "name": "Curve interpolation",
      "description": "Two interpolation schemes between pillars: log-linear on discount factors (default, fixed-income standard) and piecewise-linear on zero rates (Excel parity, opt-in). Distance metric is calendar days.",
      "priority": "must-have",
      "acceptance_criteria": [
        "At pillar dates, both schemes return the bootstrapped pillar DF exactly.",
        "Between pillars, log-linear DF returns monotonically decreasing DFs (for positive rate regimes).",
        "Scheme selection is explicit: Curve(..., interp='log_linear_df' | 'linear_zero'); default 'log_linear_df'.",
        "Interpolation weight w = (t - a) / (b - a) uses calendar day count, not business days or year fraction."
      ]
    },
    {
      "id": "F-003",
      "name": "Forward-starting OIS computation",
      "description": "Compute forward-starting OIS rates from the bootstrapped curve: f = (DF_start / DF_end - 1) * basis / days. Uses the same day-count convention as spot rates (Act/360 for TRY, per conventions.yaml).",
      "priority": "must-have",
      "acceptance_criteria": [
        "For any (start_date, end_date) pair, returns a forward rate consistent with the underlying DFs.",
        "Standard monthly forward ladder (1x2, 2x3, ..., 11x12, 1x3, 3x6, 6x9, 9x12) produced as a DataFrame via curve.forward_ladder().",
        "Forward basis implicitly tied to spot basis (TRY = Act/360). No separate forward convention config."
      ]
    },
    {
      "id": "F-004",
      "name": "User MPC policy path -> daily TLREF index",
      "description": "Given a CSV of upcoming MPC meeting dates with bps changes, build a daily TLREF index from today forward (Act/365 accrual). Initial TLREF value comes from the BISTTREF ticker row in the market snapshot. Unknown future dates carry forward the last policy rate.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Given 10 MPC meeting dates over 2 years, produce daily index for ~3723 business days (~10y horizon).",
        "Index on day 1 (valuation date) = 1.0. Index on day N = product of (1 + rate_d / 365 * delta_t).",
        "TLREF-vs-policy spread assumed 0 in V1 (deferred to V2).",
        "Initial TLREF sourced from snapshot_YYYYMMDD.csv's BISTTREF row; absence raises ERROR."
      ]
    },
    {
      "id": "F-005",
      "name": "Scenario band — full path parallel shift",
      "description": "Given a user policy path and a band parameter N bps, generate low-band and high-band daily TLREF indexes by shifting the entire policy rate path by +/-N bps uniformly. Default band = 0 bps, which short-circuits to mid curve only (no band computation).",
      "priority": "must-have",
      "acceptance_criteria": [
        "Band parameter is a CLI/API argument: --band 150 (bps, integer).",
        "Full path parallel shift: every policy rate value in the vector is shifted by +N/-N bps for high/low band respectively.",
        "Band = 0 short-circuits: only mid curve produced, no band arrays in output.",
        "Band > 0 produces three DF curves and three daily indexes (mid, low, high); all stored in output."
      ]
    },
    {
      "id": "F-006",
      "name": "Curve diagnostics",
      "description": "Run checks on market quotes and bootstrapped curve. All anomalies are categorized: WARN (curve produced, flag in output, exit 0) vs ERROR (curve not producible, abort, exit 2). Warn+continue for everything that still allows a curve to be built.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Missing quote fields trigger fallback per priority order: mid -> avg(bid,ask) -> ask -> bid -> interpolate from neighbors. Each fallback raises a WARN with the path used.",
        "bid > ask -> WARN, use avg(bid,ask).",
        "Non-monotone DFs between pillars -> WARN, report the violating range, do not repair (user decides).",
        "Negative forward rate -> WARN, report the tenor range.",
        "ERROR conditions (abort): snapshot file missing, snapshot 0 rows, all quotes invalid, mpc_path.csv unparseable, BISTTREF row missing.",
        "All flags reach both stdout and the summary JSON under diagnostics: [{severity, code, message, context}]."
      ]
    },
    {
      "id": "F-007",
      "name": "CSV snapshot ingest adapter",
      "description": "Read the canonical snapshot CSV and produce an in-memory MarketData object. Adapter is a concrete implementation of the MarketDataProvider protocol; future BloombergProvider (V2) implements the same protocol.",
      "priority": "must-have",
      "acceptance_criteria": [
        "CSV required columns: [tenor_code, tenor_days, start_date, end_date, bid, ask, mid, source]. Extra columns ignored (forgiving reader).",
        "Reader validates required columns present; missing required column -> ERROR with clear message listing what's missing.",
        "An optional first-line comment '# schema: v1' is accepted and ignored (documentation only).",
        "Dates parsed in ISO format (YYYY-MM-DD). Locale issues (comma decimal, DMY dates) -> ERROR.",
        "MarketDataProvider protocol defined in rates.io; CsvProvider is one concrete impl."
      ]
    },
    {
      "id": "F-008",
      "name": "Output persistence",
      "description": "On each run: (a) write summary JSON to data/latest/try_ois_summary.json (pillar rates, forward ladder, comparison, diagnostics, metadata) — human-readable, pretty-printed, small (~5-10 KB); (b) write row to PyArrow partitioned dataset at data/curves/try_ois/date=YYYY-MM-DD/ containing the full daily grid for historical analysis.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Summary JSON contains: schema_version, valuation_date, as_of_timestamp, pillars[], forward_ladder[], comparison[], diagnostics[], config_snapshot.",
        "Parquet dataset follows Hive-style partitioning (date=YYYY-MM-DD); readable with pyarrow.dataset or pandas.read_parquet (single-path or dataset-level).",
        "Re-running on the same day overwrites both the summary JSON and the partition directory contents.",
        "Parquet schema is versioned; schema changes accompanied by a version bump and a read migrator (not implemented in V1; hook reserved)."
      ]
    },
    {
      "id": "F-009",
      "name": "CLI (thin wrapper over library)",
      "description": "Entry point 'rates' with subcommands: bootstrap (primary: ingest -> produce outputs), diagnose (run checks only, no outputs), forward (ad-hoc forward rate query from the latest persisted curve).",
      "priority": "must-have",
      "acceptance_criteria": [
        "rates bootstrap [--snapshot PATH] [--mpc-path PATH] [--band N] [--interp MODE] runs the full pipeline. Defaults to most-recent snapshot and config/mpc_path.csv. Default band = 0.",
        "rates diagnose --snapshot PATH prints diagnostics, no outputs written. Exits 0 on WARN-only, 2 on ERROR.",
        "rates forward --start YYYY-MM-DD --end YYYY-MM-DD reads latest summary JSON and prints the forward rate.",
        "CLI uses argparse (stdlib). Each subcommand is <30 lines, all logic lives in rates.* library modules."
      ]
    },
    {
      "id": "F-010",
      "name": "Holiday calendar management",
      "description": "holidays Python package as baseline with CSV overrides in config/holidays/{tr,us,eu}.csv. CSV entries win on conflict. V1 fidelity validated once against Excel tatiller reference list for 2024-2026; results stored in docs/holidays-fidelity.md and inform whether package-primary or CSV-primary strategy is used.",
      "priority": "must-have",
      "acceptance_criteria": [
        "WORKDAY-equivalent function honors both holidays package + CSV overrides; CSV wins on conflict.",
        "A CSV override file with no rows is valid (no overrides, package-only).",
        "Per-calendar query: is_holiday(date, calendar='TR') and add_business_days(date, n, calendar='TR').",
        "Before code freeze, a one-time validation script compares holidays.Turkey(2024-2026) to the Excel reference; delta report committed to docs/holidays-fidelity.md.",
        "If delta > 5 days/year for TR, strategy flips: CSV primary, package fallback only."
      ]
    },
    {
      "id": "F-011",
      "name": "Day-count & payment convention config",
      "description": "Centralized market convention definitions in config/conventions.yaml: per-currency day-count, payment schedule (bullet <= 1Y, annual > 1Y for TRY), associated calendar. No hard-coded 360/365 magic numbers outside this module.",
      "priority": "must-have",
      "acceptance_criteria": [
        "YAML structure includes: ois_conventions[currency]{ day_count, payment{ default_frequency, bullet_until }, calendar }; tlref_convention{ day_count }; spread_reporting{ day_count }.",
        "V1 TRY values: ois day_count=Act/360, payment default annual, bullet_until=1Y, calendar=TR; TLREF day_count=Act/365; spread_reporting day_count=Act/365.",
        "Convention object loaded once at startup, passed explicitly to every module. No global state.",
        "Round-trip: rate -> DF -> rate reproduces input to 1e-12 under declared convention.",
        "CLI override: rates bootstrap --convention-override ois.TRY.day_count=Act/365 (advanced use)."
      ]
    },
    {
      "id": "F-012",
      "name": "Market vs scenario comparison report",
      "description": "Tenor-by-tenor comparison: market spot OIS rate vs user-scenario-implied spot OIS rate, plus spread in bps. Both rates expressed in a common day-count convention (Act/365, TLREF basis) for a meaningful spread. Extends to monthly forward ladder points.",
      "priority": "must-have",
      "acceptance_criteria": [
        "Comparison table columns: tenor, market_rate_act365, scenario_rate_act365, spread_bps, days_to_maturity.",
        "Market rate converted from its native Act/360 quote to Act/365 for display; scenario rate natively Act/365.",
        "Covers all pillar tenors + standard monthly forward ladder points (1x2, 2x3, ..., 11x12, 1x3, 3x6, 6x9, 9x12).",
        "Table embedded in summary JSON under comparison key.",
        "Pretty-printed to stdout on rates bootstrap (suppressible with --quiet)."
      ]
    }
  ]
}
```

### 3.2 Primary User Flow

```
09:30 — Sabah rutini
  1. Market snapshot'i elde et
     V1: data/snapshots/snapshot_YYYYMMDD.csv'yi manuel hazirla/duzenle
         (TYSO quote'lar + BISTTREF satiri + optional extra rows)
     V2+: rates ingest-bloomberg (blpapi, otomatik)

  2. MPC beklentisi degistiyse config/mpc_path.csv'yi duzenle
     (meeting_date, bps_change, rationale kolonlari)

  3. Komut:
       rates bootstrap
         # defaults: en guncel snapshot, default mpc_path, band=0, interp=log_linear_df

     Band + alternatif interpolation istiyorsan:
       rates bootstrap --band 150 --interp linear_zero

  4. Ciktilari incele:
       stdout:
         - Pillar table (bootstrap sonuclari)
         - Forward ladder
         - Market vs scenario comparison (F-012)
         - Diagnostics ozeti (WARN sayisi)
       data/latest/try_ois_summary.json   (detayli ozet + tum flag'ler)
       data/curves/try_ois/date=YYYY-MM-DD/ (full daily grid, historical)

  5. Opsiyonel: ad-hoc sorgular
       rates forward --start 2026-07-01 --end 2026-10-01
       rates diagnose --snapshot data/snapshots/snapshot_20260417.csv
```

**Error handling philosophy:** warn+continue for anything that still allows a curve to be produced. Fail-fast (ERROR, exit 2) for anything that prevents curve construction: missing snapshot file, zero valid quotes, missing BISTTREF, unparseable MPC path, schema column missing.

### 3.3 Data Requirements

```json
{
  "data_sources": [
    {
      "name": "Market snapshot",
      "type": "file",
      "format": "CSV",
      "frequency": "on-demand (V1) -> real-time (V2 via blpapi)",
      "auth_required": false,
      "schema_required_columns": ["tenor_code", "tenor_days", "start_date", "end_date", "bid", "ask", "mid", "source"],
      "special_rows": ["BISTTREF row required — provides initial TLREF value"],
      "path": "data/snapshots/snapshot_YYYYMMDD.csv",
      "notes": "Forgiving reader: extra columns ignored. First-line '# schema: v1' comment optional, documentation only."
    },
    {
      "name": "MPC policy path",
      "type": "file",
      "format": "CSV",
      "frequency": "on-demand (updated when expectations change)",
      "auth_required": false,
      "schema_required_columns": ["meeting_date", "bps_change"],
      "schema_optional_columns": ["rationale"],
      "path": "config/mpc_path.csv"
    },
    {
      "name": "Holiday overrides",
      "type": "file",
      "format": "CSV (one per calendar)",
      "frequency": "rare (annual or when Excel reference list updates)",
      "auth_required": false,
      "schema_required_columns": ["date"],
      "schema_optional_columns": ["description"],
      "path": "config/holidays/{tr,us,eu}.csv"
    },
    {
      "name": "Conventions",
      "type": "file",
      "format": "YAML",
      "frequency": "static (change only by design decision)",
      "auth_required": false,
      "path": "config/conventions.yaml"
    }
  ],
  "outputs": [
    {
      "name": "Latest summary (JSON)",
      "path": "data/latest/try_ois_summary.json",
      "format": "JSON (pretty-printed)",
      "size": "~5-10 KB",
      "purpose": "Current-day curve summary + comparison + diagnostics; consumed by humans and future web UI"
    },
    {
      "name": "Historical archive (PyArrow partitioned dataset)",
      "path": "data/curves/try_ois/date=YYYY-MM-DD/",
      "format": "Parquet (Hive-partitioned)",
      "purpose": "Time-series archive with full daily grid for backtesting, PnL attribution, regime analysis"
    }
  ]
}
```

## 4. Non-Functional Requirements

```json
{
  "performance": "Single-run bootstrap under 1 second for 22 pillars + ~3700 daily grid points on a modern laptop. Not a hard requirement in V1; pragmatic.",
  "security": "No sensitive data in V1. No secrets, no auth. Bloomberg credentials introduced in V2 via env vars.",
  "deployment": {
    "target": "local developer laptop (macOS, Linux)",
    "provider": "none (no cloud in V1)"
  },
  "scale": "Single user, single machine, single currency (TRY). Hundreds of historical snapshots — well within single-machine Parquet scale.",
  "monitoring": "print()-based logging via a rates.core.log.log() indirection helper. Library code calls log() not print(). Single-file swap to loguru/structlog later.",
  "tech_stack": {
    "python": "3.11",
    "dependencies_runtime": "numpy, pandas, pyarrow, scipy, holidays, pyyaml, pydantic",
    "dependencies_dev": "pytest",
    "package_management": "pip + pyproject.toml + requirements.txt (runtime) + requirements-dev.txt (dev)",
    "structure": "src/ layout, library (rates.*) + thin CLI (rates.cli)"
  },
  "code_ownership": "Solo dev. Type hints used pragmatically on public APIs. Docstrings on public API. CI not set up in V1."
}
```

## 5. Constraints & Assumptions

**Constraints:**
- Python 3.11
- pip + pyproject.toml + requirements.txt
- No cloud dependencies in V1
- No Bloomberg Terminal dependency in V1 (CSV only)
- No web framework in V1 (library + CLI only)
- Solo dev — pragmatic bias over enterprise rigor

**Assumptions:**
- User runs 'rates bootstrap' manually when needed (no scheduler in V1).
- Snapshot CSV is well-formed (UTF-8, ISO dates, period decimal).
- 'holidays' package's TR calendar fidelity against Excel 'tatiller' is validated once at V1 start; strategy adjusted accordingly (F-010).
- Initial TLREF comes from the BISTTREF row in the market snapshot. V2 replaces this with a live blpapi pull transparently (same interface).
- TLREF-policy spread = 0 in V1 (F-004 scope note).
- Excel AD column (buggy cumulative DF) was a dead artifact — replaced here by a correct unified bootstrap.
- Excel [1]Tatiller! external reference is a broken link — ignored; local holiday CSVs are the single source of truth.
- Excel 'low band / high band' stub (referencing empty columns) maps to F-005 scenario band with a clarified semantic (full path parallel shift).
- Excel pillar chaining (TYSO2D starts at TYSO1D end, etc.) is non-standard — V1 uses market-standard T+0 spot start for all pillars.
- User will verify CSV snapshot freshness manually in V1 (no freshness timestamp check).

## 6. Out of Scope (V1)

**Deferred to V2:**
- Bloomberg blpapi data adapter
- FX curves (USD_ois, EUR OIS, GBP/CHF OIS)
- TRY offshore curve (NDF-like)
- FX forward / swap fair pricing (USDTRY, EURTRY, cross-basis)
- Multi-scenario modeling (hawkish/base/dovish parallel, user-defined N scenarios beyond +/- band)
- TLREF-vs-policy spread modeling (constant bps or time-varying)
- TCMB swap auction blotter & live/dead tracking
- TCMB swap auction historical results ingestion
- Excel-chain pillar start mode (F-006 alternate convention)

**Deferred to V3:**
- Web dashboard (framework TBD)
- Interactive MPC path editor (UI-driven)
- Live curve streaming

**Never in this project:**
- Trading execution / order management
- Multi-user auth / tenancy
- Compliance / audit logging

## 7. Resolved Open Questions (post-grill)

All open questions from Revision 1 are now resolved. See §9 Grill Notes for the audit trail.

## 8. Implementation Hints

- **src/ layout.** rates.* importable; tests outside src in tests/.
- **Pydantic v2 for boundaries, dataclasses for internals.** CSV parse -> Pydantic validation at the IO boundary (rates.io). Internal curve objects are frozen dataclasses. One dep (pydantic) added.
- **DataFrame is implementation detail, not API.** OISCurve wraps pillars as a DataFrame + numpy arrays for daily grid, but exposes methods: curve.df_at(date), curve.forward(start, end), curve.forward_ladder(), curve.to_summary_dict(). Callers never see the DataFrame directly.
- **Protocols over ABCs.** MarketDataProvider as typing.Protocol for adapter pattern flexibility.
- **No global state.** Conventions, calendars, config passed explicitly. No module-level singletons.
- **JSON schema for outputs.** Pydantic models serialize summary JSON; generated JSON schema committed to docs/schemas/summary.schema.json for V3 web UI consumption.
- **Parquet via PyArrow dataset API.** Use pyarrow.dataset.write_dataset with partitioning='hive'. Read via pyarrow.dataset or pandas.read_parquet(path, engine='pyarrow') — both handle partitions.
- **Upsert strategy.** Re-running on the same day overwrites the partition directory (date=YYYY-MM-DD/) contents. No true append needed.
- **CLI via argparse.** Stdlib, minimal ethos.
- **Bootstrap numerical method.** Closed-form where possible (<= 1Y single payment, 2Y with known 1Y DF); scipy.optimize.brentq for irregular grids.
- **Initial TLREF flow.** MarketData object has a .special_rates: dict[str, float] populated from rows with known tickers (BISTTREF, etc.). Scenario module reads market.special_rates['BISTTREF'] as its starting point.

## 9. Grill Notes (audit trail)

Grill conducted: 2026-04-17, 5 rounds, Medium-Hard intensity (standart depth).

### Decisions from grill

| # | Branch | Decision | Notes |
|---|--------|----------|-------|
| G1 | Scenario band semantic | Full path parallel shift (all rates in vector +/- N bps uniformly) | Clarifies Excel's empty-column stub |
| G2 | Band=0 behavior | Short-circuit to mid only | No wasteful 3x identical compute |
| G3 | Bootstrap algorithm | Unified OIS swap formula, all pillars, all tenors | <=1Y degenerates to bullet; >1Y annual coupons |
| G4 | Quote tenor collision | Direct quote wins, interpolation is fallback; spread flagged | |
| G5 | Initial TLREF source | Market snapshot row (BISTTREF ticker) | V2 blpapi-ready |
| G6 | Spread convention (F-012) | Common Act/365 (TLREF basis) | Market Act/360 quotes converted for display |
| G7 | Interpolation distance | Calendar days | |
| G8 | Payment schedule config | config/conventions.yaml per-currency | ois_conventions[TRY] |
| G9 | Diagnostics severity | 2-level: WARN (exit 0) vs ERROR (exit 2) | Self-assigned, user approved |
| G10 | Pillar start convention | V1 = T+0 spot only; Excel chain V1.5+ | Push-back accepted: simpler V1 |
| G11 | JSON output scope | Summary JSON + Parquet; full JSON skipped | Push-back accepted: no format duplication |
| G12 | Parquet structure | PyArrow partitioned dataset (hive, date=...) | Backtest-friendly |
| G13 | Quote fallback priority | mid -> avg(bid,ask) -> ask -> bid -> interpolate | Each fallback raises WARN |
| G14 | CSV schema evolution | Forgiving reader, required column set, '# schema: v1' comment decorative | |
| G15 | Holidays fidelity | One-time validation against Excel tatiller for 2024-2026; strategy adjusted per result | Delta report in docs/holidays-fidelity.md |
| G16 | F-012 priority | must-have (not should-have) | V1 without this feels incomplete |
| G17 | Forward basis (F-003) | Same as spot (Act/360 for TRY); no separate config | Self-assigned, implicit |

### Intentionally deferred (not V1)

- Hypothesis-based property testing for curve invariants (user chose pytest only; flagged to revisit at V1 sign-off — 2-hour investment for regression safety net).
- Freshness check on snapshot CSV (user verifies manually in V1).
- Multi-scenario modeling beyond +/- band (hawkish/base/dovish parallel paths).

### Critical items resolved

Two items flagged critical at grill start:
- **Bootstrap algorithm unspecified** -> resolved (G3).
- **Initial TLREF source** -> resolved (G5).

No critical items remain open.

---

## Next Steps

1. ✅ Requirements validated (this document).
2. -> Invoke `system-architect` skill -> produces architecture.md from these requirements.
3. -> Optional: second `grill-me` pass on architecture.
4. -> Invoke `quant-developer` skill with requirements + architecture -> produces rates-engine/ project scaffold with typed stubs, CLAUDE.md, test structure.
5. -> Implement V1 module-by-module.
