# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Factum

Factum is an **agentic analytics system** — not a text-to-SQL tool. The core thesis: LLM-to-database systems underperform because they lack structure for planning, evidence-based reasoning, and governance. Factum provides stateful analysis sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, and API exposure so agents interact at a higher abstraction level than raw SQL.

## Build & Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .                         # core deps (Python >=3.12)

uvicorn app.main:app --reload            # FastAPI on :8000
```

Environment variables: `DUCKDB_MVP_DB` (analytics DB path).

## Testing

```bash
.venv/bin/pytest                                                                              # all tests (parallel via -n auto)
.venv/bin/pytest tests/test_storage.py -v                                                     # single module
.venv/bin/pytest tests/test_storage.py::SQLiteMetadataStoreTests::test_execute_and_query -v  # single test
.venv/bin/pytest -x                                                                           # stop on first failure
```

Tests are written with `unittest.TestCase` and collected by pytest. Tests use `tempfile.TemporaryDirectory` for isolated SQLite/DuckDB files. Integration tests spin up a real FastAPI `TestClient`. Always run tests via `.venv/bin/pytest`.

## Architecture

### Six-layer design (all implemented)

```
User / Agent / Browser
  → Interaction Layer (HTTP API, Web UI)              ← implemented
  → Agent Runtime / Session Layer                    ← implemented (sessions, steps, planning, replanning)
  → Semantic Layer (catalog, metrics, evidence)      ← implemented
  → Execution Layer (DuckDB, Trino)                  ← implemented
  → Catalog Adapters (DuckDB, Trino)                 ← implemented
  → Data Assets (seeded demo tables)                 ← implemented
```

Note: **MCP server/client layer was removed entirely.** Factum is now a pure HTTP API service.

### Runtime stack

```
Browser / Agent / HTTP Client
  → FastAPI service (app/main.py → app/api/app_factory.py create_app())
  → Web UI (app/static/admin.html + user.html — vanilla JS, no build tooling, config-gated)
  → API routers (app/api/ — one module per domain):
      sessions.py    — session CRUD + step execution + evidence + reflection-context
      planning.py    — plan CRUD, validate, approve, execute, explain, cost, patch
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
      MetadataStore ABC  → SQLiteMetadataStore
      AnalyticsEngine ABC → DuckDBAnalyticsEngine / TrinoAnalyticsEngine
