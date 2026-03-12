# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is OmniDB

OmniDB is an **agentic analytics system** — not a text-to-SQL tool. The core thesis: LLM-to-database systems underperform because they lack structure for planning, evidence-based reasoning, and governance. OmniDB provides stateful analysis sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, and MCP tool exposure so agents interact at a higher abstraction level than raw SQL.

The current MVP validates this architecture with a concrete scenario: a video platform investigating a watch-time decline.

## Build & Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                         # core deps (Python >=3.12)
pip install -e ".[hive]"                 # + Hive Metastore adapter (hmsclient)

uvicorn app.main:app --reload            # FastAPI on :8000
omnidb-mcp                               # MCP stdio server (requires FastAPI running)
```

Environment variables: `DUCKDB_MVP_DB` (analytics DB path), `OMNIDB_API_BASE_URL`, `OMNIDB_API_TIMEOUT`, `OMNIDB_MCP_TRANSPORT`.

## Testing

```bash
python3 -m unittest discover -s tests -v          # all 120 tests
python3 -m unittest tests.test_storage -v          # single module
python3 -m unittest tests.test_storage.SQLiteMetadataStoreTests.test_execute_and_query  # single test
```

Uses `unittest` only (no pytest). Tests use `tempfile.TemporaryDirectory` for isolated SQLite/DuckDB files. Integration tests spin up a real FastAPI `TestClient`.

## Architecture

### Six-layer target design

The design doc (`docs/omnidb-design-doc.md`) describes a target architecture with six logical layers. The MVP currently realizes the bottom four:

```
User / Agent / LLM Client
  → Interaction Layer (HTTP API, MCP tools, Web UI)  ← implemented
  → Agent Runtime / Session Layer                    ← partially (sessions, steps; no planner/reflection yet)
  → Semantic Layer (catalog, metrics, evidence)      ← implemented
  → Execution Layer (DuckDB adapter)                 ← implemented
  → Data Assets (seeded demo tables)                 ← implemented
```

Not yet implemented: LLM-backed planning/reflection loop, cost estimation, governance enforcement, async jobs, cross-engine federation.

### Runtime stack

```
CLI Agent / User / Browser
  → MCP Wrapper (11 tools, stdio transport)
  → FastAPI service (app/main.py — create_app() factory, all endpoints inline)
  → Optional Web UI (app/static/index.html — single-file, vanilla JS, config-gated)
  → Service layer:
      SemanticLayerService  — session/workflow/evidence orchestration
      SourceService         — source registry + adapter factory
      EngineService         — engine registry + analytics engine factory
      BindingService        — source-engine bindings (priority-based)
      QueryRouter           — table name → source → binding → engine resolution
      SemanticService       — entity/metric/mapping CRUD
      CatalogQueryService   — search, resolve (with engine info), planner-context, graph traversal
  → Storage:
      MetadataStore ABC  → SQLiteMetadataStore  (control-plane: sessions, semantic objects, evidence, bindings)
      AnalyticsEngine ABC → DuckDBAnalyticsEngine (analytical queries + demo data seeding)
