# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.
- Target-state external step submission contract lives in `docs/api/intent-steps.md`; `docs/analysis/intents/` remains the design source, not the wire spec.
- analysis refactor design docs is located at 'docs/analysis'

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
- Evidence: sessions, steps, artifacts, observations, claims, edges, recommendations

Artifacts = raw outputs. Observations = deterministic facts. Claims = synthesized conclusions with inference levels. Recommendations = derived from confirmed claims. `synthesize_findings` materializes final evidence.

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
- Current implementation still exposes legacy step types in code; do not assume the target-state wire contract is already implemented

## Sync

After changes: update this guide + affected API models, UI docs, entrypoint agent docs.

Docs layout:
- `docs/api/`: external HTTP API docs only; target-state step submission is in `intent-steps.md`, and canonical read surfaces are split into `session-state.md` and `context-surface.md`
- `docs/analysis/foundations/`: shared terminology, agent-first interaction principles, and canonical schema design baselines
- `docs/analysis/intents/`: intent-system design docs; atomic schemas live in `docs/analysis/intents/atomic/`, derived schemas live in `docs/analysis/intents/derived/`
- `docs/analysis/evidence-engine/`: Evidence Engine theme docs for overview, runtime pipeline, finding/proposition seeding, inference/gap engine, graph/ref semantics, and read surfaces
- `docs/analysis/evidence-engine/schemas/`: canonical evidence schemas (`session.md`, `finding.md`, `proposition.md`, `assessment.md`, `action-proposal.md`, `state-surface-schema.md`, `context-surface-schema.md`)
- `docs/analysis/evidence-engine/rules/`: rule contracts and supplements (`precondition-gate-contract.md`, `quality-gate-contract.md`, `comparability-gate-contract.md`, `rule-family-design-checklist.md`, `assessment-judgment-policy.md`, `rule-registry-contract.md`); align them with `docs/analysis/evidence-engine/inference-and-gap-engine.md` plus `docs/analysis/evidence-engine/schemas/assessment.md`
