# Agent Guide

Shared coding, testing, and documentation guidance for agents working in this
repository. Keep this file focused on stable rules that should be loaded for
every coding task.

## Core Rules

- Think before coding: state assumptions, surface tradeoffs, and ask only when
  ambiguity would make the change risky.
- Prefer the minimum code that solves the requested problem; do not add
  speculative flexibility, future placeholders, or unrelated abstractions.
- Make surgical changes: touch only the files required, match existing style,
  and never clean up unrelated local changes.
- Define verifiable success criteria for non-trivial work and loop until the
  relevant checks pass or explain why they could not run.
- Treat committed specs, schemas, and docs as sources of truth. If code and
  docs disagree, verify the intended current contract before changing behavior.

## Python And Typing

- Never use bare `python`, `pytest`, `mypy`, or `ruff` in this repository.
- Use repository entrypoints or explicit `.venv/bin/...` paths only. For
  targeted Python tests, prefer `make test TESTS='tests/test_file.py'` or
  `.venv/bin/pytest tests/test_file.py`.
- New or modified Python code must satisfy typing for the touched modules.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore`
  unless it is strictly necessary and locally justified.
- MCP DTOs must not expose ambiguous structures such as bare `dict`, `object`,
  or untyped mappings; define explicit typed DTO models instead.
- When changing schemas, API models, or service contracts, update type
  annotations end-to-end in the same change.

## Tests

- Use shared seeded templates and session-scoped fixtures for repeated setup;
  see [`.agents/skills/marivo-test-fixtures/SKILL.md`](.agents/skills/marivo-test-fixtures/SKILL.md).
- Keep tests aligned to the current contract, not legacy compatibility shapes.
- After metadata schema changes, refresh fresh-init assumptions and update the
  metadata template/version checks where relevant.

## Contracts And Generated Code

- Canonical contract schemas live in
  `osi-marivo-spec/schema/osi-marivo.schema.json` and
  `aoi-spec/schema/aoi.schema.json`.
- OSI/AOI spec-generated Pydantic models are an architecture constraint layer,
  not optional validation helpers. Contract-facing runtime, HTTP, MCP, adapter,
  and test code must use or conform to `marivo/contracts/generated/` for OSI/AOI
  payload shapes instead of defining parallel permissive models or accepting
  untyped `dict`/`Any` structures.
- Do not bypass generated model constraints by allowing extra fields, silently
  rewriting invalid payloads outside the spec, duplicating generated classes by
  hand, or adding compatibility shims that accept shapes rejected by the current
  OSI/AOI schemas. If the shape should be valid, update the spec first and
  regenerate the models.
- Static Python contract models under `marivo/contracts/generated/` are
  generated output. Do not edit them manually; update the relevant spec schema
  and regenerate with `.venv/bin/python scripts/generate_contract_models.py`.
- `scripts/generate_contract_models.py` must stay strict: do not relax
  generated validation or add permissive workarounds such as broad extra-field
  allowance.
- Frontend OpenAPI types are generated at `frontend/src/api/openapi.generated.ts`.
  Regenerate them with the existing `frontend` script when HTTP API contracts
  change.

## Marivo Python Track (`semantic_py` / `analysis_py`)

These rules apply to all changes inside `marivo/semantic_py/` and
`marivo/analysis_py/`, and to the skills under `marivo-skill/marivo-py-*/`.

- **Dual-track isolation.** `marivo.semantic_py` and `marivo.analysis_py`
  must not import from `marivo.runtime.*`, `marivo.adapters.*`,
  `marivo.contracts.aoi_runtime`, `marivo.contracts.generated.aoi`,
  `marivo.core.evidence`, `marivo.core.session`, or `marivo.core.intent`.
  The inverse is also forbidden. The boundary is enforced by the
  `analysis_py-independence` and `runtime-does-not-depend-on-analysis_py`
  contracts in `.importlinter`; do not weaken or bypass them.
- **One expression language.** Python-track expressions (dataset / field /
  time_field / metric function bodies) return ibis expressions only.
  Do not introduce raw SQL strings, dialect-specific SQL, or multi-dialect
  expression structures into the Python track. SQL text only lives in the
  `Provenance.source_sql` metadata field.
- **Decorator function-body AST stays restricted.** `@ms.field`,
  `@ms.time_field`, `@ms.metric`, `@ms.dataset`, and `@ms.datasource` bodies
  follow the AST whitelist enforced in `marivo/semantic_py/validator.py`
  (single-return style; no imports, control flow, local assignment, or
  lambdas inside expression-bearing decorators). New decorators or changes
  to existing ones must continue to pass through that whitelist.
- **Provenance is a required field on expression-bearing decorators.**
  `@ms.dataset`, `@ms.field`, `@ms.time_field`, and `@ms.metric` accept and
  persist `source_sql` and `source_definition` into `Provenance` on the IR;
  the `parity_status` field stays reserved for the v1.5+ parity engine.
  Do not drop these kwargs or invent parallel provenance shapes.
- **Structured exceptions over ad-hoc strings.** New exceptions in the
  Python track subclass `SemanticError` or `AnalysisError`, carry
  `kind` / `message` / `hint` / `details`, and render through the shared
  `__str__` template via `_template_fields()`. `hint` should provide a
  minimal pasteable correct snippet when feasible. Do not bypass the
  template with bespoke `f"..."` error strings.
- **Frame immutability is a public contract.** Top-level Frame APIs raise
  `FrameMutationError`; only `frame.to_pandas()` returns an isolated copy;
  read-through accessors return pandas views without defensive copies.
  Do not add mutating methods, syntactic sugar that aliases mutation, or
  hidden copy-on-read behavior to Frame classes.
- **Credentials and connections never persist.** `@ms.datasource` function
  bodies read credentials from `os.environ` / project config helpers; the
  `backend_factory` / `backends` passed to a session is never written to
  `meta.json`, `index.db`, or the IR. Generated SQL is exposed in error
  `details` only when `MARIVO_ANALYSIS_DEBUG=1`.
- **Persistent state is project-local under `.marivo/`.** Sessions, frames,
  job records, and Python semantic models all live under
  `<project_root>/.marivo/{analysis,semantic}/`. Do not introduce a global
  session registry, env-var session fallback, or cross-project state.
- **Cross-session frame ownership is mandatory.** Any intent or helper that
  consumes a frame validates `frame.meta.session_id == session.id` before
  doing work, and raises `CrossSessionFrameError` on mismatch. Do not relax
  this check for convenience.
- **No new transports in regular PRs.** The Python track is exposed only via
  `import marivo.{semantic_py,analysis_py}`. MCP / HTTP / CLI adapters for
  this track require a dedicated transport design; do not add them
  incidentally.
- **Skill examples are an executable SDK contract.** Changes to public
  symbols in `marivo.semantic_py` / `marivo.analysis_py` (signatures,
  `Literal[...]` values, exception classes, `__str__` templates, Frame
  `__repr__`) must update the corresponding files under
  `marivo-skill/marivo-py-{semantic,analysis}/references/examples/` in the
  same change. `make examples-check` is the gate (already wired into
  `make check`). Each `marivo-py-*/SKILL.md` stays within the 600-line cap.

## Repository Entrypoints

Prefer these repository entrypoints:

```bash
make test
make typecheck
make lint
make format
```

- For frontend work, run commands from `frontend/` and use the existing
  `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`, and
  `npm run test:browser` scripts when relevant.
- Tests and shared fixture details live in
  [`.agents/skills/marivo-test-fixtures/SKILL.md`](.agents/skills/marivo-test-fixtures/SKILL.md).
- Claude review instructions live in
  [`.agents/skills/claude-review/SKILL.md`](.agents/skills/claude-review/SKILL.md).
- **Mandatory:** When creating a git commit, always invoke the
  `commit-attribution` skill first. Follow its pre-commit scope check and
  attribution rules on every commit — no exceptions. The skill lives in
  [`.agents/skills/commit-attribution/SKILL.md`](.agents/skills/commit-attribution/SKILL.md).

## Documentation Routing

When working on a task, read the right docs first:

| Task Type | Read First | Then |
|-----------|-----------|------|
| Analysis engine / evidence / intents | `docs/specs/analysis/README.md` | Subtopic files below |
| Semantic layer / objects / compiler | `docs/specs/semantic/overview.md` | Schema contract files below |
| Service runtime / agent runtime / data plane | `docs/specs/service/README.md` | Subtopic files below |
| HTTP API endpoint | `docs/api/README.md` | Endpoint-specific doc |
| Frontend UI | `docs/ui/frontend-design.zh.md` | `frontend/README.md` |
| Product background / motivation | `docs/marivo-proposal.md` | `docs/marivo-for-builders.zh.md` |
| Global doc index | `docs/README.md` | — |

## Documentation Updates

- After behavior changes, update affected API, UI, user, or spec docs in the
  same change.
- Update this guide only for stable repository-wide coding and testing rules.
- Do not add product usage guidance, API walkthroughs, semantic modeling
  recipes, runtime operation details, MCP/client instructions, migration
  history, active development plans, or one-off workaround notes here.
- Put task-specific procedures in project-local skills, README files, or the
  relevant domain documentation.
