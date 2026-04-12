# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

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
Bindings must use the synced source object's full `source_objects.fqn` as `carrier_locator`;
routing and execution may accept short table names, but they must normalize them against synced
`source_objects` and prefer full-FQN matches.
Semantic object HTTP responses may expose derived lifecycle/readiness contract fields in addition to
legacy storage `status`; preserve backward compatibility unless a task explicitly removes old fields.
Lifecycle actions should use `validate`, `activate`, and `deprecate` as the primary public verbs;
`publish` is a compatibility alias for `activate`, and `activate` must never be presented as a
proxy for readiness. For semantic list filters, prefer `lifecycle_status` and
`readiness_status`; `status` remains a storage compatibility filter only. `status=active` is a
read-time compatibility alias for storage `published`; new work should still prefer
`lifecycle_status=active`.
Direct dependency information may also be exposed via `dependency_refs` on semantic object read
surfaces when a task needs catalog/debug visibility.
Runtime/catalog defaults should treat readiness as the availability gate. For entity/metric/process,
do not assume `published` implies `ready`; readiness may be blocked by dependencies, bindings, or
profile mismatches.
Treat `stale` as a proven dependency-drift state, not a generic synonym for `not_ready`; if the
current metadata cannot show that an object was previously aligned and then drifted, prefer
`not_ready`.
Request-level incompatibility must stay separate from object readiness. Do not write request-specific
dimension/process/intent mismatches back into semantic object readiness fields.
Compiler and intent execution entrypoints should enforce the same object-level readiness gate and
surface structured readiness failures instead of collapsing them into generic compile errors.
Intent metric preflight must resolve execution bindings from the same semantic runtime inspection
used for readiness; do not reintroduce separate published/table-mapping checks that can disagree
with `readiness_status`.
List endpoints return lightweight items by default (header, status, blocker_count, capabilities_summary);
use `detail=true` query parameter for backward-compatible full payload. Lightweight list responses
must still derive readiness from the same full semantic contract used by detail reads. Detail
endpoints return full objects including `dependency_refs` and stubbed `dependent_refs` (empty
list, deferred implementation).
Admin semantic catalog views should treat `lifecycle_status` and `readiness_status` as the primary
operator-facing state, with blockers shown before helper/debug actions so `published` is never
presented as a proxy for usability.
User-facing `/ui` grounding/discovery surfaces should default to ready semantic objects and only
surface non-ready objects when the caller explicitly opts into blocker inspection.

## Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/`: intents/evidence engine related schema.
- `docs/semantic`: entity/dimension/metric/process related schema.
- `factum-mcp/README.md` and `factum_mcp.inventory`: factum-mcp runtime scope, validation, and executable MCP surface inventory. Keep MCP implementation details there instead of expanding this guide.
- When canonical intent request models change, treat `factum-mcp` typed intent tool schemas as affected because the adapter reuses those request models directly.
- Do not update this document with implementation details; keep it focused on shared agent guidance and repository-wide boundaries.
