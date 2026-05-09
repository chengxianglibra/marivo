---
status: approved
created: 2026-05-09
---

# Phase 9 — Production Server Adapters Design

**Date:** 2026-05-09
**Status:** Approved
**Parent spec:** [`2026-05-06-marivo-platform-architecture-design.md`](./2026-05-06-marivo-platform-architecture-design.md)
**Position:** Phase 9 (Order 7 in migration sequence)
**Predecessor:** [`2026-05-09-phase6.3-contract-parity-tests-design.md`](./2026-05-09-phase6.3-contract-parity-tests-design.md)

---

## 1. Scope

Phase 9 replaces the thin wrapper adapters in `app/adapters/server/wrappers.py` with production-grade, native port implementations. It also establishes mandatory local/server parity gating and MySQL-backed CI.

### Sub-phases

| Sub-phase | Name | Deliverable |
|-----------|------|-------------|
| 9.1 | Adapter Extraction & Native Implementations | Split wrappers.py; native event-sourced `SqlSessionStore`; `RoutingDataSource` with DuckDB + Trino; server file stores; RuntimePorts cleanup |
| 9.2 | CI Infrastructure & Parity Gating | `test-mysql` extras; testcontainers CI; contract test expansion; mandatory parity gate; CI timeout safety |

### 1.1 In scope

1. Split `app/adapters/server/wrappers.py` into individual modules.
2. Native event-sourced `SqlSessionStore` over `session_events` table (SQLite + MySQL).
3. `step_completed` event ownership guarantee in `SqlSessionStore`.
4. `RoutingDataSource` with DuckDB + Trino engine routing, credential store, and datasource registry.
5. Server file stores: `FileAuditLog` reused from local adapter with server directory path; `MetadataEvidenceStoreAdapter.read()` implemented.
6. `RuntimePorts` and `ServerComposition` cleanup: remove infrastructure leakage fields and wire methods.
7. `LogicalQuery` gains `datasource_id` field for engine routing.
8. `test-mysql` extras group in `pyproject.toml`; Make entrypoint for CI.
9. `server-contract-tests` CI job with `timeout-minutes: 10`.
10. Contract test expansion for `SessionStore`, `StepStore`, `ArtifactStore`.
11. Parity matrix promotion from observable to blocking gate.

### 1.2 Out of scope

| Item | Lands in |
|------|----------|
| Redis / Memcached / OpenTelemetry adapters | Future phase |
| OIDC / RBAC / production AuthZ | Phase 8 |
| S3 / object storage evidence store | Future phase |
| Centralized audit sinks | Future phase |
| Snowflake / BigQuery engine adapters | Future phase (extension point designed here) |
| `app/` -> `marivo/` namespace rename | Phase 7 |
| Warm-cache / daemon-mode for local stdio | Post-Phase-7 |

---

## 2. Module Split (9.1)

The current `app/adapters/server/wrappers.py` (822 lines) splits into:

```
app/adapters/server/
  __init__.py
  model_store.py       # SqlModelStoreAdapter
  session_store.py     # NEW: native event-sourced SqlSessionStore
  data_source.py       # NEW: RoutingDataSource
  evidence_store.py    # MetadataEvidenceStoreAdapter (SQL-backed, with read() implementation)
  artifact_store.py    # existing, stays
  audit_log.py         # Re-export of local FileAuditLog with server config
  cache_store.py       # InMemoryCacheStore (simple dict-backed)
  authz.py             # NoopAuthZAdapter
  telemetry.py         # LocalTelemetryAdapter
  runtime_config.py    # TomlRuntimeConfigAdapter
  _legacy_session.py   # DELETED — no legacy bridge needed (Marivo not launched)
```

**FileAuditLog reuse:** Server mode reuses the local `FileAuditLog` implementation unchanged. The server profile factory configures it with a server-appropriate log path.

The `audit_log.py` module in `app/adapters/server/` re-exports from `app/adapters/local/`:

