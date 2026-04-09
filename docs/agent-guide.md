# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.
- `/ui` is a read-only query and troubleshooting workbench; do not add session, intent, plan, step, or job control entrypoints there.
- `/ui` session navigation is URL-driven (`tab`, `session_id`, `proposition_id`, `artifact_id`, `runtime_scope`, `status`, `session_query`); keep cross-page drill-ins compatible with that contract instead of adding ad-hoc client-side state.
- `/ui` cross-page drill-ins should reuse shared route helpers and standardized copy: `404 session not found` returns to `Sessions`, `404 proposition not found` returns to `State`, and runtime lookup failures stay on `Runtime` so the canonical chain remains available.
- `/ui` canonical drill-ins should route through `Sessions -> State -> Context`; when `latest_assessment = null`, send users to Runtime for operator-facing cause details instead of inferring them on the canonical pages.
- Target-state external step submission contract lives in `docs/api/intent-steps.md`; `docs/analysis/intents/` remains the design source, not the wire spec.
- analysis refactor design docs is located at 'docs/analysis'
- Canonical read surfaces expose externally visible state only; do not mix runtime queue/claim/retry status into `session` / `state` / `context`.
- Canonical read surfaces (`session` / `state` / `context`) must carry canonical refs and provenance handles only; semantic refs belong to semantic/runtime/compiler contracts and must not leak into evidence read payloads.
- When evidence consumers need semantic meaning from a `step_ref`, recover it via typed step metadata / compiler snapshots behind the scenes; do not add semantic refs to canonical read payloads.
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
Typed binding publish must also reject carriers that cannot be grounded to synced `source_objects`; draft bindings may keep unresolved `carrier_locator` values until publish.
Compatibility profiles remain explicit catalog artifacts: object publish does not auto-generate profile payloads, profile publish must reject subjects that are not already `published`, and published profiles freeze `subject_revision` so compiler can reject stale profile/subject pairings.
Runtime/catalog read surfaces must only expose objects backed by `published` typed contracts; do not re-derive runtime-visible contracts from legacy rows when the typed contract is absent or not published.
Legacy `/semantic/mappings` HTTP APIs are removed; new write paths must use typed bindings, and any legacy mapping compatibility must stay internal or test-only.
Compiler normalize/resolve should consume published typed refs through `SemanticRuntimeRepository`; compatibility bridges may keep execution running when promoted legacy dimension names do not yet resolve to published typed dimension contracts, but that fallback must stay explicit and local to compiler preprocessing.
Recommended implementation order is fixed: typed semantic objects and bindings first, then publish/runtime resolution, then compiler/IR, then evidence/read-surface integration.
Behavior changes that touch semantic/compiler boundaries must keep at least one end-to-end test covering `published` typed objects -> typed binding -> compile metadata -> persisted `typed_semantic_snapshot`.

Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/`: intents/evidence engine related schema.
- `docs/semantic`: entity/dimension/metric/process related schema.
