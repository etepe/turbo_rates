# Changelog

All notable changes to this project are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[SemVer](https://semver.org/). The `develop` branch carries unreleased work;
`main` carries tagged releases.

## [0.3.0] — 2026-05-28

### Added — FX IO / CLI / app wiring (the "Not in this release" item from 0.2.0)

The v0.2.0 FX math layer (M-101..M-107) is now consumable end-to-end. No
math layer changes; no V3 xccy calibration. Architecture is
`docs/fx-io-architecture.md` (post-grill, 8/8 focus areas resolved). PRs
#15 (architecture), #16 (M-108/M-109), #17 (M-110), #18 (M-111 diagnose),
#19 (M-111 bootstrap + pricing), #20 (M-112 CLI).

- **`rates.io.fx_market`** (M-108) — `FxCsvProvider` reads the multiplexed
  FX snapshot CSV (`pair, instrument_type, tenor_code, tenor_days,
  bid, ask, mid, source`) into `FXMarketData`. Row-level routing via
  Pydantic v2 discriminated union (`SPOT | FORWARD_POINT | XCCY_BASIS`);
  file-level invariants (exactly-one-SPOT, dedup, pair-not-found ordering)
  run after the streaming row pass. Ten `FX_IO_*` diagnostic codes. Joint
  calendar arithmetic for spot_date computation. PR #16.
- **`rates.io.schemas`** (M-109) — `FXSummary`, `FXConfigSnapshot`,
  `FXForwardPillarOut`, `FXBasisPillarOut`, `FXParityCheckRow`, plus
  `FX_SCHEMA_VERSION = 1` distinct from the V1 OIS `SCHEMA_VERSION` so the
  two surfaces evolve independently. `FXSummary.from_domain` mirrors the
  V1 mapper pattern. PR #16.
- **`rates.io.persistence`** (M-110) — `write_fx_summary` (atomic JSON,
  parent dirs auto-created) and `write_fx_curve_partition` (two Hive-
  partitioned Parquet datasets per run: `forward/` always,
  `basis/` only when XCCY_BASIS rows present, separate sub-datasets
  because their schemas differ). PR #17.
- **`rates.app`** (M-111) — five new entry points consuming the M-110 +
  the M-101..M-107 math layer:
  - `run_fx_diagnose` — full pipeline through parity check; no
    persistence. Mirrors V1 `run_diagnose` semantics. PR #18.
  - `run_fx_bootstrap` — same pipeline plus persistence (summary JSON +
    curve Parquet). Builds `FXParityCheckRow` rows from the
    `FX_PARITY_MISMATCH` WARNs M-105 emits, matching tenor_code against
    the forward curve's pillar table. PR #19.
  - `run_fx_price_outright` / `run_fx_price_swap` — load the persisted
    FXSummary, reconstruct curves (`_fx_forward_from_summary` /
    `_fx_basis_from_summary`), enforce the `--tenor / --value-date` mutex
    per architecture D11, delegate to `rates.fx.pricer`. `--tenor`
    resolves to a value-date first so both flags share one pricing code
    path. Extrapolation past the last pillar is WARN-only with last-
    pillar clipping. PR #19.
  - `run_fx_price_xccy` — tenor-keyed per C-103; resolves against the
    persisted basis pillar table so the market-convention maturity dates
    (e.g. USDTRY 1Y = 360 days) survive the round-trip. PR #19.
- **`rates.cli`** (M-112) — nested `rates fx <verb>` subparser tree with
  five verbs (`diagnose / bootstrap / price-outright / price-swap /
  price-xccy`). V1 OIS surface (`rates bootstrap`, `rates diagnose`,
  `rates forward`) untouched. Shared FX args factored into
  `_add_fx_shared` / `_add_fx_snapshot_args`; every subcommand body
  stays ≤30 LoC per F-009. Argparse `add_mutually_exclusive_group(
  required=True)` enforces the tenor / value-date mutex per leg. PR #20.

### Added — Cross-CSV invariants and diagnostic codes

- **`FX_SNAPSHOT_DATE_MISMATCH`** — orchestrator-level ERROR fired when
  the FX, TRY-OIS, and foreign-OIS snapshot filenames carry disagreeing
  `_YYYYMMDD` suffixes vs. `--as-of`. Joint calendar semantics already
  require both markets open on the FX swap settlement, so mixed-date runs
  have no operational meaning (architecture D12).
- **`FX_DOMESTIC_OIS_BOOTSTRAP_FAIL` / `FX_FOREIGN_OIS_BOOTSTRAP_FAIL`** —
  abort on either side per architecture D9 (a "WARN + continue without
  parity check" path would produce output indistinguishable from a clean
  run; `--no-parity-check` opt-out is deferred to v0.4).
- **Pricing-side validation:** `FX_PRICE_INVALID_VALUE_DATE`,
  `FX_PRICE_VALUE_DATE_BEFORE_SPOT`, `FX_PRICE_EXTRAPOLATION` (the last
  is WARN-only per D11; the curve hard caps at the last pillar so call
  sites clip to last-pillar forward).

### Added — Curated FX smoke gate

- **`fixtures/fx_smoke_usdtry/`** — committed curated USDTRY fixture
  (FX snapshot + TRY OIS + USD OIS). Forward points are parity-implied
  from the OIS DFs by construction, so M-105's parity check emits zero
  `FX_PARITY_MISMATCH` WARNs on this fixture.
- **`scripts/fx_smoke_test.py`** — manual gate behind architecture §10.
  Default run validates the bootstrap exit code, summary JSON Pydantic
  round-trip, and zero-WARN gate. `--regenerate` recomputes the forward
  points after any OIS-snapshot or bootstrap-math change.
- **`docs/fx-smoke-test.md`** — documents the gate and regeneration flow.

### Changed

- The single previously-skipped pytest (`tests/io/test_fx_market.py`
  awaiting `rates.io.fx_market`) is now active and passing.
- `pyproject.toml` version → `0.3.0`.

### Not in this release

- **Full V3 xccy stripping calibration.** `build_cross_basis_curve` and
  `price_xccy_basis_swap` still hold curves verbatim; `dom_ois / for_ois`
  are accepted by the pricer for signature stability but discarded.
- **Snapshot freshness check.** Still deferred from V1.5 grill.
- **Multi-scenario MPC paths (hawkish/base/dovish).** Single-path only.
- **FX scenario module.** No FX equivalent of `rates.core.scenario`.
- **Bloomberg / Refinitiv FX adapters.** `FXMarketDataProvider` `Protocol`
  is designed for them; concrete impls land later.
- **Market-noise FX smoke against real snapshots.** Out of scope —
  separate gate at a different tolerance per architecture §10 closing
  paragraph.

### Validation gates at release cut

- `mypy --strict src/rates` — clean (28 source files, up from 27).
- `ruff check src tests scripts` — clean.
- `pytest -q` — 315 passed (up from 238). New `phase8` marker covers FX
  IO schema, app pipeline, persistence round-trip, CLI shape + delegation;
  62 new tests under that marker.
- V1 OIS regression: `rates bootstrap` / `rates diagnose` / `rates
  forward` smoke unchanged. `scripts/smoke_test.py` exit 0 (manual gate).
- New FX smoke: `python scripts/fx_smoke_test.py` exit 0; 3 forward
  pillar(s), 1 basis pillar(s), **0 `FX_PARITY_MISMATCH` WARNs** on the
  curated `fixtures/fx_smoke_usdtry/` fixture.

## [0.2.0] — 2026-05-28

### Added — V1.5 hardening

- **`SC_MEETING_IN_PAST` diagnostic** (`rates.core.scenario`). Past-dated MPC
  meetings are already reflected in the snapshot `BISTTREF` reading; the
  scenario module now emits one WARN per offender and filters them out
  before the policy path is built, instead of silently double-counting the
  bps change. PR #8.
- **Hypothesis property-test suite** (`tests/properties/`, marker `phase6`).
  Seven invariants covering bullet + annual bootstrap reprice tolerance,
  curve anchor identity, forward = DF ratio, log-linear DF monotonicity,
  scenario anchor identity, and band ordering at horizon. Closes the V1
  grill's deferred property-testing item. PR #8.
- **Manual smoke test** (`scripts/smoke_test.py`, `docs/v1-smoke-test.md`).
  Reads the legacy `ois_calculations.xlsx`, serialises a canonical snapshot
  CSV, runs `rates bootstrap` via the CLI, and inspects the resulting
  summary JSON. Not part of CI; manual sign-off only. First documented
  run: 20 pillars, 0 diagnostics, reprice within `1e-10`. PR #8.

### Added — V2 FX layer (math feature-complete, no IO/CLI yet)

- **`docs/fx-architecture.md`** — full architecture document for the new
  `rates.fx` package. Mirrors `docs/architecture.md` style. PR #9.
- **`config/conventions.yaml`** extended with USD/EUR OIS rows (consumed
  unchanged by `bootstrap_curve`) and a new `fx_conventions` block per pair
  (USDTRY, EURTRY) covering quote convention, spot lag, joint settlement
  calendars, and forward-point scale. PR #9.
- **`rates.fx.types`** (M-101) — `CurrencyPair`, `FXSpotQuote`,
  `FXForwardPointQuote`, `FXSwapQuote`, `CrossCurrencyBasisQuote`,
  `FXMarketData`, `FXSwapPricing`, `QuoteConvention`, and the `FX_*`
  diagnostic code constants. PR #9.
- **`rates.fx.conventions`** (M-102) — `FXConventions.load(yaml_path)` parses
  the `fx_conventions` block; `rates.core.conventions` ignores it,
  preserving the no-cycles layering rule. PR #9.
- **`rates.fx.forward_curve`** (M-103) — `FXForwardCurve` first-class
  dataclass plus log-linear interpolation in `log(F/S)` over calendar days,
  anchored at spot. PRs #9, #10.
- **`rates.fx.basis_curve`** (M-104) — `CrossCurrencyBasisCurve` plus
  piecewise-linear interpolation on basis bps weighted by calendar days,
  anchored at `(spot_date, 0.0)`. PRs #9, #11.
- **`rates.fx.bootstrap_forward`** (M-105) — `build_fx_forward_curve` wraps
  resolved forward-point quotes (mid → avg → ask → bid → skip) into pillars
  at the outright `S + points / scale`. Cross-checks each pillar against
  covered interest parity `F_parity = S * DF_for / DF_dom` via the dual OIS
  curves and emits `FX_PARITY_MISMATCH` WARN when the diff exceeds 1 bp of
  spot. PR #10.
- **`rates.fx.bootstrap_basis`** (M-106) — `build_cross_basis_curve` takes
  quoted xccy basis spreads verbatim, enforces a single `quoted_on_foreign`
  sign convention across the input vector, dedupes maturity collisions
  (keep first), and warns on sign inversions between adjacent pillars.
  Full xccy stripping calibration deferred to V3. PR #12.
- **`rates.fx.pricer`** (M-107) — `price_outright_forward`,
  `price_fx_swap` (returns `FXSwapPricing` with near + far rates and
  `swap_points = far - near`), `price_xccy_basis_swap`. Thin wrappers over
  the curve accessors; signature surface stays stable for V3 to swap in a
  full par xccy swap calibration. PR #13.

### Added — Tests / tooling

- New `phase6` (Hypothesis properties) and `phase7` (V2 FX) pytest
  markers; `hypothesis` and `openpyxl` added under `[project.optional-dependencies] dev`.
- 67 new tests on top of v0.1.0's 172: **238 passed + 1 skipped** at this
  release (vs. 172 / 0 at v0.1.0). The single skip awaits
  `rates.io.fx_market`.

### Not in this release

- **FX layer IO / CLI / app wiring.** The FX math layer is feature-complete
  but consumable only as a library; no `rates.io.fx_market` CSV adapter,
  no `rates.app` FX subcommand orchestration, no `rates.cli` entry point.
  These land in the next release cycle.
- **Full V3 xccy stripping calibration.** Current `build_cross_basis_curve`
  and `price_xccy_basis_swap` hold curves verbatim and pass the seam through.
- **Snapshot freshness check.** V1.5 grill follow-up item; not done this cycle.
- **Multi-scenario MPC paths (hawkish/base/dovish).** Still deferred.

### Validation gates at release cut

- `mypy --strict src/rates` — clean (27 source files).
- `ruff check src tests scripts` — clean.
- `pytest -q` — 238 passed, 1 skipped.
- Manual smoke (`python scripts/smoke_test.py`) — exit 0, no diagnostics,
  all V1 pillars reprice within `1e-10`.

## [0.1.0] — 2026-05-08

Initial production-ready release. V1 OIS engine: bootstrap, curve
interpolation, forward ladder, MPC-path scenario, market-vs-scenario
report, CSV / Pydantic IO, partitioned Parquet persistence, and the
`rates` CLI. Single-currency (TRY), no Hypothesis, no FX. See
`rates_engine_requirements.md` Revision 2 and `docs/architecture.md` for
the validated design that landed in this release.

[0.2.0]: https://github.com/etepe/turbo_rates/releases/tag/v0.2.0
[0.1.0]: https://github.com/etepe/turbo_rates/releases/tag/v0.1.0