```python
# app/adapters/server/audit_log.py
from app.adapters.local.file_audit_log import FileAuditLog

__all__ = ["FileAuditLog"]
```

The `MetadataEvidenceStoreAdapter` in the current `wrappers.py` moves to `evidence_store.py` with its `read()` method implemented. Server mode continues using SQL-backed evidence storage.

---

## 3. Native Event-Sourced SqlSessionStore (9.1)

### 3.1 Event table schema

```sql
CREATE TABLE session_events (
  event_id     INTEGER PRIMARY KEY AUTOINCREMENT,  -- BIGINT UNSIGNED AUTO_INCREMENT for MySQL
  session_id   TEXT NOT NULL,                       -- VARCHAR(36) for MySQL
  seq          INTEGER NOT NULL,                    -- INT UNSIGNED for MySQL
  event_type   TEXT NOT NULL,                       -- VARCHAR(64) for MySQL
  timestamp    TEXT NOT NULL,                       -- DATETIME for MySQL
  actor        TEXT,                                -- VARCHAR(64) for MySQL
  payload_json TEXT NOT NULL,                       -- JSON for MySQL
  UNIQUE(session_id, seq)
);
CREATE INDEX idx_session_events_sid ON session_events(session_id);
CREATE INDEX idx_session_events_owner ON session_events(event_type, actor);
```

The `idx_session_events_owner` index supports `list_sessions(owner)` without a full table scan. It covers the common query pattern `WHERE event_type = 'session_created' AND actor = ?`.

**DDL strategy:** The core column set and constraints are identical across SQLite and MySQL. The `MetadataStore.initialize()` method handles dialect-specific DDL (type mappings, auto-increment syntax) the same way it does for existing tables.

### 3.2 Append semantics

- Only INSERT operations. No UPDATE or DELETE.
- `seq` is monotonic per `session_id`, starting at 1. The adapter computes `seq = MAX(seq) + 1` within the same transaction as the INSERT.
- Concurrent `append_event` calls for the same session may hit the `UNIQUE(session_id, seq)` constraint. The adapter catches `IntegrityError`, **ROLLBACKs the failed transaction, starts a NEW transaction**, re-reads `MAX(seq)`, and retries with the next seq value (max 3 attempts). The rollback+new-txn is required under MySQL REPEATABLE READ isolation (default) because a same-transaction retry would see the same snapshot and compute the same duplicate seq, causing an infinite loop.
- All event types from `SessionEvent` are persisted, not just `session_created` / `session_terminated`.

### 3.3 Read path

- `load_events(session_id)`: queries `session_events WHERE session_id = ? ORDER BY seq`, returns a `list[SessionEvent]`. Raises `NotFoundError(SESSION_NOT_FOUND)` when no events exist for the session.
- `list_sessions(owner)`: batch-loads all events for matching sessions in a single query, then groups by `session_id` in Python and rebuilds each session state. This avoids the N+1 query pattern (1 query per session for events):
  ```sql
  SELECT * FROM session_events
  WHERE session_id IN (
    SELECT DISTINCT session_id FROM session_events
    WHERE event_type = 'session_created' AND actor = ?
  )
  ORDER BY session_id, seq
  ```
- `list_sessions_paginated(**kwargs)`: same as `list_sessions` with filtering and pagination applied before rebuild.

### 3.4 step_completed event ownership

After every successful `commit_step_result()` call, the Runtime appends a `step_completed` event to the session event log via `SessionStore.append_event()`. The Runtime is responsible for constructing and appending this event — not the `StepStore` or `ArtifactStore`. The `SqlSessionStore` contract test verifies:

1. A `step_completed` event exists after a step result is committed.
2. `SessionState.updated_at` reflects the `step_completed` event timestamp (not the session creation time).

This contract is added to the `SessionStore` contract test suite and applies uniformly to local and server implementations.

### 3.5 sessions table removal

