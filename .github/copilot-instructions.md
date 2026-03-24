# Factum Copilot Instructions

## Build, run, and test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[trino]"              # optional Trino engine/adapter support

uvicorn app.main:app --reload
```

Run tests with the virtualenv interpreter:

```bash
.venv/bin/pytest                                                                              # all tests (parallel)
.venv/bin/pytest tests/test_storage.py -v                                                     # single module
.venv/bin/pytest tests/test_storage.py::SQLiteMetadataStoreTests::test_execute_and_query -v  # single test
.venv/bin/pytest tests/test_ui.py -v                                                          # UI tests
.venv/bin/pytest tests/test_ui.py::UIBothEnabledTests::test_admin_returns_html -v            # single test
```

- Tests are written with `unittest.TestCase` and collected by pytest.
- Integration-style tests use FastAPI `TestClient`; storage tests use temporary SQLite/DuckDB files.
- No dedicated lint or formatter command is configured in this repository.
- Useful environment variables: `DUCKDB_MVP_DB`, `FACTUM_CONFIG`.

## High-level architecture

- Factum is an agentic analytics system, not a text-to-SQL app. The primary abstractions are stateful sessions, semantic objects, typed analysis steps, plans, and evidence graphs.
- `app/main.py` is only the ASGI entrypoint. The real composition root is `app/api/app_factory.py:create_app()`, which initializes storage, loads YAML config, auto-registers sources/engines/bindings/governance, wires services onto `app.state`, installs middleware, includes API routers, and registers the static UI.
- There are two storage planes:
  - Metadata/control plane in SQLite for sessions, steps, evidence, sources, semantic objects, bindings, plans, policies, jobs, and approvals (`app/storage/schema.py`, `app/storage/metadata.py`, `app/storage/sqlite_metadata.py`).
  - Analytics/query plane behind `AnalyticsEngine` implementations. DuckDB is the default engine, with optional Trino support (`app/storage/analytics.py`, `app/storage/duckdb_analytics.py`, `app/storage/trino_analytics.py`).
- FastAPI routers in `app/api/` are thin transport layers. Most business logic lives in services: `SemanticLayerService` (`app/service.py`), `PlanningService` (`app/planning.py`), `SourceService` (`app/sources.py`), `EngineService` (`app/engines.py`), `BindingService` (`app/bindings.py`), `QueryRouter` (`app/routing.py`), `SemanticService` (`app/semantic.py`), `CatalogRuntimeService` (`app/semantic_runtime/`), `GovernanceService` (`app/governance.py`), `JobService` (`app/jobs.py`), and `ApprovalService` (`app/approvals.py`).
- External catalogs are not queried live on every request. `SyncEngine` syncs them into `source_objects`, and runtime search/resolution/routing works from that synced snapshot in SQLite.
- Step execution is typed rather than freeform. The taxonomy lives in `app/analysis_core/primitives.py`, primitive runners live under `app/analysis_core/step_runners/`, and composite/synthesis flows feed the evidence engine.
- Evidence packaging is a core design concept: sessions -> steps -> artifacts -> observations -> claims -> evidence edges -> recommendations. Deterministic extraction lives under `app/evidence_engine/`; language generation should explain or synthesize, not invent evidence structure.
- Query routing is explicit. `BindingService` stores source-to-engine bindings with priority and optional namespace. `QueryRouter` resolves table names through `source_objects`, intersects candidate engines across sources, chooses the highest-priority common engine, and qualifies names using binding namespace/schema rules.
- The web UI has no frontend build step. `app/static/admin.html` and `app/static/user.html` are static vanilla-JS pages served when enabled by config.

## Key conventions

- IDs use short prefixes plus 12 hex characters, e.g. `sess_<id>`, `src_<id>`, `eng_<id>`, `bind_<id>`, `ent_<id>`, `met_<id>`, `map_<id>`, `step_<id>`, `art_<id>`, `obs_<id>`, `claim_<id>`, `rec_<id>`.
- Columns that store structured payloads use a `_json` suffix and are stored as TEXT with `json.dumps` / `json.loads`.
- Timestamps are stored as TEXT. Some tables use SQLite `datetime('now')`; service-layer writes commonly use UTC ISO strings.
- Keep metadata DDL dialect-neutral in `app/storage/schema.py`. Do not add engine-specific SQL types or casts there.
- Prefer the repository’s core design principles when adding features: sessions over one-shot queries, semantics over schema guessing, typed steps over raw SQL interfaces, and deterministic fact extraction over freeform LLM output.
- Semantic entities and metrics use a lifecycle of `draft -> published -> deprecated`; publishing increments `revision`.
- Supported step types are `compare_metric`, `profile_table`, `sample_rows`, `aggregate_query`, and `synthesize_findings`. Session constraints are injected into the executable query steps.
- External catalogs are authoritative. Factum syncs snapshots into SQLite and resolves runtime behavior from those snapshots rather than re-reading adapters during every request.
- Config is YAML-driven through `factum.yaml` or `FACTUM_CONFIG`. Startup registration is intended to be idempotent and resolves bindings by source/engine display name.
- UI files live under `app/static` with no build tooling. Respect the config gates `ui.enabled`, `ui.admin_enabled`, and `ui.user_enabled`.
- FastAPI request models live in `app/api/models.py`; broader domain models live in `app/models.py`. Keep API transport code thin and domain logic in the service/runtime layers.
- Tests are written with `unittest.TestCase`, collected by pytest, and use `tempfile.TemporaryDirectory` + FastAPI `TestClient`.
- Route registration order matters in FastAPI routers: literal paths should be declared before parameterized routes.
