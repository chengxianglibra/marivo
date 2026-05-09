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
5. Server file stores: `FileEvidenceStore` and `FileAuditLog` reused from local adapters with server directory paths.
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
  evidence_store.py    # Re-export of local FileEvidenceStore with server config
  artifact_store.py    # existing, stays
  audit_log.py         # Re-export of local FileAuditLog with server config
  cache_store.py       # InMemoryCacheStore (simple dict-backed)
  authz.py             # NoopAuthZAdapter
  telemetry.py         # LocalTelemetryAdapter
  runtime_config.py    # TomlRuntimeConfigAdapter
  _legacy_session.py   # Deprecated CRUD bridge (read-only during transition)
```

**FileEvidenceStore and FileAuditLog reuse:** Server mode reuses the local `FileEvidenceStore` and `FileAuditLog` implementations unchanged. The server profile factory configures them with server-appropriate directory paths. No separate server implementation is needed.

The `evidence_store.py` and `audit_log.py` modules in `app/adapters/server/` re-export from `app/adapters/local/`:

```python
# app/adapters/server/evidence_store.py
from app.adapters.local.file_evidence_store import FileEvidenceStore

__all__ = ["FileEvidenceStore"]
```

```python
# app/adapters/server/audit_log.py
from app.adapters.local.file_audit_log import FileAuditLog

__all__ = ["FileAuditLog"]
```

The `MetadataEvidenceStoreAdapter` in the current `wrappers.py` is removed. Server mode uses `FileEvidenceStore` instead, which is the architecture spec's intent (hash-addressed, filesystem-backed).

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
- Concurrent `append_event` calls for the same session may hit the `UNIQUE(session_id, seq)` constraint. The adapter catches `IntegrityError`, re-reads `MAX(seq)`, and retries with the next seq value (max 3 attempts). This prevents silent event loss under concurrent writes.
- All event types from `SessionEvent` are persisted, not just `session_created` / `session_terminated`.

### 3.3 Read path

- `load_events(session_id)`: queries `session_events WHERE session_id = ? ORDER BY seq`, returns a `list[SessionEvent]`. Raises `NotFoundError(SESSION_NOT_FOUND)` when no events exist for the session.
- `list_sessions(owner)`: finds distinct `session_id` values where the `session_created` event has `actor = owner`, then rebuilds each session state using `core.session.rebuild.rebuild_session_state()`.
- `list_sessions_paginated(**kwargs)`: same as `list_sessions` with filtering and pagination applied before rebuild.

### 3.4 step_completed event ownership

After every successful `commit_step_result()` call, the Runtime appends a `step_completed` event to the session event log via `SessionStore.append_event()`. The Runtime is responsible for constructing and appending this event — not the `StepStore` or `ArtifactStore`. The `SqlSessionStore` contract test verifies:

1. A `step_completed` event exists after a step result is committed.
2. `SessionState.updated_at` reflects the `step_completed` event timestamp (not the session creation time).

This contract is added to the `SessionStore` contract test suite and applies uniformly to local and server implementations.

### 3.5 sessions table deprecation

The existing `sessions` CRUD table becomes read-only during 9.1. A `_legacy_session.py` module provides read-only access for migration verification. After 9.1 validates event-sourced reconstruction parity, the `sessions` table is removed.

The `SqlSessionStoreAdapter` CRUD bridge (current `wrappers.py`) moves to `_legacy_session.py` during 9.1 and is deleted before 9.2 closes.

### 3.6 list_sessions_paginated

The paginated listing queries `session_events` for distinct session IDs matching the filter criteria, then rebuilds session state for each. Pagination operates on the session ID set before rebuild to avoid loading all sessions into memory.

For MySQL, the paginated query uses a subquery or window function to find distinct session IDs with the filter criteria applied at the event level, then paginates the resulting ID set.

---

## 4. RoutingDataSource (9.1)

### 4.1 Architecture

```
RoutingDataSource
  ├─ engines: dict[str, EngineAdapter]
  │    ├─ "duckdb"  -> DuckDBEngineAdapter
  │    └─ "trino"   -> TrinoEngineAdapter
  ├─ credential_store: CredentialStore
  └─ registry: DatasourceRegistry
```

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
2. Look up the datasource in `DatasourceRegistry` to determine `source_type`.
3. Select the matching `EngineAdapter` from the `engines` dict.
4. Delegate `execute()` to the selected adapter.
5. If the source type has no registered engine adapter, raise `DomainError(DATASOURCE_UNAVAILABLE)`.

`schema(source_ref)`:

1. Resolve `source_ref.datasource_id` via `DatasourceRegistry`.
2. Select the matching engine adapter.
3. Delegate `schema()` to the selected adapter.

`resolve_tables(table_names, *, session_id)`:

1. Determine which datasources the table names belong to (via registry or query context).
2. Delegate to the appropriate engine adapter's resolve logic.

### 4.4 CredentialStore

`CredentialStore` reads datasource credentials from the metadata DB `datasources` table. It is an internal implementation detail of `RoutingDataSource`, not a new Port.

```python
class CredentialStore:
    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    def get_config(self, datasource_id: str) -> dict[str, Any]:
        """Return connection config for the given datasource."""
        row = self._metadata.query_one(
            "SELECT config_json FROM datasources WHERE datasource_id = ?",
            [datasource_id],
        )
        if row is None:
            raise DomainError(DATASOURCE_UNAVAILABLE, f"Datasource {datasource_id!r} not found")
        return json.loads(row["config_json"])