The `sessions` CRUD table and its `SqlSessionStoreAdapter` are removed. Downstream tables that reference `sessions` via foreign keys (`findings`, `plans`, etc.) have their FK constraints removed. Under MySQL, FK constraints are enforced; under SQLite they are advisory. Since Marivo has not launched, no migration of existing data is needed — the DDL is changed directly.

### 3.6 list_sessions_paginated

The paginated listing queries `session_events` for distinct session IDs matching the filter criteria, then rebuilds session state for each. Pagination operates on the session ID set before rebuild to avoid loading all sessions into memory.

For MySQL, the paginated query uses a subquery or window function to find distinct session IDs with the filter criteria applied at the event level, then paginates the resulting ID set.

---

## 4. RoutingDataSource (9.1)

### 4.1 Architecture

```
RoutingDataSource
  ├─ engine_cache: dict[DatasourceId, AnalyticsEngine]  # lazy, per-datasource
  ├─ registry: DatasourceRegistry  # reuses app.registry.datasource_registry
  └─ routing_runtime: RoutingRuntime  # for resolve_tables
```

`RoutingDataSource` reuses the existing `DatasourceRegistry` at `app/registry/datasource_registry.py` for datasource metadata lookup and `build_analytics_engine()` for engine construction. No new registry or credential store classes are needed.

### 4.2 LogicalQuery extension

`LogicalQuery` gains an optional `datasource_id` field:

```python
class LogicalQuery(BaseModel):
    sql: str
    params: dict[str, Any] = {}
    datasource_id: DatasourceId | None = None  # Phase 9 addition
```

When `datasource_id` is `None`, `RoutingDataSource` routes to the default DuckDB engine. This is a contracts change but keeps routing explicit at the query level.

### 4.3 Engine routing

`execute(query)`:

1. Read `query.datasource_id`. If `None`, route to the default DuckDB engine.
2. Look up or create the `AnalyticsEngine` for the datasource via the per-datasource engine cache:
   a. If `engine_cache[datasource_id]` exists, use it.
   b. Otherwise, call `registry.build_analytics_engine(datasource_id)` to construct one, store it in the cache, and use it.
3. Execute the query via the engine's `query_rows()`.
4. Wrap any engine exception in `DomainError(QUERY_EXECUTION_FAILED)`.
5. If the datasource_id is not found in the registry, raise `DomainError(DATASOURCE_UNAVAILABLE)`.

**Engine cache eviction:** The per-datasource cache is unbounded in Phase 9. A TODO is filed to add LRU eviction when session-level auth requires session-scoped cache keys. The cache key currently uses `DatasourceId` only; when session-level Trino auth lands, the key will need `(DatasourceId, session_id)`.

`schema(source_ref)`:

1. Resolve `source_ref.datasource_id` via `DatasourceRegistry.get_adapter()`.
2. Delegate to the catalog adapter's `list_columns()` method.
3. Convert to `SourceSchema`.

`resolve_tables(table_names, *, session_id)`:

1. Delegate to the held `RoutingRuntime.resolve_tables()`.

### 4.4 Engine adapters

`RoutingDataSource` does not define separate engine adapter classes. Instead, it uses the existing `AnalyticsEngine` hierarchy directly:

- **DuckDB:** `DuckDBAnalyticsEngine` from `app/storage/duckdb_analytics.py`
- **Trino:** `TrinoAnalyticsEngine` from `app/storage/trino_analytics.py`

Both are constructed via `DatasourceRegistry.build_analytics_engine(datasource_id)`, which uses the factory in `app/registry/factories.py`. This factory already handles connection parameter extraction, catalog adapter construction, and optional dependency handling.

**Optional dependency handling:** The `trino` Python package is an optional dependency. `TrinoAnalyticsEngine._connect()` uses `import_module("trino.dbapi")` which raises `ImportError` if trino is not installed. `RoutingDataSource.execute()` catches `ImportError` from the engine and raises `DomainError(DATASOURCE_UNAVAILABLE)` with an actionable message (e.g. "Trino driver not installed; pip install marivo[trino]").

