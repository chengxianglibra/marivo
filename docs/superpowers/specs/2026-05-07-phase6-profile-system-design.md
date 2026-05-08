# Phase 6: Profile System + SemanticLayerService Removal — Design Spec

> **STATUS: SUPERSEDED (2026-05-08)**
>
> This design bundled too much into a single phase (profile factories + `_svc` removal +
> `SemanticLayerService` deletion + `SessionManager` deletion + native event-sourced
> `SqlSessionStore` + 12 server adapter splits + contract test infrastructure). It has been
> replaced by a decomposed structure:
>
> - [Phase 6.1 — Runtime Self-Sufficiency](./2026-05-08-phase6.1-runtime-self-sufficiency-design.md) (current)
> - Phase 6.2 — Server Profile Boundary (TBD)
> - Phase 6.3 — Contract & Parity Tests (TBD)
> - Phase 8 — Production Server Adapters (parent spec §12)
>
> Refer to the new specs. The content below is preserved for historical reference only.

---

**Date:** 2026-05-07
**Status:** Superseded
**Parent spec:** `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`
**Phase:** 6 of 7 (execution order 5)

---

## 1. Overview

Phase 6 delivers the profile system and completes the `SemanticLayerService` removal. After Phase 6, `MarivoRuntime` is fully self-sufficient — no `_svc` proxy, no `SemanticLayerService` dependency. Both local and server profiles construct `MarivoRuntime` from pure port adapters + `CoreEngine`, and all adapter implementations pass contract tests.

**Acceptance criteria** (from parent spec): contract tests pass across all adapter implementations; parity tests run in CI.

**Extended scope** (per team decision): complete `_svc` removal from `MarivoRuntime` and delete `SemanticLayerService` as part of Phase 6. This makes the profile system fully self-sufficient.

### Scope

| In scope | Out of scope |
|----------|-------------|
| `SemanticResolver` port + local/server implementations | Client profile (`create_client_runtime()`) — deferred to Phase 5 |
| Local `ArtifactStore` / `StepStore` adapters | MCP dual-mode changes (Phase 5) |
| All 12 server adapters (split from `wrappers.py`) | `app/` → `marivo/` namespace migration (Phase 7) |
| `create_server_runtime()` profile factory | E2E golden tests / replay tests (Phase 7) |
| `resolve_profile()` selection authority | Full OIDC/RBAC implementation (OidcRbacAuthZ is a stub) |
| Remove `MarivoRuntime._svc` dependency | S3 evidence store implementation (uses DB-backed store) |
| Delete `SemanticLayerService` (3193 lines) | Redis cache store implementation (uses MySQL-backed store) |
| Delete `SessionManager` (20KB) | Session snapshot/compaction (full replay only) |
| Native event-sourced `SqlSessionStore` (MySQL) | OpenTelemetry real integration (OtelTelemetry is a stub) |
| Contract tests for all adapters | Data migration (service not launched, breaking changes OK) |
| Local/server parity tests | |
| testcontainers-python CI infrastructure | |

---

## 2. Sub-phase Sequence

| Sub-phase | Name | Deliverable | Gate |
|-----------|------|-------------|------|
| 6a | Kill `_svc` | `SemanticResolver` port + adapters; local `ArtifactStore`/`StepStore`; all `_svc` methods removed from `MarivoRuntime` | `MarivoRuntime.__init__` takes no `svc`; all existing tests green |
| 6b | Server adapters | All 12 server adapters as individual modules; old `wrappers.py` deleted | Each adapter passes its Port's contract test |
| 6c | Profile factories + delete `SemanticLayerService` | `create_server_runtime()`; updated `create_local_runtime()`; `resolve_profile()`; delete `service.py` | Server profile E2E works; no `from app.service import`; all tests green |
| 6d | Contract tests + parity + CI | Server adapter contract tests; parity tests; testcontainers CI job | `make test-all` green in CI |
| 6e | Native event-sourced `SqlSessionStore` | MySQL `session_events` table; delete `SessionManager`; business rules move to Runtime | Contract tests pass; session lifecycle works end-to-end |

---

## 3. Sub-phase 6a — Kill `_svc`

### 3.1 New Port: `SemanticResolver`

Abstracts the "load model + resolve metric + compile query" pipeline currently in `_svc._resolve_metric_execution_context` and related methods.

