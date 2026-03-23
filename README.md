# Factum — Agentic Analytics System

Factum is an **agentic analytics system** — not a text-to-SQL tool. It provides stateful analysis sessions, semantic discovery, typed analysis steps, deterministic evidence packaging, and a full HTTP API so agents interact with data at a higher abstraction level than raw SQL.

## What Factum includes

- A dual-backend architecture: SQLite for metadata (control-plane), DuckDB for analytics.
- A **FastAPI service** that exposes:
  - session creation, listing, and step execution
  - typed analysis plans with validation, cost estimation, and execution
  - semantic catalog (entities, metrics, mappings) with draft/published lifecycle
  - source registry with external catalog sync (DuckDB, Trino)
  - engine registry with pluggable analytics engine adapters (DuckDB, Trino)
  - source-engine bindings with priority-based query routing
  - SQL dialect translation (DuckDB → Trino)
  - catalog search, term resolution, and graph traversal
  - evidence graph retrieval with provenance tracking
  - governance policies and quality rules with enforcement
  - async job submission and execution
  - approval workflows for high-risk recommendations
  - observability with structured logging, metrics collection, and timing middleware
  - re-planning on step failure
  - cross-engine query federation
- **Deterministic evidence packaging** that converts SQL results into:
  - observations (5 types: `metric_comparison`, `funnel_drop`, `contribution_shift`, `anomaly_detection`, `aggregate_observation`) extracted by a pluggable **Extractor Registry** (`ComparisonRowExtractor`, `AggregateRowExtractor`, `FunnelExtractor`, `AnomalyExtractor`, `ContributionShiftExtractor`)
  - claims with confidence scoring, `status` (tentative/confirmed/insufficient), and `inference_level` (L0=correlation; L1=temporal precedence; L2=mechanism; L3–L5 reserved). Incremental synthesis runs after every primitive step and promotes claims at `synthesize_findings`
  - evidence edges: base types (`supports`, `contradicts`, `justifies`) + causal layer (`correlates_with`, `temporally_precedes`, `mechanistically_explains`, `eliminates_alternative`, `experimentally_confirms`)
  - recommendations with priority/risk/impact and `causal_basis` metadata (inference level, confounders, suggested validation)
- **Readiness signal** — every primitive step response includes a 5-dimensional `readiness` object (`goal_coverage`, `evidence_sufficiency`, `contradiction_resolution`, `budget_remaining`, `diminishing_returns`) and a `suggested_action` (`continue_exploring`, `synthesize`, `stop`, `resolve_contradiction`), plus `live_claims` (current tentative + confirmed claims). Signals are deterministic facts; the agent decides what to do.
- **Deterministic causal checkers** — `CrossSliceConsistencyChecker` (L0→L1), `TemporalPrecedenceChecker` (L1→L2), `DoseResponseChecker` (bonus justification), `ReversalChecker` (bonus justification) run automatically after each incremental synthesis step to upgrade claim `inference_level` without LLM involvement.
- YAML-driven configuration for sources, engines, bindings, governance, and UI.
- A split web UI: Admin (`/admin`) for infrastructure management, User (`/ui`) for analysis and investigation.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                         # core deps (Python >=3.12)
uvicorn app.main:app --reload
```

The service creates `data/mvp.duckdb` (analytics) and `data/mvp.meta.sqlite` (metadata) on first start and seeds them automatically.

## Configuration

Copy `factum.example.yaml` to `factum.yaml` (or set `FACTUM_CONFIG` env var) to auto-register sources, engines, bindings, and governance policies on startup:

```yaml
sources:
  - name: "Local Demo"
    type: duckdb
    connection: {}
    sync:
      mode: all  # all | by_select | none

engines:
  - name: "Local DuckDB"
    type: duckdb
    connection:
      path: data/mvp.duckdb

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
  quality_rules:
    - name: "Watch data freshness"
      type: freshness
      table: watch_sessions
      threshold: { max_age_hours: 24 }
      severity: warn

ui:
  enabled: true  # enables both /admin and /ui
```

Registration is idempotent — restarting the app won't create duplicates.

### Web UI

The web UI is split into two self-contained pages:

- **Admin UI** (`/admin`) — infrastructure management: Sources, Engines, Bindings, Entities, Metrics, Governance, Approvals, Observability
- **User UI** (`/ui`) — analysis and investigation: Catalog, Sessions, Evidence, Plans, Jobs

Configure independently in `factum.yaml`:

```yaml
ui:
  enabled: true           # master switch (enables both)
  # Or control each independently:
  admin_enabled: true     # override: enable/disable admin UI
  user_enabled: true      # override: enable/disable user UI
