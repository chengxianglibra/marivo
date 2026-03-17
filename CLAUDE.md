# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is OmniDB

OmniDB is an **agentic analytics system** — not a text-to-SQL tool. The core thesis: LLM-to-database systems underperform because they lack structure for planning, evidence-based reasoning, and governance. OmniDB provides stateful analysis sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, and API exposure so agents interact at a higher abstraction level than raw SQL.

## Build & Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                         # core deps (Python >=3.12)
pip install -e ".[hive]"                 # + Hive Metastore adapter (hmsclient)

uvicorn app.main:app --reload            # FastAPI on :8000
```

Environment variables: `DUCKDB_MVP_DB` (analytics DB path).

## Testing

```bash
.venv/bin/python3 -m unittest discover -s tests -v    # all ~500 tests
.venv/bin/python3 -m unittest tests.test_storage -v   # single module
.venv/bin/python3 -m unittest tests.test_storage.SQLiteMetadataStoreTests.test_execute_and_query  # single test
```

Uses `unittest` only (no pytest). Tests use `tempfile.TemporaryDirectory` for isolated SQLite/DuckDB files. Integration tests spin up a real FastAPI `TestClient`. Always run tests via `.venv/bin/python3`.

## Architecture

### Six-layer design (all implemented)

```
User / Agent / Browser
  → Interaction Layer (HTTP API, Web UI)              ← implemented
  → Agent Runtime / Session Layer                    ← implemented (sessions, steps, planning, replanning)
  → Semantic Layer (catalog, metrics, evidence)      ← implemented
  → Execution Layer (multi-engine: DuckDB, Trino, Spark)  ← implemented
  → Catalog Adapters (Local, Hive, Trino, Unity, Polaris, Glue, DuckDB)  ← implemented
  → Data Assets (seeded demo tables)                 ← implemented
```

Note: **MCP server/client layer was removed entirely.** OmniDB is now a pure HTTP API service.

### Runtime stack

```
Browser / Agent / HTTP Client
  → FastAPI service (app/main.py → app/api/app_factory.py create_app())
  → Web UI (app/static/admin.html + user.html — vanilla JS, no build tooling, config-gated)
  → API routers (app/api/ — one module per domain):
      sessions.py    — session CRUD + step execution + evidence
      planning.py    — plan CRUD, validate, approve, execute, explain, cost
      sources.py     — source CRUD, sync trigger, catalog browse
      engines.py     — engine CRUD + bindings CRUD
      routing.py     — routing resolution
      semantic.py    — entity/metric/mapping CRUD + publish
      catalog.py     — search, resolve, planner-context, graph
      governance.py  — policy/quality-rule CRUD + governance check
      jobs.py        — async job submit/status/cancel
      approvals.py   — approval request CRUD + auto-flag
      metrics.py     — observability /metrics endpoint
      health.py      — GET /health
  → Service layer:
      SemanticLayerService   — session/step/evidence orchestration (app/service.py)
      PlanningService        — plan IR, validation, execution, cost estimation (app/planning.py)
      SourceService          — source registry + adapter factory (app/sources.py)
      EngineService          — engine registry + analytics engine factory (app/engines.py)
      BindingService         — source-engine bindings (app/bindings.py)
      QueryRouter            — table → source → binding → engine resolution (app/routing.py)
      SemanticService        — entity/metric/mapping CRUD (app/semantic.py)
      CatalogRuntimeService  — search, resolve, planner-context, graph (app/semantic_runtime/)
      GovernanceService      — policy enforcement + quality rules (app/governance.py)
      JobService             — async job execution (app/jobs.py)
      ApprovalService        — approval workflow (app/approvals.py)
      ReplanningService      — re-planning on step failure (app/planner/replanning.py)
      SessionManager         — session lifecycle (app/session/session_manager.py)
  → Storage:
      MetadataStore ABC  → SQLiteMetadataStore / PostgresMetadataStore
      AnalyticsEngine ABC → DuckDBAnalyticsEngine / TrinoAnalyticsEngine / SparkConnectAnalyticsEngine / SparkThriftAnalyticsEngine