```python
# app/ports/semantic_resolver.py
from typing import Protocol
from app.contracts.ids import SessionId, ModelId, RevisionId, UserId
from app.contracts.semantic import SemanticModel
from app.contracts.values import ObserveScope, TimeScope

class SemanticResolver(Protocol):
    def resolve_metric_context(
        self,
        metric_ref: str,
        model_selector: "ModelSelector",
        **kwargs,
    ) -> dict: ...
    def resolve_metric(
        self,
        metric_ref: str,
        model_selector: "ModelSelector",
    ) -> dict: ...
    def compile_step(self, step_ir: dict, context: dict) -> dict: ...
    def resolve_scope_constraint(self, scope: ObserveScope, model: SemanticModel) -> dict: ...
    def build_scoped_query(self, base_query: str, scope_filter: dict) -> dict: ...
    def resolve_windowed_time_axis(self, **kwargs) -> dict: ...
```

**Design rule:** `SemanticResolver` methods accept pre-loaded or pre-resolved inputs where possible. Methods that need model data accept a `model_selector` and load the model internally (the resolver holds a reference to `ModelStore`). Return types are plain dicts in Phase 6 — the resolver serializes core/semantic/ return objects into dicts before returning. Formal typed return objects (e.g. `MetricExecutionContext`, `CompiledQuery`) are Phase 7 work.

### 3.2 `LocalSemanticResolver`

Loads model via `ports.model_store.get()` and delegates to `core/semantic/` pure functions.

```python
# app/adapters/local/local_semantic_resolver.py
class LocalSemanticResolver:
    def __init__(self, model_store: ModelStore) -> None:
        self._model_store = model_store

    def resolve_metric_context(self, metric_ref, model_selector, **kwargs):
        model = self._model_store.get(model_selector)
        if model is None:
            raise NotFoundError(ErrorCode.MODEL_NOT_FOUND, f"Model not found: {model_selector}")
        return metric_resolution.resolve_metric_execution_context(
            metric_ref=metric_ref, model=model, **kwargs
        )

    def compile_step(self, step_ir, context):
        return compiler.compile(step_ir, context)

    # ... other methods delegate to core/semantic/ submodules
```

### 3.3 `ServerSemanticResolver`

Same pattern: loads model from `SqlModelStore` and delegates to the same `core/semantic/` pure functions. The resolver does not call `SemanticLayerService`.

```python
# app/adapters/server/server_semantic_resolver.py
class ServerSemanticResolver:
    def __init__(self, model_store: ModelStore) -> None:
        self._model_store = model_store

    # Same method signatures as LocalSemanticResolver
    # Same delegation to core/semantic/ pure functions
    # Only difference: model comes from SqlModelStore instead of FileModelStore
```

### 3.4 Local `ArtifactStore` and `StepStore`

**`FileArtifactStore`** — stores artifacts as JSON files in `.marivo/artifacts/` (separate from `.marivo/evidence/` to avoid naming collisions). Uses `artifact-<step_id>.json` naming convention.

```python
# app/adapters/local/file_artifact_store.py
class FileArtifactStore:
    def __init__(self, artifacts_dir: Path) -> None:
        self._dir = artifacts_dir

    def insert_artifact(self, artifact: dict) -> str: ...
    def commit_artifact_with_extraction(self, ...) -> dict: ...
    def resolve_artifact_for_ref(self, ref: str) -> dict: ...
    def resolve_artifact_id_for_step(self, session_id, step_id) -> str | None: ...
    def resolve_artifact_with_id(self, artifact_id: str) -> dict: ...
```

**`SqliteStepStore`** — stores step records in `state.db` `steps` table. Append-only.

```python
# app/adapters/local/sqlite_step_store.py
class SqliteStepStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def insert_step(self, step: dict) -> None: ...
```

### 3.5 `MarivoRuntime._svc` Method Migration

