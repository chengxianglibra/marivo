# Marivo — Agentic Analytics System

Stateful sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, HTTP API. Not a text-to-SQL tool.

## Features

- **FastAPI service**: sessions, typed intents, semantic catalog, source/engine registries, bindings with routing, governance, async jobs, observability
- **Evidence packaging**: observations (5 types), claims with confidence/inference_level (L0–L5), evidence edges, recommendations with causal_basis
- **Readiness signal**: 5-dimensional readiness + suggested_action + live_claims after each typed analysis step
- **Causal checkers**: deterministic inference-level upgrades
- **Dual-backend**: SQLite (metadata) + DuckDB (analytics)
- **Independent UI**: React console in `frontend/` for HTTP API operations, semantic readiness, and evidence review

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/marivo init-local --workspace-root .
.venv/bin/marivo serve-local --workspace-root .
```

`marivo init-local` creates `.marivo/marivo.yaml` and the local metadata layout.
`marivo serve-local` starts the canonical HTTP service, waits for `/health`, and
writes `.marivo/runtime.json` for reuse by local agents.

Useful local runtime checks:

```bash
.venv/bin/marivo runtime status --workspace-root .
.venv/bin/marivo doctor --workspace-root .
.venv/bin/marivo runtime stop --workspace-root .
```

For direct service development, use the explicit service entrypoint:

```bash
.venv/bin/marivo serve --config marivo.yaml
```

Marivo only supports fresh-init for local metadata SQLite. If the metadata schema changes, delete
the old metadata file and let the service rebuild it from the current schema.

## Frontend Console

The independent UI lives in `frontend/`. It is an HTTP-only human console, not a restored FastAPI
`/ui` or `/admin` surface.

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_MARIVO_USE_MOCKS=false` and keep `VITE_MARIVO_API_BASE_URL=/api` to connect it to a live
Marivo service through the Vite dev proxy. See [`frontend/README.md`](frontend/README.md) for
scripts, OpenAPI type generation, tests, and v1 boundaries.

## Configuration

```yaml
metadata:
  engine: sqlite
  path: data/marivo.meta.sqlite

governance:
  enabled: true
  policies:
    - name: "Mask PII"
      type: field_mask
      definition: { fields: ["email", "phone"] }
      scope: { all: true }

```

`marivo init-local` writes the local workspace config automatically. For custom service
configuration, copy this shape to `marivo.yaml` or set `MARIVO_CONFIG`. Source,
engine, and mapping inventory is managed via the HTTP API, not YAML config.

## Agent Setup

Marivo remains HTTP-only. The optional MCP adapter in `marivo-mcp/` is a
client-side adapter over the canonical HTTP API.

Generate a local auto-managed MCP client config:

```bash
cd marivo-mcp
.venv/bin/marivo-mcp init --workspace-root /absolute/path/to/workspace --print-config
```

Generate a remote explicit config:

```bash
cd marivo-mcp
.venv/bin/marivo-mcp init --mode remote --base-url http://127.0.0.1:8000 --print-config
```

See `marivo-mcp/README.md` for Codex config writing, Streamable HTTP transport,
workspace-root guards, and remote failure behavior.

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
| Mappings | `POST/GET/PUT/DELETE /mappings`, `POST /routing/resolve` |
| Semantic | `POST/GET/PUT /semantic/entities|metrics|process-objects|dimensions|time|enum-sets|bindings`, `POST .../publish`, `/compiler/compatibility-profiles` |
| Catalog | `GET /catalog/search`, `GET /semantic/resolve/{name}`, `GET /catalog/graph` |
| Governance | `/policies`, `/quality-rules`, `POST /governance/check` |
| Jobs | `POST/GET /jobs`, `POST /jobs/{id}/cancel` |
| Observability | `GET /metrics`, `GET /health` |

**Typed intents**: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`, `attribute`, `diagnose`, `validate`

## Architecture

```
HTTP → FastAPI → Services → Analysis Core + Evidence Engine + Storage
```

An external MCP adapter lives in `marivo-mcp/`. It is a separate subproject and
does not change Marivo's HTTP-only product boundary. Local MCP startup resolves
or starts the HTTP runtime through `marivo serve-local`; remote MCP startup uses
an explicit `MARIVO_BASE_URL` and never falls back to local runtime.

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