```

### Three-layer data model

1. **Physical layer** — `source_objects` table holds schemas/tables/columns/partitions synced from external catalogs via `SyncEngine` (`app/sync.py`). Adapters are only called during `trigger_sync()`; post-sync all queries hit SQLite.
2. **Semantic layer** — `semantic_entities`, `semantic_metrics`, `semantic_mappings` (`app/semantic.py`). User-defined with draft/published/deprecated lifecycle and revision tracking. Mappings link semantic objects to physical `source_objects`.
3. **Evidence layer** — sessions → steps → artifacts → observations → claims → evidence_edges → recommendations. Orchestrated by `SemanticLayerService` (`app/service.py`), evidence engine in `app/evidence_engine/`.

### Evidence packaging (core design concept)

Evidence packaging is the most important design concept. Instead of returning raw rows, every step produces structured evidence:

- **Artifact** — raw step output (comparison table, aggregated result)
- **Observation** — typed factual finding extracted from artifact (e.g. "metric down 14.2% for slice X")
- **Claim** — synthesized conclusion supported/contradicted by observations
- **Evidence edge** — typed relationship: `supports`, `contradicts`, `justifies`
- **Recommendation** — action proposal backed by claims, with priority/risk/validation metric

Evidence engine is in `app/evidence_engine/`: extractors (comparison, aggregate), factories, pipeline, schemas, scoring, synthesizers. Confidence scoring: `app/evidence_engine/scoring.py`. Legacy facade at `app/evidence.py`.

Core principle: **facts by code, language by model** — factual extraction should be deterministic; LLMs may assist with synthesis and explanation but should not be the sole source of evidence structure.

### Step types

Defined in `app/analysis_core/primitives.py` (`STEP_TAXONOMY`):

- **`compare_metric`** — compare a published semantic metric between baseline and current windows; supports custom `period_start/period_end`, `filter`, `order` ASC/DESC, default `limit=10`
- **`profile_table`** — profile table row count and column-level completeness/cardinality signals
- **`sample_rows`** — return a bounded sample of rows; supports `filter`, `columns`, auto-partition
- **`aggregate_query`** — ad-hoc GROUP BY + aggregation; generates observations via `AggregateRowExtractor`; opt-out with `extract_observations=false`
- **`synthesize_findings`** — composite step; turns observations into claims and recommendations

Session constraints are auto-injected into `compare_metric`, `sample_rows`, `aggregate_query` WHERE clauses. Each step run generates independent step_id/observations (no deletion of prior same-type outputs).

### Analysis core (`app/analysis_core/`)

- **`ir.py`** — `AnalysisRequest`, `SemanticIntent`, step IR data classes
- **`primitives.py`** — `STEP_TAXONOMY`, `PRIMITIVE_STEP_TYPES`, `COMPOSITE_STEP_TYPES`, `step_category_for()`
- **`compiler.py`** — `CompiledQuery` — SQL compilation from semantic intent
- **`executor.py`** — `ExecutionResult` — typed execution result
- **`composites.py`** — `CompositeStepTemplate`, `CompositeWorkflowSpec` for multi-step workflows
- **`step_registry.py`** — `StepRunnerRegistry` for registering and dispatching step runners
- **`step_runners/generic.py`** — primitive step runners (compare_metric, profile_table, sample_rows, aggregate_query)
- **`step_runners/synthesis.py`** — synthesize_findings runner
- **`workflows/`** — catalog workflow runtime

### Execution layer (`app/execution/`)

- **`orchestrator.py`** — `WorkflowOrchestrator`, `WorkflowStepExecutor` protocol
- **`federation.py`** — `FederationPlanner`, `FederationRuntime` — cross-engine query federation
- **`routing_runtime.py`** — runtime query routing with engine selection
- **`costing.py`** — query cost estimation
- **`capabilities.py`** — engine capability negotiation
- **`translation.py`** — SQL dialect translation integration
- **`feedback.py`** — execution feedback collection
- **`errors.py`** — typed execution error hierarchy

### Adapter contracts

Three pluggable ABC contracts ensure business logic never touches engine-specific code:

- **MetadataStore** (`app/storage/metadata.py`) — `initialize`, `execute`, `execute_many`, `query_rows`, `query_one`, `connect`. Implementations: `SQLiteMetadataStore`, `PostgresMetadataStore`.
- **AnalyticsEngine** (`app/storage/analytics.py`) — `initialize`, `query_rows`, `table_exists`, `table_row_count`. Implementations: `DuckDBAnalyticsEngine`, `TrinoAnalyticsEngine`, `SparkConnectAnalyticsEngine`, `SparkThriftAnalyticsEngine`.
- **CatalogAdapter** (`app/adapters/base.py`) — `source_type`, `capabilities`, `test_connection`, `list_schemas`, `list_tables`, `get_table_detail`, `list_columns`, optional `list_partitions`/`get_table_stats`. Implementations: `LocalCatalogAdapter`, `HiveMetastoreAdapter`, `TrinoCatalogAdapter`, `UnityCatalogAdapter`, `PolarisAdapter`, `GlueCatalogAdapter`, `DuckDBCatalogAdapter`.

### Source-engine bindings and query routing

- **BindingService** (`app/bindings.py`) — CRUD for `source_engine_bindings` table. Links sources to engines with priority-based selection. Idempotent `ensure_binding()` keyed on `(source_id, engine_id)`. Optional `namespace` for table qualification.
- **QueryRouter** (`app/routing.py`) — resolves `table names → source_objects → source_id → bindings → engine`. Picks the highest-priority common engine across all sources. Raises `ValueError` if no single engine covers all tables.

### Governance and approvals

- **GovernanceService** (`app/governance.py`) — policy CRUD (aggregate_only, field_mask, row_filter, max_rows) + quality rules (freshness, null_rate, row_count_min) + enforcement
- **GovernanceEngine** (`app/governance_engine/`) — `repository.py`, `runtime.py`, `approvals.py` — modular governance runtime
- **ApprovalService** (`app/approvals.py`) — approval request CRUD + auto-flagging for high-risk recommendations

### Planning and re-planning

- **PlanningService** (`app/planning.py`) — plan lifecycle: draft → validated → approved → executing → completed/failed. Topological execution, per-step status, cost estimation, budget enforcement, `continue_on_failure` support. Auto-approves clean plans; requires explicit approval for governance/budget blocks.
- **ReplanningService** (`app/planner/replanning.py`) — re-planning on step failure

### Semantic runtime (`app/semantic_runtime/`)

Modular semantic layer services extracted from the monolithic `CatalogQueryService`:
- **`repository.py`** — semantic object persistence
- **`resolution.py`** — term/metric resolution
- **`catalog.py`** / `CatalogRuntimeService` — search, resolve (with engine info), graph traversal
- **`planner_context.py`** — planner context assembly
- **`semantic_metadata.py`** — metadata helpers

### Registry (`app/registry/`)

Thin service registries extracted for testability:
- **`source_registry.py`**, **`engine_registry.py`**, **`binding_registry.py`** — CRUD registries
- **`factories.py`** — adapter/engine factory construction
- **`sync_runtime.py`** — sync orchestration

## Conventions

- **ID format**: `prefix_uuid12hex` — e.g. `sess_`, `src_`, `eng_`, `bind_`, `ent_`, `met_`, `map_`, `obj_`, `obs_`, `claim_`, `rec_`, `edge_`, `step_`, `art_`, `sync_`, `sel_`, `plan_`, `pol_`, `qr_`, `job_`, `apr_`
- **JSON columns**: suffixed `_json` (e.g. `constraints_json`, `keys_json`). Stored as TEXT, serialized with `json.dumps`/`json.loads`.
- **Timestamps**: TEXT type, either `datetime('now')` SQLite default or ISO format via `datetime.now(timezone.utc).isoformat()`
- **DDL**: all in `app/storage/schema.py`, must stay **dialect-neutral** (no DuckDB casts, no PostgreSQL-specific types) for SQLite/MySQL/PostgreSQL portability
- **Web UI**: split into two files — `app/static/admin.html` (Infrastructure, Semantic Layer, Governance, System) and `app/static/user.html` (Discovery, Analysis, Execution). Shared assets: `app/static/shared.css` + `app/static/shared.js`. Gated by `ui.admin_enabled` / `ui.user_enabled` in config.
- **Pydantic models**: FastAPI request bodies in `app/api/models.py`; domain models in `app/models.py`
- **DB path**: defaults to `data/mvp.duckdb` (analytics) + `data/mvp.meta.sqlite` (metadata). Override with `DUCKDB_MVP_DB` env var.
- **Semantic lifecycle**: entities/metrics follow draft → published → deprecated with revision tracking. Publishing increments revision.
- **Sync model**: external catalogs (Hive Metastore etc.) are the authority; OmniDB stores synced snapshots. Sync uses `sync_version` to detect and remove stale objects.
- **Route ordering**: In `app/main.py` / API routers, literal path routes must be registered before parameterized routes.
- **No MCP**: The MCP server/client layer was removed. There is no `omnidb-mcp` entry point and no `app/mcp_server.py`.

## Key Design Decisions

These decisions guide all future work:

1. **Sessions over one-shot queries** — analysis is stateful; every investigation belongs to a session with goal, constraints, budget, policy.
2. **Semantics over schema** — agents operate on metrics/entities/dimensions, not table names and column guessing.
3. **Typed steps over SQL strings** — the external contract is step-oriented; SQL is an internal compilation target.
4. **Deterministic fact extraction** — evidence is extracted by code wherever possible; LLMs assist with language, not structure.
5. **Pure HTTP API** — MCP layer removed; agents and UIs interact via the FastAPI service directly.
6. **Engine abstraction with implementation honesty** — abstract across engines, but surface engine differences through cost/capability metadata.
7. **Sync-based catalog integration** — OmniDB syncs snapshots from external catalogs rather than querying them at read time.

## Roadmap

Completed:
- Catalog adapters: Unity Catalog, Polaris, AWS Glue, Trino, DuckDB
- PostgreSQL metadata store
- Typed planning with plan IR, validation, dry-run, cost estimates, re-planning
- Evidence engine: multiple observation types, AggregateRowExtractor, provenance tokens, metric resolution via semantic layer, QueryRouter wired into step runners
- Multi-engine: Spark Connect + Thrift adapters, SQL dialect translation, cross-engine federation layer
- Governance enforcement, async jobs, observability, approval hooks

Remaining / future:
- Auth/RBAC
- LLM-backed planning and reflection loop
- Production async job queue (currently sync fallback)
- Streaming step execution