| Method | Target |
|--------|--------|
| `resolve_metric_execution_context` | `ports.semantic_resolver.resolve_metric_context()` |
| `resolve_metric` | `ports.semantic_resolver.resolve_metric()` |
| `resolve_metric_table` | `ports.model_store.get()` + core extraction |
| `resolve_metric_dimensions` | `ports.model_store.get()` + core extraction |
| `compile_step` | `ports.semantic_resolver.compile_step()` |
| `resolve_windowed_query_time_axis` | `ports.semantic_resolver.resolve_windowed_time_axis()` |
| `build_scoped_query` | `ports.semantic_resolver.build_scoped_query()` |
| `resolve_scope_constraint_column` | `ports.semantic_resolver.resolve_scope_constraint()` |
| `resolve_engine_for_session` / `resolve_engine` | Direct `ports.data_source` (already done for intent runners) |
| `commit_artifact_with_extraction` | `ports.artifact_store.commit_artifact_with_extraction()` |
| `insert_step` | `ports.step_store.insert_step()` |
| `resolve_artifact_for_ref` | `ports.artifact_store.resolve_artifact_for_ref()` |
| `resolve_artifact_id_for_step` | `ports.artifact_store.resolve_artifact_id_for_step()` |
| `discover_catalog` | `ports.data_source.schema()` directly (remove from Runtime) |
| `insert_artifact` | `ports.artifact_store.insert_artifact()` |

After migration, `MarivoRuntime.__init__` is `__init__(self, ports: RuntimePorts, core: CoreEngine)` with no `svc` parameter.

### 3.6 `RuntimePorts` Update

```python
@dataclass
class RuntimePorts:
    # Existing ports (unchanged)
    model_store: ModelStore
    session_store: SessionStore
    evidence_store: EvidenceStore
    data_source: DataSource
    cache_store: CacheStore
    authz: AuthZ
    audit_log: AuditLog
    telemetry: Telemetry
    runtime_config: RuntimeConfig
    # New ports (no longer optional)
    artifact_store: ArtifactStore       # was Optional, now required
    step_store: StepStore               # was Optional, now required
    semantic_resolver: SemanticResolver  # new
```

---

## 4. Sub-phase 6b — Server Adapters

### 4.1 Server Adapter Implementations

| Port | Adapter | Storage | Key details |
|------|---------|---------|-------------|
| `ModelStore` | `SqlModelStore` | MySQL via SQLAlchemy Core | Direct SQL; owner/visibility filtering; revision-aware reads; integer revision → `RevisionId` string conversion |
| `SessionStore` | `SqlSessionStore` | MySQL `session_events` table | Native event-sourced (implemented in 6e); during 6b/6c a minimal CRUD-backed adapter provides append/load against a temporary `sessions` + `steps` schema |
| `EvidenceStore` | `MetadataEvidenceStore` | MySQL via evidence repositories | Reads/writes from `app/storage/` evidence repos; hash verification on read |
| `DataSource` | `RoutingDataSource` | Routes by source type | Selects DuckDB/Trino/Snowflake/BQ adapter based on model config; delegates `execute()` and `schema()` |
| `CacheStore` | `SqlCacheStore` | MySQL cache table | TTL-based expiration; lazy cleanup on read |
| `AuthZ` | `OidcRbacAuthZ` | Stub | Always returns `allowed=True`. Real OIDC + RBAC implementation deferred |
| `AuditLog` | `CentralizedAuditLog` | MySQL `audit_log` table | Structured audit entries; write failure → log to stderr, never crash |
| `Telemetry` | `OtelTelemetry` | Stub | No-op. Real OpenTelemetry integration deferred |
| `RuntimeConfig` | `ServerRuntimeConfig` | Server config + env vars | Reads from server config file; env var overrides |
| `ArtifactStore` | `SqlArtifactStore` | MySQL | Artifact + step persistence in metadata DB |
| `StepStore` | `SqlStepStore` | MySQL | Step records in metadata DB; append-only |
| `SemanticResolver` | `ServerSemanticResolver` | Composes `SqlModelStore` + `core/semantic/` | Loads model from `SqlModelStore`, delegates to core pure functions |

### 4.2 Adapter Organization

Split `app/adapters/server/wrappers.py` (19KB monolith) into individual modules:

```
app/adapters/server/
  __init__.py
  sql_model_store.py
  sql_session_store.py          # Stub for 6b; native event-sourced in 6e
  metadata_evidence_store.py
  routing_data_source.py
  sql_cache_store.py
  oidc_rbac_authz.py            # Stub: always allowed=True
  centralized_audit_log.py
  otel_telemetry.py             # Stub: no-op
  server_runtime_config.py
  sql_artifact_store.py
  sql_step_store.py
  server_semantic_resolver.py
```

