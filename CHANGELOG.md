# Changelog

All notable changes to this project are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[SemVer](https://semver.org/). The `develop` branch carries unreleased work;
`main` carries tagged releases.

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
