# Rates Engine

Turkish Lira OIS curve engine: bootstrap from market quotes, build scenario index from a
CBRT MPC policy path, and produce a market-vs-scenario comparison.

V1 is a Python library + thin CLI. Excel dependency is severed: no runtime Excel reading,
no VBA, no workbook bridge.

## Status

Phase 1 of 5 — Foundation scaffold. Modules `rates.core.{conventions,calendar,daycount,log,diagnostics,types}` exist as **typed stubs**. Real implementation begins on this branch.

See [`docs/architecture.md`](docs/architecture.md) for the full design and
[`rates_engine_requirements.md`](rates_engine_requirements.md) for the validated requirements.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run tests (Phase 1)

```bash
pytest tests/foundation -v
```

Most tests are currently `pytest.skip("not yet implemented")` placeholders — they become
real assertions as Phase 1 modules are implemented.

## Type-check & lint

```bash
mypy           # strict on rates.core and rates.io only
ruff check .   # lint
ruff format .  # format
```

## CLI (Phase 5 — not yet wired)

The `rates` entry point is declared in `pyproject.toml` but its implementation lives in
`rates.cli` which arrives in Phase 5. Running `rates` before then will fail.

## Project layout

```
rates_engine/
├── pyproject.toml            # build, deps, mypy/ruff/pytest config
├── requirements*.txt
├── docs/                     # validated requirements + architecture
├── config/
│   ├── conventions.yaml      # day-count, payment, business-day conventions per ccy
│   ├── mpc_path.csv          # CBRT MPC schedule (user-editable)
│   └── holidays/{tr,us,eu}.csv
├── data/                     # (gitignored) snapshots, latest summary, parquet archive
├── scripts/                  # one-off utilities (e.g. holiday fidelity check)
├── src/rates/
│   ├── core/                 # math + foundation (Phase 1-3)
│   ├── io/                   # providers, schemas, persistence (Phase 4)
│   ├── app.py                # pipeline orchestrator (Phase 5)
│   └── cli.py                # argparse entry (Phase 5)
└── tests/
    └── foundation/           # Phase 1 tests
```

## Development workflow (per `CLAUDE.md`)

- Never commit directly to `main`.
- Feature branches: `feature/MON-{ticket}-{short-desc}` from `develop`.
- Open a PR for review; do not merge without explicit approval.