### 4.5 Extension point

Additional engine types (Snowflake, BigQuery) follow the same `AnalyticsEngine` + `build_analytics_engine()` pattern. Phase 9 ships DuckDB + Trino; future engines register into the `DatasourceRegistry` via `factories.py` and the engine cache handles them automatically.

---

## 5. Server File Stores (9.1)

### 5.1 EvidenceStore — server keeps SQL-backed adapter

Server mode continues using the SQL-backed evidence adapter (`MetadataEvidenceStoreAdapter` in `wrappers.py`, moving to `evidence_store.py` after split). `FileEvidenceStore` remains local-only.

**Why not switch to FileEvidenceStore:** The current adapter writes canonical findings, propositions, and assessments to multiple SQL tables (`findings`, `propositions`, `assessments`, etc.). Downstream code queries these SQL tables directly via repository classes (`FindingRepository`, `AssessmentRepository`, etc.). `FileEvidenceStore` only stores `Evidence` blobs — it cannot serve SQL queries by session/type. Switching would break the evidence pipeline.

The `MetadataEvidenceStoreAdapter.read()` method (currently `NotImplementedError`) will be implemented as part of Phase 9 to complete the `EvidenceStore` contract. The implementation reconstructs an `Evidence` object from the SQL tables using the repository classes.

### 5.2 FileAuditLog

Server mode uses `FileAuditLog` from `app/adapters/local/file_audit_log.py` with a server-configured log path. No separate server implementation.

### 5.3 ServerConfig update

```python
@dataclass
class ServerConfig:
    marivo_config: MarivoConfig
    db_path: Path | str | None = None
    metadata_store: MetadataStore | None = None
    analytics_engine: AnalyticsEngine | None = None
    # Phase 9 additions:
    file_store_dir: Path | str | None = None   # evidence dir; default from config
    audit_dir: Path | str | None = None         # audit log dir; default from config
```

When `file_store_dir` or `audit_dir` is `None`, the server profile reads the path from `marivo_config` or uses a sensible default relative to the metadata store location.

### 5.4 Shared MetadataStore instance

`create_server_runtime()` creates a single shared `MetadataStore` instance for the metadata DB and passes it to all adapters that need metadata DB access (`SqlModelStoreAdapter`, `SqlSessionStore`, `MetadataArtifactStoreAdapter`, `MetadataStepStoreAdapter`, `CredentialStore`, `DatasourceRegistry`). This prevents N adapters from creating N independent connection pools against the same database, which causes connection pool exhaustion under load. The `MetadataStore` manages its own connection pool (via `LifoQueue` for SQLite, via PyMySQL for MySQL); no separate SQLAlchemy engine is needed.

### 5.5 Credential security note

`CredentialStore` reads datasource credentials from `datasources.config_json` in the same plaintext format used by the current `DatasourceService`. Phase 9 does not change the credential storage format. Credential encryption is a separate concern for a future phase.

---

## 6. RuntimePorts Cleanup (9.1)

### 6.1 Removed fields

The following fields are removed from `RuntimePorts` because they are internal implementation details of adapters, not port contracts:

| Field | Reason |
|-------|--------|
| `semantic_repository` | Internal to `SqlModelStoreAdapter` |
| `semantic_resolver` | Unused |
| `metadata` | Internal to multiple adapters |
| `evidence_repos` | Internal to `MetadataEvidenceStoreAdapter` (now removed) |
| `analytics` | Internal to `DataSourceAdapter` (now `RoutingDataSource`) |
| `calendar_data_reader` | Unused |
| `time_axis_metadata_provider` | Unused |

### 6.2 Added fields

No new fields are added to `RuntimePorts`. The `datasource_registry` is internal to `RoutingDataSource`, not a port concern.

### 6.3 Removed methods

- `MarivoRuntime.wire_datasource_svc()` — adapters are self-sufficient after Phase 9.
- `MarivoRuntime.wire_semantic_v2_svc()` — adapters are self-sufficient after Phase 9.

