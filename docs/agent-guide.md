# Agent Guide

Shared guidance for agents. `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md` point here.

## Core Rules

- Agentic analytics, not text-to-SQL. HTTP-only (no MCP).
- Contract: sessions, semantic entities/metrics, typed steps.
- Facts extracted deterministically by code. Models explain, not define evidence.
- Prefer typed steps over raw SQL.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && uvicorn app.main:app --reload
```

Tests: `.venv/bin/pytest`. Requires Python 3.12+, `DUCKDB_MVP_DB`. SQLite metadata, DuckDB/Trino engines.

## Architecture

Client → FastAPI → `app/api/` → service/planning → semantic/routing/execution → SQLite + engines.
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
- Mandatory canonical result artifacts must not commit with zero findings; successful empty result is illegal in target-state docs
- Step refs: prefer structured typed refs over bare string ids
- Numeric summaries (v1): `welch_t` compatible; rates → `two_proportion_z`
- `metric_query` modes: `compare` and `single_window`
- `metric_query` order: `compare` → `delta_pct ASC|DESC`; `single_window` → `current_value ASC|DESC`, `current_sessions ASC|DESC`
- `time_axis` priority: request override > metadata > heuristics
- `time_axis` resolution: `semantic_entities.properties.time_capabilities` > `source_objects.properties.time_capabilities` > heuristics
- Timezone (phase-1): session-consistent naive timestamps; no offsets in hour-grain windows
- Windows: half-open `[start, end)`
- Mixed layouts: timestamp fields (correctness) + partition fields (pruning); hour-grain pruning bounds edge days by hour
- `metric_query` observations: inherit `time_scope.current` as `observed_window`
- `metric_query(single_window)`: current-window observations only (no `baseline_*`/`delta_pct`)
- `aggregate_query` observations: inherit request window; temporal `group_by` refines to per-row windows
- Design drafts must not introduce session-level enforced scope inheritance
- Assessment snapshots (v1): on-demand creation; proposition registration may leave `latest_assessment = null` until first committed assessment output
- Assessment latest selection (v1): strict linear supersede chain only; do not fallback to max seq or latest timestamp on chain corruption
- Gap reopen (v1): resolved gap stays resolved; later recurrence opens a new gap instance
- Session lifecycle (v1): `open | closed | aborted` (no `closing`/async state)
- Session terminal reasons (v1): `answered`, `abandoned`, `rolled_over`, `governance_terminated`, `budget_exhausted`, `timed_out`
- Session termination model (v1): explicit-first; system-derived governance / budget / timeout signals do not auto-close the session, and ordinary step failure is non-terminal
- Write access: `open` only; `closed`/`aborted` = read-only
- Session mutability: `governance.policy_refs` immutable (require rollover); `goal.question`, `governance.budget`, `governance.warnings` mutable
- Rollover trigger: binary check (immutable field value changed)
- Rollover API: `POST /sessions/{id}/rollover` with new values

## Sync

After changes: update this guide + affected API models, UI docs, entrypoint agent docs.

Docs layout:
- `docs/api/`: external HTTP API docs only
- `docs/analysis/`: analysis-intent drafts, canonical evidence schemas (`session.md`, `finding.md`, `proposition.md`, `assessment.md`, `state-surface-schema.md`, `context-surface-schema.md`) and cross-cutting contracts such as `artifact-finding-extraction-contract.md`
- Rule contracts: `precondition-gate-contract.md`, `quality-gate-contract.md`, `comparability-gate-contract.md` (align with `inference-rule-engine-contract.md`, `assessment.md`)
