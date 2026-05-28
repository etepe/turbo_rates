# FX Module — Architecture Document (V2 scaffold)

**Based on:** `rates_engine_requirements.md` §6 (deferred to V2 — FX
forward/swap fair pricing, cross-currency basis) and `docs/architecture.md`
(V1 hexagonal layering and contract style).
**Date:** 2026-05-28
**Status:** Scaffold-stage validated. Implementation deferred to V2.

This document mirrors `docs/architecture.md` for the new `rates.fx` package.
It freezes module boundaries, contracts, and diagnostic codes so the typed
stubs in `src/rates/fx/` are traceable to documented decisions before any
math is implemented.

---

## 1. Scope

| Item | In | Notes |
|---|---|---|
| USDTRY / EURTRY outright forward fair-price | ✅ | Covered interest parity over dual OIS curves |
| FX swap (near + far leg, par-par) | ✅ | Two settlement legs, common notional |
| Cross-currency basis swap fair-spread | ✅ | xccy basis stripped from market xccy quotes vs implied parity |
| Foreign-currency OIS curve (USD_ois, EUR_ois) | ✅ (re-uses M-007) | `bootstrap_curve` consumed twice; conventions extended |
| FX options (vanilla, smile, vol surface) | ❌ | Out of V2 scope |
| TRY offshore (NDF-like) | ❌ | Out of V2 scope |
| Real-time spot streaming | ❌ | V3 |

The FX layer **consumes** existing OIS infrastructure unchanged. No edits to
`rates.core` are required beyond adding USD/EUR convention rows.

---

## 2. Architectural Drivers (delta from V1)

| # | Driver | FX-specific consequence |
|---|---|---|
| D1 (change driver) | FX market data provider must sit behind a Protocol parallel to V1's `MarketDataProvider` — V2 Bloomberg adapter and CSV adapter must be interchangeable. |
| D2 (risk driver) | Covered interest parity is the central numerical identity; deviations must be reported as `FX_PARITY_MISMATCH` WARN. Reprice tolerance for FX forwards: **1e-9 absolute on the forward rate** (looser than OIS 1e-10 because spot quotes carry larger precision noise). |
| D3 (complexity) | `opus` for `bootstrap_forward`, `bootstrap_basis`, `pricer`. `sonnet` for `forward_curve`, `basis_curve`, `conventions`, `types`. |
| D6 (extensibility) | `FXForwardCurve` and `CrossCurrencyBasisCurve` are first-class dataclasses. Internally each consumes one or more `OISCurve` instances, but the consumer API never exposes them — the caller asks `curve.forward_at(settle_date)` regardless of the underlying decomposition. |
| D7 (observability) | All FX diagnostics flow through the same `DiagnosticsCollector` instance threaded by `rates.app`. Codes are prefixed `FX_` to disambiguate from `BS_` and `SC_`. |

---

## 3. Module Decomposition

| ID | Module | Responsibility | Complexity | Agent | Features |
|---|---|---|---|---|---|
| M-101 | `rates.fx.types` | Frozen FX domain dataclasses: `CurrencyPair`, `FXSpotQuote`, `FXForwardPointQuote`, `FXSwapQuote`, `CrossCurrencyBasisQuote`, `FXMarketData`; diagnostic code constants. | low | sonnet | F-101 |
| M-102 | `rates.fx.conventions` | `fx_conventions` YAML block per pair: quote convention (direct vs indirect), spot lag (T+1 / T+2), domestic+foreign calendar codes, day-count for forward-point accrual. | low | sonnet | F-101 |
| M-103 | `rates.fx.forward_curve` | `FXForwardCurve` dataclass: pillar dates + outright forwards; `forward_at(date)` log-linear interp on `log(F/S)`. Anchors at spot date with `forward_at(spot_date) == spot_rate`. | medium | sonnet | F-102 |
| M-104 | `rates.fx.basis_curve` | `CrossCurrencyBasisCurve` dataclass: tenor-keyed basis spreads (bps); `basis_at(date)` linear interp; anchored at spot with zero basis if not bootstrapped. | medium | sonnet | F-104 |
| M-105 | `rates.fx.bootstrap_forward` | Bootstrap `FXForwardCurve` from spot + forward-point quotes + dual OIS curves. Each pillar: `F = S * DF_for / DF_dom` (covered parity). Reprice tolerance 1e-9. | **high** | opus | F-102 |
| M-106 | `rates.fx.bootstrap_basis` | Bootstrap `CrossCurrencyBasisCurve` from xccy par-swap quotes (USDTRY xccy, EURTRY xccy) using dual OIS curves and FX forward curve as anchors. | **high** | opus | F-104 |
| M-107 | `rates.fx.pricer` | Pure pricing functions: `price_outright_forward`, `price_fx_swap`, `price_xccy_basis_swap`. Each returns fair rate/spread plus optional risk decomposition (DV01 hook deferred). | medium | opus | F-103, F-104 |