```

### Three-layer data model

1. **Physical layer** — `source_objects` table holds schemas/tables/columns/partitions synced from external catalogs via `SyncEngine` (`app/sync.py`). Adapters are only called during `trigger_sync()`; post-sync all queries hit SQLite.
2. **Semantic layer** — `semantic_entities`, `semantic_metrics`, `semantic_mappings` (`app/semantic.py`). User-defined with draft/published/deprecated lifecycle and revision tracking. Metrics support optional `desired_direction` (`up`/`down`/`neutral`) to indicate whether increases or decreases are desirable. Mappings link semantic objects to physical `source_objects`.
3. **Evidence layer** — sessions → steps → artifacts → observations → claims → evidence_edges → recommendations. Orchestrated by `SemanticLayerService` (`app/service.py`), evidence engine in `app/evidence_engine/`.

### Evidence packaging (core design concept)

Evidence packaging is the most important design concept. Instead of returning raw rows, every step produces structured evidence:

- **Artifact** — raw step output (comparison table, aggregated result)
- **Observation** — typed factual finding extracted from artifact (e.g. "metric down 14.2% for slice X"); includes `observed_window` (ISO date range, nullable) and `temporal_order` (session-scoped sequence number)
- **Claim** — synthesized conclusion; `status` is `tentative` (incremental) → `confirmed`/`insufficient` (after `synthesize_findings`); includes `inference_level` (L0–L5) and `inference_justification` tokens
- **Evidence edge** — base layer: `supports`, `contradicts`, `justifies`; causal layer: `correlates_with`, `temporally_precedes`, `mechanistically_explains`, `eliminates_alternative`, `experimentally_confirms`
- **Recommendation** — action proposal backed by claims; `type` is `action_required` (default) or `no_action_required` (metric aligned with `desired_direction` or delta < 5%); includes priority/risk/validation metric and `causal_basis` (inference level, confounders, suggested validation)

Evidence engine is in `app/evidence_engine/`:
- **Extractor Registry** (`registry.py`) — `ExtractorRegistry` with 5 registered extractors: `ComparisonRowExtractor`, `AggregateRowExtractor`, `FunnelExtractor` (`extractors/funnel.py`), `AnomalyExtractor` (`extractors/anomaly.py`), `ContributionShiftExtractor` (`extractors/contribution_shift.py`)
- **Incremental synthesizer** (`incremental_synthesizer.py`) — runs after every primitive step; creates/updates tentative claims keyed by (metric, slice); detects contradictions; runs causal checkers
- **Causal checkers** (`causal_checkers.py`) — `CausalCheckerRegistry` with 4 checkers: `CrossSliceConsistencyChecker` (L0→L1, threshold 80% directional consistency), `TemporalPrecedenceChecker` (L1→L2, requires `observed_window`), `DoseResponseChecker` (bonus, Spearman ρ≥0.7), `ReversalChecker` (bonus, sustained reversal ≥2 periods)
- **Readiness** (`readiness.py`) — `compute_readiness()` and `load_live_claims()`; appended to every primitive step response
- Factories, pipeline, schemas, scoring, synthesizers, extractors (comparison, aggregate). Confidence scoring: `app/evidence_engine/scoring.py`. Legacy facade at `app/evidence.py`.

**Reflection module**: `app/reflection/context.py` — `build_reflection_context(metadata_store, session_id, plan_id)`. Served as `GET /sessions/{id}/reflection-context`. Gated by `reflection.enabled` config (default: true).

Core principle: **facts by code, language by model** — factual extraction should be deterministic; LLMs may assist with synthesis and explanation but should not be the sole source of evidence structure.

### Step types

Defined in `app/analysis_core/primitives.py` (`STEP_TAXONOMY`):

- **`compare_metric`** — compare a published semantic metric between baseline and current windows; `period_end` required, `period_start` optional (defaults to `period_end` for single-day); `comparison_type` (`dod|wow|mom|yoy`) auto-computes baseline; `baseline_start/end` for explicit override (takes priority); unequal windows warn, not reject; `debug` field attached on failure; `order` ASC/DESC, default `limit=10`
- **`profile_table`** — profile table row count and column-level completeness/cardinality signals
- **`sample_rows`** — return a bounded sample of rows; supports `filter`, `columns`, auto-partition
- **`aggregate_query`** — ad-hoc GROUP BY + aggregation; generates observations via `AggregateRowExtractor`; opt-out with `extract_observations=false`
- **`attribute_change`** — explicit attribution step for a published metric across candidate dimensions; produces `contribution_shift` observations and can justify an upstream anomaly via `anomaly_observation_id`
- **`synthesize_findings`** — composite step; promotes `tentative` claims → `confirmed`/`insufficient`; generates recommendations; does **not** count toward `budget.max_steps`

Every primitive step response includes `readiness` (5-dimensional signal) and `live_claims` (tentative + confirmed claims). Incremental synthesis via `IncrementalSynthesizer` runs automatically after each primitive step as a side-effect. Session constraints are auto-injected into `compare_metric`, `sample_rows`, `aggregate_query`, and `attribute_change` WHERE clauses. Each step run generates independent step_id/observations (no deletion of prior same-type outputs).

### Analysis core (`app/analysis_core/`)

- **`ir.py`** — `AnalysisRequest`, `SemanticIntent`, step IR data classes
- **`primitives.py`** — `STEP_TAXONOMY`, `PRIMITIVE_STEP_TYPES`, `COMPOSITE_STEP_TYPES`, `step_category_for()`
- **`compiler.py`** — `CompiledQuery` — SQL compilation from semantic intent
- **`executor.py`** — `ExecutionResult` — typed execution result
- **`composites.py`** — `CompositeStepTemplate`, `CompositeWorkflowSpec` for multi-step workflows
- **`step_registry.py`** — `StepRunnerRegistry` for registering and dispatching step runners
- **`step_runners/generic.py`** — primitive step runners (compare_metric, profile_table, sample_rows, aggregate_query)
- **`step_runners/attribution.py`** — primitive step runner for attribute_change
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

- **MetadataStore** (`app/storage/metadata.py`) — `initialize`, `execute`, `execute_many`, `query_rows`, `query_one`, `connect`. Implementation: `SQLiteMetadataStore`.
- **AnalyticsEngine** (`app/storage/analytics.py`) — `initialize`, `query_rows`, `table_exists`, `table_row_count`. Implementations: `DuckDBAnalyticsEngine`, `TrinoAnalyticsEngine`.
- **CatalogAdapter** (`app/adapters/base.py`) — `source_type`, `capabilities`, `test_connection`, `list_schemas`, `list_tables`, `get_table_detail`, `list_columns`, optional `list_partitions`/`get_table_stats`. Implementations: `DuckDBCatalogAdapter`, `TrinoCatalogAdapter`.

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
- **DDL**: all in `app/storage/schema.py`, must stay **dialect-neutral** (no DuckDB casts, no PostgreSQL-specific types) for SQLite portability
- **Web UI**: split into two files — `app/static/admin.html` (Infrastructure, Semantic Layer, Governance, System) and `app/static/user.html` (Discovery, Analysis, Execution). Shared assets: `app/static/shared.css` + `app/static/shared.js`. Gated by `ui.admin_enabled` / `ui.user_enabled` in config.
- **Pydantic models**: FastAPI request bodies in `app/api/models.py`; domain models in `app/models.py`
- **DB path**: defaults to `data/mvp.duckdb` (analytics) + `data/mvp.meta.sqlite` (metadata). Override with `DUCKDB_MVP_DB` env var.
- **Semantic lifecycle**: entities/metrics follow draft → published → deprecated with revision tracking. Publishing increments revision.
- **Sync model**: external catalogs (Trino etc.) are the authority; Factum stores synced snapshots. Sync uses `sync_version` to detect and remove stale objects.
- **Route ordering**: In `app/main.py` / API routers, literal path routes must be registered before parameterized routes.
- **No MCP**: The MCP server/client layer was removed. There is no `factum-mcp` entry point and no `app/mcp_server.py`.

## Key Design Decisions

These decisions guide all future work:

1. **Sessions over one-shot queries** — analysis is stateful; every investigation belongs to a session with goal, constraints, budget, policy.
2. **Semantics over schema** — agents operate on metrics/entities/dimensions, not table names and column guessing.
3. **Typed steps over SQL strings** — the external contract is step-oriented; SQL is an internal compilation target.
4. **Deterministic fact extraction** — evidence is extracted by code wherever possible; LLMs assist with language, not structure.
5. **Pure HTTP API** — MCP layer removed; agents and UIs interact via the FastAPI service directly.
6. **Engine abstraction with implementation honesty** — abstract across engines, but surface engine differences through cost/capability metadata.
7. **Sync-based catalog integration** — Factum syncs snapshots from external catalogs rather than querying them at read time.

## Post-Implementation Checklist

After completing any feature, API change, step type addition, or behaviour change, verify whether the following need updating. Do not skip this check — documentation drift is a first-class bug.

### 1. Web UI (`app/static/`)
- **`admin.html`** — if you added/changed sources, engines, bindings, semantic objects, governance policies, or observability
- **`user.html`** — if you added/changed sessions, steps, plans, evidence display, catalog, or jobs
- **`shared.js` / `shared.css`** — if you changed shared components, status badges, graph rendering, or design tokens

### 2. API models / route docs
- **`app/api/models.py`** — if request or response shapes changed
- **`app/models.py`** — if domain model fields changed
- FastAPI auto-generates `/docs` (Swagger) from Pydantic models — keep models in sync with actual behaviour

### 3. README (`README.md`)
- Update if public-facing behaviour, setup steps, environment variables, or supported step types changed

### 4. Agent docs (all three must stay in sync)
- **`CLAUDE.md`** — Claude Code instructions (this file)
- **`AGENTS.md`** — identical copy of CLAUDE.md; sync manually after every edit
- **`.github/copilot-instructions.md`** — GitHub Copilot instructions; update the relevant section if architecture, conventions, or step types changed

### 5. Factum skill docs (`~/.claude/skills/factum/`)
- **`SKILL.md`** — update if step types, API endpoints, request/response fields, scoping rules, or causal inference behaviour changed
- **`references/planning.md`** — update if plan lifecycle, patch semantics, or execution behaviour changed
- **`references/semantic-layer.md`** — update if entity/metric/mapping CRUD or lifecycle changed
- **`references/governance.md`** — update if policy types, quality rules, or approval flow changed
- **`references/infrastructure.md`** — update if source/engine/binding/routing/sync/jobs/observability changed

> Rule of thumb: if an agent calling the Factum API would get a different result or a 4xx error compared to what the docs say, update the docs first before considering the implementation done.

## Roadmap

Completed:
- Catalog adapters: DuckDB, Trino
- Typed planning with plan IR, validation, dry-run, cost estimates, re-planning
- Evidence engine: multiple observation types, AggregateRowExtractor, provenance tokens, metric resolution via semantic layer, QueryRouter wired into step runners
- SQL dialect translation (DuckDB → Trino), cross-engine federation layer
- Governance enforcement, async jobs, observability, approval hooks

Remaining / future:
- Auth/RBAC
- LLM-backed planning and reflection loop
- Production async job queue (currently sync fallback)
- Streaming step execution