Old `wrappers.py` is deleted.

### 4.3 Type Conversion Boundary

Each server adapter owns bidirectional conversion between `app.contracts.*` domain types and `app.storage.*` / `app.api.models.*` infrastructure types:

- **Read path**: SQL row → `to_domain()` → domain object
- **Write path**: domain object → `from_domain()` → SQL parameters

### 4.4 Error Translation

All server adapters catch infrastructure exceptions and translate them into `DomainError` subclasses:

| Adapter | Infrastructure exception | Domain error |
|---------|------------------------|-------------|
| `SqlModelStore` | `sqlalchemy.exc.OperationalError` (connection) | `DomainError(DATASOURCE_UNAVAILABLE)` |
| `SqlModelStore` | `sqlalchemy.exc.IntegrityError` (constraint) | `ConflictError` or `ValidationError` |
| `SqlSessionStore` | `sqlalchemy.exc.OperationalError` (connection) | `DomainError(DATASOURCE_UNAVAILABLE)` |
| `OidcRbacAuthZ` | N/A (stub) | N/A |
| `CentralizedAuditLog` | Write failure | Log to stderr, never crash |
| `OtelTelemetry` | N/A (stub) | N/A |

---

## 5. Sub-phase 6c — Profile Factories + Delete `SemanticLayerService`

### 5.1 Server Profile Factory

```python
# app/profiles/server.py
@dataclass
class ServerConfig:
    db_url: str
    config_file: Path | None = None

def create_server_runtime(config: ServerConfig) -> MarivoRuntime:
    model_store = SqlModelStore(config.db_url)
    session_store = SqlSessionStore(config.db_url)
    evidence_store = MetadataEvidenceStore(config.db_url)
    data_source = RoutingDataSource(config.db_url)
    cache_store = SqlCacheStore(config.db_url)
    authz = OidcRbacAuthZ()
    audit_log = CentralizedAuditLog(config.db_url)
    telemetry = OtelTelemetry()
    runtime_config = ServerRuntimeConfig(config.config_file)
    artifact_store = SqlArtifactStore(config.db_url)
    step_store = SqlStepStore(config.db_url)
    semantic_resolver = ServerSemanticResolver(model_store)

    ports = RuntimePorts(
        model_store=model_store,
        session_store=session_store,
        evidence_store=evidence_store,
        data_source=data_source,
        cache_store=cache_store,
        authz=authz,
        audit_log=audit_log,
        telemetry=telemetry,
        runtime_config=runtime_config,
        artifact_store=artifact_store,
        step_store=step_store,
        semantic_resolver=semantic_resolver,
    )
    core = CoreEngine()
    return MarivoRuntime(ports, core)
```

**Profile selection guard:** `create_server_runtime()` logs a warning when `MARIVO_DEPLOYMENT=local` is set without an explicit `--profile server` override.

### 5.2 Updated `create_local_runtime()`

```python
# app/profiles/local.py — additions
def create_local_runtime(config: LocalConfig) -> MarivoRuntime:
    marivo_dir = config.workspace_root / ".marivo"
    model_store = FileModelStore(marivo_dir / "models")
    session_store = SqliteSessionStore(marivo_dir / "state.db")
    evidence_store = FileEvidenceStore(marivo_dir / "evidence")
    data_source = create_data_source(config.datasource_type, config.datasource_config)
    cache_store = SqliteCacheStore(marivo_dir / "state.db")
    authz = NoopAuthZ()
    audit_log = FileAuditLog(marivo_dir / "audit.jsonl")
    telemetry = LocalTelemetry(sink=config.telemetry_sink)
    runtime_config = TomlRuntimeConfig(marivo_dir / "marivo.toml")
    artifact_store = FileArtifactStore(marivo_dir / "artifacts")
    step_store = SqliteStepStore(marivo_dir / "state.db")
    semantic_resolver = LocalSemanticResolver(model_store)

    ports = RuntimePorts(
        model_store=model_store,
        session_store=session_store,
        evidence_store=evidence_store,
        data_source=data_source,
        cache_store=cache_store,
        authz=authz,
        audit_log=audit_log,
        telemetry=telemetry,
        runtime_config=runtime_config,
        artifact_store=artifact_store,
        step_store=step_store,
        semantic_resolver=semantic_resolver,
    )
    core = CoreEngine()
    return MarivoRuntime(ports, core)
```

