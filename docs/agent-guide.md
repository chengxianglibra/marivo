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

Supported: `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `attribute_change`, `synthesize_findings`.

Rules:

- `compare_metric` and `aggregate_query` use typed `time_scope` and `scope`
- `time_axis` resolution prefers `semantic_entities.properties.time_capabilities`, then synced `source_objects.properties.time_capabilities`, then heuristics
- phase-1 timezone policy is session-consistent naive timestamps only; hour-grain windows must not include timezone offsets
- normalized windows are half-open: `[start, end)`
- `compare_metric` observations inherit `time_scope.current` as their `observed_window`
- `aggregate_query` observations inherit the request window; temporal `group_by` can refine them to per-row windows
- session constraints auto-inject into supported query steps

## Keep In Sync

After behavior changes, update this guide and any affected API models, UI docs, and entrypoint agent docs.