### 6.4 ServerComposition cleanup

`ServerComposition` currently carries `datasource_service`, `query_router`, and `semantic_v2_service` fields. After Phase 9, these are internal to adapters and not needed on the composition object. The composition is simplified to:

```python
@dataclass
class ServerComposition:
    runtime: MarivoRuntime
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    resolved_analytics_path: Path | str
```

The `datasource_service`, `query_router`, and `semantic_v2_service` fields are removed. Callers that currently access these through `ServerComposition` must go through the runtime's port interfaces instead.

### 6.5 LogicalQuery contract change

`LogicalQuery` gains `datasource_id: DatasourceId | None = None`. This is a contracts-level change that affects the `DataSource` port. All existing call sites that construct `LogicalQuery` without `datasource_id` continue to work because the field defaults to `None` (routed to DuckDB).

### 6.6 Local SqliteSessionStore schema harmonization

The local `SqliteSessionStore` at `app/adapters/local/sqlite_session_store.py` is updated to use the same schema as the server `SqlSessionStore`. Changes:

1. Add `event_id INTEGER PRIMARY KEY AUTOINCREMENT` column.
2. Change from `PRIMARY KEY (session_id, seq)` to `UNIQUE(session_id, seq)`.
3. Rename `payload` column to `payload_json`.
4. Update index from `(actor, event_type)` to `(event_type, actor)`.

This ensures both local and server adapters share identical DDL, enabling shared contract tests and eliminating developer confusion. Since Marivo has not launched, no migration of existing databases is needed — the schema is changed directly.

### 6.7 Atomicity of step + event writes

When a step result is committed (`commit_step_result()`) and a `step_completed` event is appended, both operations should happen in the same database transaction when using a shared metadata store connection. If the event append fails after the step/artifact commit, the session state is inconsistent. The `SqlSessionStore.append_event()` and step/artifact commits must share a transaction boundary.

---

## 7. CI Infrastructure (9.2)

### 7.1 pyproject.toml extras

```toml
[project.optional-dependencies]
test-mysql = ["testcontainers[mysql]"]
```

This is separate from the existing `mysql = ["PyMySQL>=1.1"]` group. The `test-mysql` group is for CI test execution only; `mysql` is the runtime driver dependency.

### 7.2 Make entrypoint

```makefile
test-mysql:
	pip install -e ".[mysql,test-mysql]"
	pytest tests/contracts/ -m mysql
```

CI uses `make test-mysql` rather than raw `pip install` commands in CI yaml.

### 7.3 CI job: server-contract-tests

```yaml
server-contract-tests:
  timeout-minutes: 10
  steps:
    - uses: actions/checkout@v4
    - run: make test-mysql
    - run: pytest tests/contracts/ -m "not mysql"  # SQLite server contract tests
    - run: pytest tests/contracts/ -m mysql          # MySQL server contract tests
    - run: pytest tests/contracts/test_parity.py     # Mandatory parity gate
```

The explicit `timeout-minutes: 10` prevents a hung testcontainer from burning a runner for hours.

### 7.4 Contract test expansion

Phase 6.3 covers `DataSource` and `ModelStore` only. Phase 9 adds:

| Port | Contract cases |
|------|----------------|
| `SessionStore` | append_event, load_events, list_sessions, step_completed guarantee, NotFoundError for missing sessions |
| `StepStore` | insert_step, list_steps |
| `ArtifactStore` | insert_artifact, commit_artifact_with_extraction, resolve_artifact |
| `EvidenceStore` | FileEvidenceStore contract (already covered locally; server reuses same adapter) |

---

## 8. Parity Gating (9.2)

### 8.1 Parity matrix

