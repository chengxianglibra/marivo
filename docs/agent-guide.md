# Agent Guide

Shared agent guidance for this repo. `AGENTS.md`, `CLAUDE.md`, and `.github/copilot-instructions.md` should stay as thin pointers to this file.

## Core Rules

- Factum is agentic analytics, not text-to-SQL.
- Factum is HTTP-only. Do not assume any MCP layer exists.
- External contract: sessions, semantic entities/metrics, and typed analysis steps.
- Facts/evidence must be extracted deterministically by code. Models may explain, not define evidence structure.
- Prefer typed steps over exposing raw SQL.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

Tests: `.venv/bin/pytest`

Requirements: Python 3.12+, `DUCKDB_MVP_DB`. Metadata store is SQLite; engines are DuckDB and Trino.

## Architecture

Flow: client -> FastAPI -> `app/api/` -> service/planning -> semantic/routing/execution -> SQLite metadata + engines.

Normal metadata reads use synced snapshots in `source_objects`, not live catalogs.

## Persistent Model

- Physical: synced source objects
- Semantic: entities, metrics, mappings
- Evidence: sessions, steps, artifacts, observations, claims, edges, recommendations

Artifacts are raw outputs. Observations are deterministic facts. Claims are synthesized conclusions with inference levels. Recommendations are derived from confirmed claims. `synthesize_findings` materializes final evidence objects.

## Steps

Defined in `app/analysis_core/primitives.py`.

Supported: `metric_query`, `profile_table`, `sample_rows`, `aggregate_query`, `attribute_change`, `synthesize_findings`.

Rules:

- `metric_query` request contract: `table`, `metric`, `time_scope` (required), plus `dimensions`, `scope`, `time_axis`, `order`, `limit`
- `aggregate_query` request contract: `table`, `measures`, `time_scope` (required), plus `group_by`, `scope`, `time_axis`, `order`, `limit`
- `time_scope` is the only time-window contract; `scope` is the only step-level non-time scope contract
- `scope.constraints` is for scalar entity/row scope; `scope.predicate` may contain only non-time conditions
- `metric_query` executes both `time_scope.mode = compare` and `time_scope.mode = single_window`
- `metric_query` order is mode-aware: `compare` supports `delta_pct ASC|DESC`; `single_window` supports `current_value ASC|DESC` and `current_sessions ASC|DESC`
- `time_axis` request overrides take priority over metadata, which takes priority over heuristics
- `time_axis` resolution prefers `semantic_entities.properties.time_capabilities`, then synced `source_objects.properties.time_capabilities`, then heuristics
- phase-1 timezone policy is session-consistent naive timestamps only; hour-grain windows must not include timezone offsets
- normalized windows are half-open: `[start, end)`
- mixed layouts use timestamp fields for correctness and partition fields for pruning; hour-grain partition pruning bounds edge days by hour when those fields exist
- `metric_query` observations inherit `time_scope.current` as their `observed_window`
- `metric_query(single_window)` emits current-window observations only; it does not fabricate `baseline_*` or `delta_pct`
- `aggregate_query` observations inherit the request window; temporal `group_by` can refine them to per-row windows
- session `constraints` / `raw_filter` auto-inject into supported query steps

## Keep In Sync

After behavior changes, update this guide and any affected API models, UI docs, and entrypoint agent docs.