**Total: 7 new modules** in `src/rates/fx/`.

### Feature IDs (parallel to V1)

| ID | Name | Priority |
|---|---|---|
| F-101 | FX market data ingestion (spot, forward points, xccy par-swap quotes) | must-have |
| F-102 | FX forward curve bootstrap (covered interest parity) | must-have |
| F-103 | FX forward / swap fair pricing | must-have |
| F-104 | Cross-currency basis curve bootstrap | must-have |
| F-105 | FX diagnostics (parity mismatch, non-monotone forward points, etc.) | must-have |
| F-106 | Multi-currency conventions extension (`fx_conventions` YAML block) | must-have |

---

## 4. Layering & Dependency Direction

```
                  ┌─────────────────────────┐
                  │ M-015  rates.cli        │  (V1; FX subcommands added in V2)
                  └────────────┬────────────┘
                               ▼
                  ┌─────────────────────────┐
                  │ M-014  rates.app        │  (V1; FX pipeline added in V2)
                  └────┬───────────────┬────┘
                       │               │
          ┌────────────┘               └───────────────┐
          ▼                                            ▼
┌─────────────────────────┐               ┌──────────────────────────┐
│  rates.fx.*  (M-101..7) │  ◄── uses ── │  rates.core.*  (V1)       │
└───────────┬─────────────┘               │  (OISCurve, Conventions,  │
            │                              │   diagnostics, daycount)  │
            ▼                              └──────────────────────────┘
┌──────────────────────────┐
│  rates.io.fx_market      │  (V2 — parallel to rates.io.market)
└──────────────────────────┘
```

**No cycles.** `rates.fx` imports from `rates.core` but never the reverse.
`rates.core` is unaware FX exists. `rates.app` orchestrates both layers.

---

## 5. Contract Catalogue

Contract IDs continue from V1 (which ended at C-013).

| Contract | From | To | Interface | Error cases |
|---|---|---|---|---|
| **C-101** `FXMarketDataProvider` | `rates.app` | `rates.io.fx_market` | `load(valuation_date: date \| None) -> FXMarketData` | FileNotFoundError, FXSchemaError, EmptyDataError |
| **C-102** `build_fx_forward_curve` | `rates.app` | `M-105` | `(market: FXMarketData, dom_ois: OISCurve, for_ois: OISCurve, conv: FXConvention, dg: DiagnosticsCollector) -> FXForwardCurve` | FXSpotMissingError, FXBootstrapError |
| **C-103** `FXForwardCurve` accessors | `rates.app`, `rates.fx.pricer` | `M-103` | `forward_at(settle_date) -> float`, `pillars() -> DataFrame`, `to_summary_dict() -> dict` | DateOutOfRange |
| **C-104** `build_cross_basis_curve` | `rates.app` | `M-106` | `(market, dom_ois, for_ois, fx_fwd: FXForwardCurve, conv, dg) -> CrossCurrencyBasisCurve` | XccyBootstrapError |
| **C-105** `CrossCurrencyBasisCurve` accessors | `rates.app`, `rates.fx.pricer` | `M-104` | `basis_at(date) -> float`, `pillars() -> DataFrame` | DateOutOfRange |
| **C-106** `price_outright_forward` | `rates.app`, `rates.cli` | `M-107` | `(fx_fwd: FXForwardCurve, settle_date: date) -> float` | DateOutOfRange |
| **C-107** `price_fx_swap` | `rates.app`, `rates.cli` | `M-107` | `(fx_fwd: FXForwardCurve, near: date, far: date) -> FXSwapPricing` | DateOutOfRange |
| **C-108** `price_xccy_basis_swap` | `rates.app`, `rates.cli` | `M-107` | `(basis: CrossCurrencyBasisCurve, dom_ois, for_ois, maturity: date) -> float` | DateOutOfRange |

---

## 6. Diagnostic Codes

