# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.

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

UI/Admin notes:
- `/admin` remains a single-page shell in `app/static/admin.html`.
- `app/static/admin.html` is the shell entrypoint; admin-specific behavior is split across `/static/admin/*.js`, `/static/admin/semantic-catalog/*.js`, and `/static/admin.css`.
- `Semantic Catalog` routing uses `tab=semantic-catalog`, `subtab=<object-kind>`, and `object_id=<catalog-id>` as the canonical locator; `binding_id` is only for execution-engine bindings and legacy typed-binding deep-link compatibility.
- `Semantic Catalog` T7 now ships object-specific contract pages for all eight subtabs: `Entities`, `Metrics`, `Process Objects`, `Dimensions`, `Time`, `Enum Sets`, `Typed Bindings`, and `Compatibility Profiles`. Keep summary cards, mixed create/edit forms, relation jumps, publish freeze states, typed binding grounding cues, enum/time guidance, and on-demand planner-context helper wiring on typed HTTP contracts only; do not reintroduce legacy mappings or runtime result panels into contract views.
- `Analysis Ops` T8 now ships real session inventory/detail rendering for `tab=analysis-ops` with `session_id` route recovery, `Terminate Session` as the only write action, and `/ui` deep links for Sessions, State, Runtime, and Jobs. Do not add create-session, intent, step, or plan-management controls to `/admin`.
- `Runtime & Jobs` T9 now ships real operator-facing rendering for `tab=runtime-jobs` across `session-runtime`, `proposition-runtime`, `artifact-runtime`, and `jobs`, with URL recovery for `session_id`, `proposition_id`, `artifact_id`, and `job_id`. Keep the page read-only, keep reminding users this is runtime truth rather than canonical result, and do not add job submit/cancel, retry/replay, or publish controls.
- `Governance` T10 now ships real `Policies`, `Quality Rules`, `Approvals`, and `Governance Helpers` subtabs under `tab=governance`, with object locators `policy_id`, `rule_id`, and `request_id` plus `session_id` for approvals filters and helper inputs. Keep `Governance Helpers` diagnostic-only, keep the global pending approvals badge on the governance nav item, and keep `/admin` in sync with the current HTTP contract: it does not fake unsupported policy or quality-rule edit capabilities beyond `policy` enable/disable + definition update and `quality rule` create/delete only.
- `Observability` T11 now ships a real read-only `tab=observability` page backed by `GET /health`, `GET /metrics`, and `GET /metrics?format=prometheus`, with auto-refresh, health/metric summary cards, and JSON/raw text detail panes. Keep it strictly read-only and do not expand it into an incident console or runtime control surface.
