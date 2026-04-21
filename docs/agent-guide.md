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

Tests: `make test` or `.venv/bin/pytest`. Use `make test TESTS='tests/test_file.py'`
for targeted runs through the repository entrypoint. Requires Python 3.12+. SQLite metadata,
DuckDB/Trino engines. Tests pass explicit db_path and metadata store/file paths directly.
App startup requires `factum.yaml` metadata config with `metadata.engine=sqlite` and
`metadata.path=<sqlite-file>`.
- Prefer `tests/shared_fixtures.py` named DuckDB templates for repeated test data. When multiple
  test classes need the same seeded analytics tables, build them once as a deterministic named
  template and copy that template into each temporary db path instead of re-seeding in every
  `setUpClass`.
- Bump a named template's version string when its seeded schema or rows change so cached `/tmp`
  copies rebuild automatically.
- Fresh SQLite metadata stores are initialized from the cached empty schema template in
  `tests/shared_fixtures.py`; existing metadata files still run the real initializer so migration
  tests keep covering schema upgrades. Bump the metadata template version when metadata DDL changes.
- For heavy intent API tests, prefer class-level reuse of published semantic objects and seeded
  upstream artifacts over creating and publishing new metrics/bindings inside individual test
  methods. When compare/correlate-style tests only need committed upstream artifacts, seed the
  minimal artifact payloads directly instead of executing repeated observe setup queries.
- When an intent API test file covers multiple semantic scenarios, split them into scenario-specific
  test classes so each `setUpClass` only creates the metrics, dimensions, bindings, and seeded
  upstream tables required by that group.
- For repeated intent bridge/import tables, add them to a named DuckDB template instead of creating
  and repopulating the table inside each test class setup.
- Do not add unit tests whose individual execution time exceeds 10 seconds. If a test requires
  heavier setup, refactor it to use shared fixtures, named DuckDB templates, or class-level
  `setUpClass` seeding so the per-test runtime stays under the limit.

## Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/`: intents/evidence engine related schema.
- `docs/semantic`: entity/dimension/metric/process related schema.
- `docs/service/`: service runtime and operator design notes; agent local/remote target resolution lives in `agent-runtime-target-resolution.md`, and source/execution/mapping target-state modeling lives in `source-execution-mapping-contract.md`
- `factum-mcp/README.md` and `factum_mcp.inventory`: factum-mcp runtime scope, validation, and executable MCP surface inventory. Keep MCP implementation details there instead of expanding this guide.
- When canonical intent request models change, treat `factum-mcp` typed intent tool schemas as affected because the adapter reuses those request models directly. All typed intent MCP tools expose an explicit top-level `session_id` for the HTTP path, and the remaining parameters should map directly to canonical HTTP body fields instead of MCP-only wrapper objects or JSON-encoded strings.
- Typed intent `metric` parameters use canonical semantic refs such as `metric.watch_time`; do not pass bare metric names like `watch_time`.
- Agent flows that create analysis sessions should explicitly terminate them via `POST /sessions/{session_id}/terminate` when investigation writes are complete; leaving sessions open is not the intended steady state.
- Calendar alignment policies are discoverable builtin refs. For holiday, weekday, or natural
  alignment requests, use `GET /catalog/search?type=calendar_policy` or `GET /semantic/resolve/{ref}`
  to select fixed refs such as `calendar_policy.holiday_yoy`, `calendar_policy.weekday_yoy`, and
  `calendar_policy.natural_yoy`; do not guess policy refs from prose.
- When an observation freezes `resolved_policy_summary`, preserve any bucket-pairing strictness
  metadata such as fallback use, reused baseline buckets, and `rollup_safe`; do not present
  holiday/weekday alignment as strict 1:1 pairing when the frozen metadata says otherwise.
- For `observe(time_series)`, if the returned series backfills requested buckets with `value=null`, surface that as a first-class quality signal (`analytical_metadata.data_complete=false`, `quality_status=needs_attention`) instead of only burying it in coverage summary metadata.
- Do not update this document with implementation details; keep it focused on shared agent guidance and repository-wide boundaries.