```

### Three-layer data model

1. **Physical layer** — `source_objects` table holds schemas/tables/columns/partitions synced from external catalogs via `SyncEngine` (`app/sync.py`). Adapters are only called during `trigger_sync()`; post-sync all queries hit SQLite.
2. **Semantic layer** — `semantic_entities`, `semantic_metrics`, `semantic_mappings` (`app/semantic.py`). User-defined with draft/published/deprecated lifecycle and revision tracking. Mappings link semantic objects to physical `source_objects`.
3. **Evidence layer** — sessions → steps → artifacts → observations → claims → evidence_edges → recommendations. Orchestrated by `SemanticLayerService` (`app/service.py`), with deterministic heuristic synthesis in `app/evidence.py`.

### Evidence packaging (core design concept)

Evidence packaging is the most important design concept. Instead of returning raw rows, every step produces structured evidence:

- **Artifact** — raw step output (comparison table, aggregated result)
- **Observation** — typed factual finding extracted from artifact (e.g. "watch time down 14.2% for slice X")
- **Claim** — synthesized conclusion supported/contradicted by observations
- **Evidence edge** — typed relationship: `supports`, `contradicts`, `justifies`
- **Recommendation** — action proposal backed by claims, with priority/risk/validation metric

Confidence scoring uses a deterministic weighted formula (effect strength, consistency, sample size, data quality, contradiction penalty). See `app/evidence.py:score_confidence()`.

Core principle: **facts by code, language by model** — factual extraction should be deterministic; LLMs may assist with synthesis and explanation but should not be the sole source of evidence structure.

### Adapter contracts

Three pluggable ABC contracts ensure business logic never touches engine-specific code:

- **MetadataStore** (`app/storage/metadata.py`) — `initialize`, `execute`, `execute_many`, `query_rows`, `query_one`, `connect`. Implementations: `SQLiteMetadataStore`, stub `PostgresMetadataStore`.
- **AnalyticsEngine** (`app/storage/analytics.py`) — `initialize`, `query_rows`, `table_exists`, `table_row_count`. Implementations: `DuckDBAnalyticsEngine`, `TrinoAnalyticsEngine`. Future: Spark.
- **CatalogAdapter** (`app/adapters/base.py`) — `source_type`, `capabilities`, `test_connection`, `list_schemas`, `list_tables`, `get_table_detail`, `list_columns`, optional `list_partitions`/`get_table_stats`. Implementations: `LocalCatalogAdapter` (mock), `HiveMetastoreAdapter`. Future: Unity Catalog, Polaris, AWS Glue.

### Source-engine bindings and query routing

- **BindingService** (`app/bindings.py`) — CRUD for `source_engine_bindings` table. Links sources to engines with priority-based selection. Idempotent `ensure_binding()` keyed on `(source_id, engine_id)`.
- **QueryRouter** (`app/routing.py`) — resolves `table names → source_objects → source_id → bindings → engine`. Picks the highest-priority common engine across all sources. Raises `ValueError` if no single engine covers all tables.

### MCP tool-to-endpoint mapping

| MCP tool | FastAPI endpoint |
|---|---|
| `omnidb_get_health` | `GET /health` |
| `omnidb_get_catalog` | `GET /catalog` |
| `omnidb_create_session` | `POST /sessions` |
| `omnidb_run_step` | `POST /sessions/{id}/steps/{type}` |
| `omnidb_run_watch_time_workflow` | `POST /sessions/{id}/workflow/watch-time-drop` |
| `omnidb_get_evidence` | `GET /sessions/{id}/evidence` |
| `omnidb_list_sources` | `GET /sources` |
| `omnidb_search_catalog` | `GET /catalog/search?q=...&type=...` |
| `omnidb_resolve_term` | `GET /semantic/resolve/{name}` |
| `omnidb_get_planner_context` | `GET /sessions/{id}/planner-context` |

MCP server is a thin proxy — all business logic lives in FastAPI. Tools support both JSON and markdown `response_format`.

## Conventions

- **ID format**: `prefix_uuid12hex` — e.g. `sess_`, `src_`, `eng_`, `bind_`, `ent_`, `met_`, `map_`, `obj_`, `obs_`, `claim_`, `rec_`, `edge_`, `step_`, `art_`, `sync_`
- **JSON columns**: suffixed `_json` (e.g. `constraints_json`, `keys_json`). Stored as TEXT, serialized with `json.dumps`/`json.loads`.
- **Timestamps**: TEXT type, either `datetime('now')` SQLite default or ISO format via `datetime.now(timezone.utc).isoformat()`
- **DDL**: all in `app/storage/schema.py`, must stay **dialect-neutral** (no DuckDB casts, no PostgreSQL-specific types) for SQLite/MySQL/PostgreSQL portability
- **Web UI**: optional admin UI in `app/static/index.html`, gated by `ui.enabled` in config. Single self-contained HTML file (vanilla JS, no build tooling). Served at `GET /ui` with static assets at `/static/`.
- **Pydantic models**: FastAPI request bodies in `app/models.py`, MCP input models in `app/mcp_server.py`
- **DB path**: defaults to `data/mvp.duckdb` (analytics) + `data/mvp.meta.sqlite` (metadata). Override with `DUCKDB_MVP_DB` env var.
- **Semantic lifecycle**: entities/metrics follow draft → published → deprecated with revision tracking. Publishing increments revision.
- **Sync model**: external catalogs (Hive Metastore etc.) are the authority; OmniDB stores synced snapshots. Sync uses `sync_version` to detect and remove stale objects.

## Key Design Decisions

These decisions from the design doc (`docs/omnidb-design-doc.md`) should guide all future work:

1. **Sessions over one-shot queries** — analysis is stateful; every investigation belongs to a session with goal, constraints, budget, policy.
2. **Semantics over schema** — agents operate on metrics/entities/dimensions, not table names and column guessing.
3. **Typed steps over SQL strings** — the external contract is step-oriented; SQL is an internal compilation target.
4. **Deterministic fact extraction** — evidence is extracted by code wherever possible; LLMs assist with language, not structure.
5. **Thin protocol adapters** — HTTP and MCP layers expose the service cleanly without embedding domain logic.
6. **Engine abstraction with implementation honesty** — abstract across engines, but surface engine differences through cost/capability metadata.
7. **Sync-based catalog integration** — OmniDB syncs snapshots from external catalogs rather than querying them at read time.

## Roadmap (from design doc)

- **Next-1**: Additional catalog adapters (Unity Catalog, Polaris, AWS Glue) + PostgreSQL metadata store
- **Next-2**: Typed planning — plan/step IR, validation, dry-run, cost estimates, re-planning
- **Next-3**: Strengthen evidence — more observation types, funnel/contribution analysis, provenance tokens, resolve metrics from semantic layer at execution time, wire QueryRouter into step runners
- **Next-4**: Multi-engine execution — Spark analytics engine adapter, SQL dialect translation, cross-engine federation
- **Next-5**: Productionize — auth/RBAC, governance enforcement, async jobs, observability, approval hooks