### 5.3 Profile Selection Authority

```python
# app/profiles/resolver.py
class ProfileMode(str, Enum):
    local = "local"
    server = "server"

def resolve_profile(
    explicit_flag: ProfileMode | None = None,
    env_var: str | None = None,
    workspace_config: Path | None = None,
) -> ProfileMode:
    """Resolve profile with priority order from parent spec §7."""
    if explicit_flag:
        return explicit_flag
    if env_var:
        return ProfileMode(env_var)
    if workspace_config and workspace_config.exists():
        # Read [profile] mode from marivo.toml or server.toml
        ...
    return ProfileMode.local  # default
```

### 5.4 `create_runtime()` Dispatcher

```python
# app/runtime/factory.py
def create_runtime(config: LocalConfig | ServerConfig) -> MarivoRuntime:
    if isinstance(config, LocalConfig):
        from app.profiles.local import create_local_runtime
        return create_local_runtime(config)
    elif isinstance(config, ServerConfig):
        from app.profiles.server import create_server_runtime
        return create_server_runtime(config)
    raise ValidationError(f"Unknown config type: {type(config)}")
```

### 5.5 Delete `SemanticLayerService`

1. Verify no imports of `app.service.SemanticLayerService` exist
2. Delete `app/service.py` (3193 lines)
3. Delete `create_runtime_from_service()` from `app/runtime/factory.py`
4. Update `.importlinter` — remove `app.service` from allowed exceptions
5. Verify `make test` + `make typecheck` + `make lint` all green

---

## 6. Sub-phase 6d — Contract Tests + Parity Tests + CI

### 6.1 Contract Test Infrastructure

Server adapter factories added to the parametrize lists:

```python
# tests/contracts/conftest.py
model_store_factories = [
    ("FileModelStore", lambda: FileModelStore(tmp_path / "models")),
    ("SqlModelStore", lambda: SqlModelStore(mysql_db_url)),
]

session_store_factories = [
    ("SqliteSessionStore", lambda: SqliteSessionStore(tmp_path / "state.db")),
    ("SqlSessionStore", lambda: SqlSessionStore(mysql_db_url)),
]
# ... same pattern for all ports
```

### 6.2 MySQL Fixture (testcontainers)

```python
# tests/conftest.py
import pytest
from testcontainers.mysql import MySqlContainer

@pytest.fixture(scope="session")
def mysql_container():
    with MySqlContainer("mysql:8.0") as mysql:
        yield mysql

@pytest.fixture(scope="session")
def mysql_db_url(mysql_container):
    return mysql_container.get_connection_url()
```

### 6.3 Contract Test Files

| Test file | Local adapter | Server adapter |
|-----------|--------------|----------------|
| `test_model_store.py` | FileModelStore | SqlModelStore |
| `test_session_store.py` | SqliteSessionStore | SqlSessionStore |
| `test_evidence_store.py` | FileEvidenceStore | MetadataEvidenceStore |
| `test_data_source.py` | DuckDBDataSource | RoutingDataSource |
| `test_cache_store.py` | SqliteCacheStore | SqlCacheStore |
| `test_authz.py` | NoopAuthZ | OidcRbacAuthZ |
| `test_audit_log.py` | FileAuditLog | CentralizedAuditLog |
| `test_telemetry.py` | LocalTelemetry | OtelTelemetry |
| `test_runtime_config.py` | TomlRuntimeConfig | ServerRuntimeConfig |
| `test_artifact_store.py` | FileArtifactStore | SqlArtifactStore |
| `test_step_store.py` | SqliteStepStore | SqlStepStore |
| `test_semantic_resolver.py` | LocalSemanticResolver | ServerSemanticResolver |

### 6.4 Parity Tests