| Port | Local adapter | Server adapter (SQLite) | Server adapter (MySQL) |
|------|--------------|------------------------|------------------------|
| ModelStore | FileModelStore | SqlModelStoreAdapter | SqlModelStoreAdapter |
| SessionStore | SqliteSessionStore | SqlSessionStore (event-sourced) | SqlSessionStore (event-sourced) |
| DataSource | DuckDBDataSource | RoutingDataSource (DuckDB) | RoutingDataSource (DuckDB) |
| EvidenceStore | FileEvidenceStore | MetadataEvidenceStoreAdapter | MetadataEvidenceStoreAdapter |
| AuditLog | FileAuditLog | FileAuditLog | FileAuditLog |
| StepStore | SqliteStepStore | MetadataStepStoreAdapter | MetadataStepStoreAdapter |
| ArtifactStore | — | MetadataArtifactStoreAdapter | MetadataArtifactStoreAdapter |

### 8.2 Parity rules

Parity checks:

- Same case name
- Same success or failure shape
- Same `ErrorCode` when a failure is expected
- Same stable contract fields in successful results

Parity does not check:

- Internal SQL text
- File layout
- Cache strategy
- Adapter-specific helper methods
- Transport details

### 8.3 Blocking gate

Phase 6.3's parity skeleton is "observable but non-blocking." Phase 9.2 promotes it to a blocking gate: any local/server parity failure is a CI failure. The parity test suite must pass for Phase 9 to close.

Adapter-specific edge cases (behavior not part of the port contract) remain in adapter-specific tests and are not enforced by the parity gate.

---

## 9. Placeholder Ports

Phase 9 does not introduce product behavior for `Telemetry`, `CacheStore`, or `AuthZ`. These remain placeholder Ports:

- `Telemetry`: `LocalTelemetryAdapter` (no-op)
- `CacheStore`: `InMemoryCacheStore` (simple dict-backed, no Redis)
- `AuthZ`: `NoopAuthZAdapter` (always allows)

Phase 9 must not introduce Redis, OpenTelemetry, OIDC/RBAC, or production authorization behavior.

---

## 10. Test Coverage Specification

### 10.1 SessionStore contract test cases

| Case | Input | Expected |
|------|-------|----------|
| `append_and_load` | Append session_created + step_completed, load events | Returns both events in seq order |
| `not_found` | Load events for non-existent session | Raises `NotFoundError(SESSION_NOT_FOUND)` |
| `owner_isolation` | Create sessions for two owners, list by one | Returns only the owned sessions |
| `event_ordering` | Append 3 events, load | Returns events in seq order |
| `concurrent_retry` | Two threads append to same session simultaneously | Both events stored, no IntegrityError leaked |
| `step_completed_guarantee` | Commit step result, then load session events | step_completed event exists with correct timestamp |
| `other_event_types` | Append non-created/terminated event | Event persisted in session_events (not silently dropped) |

### 10.2 RoutingDataSource contract test cases

| Case | Input | Expected |
|------|-------|----------|
| `duckdb_default` | `LogicalQuery(datasource_id=None)` | Routes to DuckDB engine |
| `duckdb_explicit` | `LogicalQuery(datasource_id="ds_duckdb")` | Routes to DuckDB engine |
| `trino_routing` | `LogicalQuery(datasource_id="ds_trino")` | Routes to Trino engine |
| `unknown_datasource` | `LogicalQuery(datasource_id="ds_missing")` | Raises `DomainError(DATASOURCE_UNAVAILABLE)` |
| `trino_not_installed` | Trino datasource configured, trino package missing | Raises `DomainError(DATASOURCE_UNAVAILABLE)` with install instructions |
| `resolve_tables` | `resolve_tables(["table1"])` | Delegates to RoutingRuntime |

### 10.3 LogicalQuery.datasource_id test

| Case | Input | Expected |
|------|-------|----------|
| `default_none` | `LogicalQuery(sql="SELECT 1")` | `datasource_id` is `None` |
| `explicit_id` | `LogicalQuery(sql="SELECT 1", datasource_id="ds_abc")` | `datasource_id` is `"ds_abc"` |

### 10.4 list_sessions_paginated test

