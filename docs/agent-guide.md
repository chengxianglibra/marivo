# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.
- Target-state external step submission contract lives in `docs/api/intent-steps.md`; `docs/analysis/intents/` remains the design source, not the wire spec.
- analysis refactor design docs is located at 'docs/analysis'
- Canonical read surfaces expose externally visible state only; do not mix runtime queue/claim/retry status into `session` / `state` / `context`.
- Evidence Engine runtime lifecycle, runtime status surface, and migration/invalidation policies live under `docs/analysis/evidence-engine/`.

## Python / Typing

- Never use bare `python`, `pytest`, `mypy`, or `ruff` in this repository. Use `make` targets or
  explicit `.venv/bin/...` paths only.
- Repository guard scripts accept both `.venv` and shells that expose the repository root via
  `VIRTUAL_ENV`, but the invoked tool must still come from `.venv/bin/...`.
- All new or modified Python code must satisfy `mypy` for the touched modules.
- Add explicit type annotations for public functions, dataclass/model fields, and non-trivial locals when needed for `mypy` clarity.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore` unless strictly necessary.
- If `# type: ignore` is unavoidable, keep it narrow and add a short reason.
- When changing schemas, API models, or service contracts, update type annotations end-to-end in the same change.
- Before finishing a Python change, run the repository `mypy` check for the touched paths via
  `make typecheck` or `.venv/bin/mypy`, or explain why it could not be run.

## Code Style (Ruff)

Use `make lint` and `make format` or the explicit `.venv/bin/ruff` wrapper paths; never call bare
`ruff`. `ruff --fix` and `ruff format` run as pre-commit hooks. All generated code must pass them
without requiring a fix cycle. Enabled rule families: `E/W` (pycodestyle), `F` (pyflakes), `I`
(isort), `N` (pep8-naming), `UP` (pyupgrade), `B` (bugbear), `C4` (comprehensions), `SIM`
(simplify), `TCH` (type-checking imports), `RUF` (ruff-specific).

**Non-obvious gotchas to avoid:**

- **RUF046** — `round()` with no `ndigits` already returns `int`; never wrap it:
  - Wrong: `int(round(x))` / `int(round(float(x)))`
  - Right: `round(x)` / `round(float(x))`
- **N806** — Local variables inside functions must be lowercase (including pseudo-constants):
  - Wrong: `_MAXIT = 200` / `_EPS = 3e-7` inside a `def`
  - Right: `_maxit = 200` / `_eps = 3e-7` (module-level constants may stay UPPER)
- **N802** — Function names must be lowercase (`def myFunc` → `def my_func`); exempt in tests.
- **UP** — Use modern Python 3.10+ syntax: `X | Y` unions instead of `Optional[X]`, `list[x]`
  instead of `List[x]`, etc.
- **B** — Avoid mutable default arguments, use `assert` only in tests, no bare `except`.
- **SIM** — Prefer ternary / `any()` / `all()` over equivalent `if` chains where natural.
- **I** — Imports must be isort-sorted: stdlib → third-party → first-party (`app`).

Line length is 100 (formatter handles wrapping; no need to manually break lines).
`app/api/**/*.py` ignores `B008` (FastAPI `Depends` calls in defaults are fine).

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && uvicorn app.main:app --reload
```

Preferred repository entrypoints:

```bash
make test
make typecheck
make lint
make format
```

Tests: `make test` or `.venv/bin/pytest`. Requires Python 3.12+, `DUCKDB_MVP_DB`. SQLite metadata,
DuckDB/Trino engines.

## Architecture

Client → FastAPI → `app/api/` → service → semantic/routing/execution → SQLite + engines.
Metadata reads use synced `source_objects`, not live catalogs.
Semantic metadata writes go through `app/semantic.py` as a facade over `app/semantic_service/`; keep route-level dependencies on the facade contract unless a task explicitly changes the app wiring.
Typed semantic objects are only mutable in `draft`; service-layer publish must reject objects whose referenced semantic dependencies are not already `published`.

Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/`: intents/evidence engine related schema.
- `docs/semantic`: entity/dimension/metric/process related schema.
