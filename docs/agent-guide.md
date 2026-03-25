# Agent Guide

Shared coding-agent guidance for this repository. `AGENTS.md`, `CLAUDE.md`, and `.github/copilot-instructions.md` should stay as thin entrypoints to this file.

## What Factum Is

Factum is an agentic analytics system, not a text-to-SQL tool. Agents should work with:

- stateful analysis sessions
- semantic entities and metrics
- typed analysis steps
- deterministic evidence packaging
- a pure HTTP API

Core rule: facts should be extracted by code; models may help with language and explanation, not evidence structure.

## Build And Test

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

Use `.venv/bin/pytest` for tests.

Important:

- Python >= 3.12
- env var: `DUCKDB_MVP_DB`
- metadata store is SQLite; analytics engines are DuckDB and Trino

## Architecture

Request path:

```text
Browser / Agent / HTTP Client
  -> FastAPI service
  -> API routers in app/api/
  -> service layer
  -> metadata store / analytics engines
```

Main layers:

1. Interaction: HTTP API and static web UI
2. Runtime: sessions, steps, planning, replanning
3. Semantic: catalog, entities, metrics, mappings
4. Execution: query compilation, routing, federation, costing
5. Integration: catalog adapters and engine factories
6. Data assets: synced catalog snapshots and demo data

Hard boundary: MCP was removed. Factum is HTTP-only.

## Data Model

Three persistent layers matter:

1. Physical: `source_objects` synced from external catalogs; normal reads hit SQLite snapshots, not live catalogs
2. Semantic: entities, metrics, mappings with `draft -> published -> deprecated`
3. Evidence: sessions, steps, artifacts, observations, claims, edges, recommendations

Evidence packaging is central:

- artifact = raw output
- observation = deterministic fact extracted from output
- claim = synthesized conclusion with inference level; L1 may come from cross-slice, cross-scope, or cross-metric consistency
- `temporally_precedes` is a claim-to-claim causal edge backed by real observation windows or relation-backed hourly peak/decay lead-lag, not by step execution order; current comparisons assume observations in the same session use a consistent timezone/time basis
- evidence edge = relation between evidence objects
- recommendation = action backed by confirmed claims; derivation is template-driven from final claims + claim relations, and responses include a stable `template_id` for debugging/UX

## Step Model

Defined in [`app/analysis_core/primitives.py`](/Users/lichengxiang/source/oss/factum/app/analysis_core/primitives.py).

Supported step types:

- `compare_metric`
- `profile_table`
- `sample_rows`
- `aggregate_query`
- `attribute_change`
- `synthesize_findings`

Rules:

- prefer typed steps over raw SQL as the external contract
- primitive steps produce `readiness` and `live_claims`
- final evidence edges and recommendations are materialized by `synthesize_findings`
- session constraints are auto-injected into supported query steps

## Important Modules

- [`app/main.py`](/Users/lichengxiang/source/oss/factum/app/main.py): app entrypoint
- [`app/api/`](/Users/lichengxiang/source/oss/factum/app/api): HTTP routers
- [`app/service.py`](/Users/lichengxiang/source/oss/factum/app/service.py): session and evidence orchestration
- [`app/planning.py`](/Users/lichengxiang/source/oss/factum/app/planning.py): plan lifecycle and execution
- [`app/semantic.py`](/Users/lichengxiang/source/oss/factum/app/semantic.py): semantic CRUD
- [`app/routing.py`](/Users/lichengxiang/source/oss/factum/app/routing.py): source-to-engine routing
- [`app/evidence_engine/`](/Users/lichengxiang/source/oss/factum/app/evidence_engine): deterministic extraction and synthesis
- [`app/storage/schema.py`](/Users/lichengxiang/source/oss/factum/app/storage/schema.py): dialect-neutral DDL

## Conventions

- ID format: `prefix_uuid12hex`
- JSON columns use `_json` suffix and are stored as TEXT
- timestamps are TEXT, usually SQLite `datetime('now')` or UTC ISO strings
- DDL in [`app/storage/schema.py`](/Users/lichengxiang/source/oss/factum/app/storage/schema.py) must remain dialect-neutral
- route registration must put literal paths before parameterized paths
- web UI is static: [`app/static/admin.html`](/Users/lichengxiang/source/oss/factum/app/static/admin.html) and [`app/static/user.html`](/Users/lichengxiang/source/oss/factum/app/static/user.html)

## Design Rules

1. Sessions over one-shot queries
2. Semantics over schema guessing
3. Typed steps over SQL strings as external contract
4. Deterministic fact extraction where possible
5. Pure HTTP API, no MCP
6. Honest engine abstraction: surface capability and cost differences
7. Sync-based catalog integration, not live metadata reads on normal requests

## Change Checklist

After changing behavior, check whether these also need updates:

- [`README.md`](/Users/lichengxiang/source/oss/factum/README.md)
- [`app/api/models.py`](/Users/lichengxiang/source/oss/factum/app/api/models.py)
- [`app/models.py`](/Users/lichengxiang/source/oss/factum/app/models.py)
- web UI in [`app/static/`](/Users/lichengxiang/source/oss/factum/app/static)
- agent docs: [`docs/agent-guide.md`](/Users/lichengxiang/source/oss/factum/docs/agent-guide.md), [`CLAUDE.md`](/Users/lichengxiang/source/oss/factum/CLAUDE.md), [`AGENTS.md`](/Users/lichengxiang/source/oss/factum/AGENTS.md), [`.github/copilot-instructions.md`](/Users/lichengxiang/source/oss/factum/.github/copilot-instructions.md)
- Factum skill docs if API behavior, step semantics, or governance behavior changed

Rule of thumb: if an agent would now get a different result or different validation behavior from the API, update the docs too.

## Current Scope

Implemented:

- DuckDB and Trino adapters
- planning, validation, dry-run, cost estimation, replanning
- semantic layer and routing
- deterministic evidence engine
- governance, approvals, jobs, observability
- SQL translation and cross-engine federation

Still future-facing:

- auth / RBAC
- stronger LLM planning and reflection loops
- production async job queue
- streaming step execution