| Case | Input | Expected |
|------|-------|----------|
| `basic_pagination` | 30 sessions, limit=10 | Returns 10 items + next_page_token |
| `filter_by_status` | Mixed open/closed sessions, status="closed" | Returns only closed sessions |
| `empty_page` | No matching sessions | Returns empty items, no next_page_token |

### 10.5 Parity tests

The existing `test_parity.py` pattern is extended to cover:

| Port | Local adapter | Server adapter |
|------|--------------|----------------|
| SessionStore | SqliteSessionStore | SqlSessionStore (event-sourced) |
| DataSource | DuckDBDataSource | RoutingDataSource (DuckDB only) |
| EvidenceStore | FileEvidenceStore | MetadataEvidenceStoreAdapter |
| StepStore | SqliteStepStore | MetadataStepStoreAdapter |
| AuditLog | FileAuditLog | FileAuditLog (same adapter) |

Parity failures are blocking (§8.3).

---

## 11. Acceptance Criteria

### 9.1 closes when:

- [ ] `wrappers.py` is split into individual modules; no adapter class remains in `wrappers.py`
- [ ] `SqlSessionStore` uses native event-sourced `session_events` table for SQLite and MySQL
- [ ] `append_event` retries on UNIQUE violation with ROLLBACK + new transaction (max 3 attempts)
- [ ] `list_sessions` uses batch-load (single subquery + Python grouping) instead of N+1 queries
- [ ] `step_completed` event ownership is guaranteed and tested
- [ ] `RoutingDataSource` routes queries to per-datasource cached engines via `DatasourceRegistry.build_analytics_engine()`
- [ ] `RoutingDataSource.resolve_tables()` delegates to `RoutingRuntime`
- [ ] Server mode uses `MetadataEvidenceStoreAdapter` with implemented `read()` method
- [ ] Server mode uses `FileAuditLog` with server directory path
- [ ] `create_server_runtime()` creates a single shared MetadataStore instance for all metadata DB adapters (no N independent connection pools)
- [ ] `RuntimePorts` no longer carries `semantic_repository`, `metadata`, `evidence_repos`, `analytics`, `calendar_data_reader`, or `time_axis_metadata_provider`; no `datasource_registry` field added
- [ ] `wire_datasource_svc()` and `wire_semantic_v2_svc()` are removed from `MarivoRuntime`
- [ ] `LogicalQuery` gains `datasource_id: DatasourceId | None = None`
- [ ] `ServerComposition` no longer carries `datasource_service`, `query_router`, or `semantic_v2_service`
- [ ] `_legacy_session.py` does not exist — no legacy bridge needed
- [ ] Local `SqliteSessionStore` schema harmonized with server schema (event_id, payload_json, UNIQUE constraint)
- [ ] FK constraints referencing `sessions` table removed from DDL
- [ ] All existing tests green

### 9.2 closes when:

- [ ] `test-mysql` extras group exists in `pyproject.toml`
- [ ] `make test-mysql` runs MySQL-backed contract tests via testcontainers
- [ ] `server-contract-tests` CI job has `timeout-minutes: 10`
- [ ] Contract tests cover `SessionStore`, `StepStore`, `ArtifactStore`, and `EvidenceStore` (cases in §10)
- [ ] Parity gate is blocking: local/server parity failures fail CI
- [ ] MySQL-backed server adapter contract tests pass in CI
- [ ] `_legacy_session.py` does not exist — never created since no legacy bridge is needed
- [ ] `sessions` table DDL is removed from schema.py

---

## 12. Phase Boundary

Phase 9 does not:

- Change the MCP tool schema or add new tools
- Introduce new Port Protocols (all Ports are already defined)
- Modify `core/` import rules or domain logic
- Perform the `app/` -> `marivo/` namespace rename (Phase 7)
- Implement AuthZ, Telemetry, or CacheStore product behavior

Phase 7 (Namespace Cutover) follows Phase 9 and is the final mechanical rename pass.