```python
# tests/parity/test_intent_parity.py
@pytest.mark.parametrize("intent_name", [
    "observe", "compare", "decompose", "detect",
    "correlate", "forecast", "test", "attribute",
    "validate", "diagnose",
])
def test_intent_result_parity(intent_name, local_runtime, server_runtime, sample_model):
    """Same intent + same data → structurally equivalent results."""
    local_session = local_runtime.create_session(goal="parity test", actor=UserId("test"))
    server_session = server_runtime.create_session(goal="parity test", actor=UserId("test"))

    local_result = getattr(local_runtime, intent_name)(local_session, params)
    server_result = getattr(server_runtime, intent_name)(server_session, params)

    assert local_result["status"] == server_result["status"]
    assert local_result["step_type"] == server_result["step_type"]
    assert len(local_result.get("findings", [])) == len(server_result.get("findings", []))
```

Parity scope: structural equivalence — same step types, same finding counts, same proposition structures. Not byte-identical (timestamps, session IDs, evidence hashes differ).

### 6.5 CI Configuration

```yaml
# .github/workflows/test.yml — new job
server-contract-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
    - run: pip install -e ".[test,mysql]"
    - run: make test-contracts-server
    - run: make test-parity
```

### 6.6 Make Targets

```makefile
test-contracts: ## Run local adapter contract tests (no Docker needed)
	.venv/bin/pytest tests/contracts/ -k "not mysql"

test-contracts-server: ## Run server adapter contract tests (requires Docker)
	.venv/bin/pytest tests/contracts/ -k "mysql"

test-parity: ## Run local/server parity tests (requires Docker)
	.venv/bin/pytest tests/parity/

test: test-contracts test-unit test-integration  ## Full test suite (no Docker)
test-all: test test-contracts-server test-parity  ## Full + server tests (Docker)
```

Gate rule: `make test` must pass in every PR. `make test-all` runs in CI on main branch and is required for Phase 6 closure.

### 6.7 New Dependencies

```toml
# pyproject.toml
[project.optional-dependencies]
mysql = ["pymysql", "cryptography"]
test-mysql = ["testcontainers[mysql]"]
```

---

## 7. Sub-phase 6e — Native Event-Sourced `SqlSessionStore`

### 7.1 MySQL Schema

```sql
CREATE TABLE session_events (
    session_id  VARCHAR(36) NOT NULL,
    seq         BIGINT NOT NULL AUTO_INCREMENT,
    event_type  VARCHAR(64) NOT NULL,
    timestamp   DATETIME(6) NOT NULL,
    payload     JSON NOT NULL,
    actor       VARCHAR(255),
    PRIMARY KEY (session_id, seq),
    INDEX idx_session_timestamp (session_id, timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Same schema shape as `SqliteSessionStore`'s `state.db` but on MySQL. `session_events` is the server profile's single source of truth. No old `sessions` table — service not launched, breaking changes OK.

### 7.2 `SqlSessionStore` Implementation

```python
# app/adapters/server/sql_session_store.py
class SqlSessionStore:
    """Native event-sourced session store backed by MySQL."""

    def __init__(self, db_url: str) -> None:
        self._engine = sqlalchemy.create_engine(db_url)
        self._ensure_schema()

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        with self._engine.connect() as conn:
            conn.execute(
                insert(session_events_table),
                {
                    "session_id": session_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp,
                    "payload": json.dumps(event.payload, sort_keys=True),
                    "actor": event.actor,
                },
            )
            conn.commit()

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(session_events_table)
                .where(session_events_table.c.session_id == session_id)
                .order_by(session_events_table.c.seq)
            ).fetchall()
            return [
                SessionEvent(
                    session_id=row.session_id,
                    event_type=row.event_type,
                    timestamp=row.timestamp.isoformat(),
                    payload=json.loads(row.payload),
                    actor=row.actor,
                )
                for row in rows
            ]
```

### 7.3 Event Types

| event_type | When | payload |
|------------|------|---------|
| `session_created` | `create_session()` | `{"goal": str}` |
| `session_terminated` | `terminate_session()` | `{}` |
| `step_completed` | After intent execution | `{"step_id": str, "step_type": str, "metric": str, ...}` |
| `model_published` | Semantic model publish | `{"model_id": int, "name": str}` |

### 7.4 Delete `SessionManager`

1. Verify no remaining imports of `app.session.session_manager.SessionManager`
2. Delete `app/session/session_manager.py` (20KB)
3. Remove `SessionManager` from `AppServices` in `app/api/deps.py`
4. All session lifecycle code goes through `MarivoRuntime` → `ports.session_store` + `rebuild_session_state()`

### 7.5 Business Rules Migration

Business rules that were in `SessionManager` move to `MarivoRuntime` methods:

```python
class MarivoRuntime:
    def terminate_session(self, session_id: SessionId, *, actor: UserId) -> None:
        # AuthZ check (was ownership check in SessionManager)
        decision = self._ports.authz.check(actor, Action("terminate"), ResourceId(session_id))
        if not decision.allowed:
            raise ForbiddenError(...)
        self._ports.session_store.append_event(session_id, SessionEvent(
            session_id=session_id,
            event_type="session_terminated",
            timestamp=utcnow_iso(),
            payload={},
            actor=actor,
        ))
