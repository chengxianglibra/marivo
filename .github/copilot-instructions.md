# OmniDB Copilot Instructions

## Build, run, and test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[hive]"                 # optional Hive Metastore adapter
pip install -e ".[trino]"                # optional Trino analytics adapter

uvicorn app.main:app --reload
omnidb-mcp                               # expects the FastAPI service to already be running

python3 -m unittest discover -s tests -v
python3 -m unittest tests.test_storage -v
python3 -m unittest tests.test_storage.SQLiteMetadataStoreTests.test_execute_and_query
python3 -m unittest tests.test_ui_playwright -v  # after: pip install playwright && playwright install chromium
```

- Tests use `unittest`, not `pytest`.
- Integration tests use FastAPI `TestClient`; storage tests use temporary SQLite/DuckDB files.
- No dedicated lint or formatter command is configured in the repo today.
- Useful environment variables: `DUCKDB_MVP_DB`, `OMNIDB_CONFIG`, `OMNIDB_API_BASE_URL`, `OMNIDB_API_TIMEOUT`, `OMNIDB_MCP_TRANSPORT`.

## High-level architecture

- OmniDB is an agentic analytics system, not a text-to-SQL app. The main abstractions are stateful sessions, semantic objects, typed analysis steps, and evidence graphs.
- `app/main.py` is the composition root. `create_app()` initializes the metadata store and analytics engine, loads YAML config, auto-registers sources/engines/bindings/governance, wires services onto `app.state`, and defines the FastAPI routes inline.
- There are two storage planes:
  - Metadata/control plane in SQLite for sessions, steps, evidence, sources, semantic objects, bindings, plans, policies, jobs, and approvals (`app/storage/schema.py`, `app/storage/metadata.py`, `app/storage/sqlite_metadata.py`).
  - Analytics/query plane behind `AnalyticsEngine` implementations (`app/storage/analytics.py` and engine implementations). The default files are `data/mvp.duckdb` and `data/mvp.meta.sqlite`.
- Catalog flow is layered:
  - External catalogs are read through `CatalogAdapter` implementations and synced into `source_objects` via `SyncEngine`.
  - The semantic layer (`app/semantic.py`) defines entities, metrics, and mappings over those synced physical objects.
  - The runtime layer (`app/service.py`) executes typed steps and workflows, producing artifacts, observations, claims, evidence edges, and recommendations.
- Evidence is meant to be deterministic first. Structured findings are extracted by code (`app/evidence.py`); language-model output should support explanation, not replace evidence structure.
- Query routing is explicit. `BindingService` stores source-to-engine bindings with priority and optional namespace. `QueryRouter` (`app/routing.py`) resolves table names through `source_objects`, intersects candidate engines across sources, chooses the highest-priority common engine, and qualifies names using binding namespace/schema rules.
- Protocol layers are intentionally thin:
  - FastAPI is the source of truth for business logic.
  - `app/mcp_server.py` is a thin MCP wrapper over the HTTP API.
  - `/admin` and `/ui` are static vanilla-JS pages served by FastAPI when enabled by config.
- Planning, governance, jobs, approvals, and observability are active subsystems (`app/planning.py`, `app/governance.py`, `app/jobs.py`, `app/approvals.py`, `app/observability.py`). Treat older guidance that only mentions the earlier MVP layers as incomplete.

## Key conventions

- IDs use short prefixes plus 12 hex characters, e.g. `sess_<id>`, `src_<id>`, `eng_<id>`, `bind_<id>`, `ent_<id>`, `met_<id>`, `map_<id>`, `step_<id>`, `art_<id>`, `obs_<id>`, `claim_<id>`, `rec_<id>`.
- Columns that store structured payloads use a `_json` suffix and are stored as TEXT serialized with `json.dumps` / `json.loads`.
- Timestamps are stored as TEXT. Some tables use SQLite `datetime('now')`; service-layer writes usually use UTC ISO strings.
- Keep metadata DDL dialect-neutral in `app/storage/schema.py`. Do not add engine-specific SQL types or casts there.
- Favor the existing design principles from the repository docs: sessions over one-shot queries, semantics over schema guessing, typed steps over raw SQL interfaces, and deterministic fact extraction over freeform LLM output.
- Semantic entities and metrics use a lifecycle of `draft -> published -> deprecated`; publishing increments `revision`.
- External catalogs are authoritative. Runtime reads from synced snapshots in SQLite rather than querying adapters live on every request.
- Keep protocol and integration layers thin: FastAPI request models live in `app/models.py`, MCP input models live in `app/mcp_server.py`, and adapter/engine abstractions should not absorb domain logic.
- Config is YAML-driven through `omnidb.yaml` or `OMNIDB_CONFIG`. Startup registration is idempotent and resolves bindings by source/engine display name.
- UI files are static HTML/JS under `app/static` with no frontend build step. Respect the existing config gates: `ui.enabled`, `ui.admin_enabled`, and `ui.user_enabled`.
- Tests follow `unittest` patterns with `tempfile.TemporaryDirectory` and FastAPI `TestClient`. Playwright UI tests are optional and skip if Playwright or Chromium is unavailable.