```

Both UIs are single self-contained HTML files (vanilla JS, no build tooling). All API calls go to the existing FastAPI endpoints.

Environment variables: `DUCKDB_MVP_DB`, `FACTUM_CONFIG`.

## Example flow

Create a session:

```bash
curl -s http://127.0.0.1:8000/sessions \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"goal": "Investigate why video watch time dropped over the last two weeks."}'
```

List sessions:

```bash
curl -s http://127.0.0.1:8000/sessions | python3 -m json.tool
```

Run a generic step:

```bash
curl -s http://127.0.0.1:8000/sessions/<session_id>/steps/compare_metric \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"params": {"metric_name": "watch_time", "limit": 20}}'
```

Fetch the evidence graph:

```bash
curl -s http://127.0.0.1:8000/sessions/<session_id>/evidence | python3 -m json.tool
```

Draft and execute an analysis plan:

```bash
# Draft
curl -s http://127.0.0.1:8000/sessions/<session_id>/plans \
  -X POST -H "Content-Type: application/json" \
  -d '{"steps": [{"step_type": "compare_metric"}, {"step_type": "profile_table", "dependencies": [0]}]}'

# Validate → Execute (auto-approved if no governance/budget blocks)
curl -s -X POST http://127.0.0.1:8000/sessions/<session_id>/plans/<plan_id>/validate
curl -s -X POST http://127.0.0.1:8000/sessions/<session_id>/plans/<plan_id>/execute
```

## Endpoints

#### Web UI

- `GET /admin` — admin web interface (when admin UI enabled)
- `GET /ui` — user/analytics web interface (when user UI enabled)

#### Core session and steps

- `GET /health`
- `POST /sessions` / `GET /sessions` / `GET /sessions/{session_id}`
- `POST /sessions/{session_id}/steps/{step_type}`
- `GET /sessions/{session_id}/evidence`
- `GET /sessions/{session_id}/planner-context`
- `GET /sessions/{session_id}/reflection-context` — structured evidence-gap summary for agents (readiness, tentative claims, evidence gaps, available step types)

#### Planning

- `POST /sessions/{session_id}/plans` — draft a plan
- `GET /sessions/{session_id}/plans` / `GET .../plans/{plan_id}`
- `PATCH .../plans/{plan_id}`
- `POST .../plans/{plan_id}/validate`
- `POST .../plans/{plan_id}/approve`
- `POST .../plans/{plan_id}/execute`
- `GET .../plans/{plan_id}/explain`
- `POST .../plans/{plan_id}/estimate-costs`
- `GET .../plans/{plan_id}/budget-check`
- `POST .../plans/{plan_id}/patch` — agent-submitted incremental patch (add/modify/skip steps)

#### Source registry

- `POST /sources` / `GET /sources` / `GET /sources/{source_id}`
- `PUT /sources/{source_id}` / `DELETE /sources/{source_id}`
- `POST /sources/{source_id}/sync` / `GET /sources/{source_id}/sync/{job_id}`
- `GET/POST/DELETE /sources/{source_id}/sync/selections`
- `DELETE /sources/{source_id}/sync/selections/{selection_id}`
- `GET /sources/{source_id}/catalog/schemas` / `GET .../catalog/tables`
- `GET /sources/{source_id}/objects`
- `GET /sources/{source_id}/engines` — list engines bound to a source

#### Engine registry

- `POST /engines` / `GET /engines` / `GET /engines/{engine_id}`

#### Source-engine bindings and routing

- `POST /bindings` / `GET /bindings` / `GET /bindings/{binding_id}` / `DELETE /bindings/{binding_id}`
- `POST /routing/resolve` — resolve table names to the best available engine

#### Semantic CRUD

- `POST/GET/PUT /semantic/entities` + `POST .../publish`
- `POST/GET/PUT /semantic/metrics` + `POST .../publish`
- `POST/GET/DELETE /semantic/mappings`

#### Catalog query

- `GET /catalog/search?q=...&type=...`
- `GET /semantic/resolve/{name}` — resolve a business term to semantic + physical assets (includes engine info)
- `GET /catalog/graph?root=...&depth=...`

#### Governance

- `POST/GET/PUT/DELETE /policies`
- `POST/GET/DELETE /quality-rules`
- `POST /governance/check` — enforce governance policies on a query

#### Async jobs

- `POST /jobs` — submit a background job (step or plan execution)
- `GET /jobs` / `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/cancel`

#### Approvals

- `POST /approvals` / `GET /approvals` / `GET /approvals/{request_id}`
- `POST /approvals/{request_id}/approve` / `POST .../reject`
- `POST /sessions/{session_id}/approvals/auto-flag`

#### Observability

- `GET /metrics` — request count, step count, error count, timing statistics

**Supported step types:** `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, `synthesize_findings`