```

The store is a dumb event log. All business logic lives in Runtime.

### 7.6 Contract Test

`SqlSessionStore` added to the parametrize suite alongside `SqliteSessionStore`:

```python
session_store_factories = [
    ("SqliteSessionStore", lambda: SqliteSessionStore(tmp_path / "state.db")),
    ("SqlSessionStore", lambda: SqlSessionStore(mysql_db_url)),
]
```

Same tests: append + load roundtrip, ordering guarantees, append-only enforcement.

---

## 8. Import-Linter Rule Additions

```ini
[importlinter:contract:intents-no-analysis-core]
name = intents/ must not import analysis_core/ or evidence_engine/
type = forbidden
source_modules =
    app.intents
forbidden_modules =
    app.analysis_core
    app.evidence_engine

[importlinter:contract:no-semantic-layer-service]
name = No module may import SemanticLayerService
type = forbidden
source_modules =
    app
forbidden_modules =
    app.service
```

---

## 9. Final Package Structure (Phase 6 complete)

```
app/
  contracts/             # Phase 2, unchanged
  ports/                 # Phase 2 + 6a
    semantic_resolver.py   # NEW
    artifact_store.py      # Already exists
    step_store.py          # Already exists
    ... (all other ports unchanged)
  core/                  # Phase 3, unchanged
  runtime/               # Phase 3 + 6a
    runtime.py             # No _svc reference
    ports.py               # RuntimePorts with all ports required
    factory.py             # create_runtime(config) dispatcher only
  intents/               # Phase 4 (no analysis_core imports after 6c)
  adapters/
    local/                # Phase 4 + 6a
      file_artifact_store.py        # NEW
      sqlite_step_store.py          # NEW
      local_semantic_resolver.py    # NEW
      ... (all other local adapters unchanged)
    server/               # Phase 6b — split from wrappers.py
      sql_model_store.py            # NEW
      sql_session_store.py          # NEW (6e: native event-sourced)
      metadata_evidence_store.py    # NEW
      routing_data_source.py        # NEW
      sql_cache_store.py            # NEW
      oidc_rbac_authz.py            # NEW (stub)
      centralized_audit_log.py      # NEW
      otel_telemetry.py             # NEW (stub)
      server_runtime_config.py      # NEW
      sql_artifact_store.py         # NEW
      sql_step_store.py             # NEW
      server_semantic_resolver.py   # NEW
  profiles/
    local.py              # Updated: adds artifact_store, step_store, semantic_resolver
    server.py             # NEW
    resolver.py           # NEW
  service.py              # DELETED (6c)
  session/
    session_manager.py    # DELETED (6e)
tests/
  contracts/              # Expanded: server adapter factories added
  parity/                 # NEW: local/server behavioral parity
```

---

## 10. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `_svc` removal breaks existing HTTP E2E tests | Medium | High | 6a gate requires all tests green after each method migration; migrate one method at a time |
| `SemanticResolver` port grows too broad | Medium | Medium | Keep methods focused on resolution/compilation only; if it exceeds 6-7 methods, split into `ModelResolver` + `QueryCompiler` |
| `SqlSessionStore` event-sourced implementation loses `SessionManager` business logic | Medium | High | All business rules (ownership, status transitions) move explicitly to `MarivoRuntime`; audit each rule during migration |
| Server adapter contract tests flaky due to MySQL testcontainers | Low | Medium | `session`-scoped container; retry on connection; separate CI job |
| `OidcRbacAuthZ` stub masks missing real implementation | Low | Low | Stub is explicit: class name and docstring make it clear; real implementation is a tracked follow-up |
| `analysis_core/` still imported by intent runners | Low | Medium | Add `intents-no-analysis-core` import-linter rule in 6c |
