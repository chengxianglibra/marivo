# Marivo — Agentic Analytics System

Stateful sessions, semantic discovery, typed analysis steps, deterministic evidence packaging. Dual-mode: local agentic (MCP stdio) and enterprise (HTTP API). Not a text-to-SQL tool.

## Features

- **Five-layer architecture**: Surfaces → Runtime → Core Engine → Ports → Adapters, with strict Core isolation enforced by import-linter
- **Dual-mode**: local agentic (MCP stdio, no daemon, `.marivo/` workspace) + enterprise (HTTP API, centralized governance)
- **Profile system**: adapter composition via `profiles/local.py` and `profiles/server.py` — same Runtime, different backends
- **Typed intents**: sessions, semantic catalog, source/engine registries, bindings with routing, async jobs, observability
- **Evidence packaging**: observations (5 types), claims with confidence/inference_level (L0–L5), evidence edges, recommendations with causal_basis
- **Readiness signal**: 5-dimensional readiness + suggested_action + live_claims after each typed analysis step
- **Causal checkers**: deterministic inference-level upgrades
- **Dual-backend**: SQLite (metadata) + DuckDB/Trino (analytics)
- **Independent UI**: React console in `frontend/` for HTTP API operations, semantic readiness, and evidence review

## Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .

# Local mode (MCP stdio, no daemon)
.venv/bin/marivo init --workspace-root .
# Configure your MCP client to use: .venv/bin/marivo mcp stdio

# HTTP server mode
.venv/bin/marivo init-local --workspace-root .
.venv/bin/marivo serve-local --workspace-root .
```

`marivo init` / `marivo init-local` creates `.marivo/marivo.yaml` and the local metadata layout.
`marivo serve-local` starts the HTTP service, waits for `/health`, and writes
`.marivo/runtime.json` for reuse by local agents.

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

```

`marivo init-local` writes the local workspace config automatically. For custom service
configuration, copy this shape to `marivo.yaml` or set `MARIVO_CONFIG`. Profile
selection is resolved per entry point via `profiles/resolver.py`. Source, engine,
and mapping inventory is managed via the HTTP API, not YAML config.

## Agent Setup

MCP is integrated in `marivo/transports/mcp/` with two transports:

- **stdio** (local agentic): embedded in-process, no daemon required
- **HTTP MCP** (enterprise): connects to a running Marivo HTTP service

Configure your MCP client for local stdio mode:

```json
{
  "command": ".venv/bin/marivo",
  "args": ["mcp", "stdio"],
  "cwd": "/absolute/path/to/workspace"
}
```

For enterprise HTTP MCP, point your client at the running Marivo service endpoint.

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
| Jobs | `POST/GET /jobs`, `POST /jobs/{id}/cancel` |
| Observability | `GET /metrics`, `GET /health` |

**Typed intents**: `observe`, `compare`, `decompose`, `correlate`, `detect`, `test`, `forecast`, `attribute`, `diagnose`, `validate`

## Architecture

```
Surfaces (CLI, MCP stdio+HTTP, HTTP API, SDK)
  → Runtime (session, semantic ops, intent execution, evidence ops)
    → Core Engine (pure domain logic, zero I/O)
      → Ports (Protocol interfaces: ModelStore, SessionStore, EvidenceStore, DataSource)
        → Adapters/Profiles (local: File/SQLite/DuckDB; server: SQL/Trino)
```

**Dual-mode deployment:**

- **Local agentic**: MCP stdio transport, no daemon, short-lived processes, SQLite + DuckDB, `.marivo/` workspace
- **Enterprise**: HTTP API + HTTP MCP, centralized governance, SQLite/MySQL metadata

MCP transports live in `marivo/transports/mcp/` (stdio and HTTP). `core/` contains
pure domain logic with zero I/O dependencies — enforced by import-linter in CI.

- **Surfaces**: CLI, MCP (stdio + HTTP), HTTP API
- **Runtime**: SemanticLayerService, SourceService, EngineService, BindingService, QueryRouter, SemanticService, GovernanceService, JobService
- **Core engine**: IR, compiler, executor, primitives, composites
- **Evidence engine**: extractors, synthesizers, causal checkers, readiness
- **Ports & Adapters**: SQLite/MySQL metadata + DuckDB/Trino analytics, FileModelStore, SqlModelStore, etc.

## Key Concepts

- **Canonical read surfaces**: session decisions come from `/sessions/{id}/state`; proposition closure comes from `/sessions/{id}/propositions/{pid}/context`
- **Inference levels**: L0=correlation, L1=consistency, L2=temporal precedence, L3=mechanism
- **Published semantic contracts**: runtime resolution and typed analysis should rely on published semantic objects
- **Profiles**: local and server profiles compose different adapters for the same Runtime (`profiles/local.py`, `profiles/server.py`)
- **Core isolation**: `core/` contains pure domain logic with zero I/O — no imports of adapters, transports, or storage libraries
- **Ports**: domain-defined abstract interfaces (Protocol classes) for storage, data access, and external integrations

## Tests

```bash
make test                     # all tests
.venv/bin/pytest tests/test_storage.py -v
make typecheck                # mypy
make lint                     # ruff check
make format                   # ruff format + ruff check --fix
```