```

Credential format by source type:

- **DuckDB:** No credentials needed. Config contains `db_path`.
- **Trino:** Config contains `host`, `port`, `user`, `password`, `http_scheme`, `catalog`, `schema`, `request_timeout`.

### 4.5 DatasourceRegistry

`DatasourceRegistry` reads the `datasources` table to map `datasource_id` to `source_type` + `config`. It is a slim read-only version of the current `DatasourceService`, with CRUD operations removed.

```python
class DatasourceRegistry:
    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    def get(self, datasource_id: str) -> DatasourceEntry:
        """Return datasource metadata (source_type, config)."""
        ...

    def list_by_type(self, source_type: str) -> list[DatasourceEntry]:
        """List all datasources of a given type."""
        ...
```

### 4.6 Engine adapters

**DuckDBEngineAdapter** wraps the existing `AnalyticsEngine`:

```python
class DuckDBEngineAdapter:
    def __init__(self, engine: AnalyticsEngine) -> None:
        self._engine = engine

    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...
```

**TrinoEngineAdapter** wraps `TrinoCatalogAdapter` for schema operations and uses Trino DB-API for query execution:

```python
class TrinoEngineAdapter:
    def __init__(self, catalog_adapter: TrinoCatalogAdapter, connection_params: dict) -> None:
        self._catalog = catalog_adapter
        self._params = connection_params

    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...
```

**Optional dependency handling:** The `trino` Python package is an optional dependency. If it is not installed, `TrinoEngineAdapter.execute()` and `schema()` catch `ImportError` and raise `DomainError(DATASOURCE_UNAVAILABLE)` with an actionable message (e.g. "Trino driver not installed; pip install marivo[trino]"). This prevents confusing stack traces when a Trino datasource is configured but the driver is missing.

### 4.7 Extension point

Additional engine adapters (Snowflake, BigQuery) follow the same `EngineAdapter` pattern. Phase 9 defines the interface and ships DuckDB + Trino; future engines register into the `engines` dict in `create_server_runtime()`.

---

## 5. Server File Stores (9.1)

### 5.1 FileEvidenceStore

Server mode uses `FileEvidenceStore` from `app/adapters/local/file_evidence_store.py` with a server-configured directory path. No separate server implementation.

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

### 5.4 Shared SQLAlchemy engine

`create_server_runtime()` creates a single shared SQLAlchemy engine for the metadata DB and passes it to all adapters that need metadata DB access (`SqlModelStoreAdapter`, `SqlSessionStore`, `MetadataArtifactStoreAdapter`, `MetadataStepStoreAdapter`, `CredentialStore`, `DatasourceRegistry`). This prevents N adapters from creating N independent connection pools against the same database, which causes connection pool exhaustion under load.

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

| Field | Type | Purpose |
|-------|------|---------|
| `datasource_registry` | `Any \| None` | `RoutingDataSource` registry access (server-mode only) |

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
| EvidenceStore | FileEvidenceStore | FileEvidenceStore | FileEvidenceStore |
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

## 10. Acceptance Criteria

### 9.1 closes when:

- [ ] `wrappers.py` is split into individual modules; no adapter class remains in `wrappers.py`
- [ ] `SqlSessionStore` uses native event-sourced `session_events` table for SQLite and MySQL
- [ ] `step_completed` event ownership is guaranteed and tested
- [ ] `RoutingDataSource` routes to DuckDB and Trino based on datasource metadata
- [ ] `CredentialStore` and `DatasourceRegistry` read from metadata DB
- [ ] Server mode uses `FileEvidenceStore` and `FileAuditLog` with server directory paths
- [ ] `create_server_runtime()` creates a single shared SQLAlchemy engine for all metadata DB adapters
- [ ] `RuntimePorts` no longer carries `semantic_repository`, `metadata`, `evidence_repos`, `analytics`, `calendar_data_reader`, or `time_axis_metadata_provider`
- [ ] `wire_datasource_svc()` and `wire_semantic_v2_svc()` are removed from `MarivoRuntime`
- [ ] `LogicalQuery` gains `datasource_id: DatasourceId | None = None`
- [ ] `ServerComposition` no longer carries `datasource_service`, `query_router`, or `semantic_v2_service`
- [ ] `_legacy_session.py` provides read-only access to the deprecated `sessions` table
- [ ] All existing tests green

### 9.2 closes when:

- [ ] `test-mysql` extras group exists in `pyproject.toml`
- [ ] `make test-mysql` runs MySQL-backed contract tests via testcontainers
- [ ] `server-contract-tests` CI job has `timeout-minutes: 10`
- [ ] Contract tests cover `SessionStore`, `StepStore`, `ArtifactStore`, and `EvidenceStore`
- [ ] Parity gate is blocking: local/server parity failures fail CI
- [ ] MySQL-backed server adapter contract tests pass in CI
- [ ] `_legacy_session.py` and the `sessions` CRUD table are removed

---

## 11. Phase Boundary

Phase 9 does not:

- Change the MCP tool schema or add new tools
- Introduce new Port Protocols (all Ports are already defined)
- Modify `core/` import rules or domain logic
- Perform the `app/` -> `marivo/` namespace rename (Phase 7)
- Implement AuthZ, Telemetry, or CacheStore product behavior

Phase 7 (Namespace Cutover) follows Phase 9 and is the final mechanical rename pass.
