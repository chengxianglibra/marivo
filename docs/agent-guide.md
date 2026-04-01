# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.
- Target-state external step submission contract lives in `docs/api/intent-steps.md`; `docs/analysis/intents/` remains the design source, not the wire spec.
- analysis refactor design docs is located at 'docs/analysis'

## Python / Typing

- All new or modified Python code must satisfy `mypy` for the touched modules.
- Add explicit type annotations for public functions, dataclass/model fields, and non-trivial locals when needed for `mypy` clarity.
- Do not introduce new implicit `Any`, broad `cast(...)`, or `# type: ignore` unless strictly necessary.
- If `# type: ignore` is unavoidable, keep it narrow and add a short reason.
- When changing schemas, API models, or service contracts, update type annotations end-to-end in the same change.
- Before finishing a Python change, run the repository `mypy` check for the touched paths, or explain why it could not be run.

## Code Style (Ruff)

`ruff --fix` and `ruff-format` run as pre-commit hooks. All generated code must pass them
without requiring a fix cycle. Enabled rule families: `E/W` (pycodestyle), `F` (pyflakes),
`I` (isort), `N` (pep8-naming), `UP` (pyupgrade), `B` (bugbear), `C4` (comprehensions),
`SIM` (simplify), `TCH` (type-checking imports), `RUF` (ruff-specific).

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

Tests: `.venv/bin/pytest`. Requires Python 3.12+, `DUCKDB_MVP_DB`. SQLite metadata, DuckDB/Trino engines.

## Architecture

Client → FastAPI → `app/api/` → service → semantic/routing/execution → SQLite + engines.
Metadata reads use synced `source_objects`, not live catalogs.

## Model

- Physical: synced source objects
- Semantic: entities, metrics, mappings
- Evidence (canonical pipeline): sessions, steps, artifacts, findings, propositions, assessments, evidence_gaps, inference_records, action_proposals
- Evidence (legacy, to be removed in phase 6): observations, claims, evidence_edges, recommendations

Canonical pipeline: `artifact → finding → proposition → assessment → action proposal`. Artifacts are committed step outputs. Findings are deterministically extracted atomic fact units (`findings` table). Propositions are judgment-layer objects seeded from findings (`propositions` table). Assessments are immutable evaluation snapshots with evidence membership and gap tracking (`assessments`, `evidence_gaps`, `inference_records` tables). Action proposals are planning-shortcut projections derived from latest assessments (`action_proposals` table). DDL: `app/storage/schema.py`.

## Steps

Defined in `app/analysis_core/primitives.py`: `metric_query`, `profile_table`, `sample_rows`, `aggregate_query`, `attribute_change`, `synthesize_findings`.

### Contracts

- `metric_query`: `table`, `metric`, `time_scope` (required) + `dimensions`, `scope`, `time_axis`, `order`, `limit`
- `aggregate_query`: `table`, `measures`, `time_scope` (required) + `group_by`, `scope`, `time_axis`, `order`, `limit`
- `time_scope` = time windows; `scope` = non-time scope
- `scope.constraints` = scalar entity/row scope; `scope.predicate` = non-time conditions only
- Session root does not carry canonical execution scope; analysis constraints belong to step-level `scope` / `time_scope`

### Rules

- Design drafts (`docs/analysis/`): use `time_scope`/`scope` split; keep artifact/projection separated
- External wire docs (`docs/api/intent-steps.md`): define the target-state per-intent submission surface for `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`, `attribute`, `diagnose`, and `validate`
- **Implemented intents** (all registered in `IntentRunnerRegistry` via `app/intents/`):
  - Atomic: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`
  - Derived: `attribute` (→ `observe×2 + compare + decompose×D`), `diagnose` (→ `detect + (observe×2 + compare + decompose×D)×K`), `validate` (→ `observe×2 + test`; `sample_kind="auto"` fails `SAMPLE_KIND_AMBIGUOUS` in v1)
  - No stubs remain — `_STUB_INTENT_TYPES` is empty
- `diagnose` expansion contract: `detect` scans for anomaly candidates; top-`followup_limit` candidates each get `observe(current) + observe(baseline) + compare(scalar) + decompose×len(candidate_dimensions)`; baseline policy is `previous_adjacent_equal_length` (fixed, non-configurable); design doc: `docs/analysis/intents/derived/diagnose.md`
- `validate` expansion contract: two explicit `left`/`right` populations; `sample_kind` selects `numeric_sample_summary` or `rate_sample_summary` for internal `observe`s; `method` passed through to `test`; output is `validation_bundle`; design doc: `docs/analysis/intents/derived/validate.md`

## Sync

After changes: update this guide + affected API models, UI docs, entrypoint agent docs.

Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/foundations/`: shared terminology, agent-first interaction principles, and canonical schema design baselines
- `docs/analysis/intents/`: intent-system design docs; atomic schemas live in `docs/analysis/intents/atomic/`, derived schemas live in `docs/analysis/intents/derived/`
- `docs/analysis/evidence-engine/`: Evidence Engine theme docs for overview, runtime pipeline, finding/proposition seeding, inference/gap engine, assessment evaluation context, support/oppose/status resolution, gap-confidence-transition materialization, proposal policy engine, graph/ref semantics, and read surfaces
- `docs/analysis/evidence-engine/schemas/`: canonical evidence schemas (`session.md`, `finding.md`, `proposition.md`, `assessment.md`, `action-proposal.md`, `state-surface-schema.md`, `context-surface-schema.md`)
- `docs/analysis/evidence-engine/rules/`: rule contracts and supplements (`precondition-gate-contract.md`, `quality-gate-contract.md`, `comparability-gate-contract.md`, `rule-family-design-checklist.md`, `assessment-judgment-policy.md`, `rule-registry-contract.md`); align them with `docs/analysis/evidence-engine/inference-and-gap-engine.md` plus `docs/analysis/evidence-engine/schemas/assessment.md`
