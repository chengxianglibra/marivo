# Factum — Agentic Analytics System

Stateful sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, HTTP API. Not a text-to-SQL tool.

## Features

- **FastAPI service**: sessions, typed intents, semantic catalog, source/engine registries, bindings with routing, governance, async jobs, observability
- **Evidence packaging**: observations (5 types), claims with confidence/inference_level (L0–L5), evidence edges, recommendations with causal_basis
- **Readiness signal**: 5-dimensional readiness + suggested_action + live_claims after each typed analysis step
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

# Run a typed intent
curl -s http://127.0.0.1:8000/sessions/<id>/intents/detect -X POST \
  -H "Content-Type: application/json" \
  -d '{"metric": "metric.watch_time",
       "time_scope": {"kind": "range", "start": "2026-03-01", "end": "2026-03-08"}}'

# Typed intent metric params use canonical refs only
# Example: "metric.watch_time" (not "watch_time")

# Read canonical session state
curl -s http://127.0.0.1:8000/sessions/<id>/state | python3 -m json.tool
```

## Endpoints

| Domain | Endpoints |
|--------|-----------|
| Sessions | `POST/GET /sessions`, `POST /sessions/{id}/intents/*`, `GET /sessions/{id}/state`, `GET /sessions/{id}/propositions/{pid}/context` |
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

**Typed intents**: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`, `attribute`, `diagnose`, `validate`

## Architecture

```
HTTP → FastAPI → Services → Analysis Core + Evidence Engine + Storage
```

An external MCP adapter scaffold now lives in `factum-mcp/`. It is a separate
subproject and does not change Factum's HTTP-only product boundary.

- **Services**: SemanticLayerService, SourceService, EngineService, BindingService, QueryRouter, SemanticService, GovernanceService, JobService
- **Analysis core**: IR, compiler, executor, primitives, composites
- **Evidence engine**: extractors, synthesizers, causal checkers, readiness
- **Storage**: SQLite metadata + DuckDB/Trino analytics

## Key Concepts

- **Canonical read surfaces**: session decisions come from `/sessions/{id}/state`; proposition closure comes from `/sessions/{id}/propositions/{pid}/context`
- **Inference levels**: L0=correlation, L1=consistency, L2=temporal precedence, L3=mechanism
- **Published semantic contracts**: runtime resolution and typed analysis should rely on published semantic objects

## Tests

```bash
make test                     # all tests
.venv/bin/pytest tests/test_storage.py -v
make typecheck                # mypy
make lint                     # ruff check
make format                   # ruff format + ruff check --fix
```