## Architecture

```text
Browser / Agent / HTTP Client
  → FastAPI service (app/main.py → app/api/app_factory.py)
  → Web UI:
      Admin UI (app/static/admin.html) — Sources, Engines, Bindings, Semantic, Governance, Observability
      User UI  (app/static/user.html)  — Catalog, Sessions, Plans, Evidence, Jobs
  → Middleware:
      TimingMiddleware — request timing + metrics collection
  → API routers (app/api/ — one module per domain):
      sessions, planning, sources, engines, routing, semantic, catalog,
      governance, jobs, approvals, metrics, health
  → Service layer:
      SemanticLayerService   — session/step/evidence orchestration
      PlanningService        — plan CRUD, validation, execution, cost estimation
      ReplanningService      — re-planning on step failure
      SourceService          — source registry + adapter factory
      EngineService          — engine registry + analytics engine factory
      BindingService         — source-engine bindings (priority-based)
      QueryRouter            — table name → source → binding → engine resolution
      SemanticService        — entity/metric/mapping CRUD
      CatalogRuntimeService  — search, resolve, planner-context, graph traversal
      GovernanceService      — policy/quality CRUD + enforcement
      JobService             — async job submission + execution
      ApprovalService        — approval request CRUD + auto-flagging
      MetricsCollector       — request/step/error counters
  → Analysis core (app/analysis_core/):
      IR, compiler, executor, primitives, composites, step registry, step runners
  → Execution layer (app/execution/):
      orchestrator, federation, routing_runtime, costing, capabilities, translation
  → Evidence engine (app/evidence_engine/):
      registry (ExtractorRegistry), extractors (comparison, aggregate, funnel, anomaly,
      contribution_shift), factories, pipeline, scoring, synthesizers,
      incremental_synthesizer, causal_checkers, readiness
  → Reflection (app/reflection/):
      context (build_reflection_context)
  → Storage:
      MetadataStore ABC  → SQLiteMetadataStore
      AnalyticsEngine ABC → DuckDBAnalyticsEngine, TrinoAnalyticsEngine
  → Catalog adapters:
      CatalogAdapter ABC → DuckDBCatalogAdapter, TrinoCatalogAdapter
```

## Implementation notes

- Dual-backend: SQLite for metadata (control-plane), DuckDB for analytical queries.
- Source-engine bindings link catalog sources to query engines with priority-based selection.
- The query router resolves table names through `source_objects → source → binding → engine`, supporting multi-source queries when a common engine exists.
- SQL dialect translation (`app/dialect.py`): DuckDB SQL is translated to Trino dialect (casts).
- Evidence packaging produces structured observations, claims, and recommendations rather than free-form SQL results. Facts are extracted deterministically; language models may assist with synthesis but not with fact extraction.
- **Incremental synthesis**: after every primitive step, `IncrementalSynthesizer` creates or updates `tentative` claims keyed by (metric, slice). `synthesize_findings` promotes tentative → `confirmed` or `insufficient` — it does not create claims from scratch.
- **Readiness signal**: every primitive step response includes `readiness` (5 float dimensions in [0, 1]) and `live_claims`. `suggested_action` is a deterministic signal — Factum never auto-triggers next steps.
- **Causal inference levels**: `inference_level` on claims is upgraded deterministically by causal checkers running after each incremental synthesis. L0 = correlation; L1 = temporal precedence; L2 = mechanism. L3–L5 are reserved for experimental/A-B evidence.
- Plan lifecycle: draft → validated → approved → executing → completed/failed. Clean plans are auto-approved; plans with governance/budget blocks require explicit approval. Plans can be patched via `POST .../patch` which resets to draft, applies the patch, and re-validates.
- Session constraints are auto-injected as SQL WHERE filters into `compare_metric`, `sample_rows`, and `aggregate_query` steps.
- Cross-engine federation is supported via `FederationPlanner` and `FederationRuntime` in `app/execution/federation.py`.

## Running tests

```bash
.venv/bin/python3 -m unittest discover -s tests -v    # all ~663 tests
.venv/bin/python3 -m unittest tests.test_storage -v   # single module
.venv/bin/python3 -m unittest tests.test_storage.SQLiteMetadataStoreTests.test_execute_and_query  # single test
```

Uses `unittest` only (no pytest). Tests use `tempfile.TemporaryDirectory` for isolated SQLite/DuckDB files. Integration tests spin up a real FastAPI `TestClient`. Playwright-based E2E tests (`tests/test_ui_playwright.py`) are skipped gracefully when playwright is not installed.