| Code | Severity | Trigger |
|---|---|---|
| `FX_SPOT_MISSING` | ERROR | `FXMarketData.spot_rate` absent for the requested pair |
| `FX_FOR_CCY_CURVE_MISSING` | ERROR | Foreign OIS curve not provided to the bootstrap or pricer |
| `FX_FWD_POINTS_NON_MONOTONE` | WARN | Forward-point quotes not strictly ordered by tenor — possible quote error |
| `FX_PARITY_MISMATCH` | WARN | Implied parity forward differs from quoted forward by > 1 bp at any pillar |
| `FX_BASIS_INVERTED` | WARN | Cross-currency basis spread changes sign between adjacent pillars |
| `FX_XCCY_QUOTE_SKIPPED` | WARN | xccy basis quote unusable (missing legs); excluded from bootstrap |
| `FX_BOOTSTRAP_NON_CONVERGENT` | ERROR | Numerical solver did not converge — curve not producible |

---

## 7. Conventions Extension

`config/conventions.yaml` gains:

```yaml
ois_conventions:
  TRY:
    # ... unchanged ...
  USD:
    day_count: Act/360
    business_day_convention: modified_following
    calendar: US
    payment:
      default_frequency: annual
      bullet_until: 1Y
  EUR:
    day_count: Act/360
    business_day_convention: modified_following
    calendar: EU
    payment:
      default_frequency: annual
      bullet_until: 1Y

fx_conventions:
  USDTRY:
    quote_convention: direct          # TRY per 1 USD
    spot_lag_days: 1
    settlement_calendars: [US, TR]    # spot must be a business day in both
    forward_point_scale: 10000        # quoted "pips" = points * 10000
  EURTRY:
    quote_convention: direct
    spot_lag_days: 2
    settlement_calendars: [EU, TR]
    forward_point_scale: 10000
```

Holiday calendars `config/holidays/us.csv` and `config/holidays/eu.csv` are
introduced as placeholder files (header only) for V2. The `holidays` Python
package supplies the baseline.

---

## 8. Build Order

Stub-stage (this PR — V1.5):

1. M-101 `rates.fx.types` — pure dataclasses, no math.
2. M-102 `rates.fx.conventions` — YAML extension reader.
3. M-103 `rates.fx.forward_curve` — `FXForwardCurve` dataclass with stubbed accessors.
4. M-104 `rates.fx.basis_curve` — same shape.
5. M-105 `rates.fx.bootstrap_forward` — function signature + stub body raising `NotImplementedError("V2")`.
6. M-106 `rates.fx.bootstrap_basis` — same.
7. M-107 `rates.fx.pricer` — three function signatures.

V2 implementation order (later PRs, opus-agent):

1. M-105 first (forward curve unlocks pricing).
2. M-107 pricer for outright forward + swap.
3. M-106 xccy basis bootstrap (needs M-105 output).
4. M-107 xccy basis pricer extension.

---

## 9. Open Questions (V2 grill-fodder)

These are explicitly deferred and not resolved by this scaffold:

| # | Question | Default for stub |
|---|---|---|
| FX-O1 | Spot settlement convention when domestic + foreign holiday calendars conflict on a candidate date — both-business-day required, or first/preceding? | Both-business-day (most conservative). Conventions YAML lists both calendars. |
| FX-O2 | xccy basis interpolation scheme: piecewise-linear on basis bps, or linear on basis-implied DF? | Piecewise-linear on basis bps (closer to street convention). |
| FX-O3 | DV01 / risk decomposition: emit alongside fair price, or separate function call? | Separate. V2 deferred. |
| FX-O4 | Whether `FXForwardCurve` should embed the spot rate or accept it externally. | Embed; spot is the natural anchor analogous to `OISCurve.valuation_date`. |
| FX-O5 | xccy quoted basis sign convention — added to USD leg (US convention) or to TRY leg? | USD leg (street convention). Document at the point of use. |

---

## 10. Stub Acceptance Criteria

The scaffold PR is acceptable when:

- `mypy --strict` passes over `src/rates/fx/*`.
- `ruff check src tests scripts` is clean.
- All seven stub modules import without runtime error.
- `tests/fx/` exists with one test file per module; every test is decorated
  `@pytest.mark.skip(reason="V2 — stub only")` and `@pytest.mark.phase7`.
- `pytest -q` reports the same passing count as before (181) plus the new
  skipped tests.
- `docs/fx-architecture.md` (this file) committed alongside.
- No changes to `rates.core` semantics — adding USD/EUR rows to
  `conventions.yaml` is permitted; touching `bootstrap_curve` is not.

Implementation of any FX math is **deferred to a follow-up PR**.
