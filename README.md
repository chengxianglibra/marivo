# Factum — Agentic Analytics System

Stateful sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, HTTP API. Not a text-to-SQL tool.

## Features

- **FastAPI service**: sessions, typed plans, semantic catalog, source/engine registries, bindings with routing, governance, async jobs, observability
- **Evidence packaging**: observations (5 types), claims with confidence/inference_level (L0–L5), evidence edges, recommendations with causal_basis
- **Readiness signal**: 5-dimensional readiness + suggested_action + live_claims after each step
- **Causal checkers**: deterministic inference-level upgrades
- **Dual-backend**: SQLite (metadata) + DuckDB (analytics)
- **Web UI**: Admin (`/admin`) plus a read-only query workbench (`/ui`) for Sessions, State, Context, Runtime, Grounding, and Jobs

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

## Configuration

```yaml
sources:
  - name: "Local Demo"
    type: duckdb
    connection: {}
    sync: { mode: all }

engines:
  - name: "Local DuckDB"
    type: duckdb
    connection: { path: data/mvp.duckdb }

bindings:
  - source: "Local Demo"
    engine: "Local DuckDB"
    priority: 10

governance:
  enabled: true
  policies:
    - name: "Mask PII"
      type: field_mask
      definition: { fields: ["email", "phone"] }
      scope: { all: true }

ui:
  enabled: true
```

Copy to `factum.yaml` or set `FACTUM_CONFIG`.

## Example

```bash
# Create session
curl -s http://127.0.0.1:8000/sessions -X POST \
  -H "Content-Type: application/json" \
  -d '{"goal": "Investigate watch time drop"}'

# Run step
curl -s http://127.0.0.1:8000/sessions/<id>/steps/metric_query -X POST \
  -H "Content-Type: application/json" \
  -d '{"table": "analytics.watch_events", "metric": "watch_time",
       "time_scope": {"mode": "single_window", "grain": "day",
                      "current": {"start": "2026-03-01", "end": "2026-03-08"}}}'

# Evidence graph
curl -s http://127.0.0.1:8000/sessions/<id>/evidence | python3 -m json.tool
```

## Endpoints

| Domain | Endpoints |
|--------|-----------|
| Sessions | `POST/GET /sessions`, `POST /sessions/{id}/steps/{type}`, `GET .../evidence|debug|reflection-context` |
| Sources | `POST/GET/PUT/DELETE /sources`, `POST .../sync`, `GET .../catalog/schemas|tables` |
| Engines | `POST/GET /engines` |
| Bindings | `POST/GET/DELETE /bindings`, `POST /routing/resolve` |
| Semantic | `POST/GET/PUT /semantic/entities|metrics|process-objects|dimensions|time|enum-sets|bindings`, `POST .../publish`, `/compiler/compatibility-profiles` |
| Catalog | `GET /catalog/search`, `GET /semantic/resolve/{name}`, `GET /catalog/graph` |
| Governance | `/policies`, `/quality-rules`, `POST /governance/check` |
| Jobs | `POST/GET /jobs`, `POST /jobs/{id}/cancel` |
| Approvals | `POST/GET /approvals`, `POST .../approve|reject` |
| Observability | `GET /metrics`, `GET /health` |
| UI | `GET /admin`, `GET /ui` (read-only query/troubleshooting workbench over canonical and runtime read surfaces) |

**Steps**: `metric_query`, `profile_table`, `sample_rows`, `aggregate_query`, `attribute_change`, `correlate_metrics`, `synthesize_findings`

## Architecture

```
HTTP → FastAPI → Services → Analysis Core + Evidence Engine + Storage
```

- **Services**: SemanticLayerService, SourceService, EngineService, BindingService, QueryRouter, SemanticService, GovernanceService, JobService
- **Analysis core**: IR, compiler, executor, primitives, composites
- **Evidence engine**: extractors, synthesizers, causal checkers, readiness
- **Storage**: SQLite metadata + DuckDB/Trino analytics

## Key Concepts

- **Incremental synthesis**: tentative claims after each step; `synthesize_findings` promotes to confirmed/insufficient
- **Inference levels**: L0=correlation, L1=consistency, L2=temporal precedence, L3=mechanism
- **Plan lifecycle**: draft → validated → approved → executing → completed/failed

## Tests

```bash
make test                     # all tests
.venv/bin/pytest tests/test_storage.py -v
make typecheck                # mypy
make lint                     # ruff check
make format                   # ruff format + ruff check --fix
```
