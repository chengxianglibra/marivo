# Phase 4: Local Embedded Runtime — Design Spec

**Date:** 2026-05-07
**Status:** Draft
**Parent spec:** `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`
**Phase:** 4 of 7 (execution order 4)

---

## 1. Overview

Phase 4 delivers a working local embedded runtime: an MCP stdio process that directly instantiates `MarivoRuntime` in-process (no HTTP), backed by a new `.marivo/` directory layout with local adapters. This phase also completes the intent method migration from `svc.run_intent` to `(core, ports)` runners, removing `MarivoRuntime`'s dependency on `SemanticLayerService`.

**Acceptance criteria** (from parent spec): an agent + Marivo can finish a sample analysis end-to-end in local mode against `.marivo/`. MCP session-lifetime behavior is documented and tested.

### Scope

| In scope | Out of scope |
|----------|-------------|
| All 9 local adapters with contract tests | Server adapters (`SqlModelStore(mysql)`, `S3EvidenceStore`, etc.) |
| `create_local_runtime()` factory | `create_server_runtime()` / `create_client_runtime()` factories |
| `marivo init` CLI command | `marivo doctor`, `marivo profile`, `marivo push/pull` |
| New `.marivo/` directory layout (`marivo.toml`, `state.db`, `models/`, `evidence/`) | Auto-migration from `marivo.yaml` + `metadata.sqlite` |
| Intent migration: Runtime → `(core, ports)` runners | Removing `SemanticLayerService` entirely (Phase 6) |
| MCP stdio embedded mode | HTTP MCP transport changes |
| `marivo-mcp[local]` optional dependency | Merging `marivo-mcp/` into `app/transports/mcp/` (Phase 7) |

---

## 2. Sub-phase Sequence (Approach A: Bottom-Up Layered)

| Sub-phase | Name | Deliverable | Gate |
|-----------|------|-------------|------|
| 4a | Local Adapters | All 9 local adapter implementations + contract tests | Contract tests pass for each Port; existing tests green |
| 4b | Intent Migration | `MarivoRuntime` intent methods call `(core, ports)` runners directly; no `svc` dependency | All existing E2E tests green; `MarivoRuntime.__init__` no longer accepts `svc` |
| 4b-1 | CoreEngine cleanup | Pure methods to `core/`; I/O proxies removed; CoreEngine svc-free | `CoreEngine()` takes no `svc`; existing tests green |
| 4b-2 | Intent runner migration | All intent runners use `ports.*` directly; `execute_compiled` → `ports.data_source` | Each runner migrated; E2E tests green per runner |
| 4b-3 | Runtime lifecycle | Session methods use `ports.session_store` directly; `svc` removed from Runtime | All E2E tests green |
| 4c | Factory + Init + Layout | `create_local_runtime()`, `marivo init` CLI, new `.marivo/` layout | `marivo init` creates valid layout; `create_local_runtime()` produces a working Runtime |
| 4d | MCP Stdio Embedded | `marivo-mcp[local]` extras; stdio mode creates embedded Runtime; end-to-end analysis works | Agent + Marivo local stdio completes observe → compare → decompose cycle |

---

## 3. Local Adapters (Phase 4a)

### 3.1 New `.marivo/` Directory Layout

```
.marivo/
  VERSION             # Layout schema version (content: "1")
  marivo.toml         # Local configuration
  models/             # Semantic model files (yaml / json)
  state.db            # SQLite WAL: session events + cache metadata
  evidence/           # Hash-addressed evidence files (<sha256>.json)
```

`VERSION` contains a single integer identifying the layout schema version. `marivo init` writes `1`. Future schema changes increment this value; `marivo init` checks it before operating.

`sessions/` and `cache/` directories from the parent spec are omitted: session cache lives in `state.db`; `sessions/` is a derived export for later phases.

### 3.2 Configuration Format: marivo.toml

```toml
[profile]
mode = "local"

[datasource]
type = "duckdb"          # "duckdb" | "trino" | "snowflake" | "bigquery"
# DuckDB-specific options:
# path = "data.parquet"  # Optional: path to data file(s)
# Trino-specific options:
# host = "localhost"
# port = 8080
# catalog = "analytics"
# schema = "public"

[telemetry]
sink = "none"            # "none" | "file"
```

`type` selects the `DataSource` adapter. Both local and remote modes support all datasource types — the distinction is the MCP process location, not the data location. Phase 4 implements `DuckDBDataSource` fully; other datasource types (`TrinoDataSource`, `SnowflakeDataSource`, `BigQueryDataSource`) are new `DataSource` Port implementations, **not** reuses of existing `CatalogAdapter` classes. `CatalogAdapter` subclasses (`DuckDBCatalogAdapter`, `TrinoCatalogAdapter`) only provide schema discovery; `DataSource` requires `execute()` and `schema()`. Phase 4 stubs non-DuckDB DataSource adapters as `NotImplementedError` for `execute()`; they are completed in subsequent phases.

TOML chosen over YAML: `tomllib` is Python 3.11+ standard library (no external dependency); TOML's limited nesting constrains configuration complexity.

### 3.3 Adapter Implementations

| Port | Adapter | Storage | Key details |
|------|---------|---------|-------------|
| `ModelStore` | `FileModelStore` | `models/` yaml/json files | Auto-detects format by extension (`.yaml`/`.yml` → YAML, `.json` → JSON); optimistic concurrency: write checks revision; atomic rename via `tmp-<uuid>` temp file then `os.rename`; no owner/visibility filtering (single-user); **mtime-based cache**: `get()` checks file `st_mtime` on each call — if unchanged since last read, returns cached content; if changed or new, re-reads from disk. This ensures newly added or updated model files are immediately accessible in the next intent call while avoiding redundant disk reads for unchanged models. `list()` scans `models/` directory and returns `ModelSummary` for each file (single-user: ignores `owner`/`visibility` filters). Write path: write to `tmp-<uuid>`, then atomic rename to final path. Orphaned temp files from crashed writes are cleaned lazily: on next write to the same model, overwrite any existing temp file. |
| `SessionStore` | `SqliteSessionStore` | `state.db` `session_events` table | Append-only; WAL mode; per-request independent transaction |
| `EvidenceStore` | `FileEvidenceStore` | `evidence/<sha256>.json` | SHA-256 addressing; write to `tmp-<uuid>`, then atomic rename; canonical form: sorted-key UTF-8 JSON |
| `DataSource` | `DuckDBDataSource` (and others via config) | In-memory DuckDB + optional file | Executes `LogicalQuery` (compiled SQL) against DuckDB; `schema()` queries DuckDB catalog or probes parquet/csv files directly; does not depend on SemanticModel objects — model loading is the intent runner's responsibility via `ports.model_store`. Other datasource types (Trino, Snowflake, BigQuery) selected by `[datasource] type` in `marivo.toml`; they reuse existing adapters. |
| `CacheStore` | `SqliteCacheStore` | `state.db` `cache_entries` table | TTL-based expiration; lazy cleanup on read |
| `AuthZ` | `NoopAuthZ` | — | Always returns `allowed=True` |
| `AuditLog` | `FileAuditLog` | `.marivo/audit.jsonl` | Append-only JSONL |
| `Telemetry` | `LocalTelemetry` | `.marivo/telemetry.jsonl` or no-op | No-op by default; `sink = "file"` writes JSONL |
| `RuntimeConfig` | `TomlRuntimeConfig` | `marivo.toml` | Reads TOML config; `get(key)` returns string or None |

### 3.4 Contract Tests

Each Port defines a contract test suite in `tests/contracts/` using `pytest.mark.parametrize`. Phase 4a covers local adapter implementations; Phase 6 adds server adapter implementations to the same parametrized suites.

```
tests/contracts/
  test_model_store.py     # Parametrized over [FileModelStore]
  test_session_store.py   # Parametrized over [SqliteSessionStore]
  test_evidence_store.py  # Parametrized over [FileEvidenceStore]
  test_data_source.py     # Parametrized over [DuckDBDataSource]
  test_cache_store.py     # Parametrized over [SqliteCacheStore]
  test_authz.py           # Parametrized over [NoopAuthZ]
  test_audit_log.py       # Parametrized over [FileAuditLog]
  test_telemetry.py       # Parametrized over [LocalTelemetry]
  test_runtime_config.py  # Parametrized over [TomlRuntimeConfig]
```

### 3.5 SQLite State Database Schema

`state.db` contains two tables:

```sql
CREATE TABLE session_events (
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_type  TEXT NOT NULL,
    timestamp   TEXT NOT NULL,  -- ISO-8601, maps to SessionEvent.timestamp
    payload     TEXT NOT NULL,  -- JSON
    actor       TEXT,           -- maps to SessionEvent.actor (NULL = system)
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE cache_entries (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,  -- JSON
    expires_at  TEXT            -- ISO-8601, NULL = no expiry
);
```

Schema aligned with `SessionEvent` Pydantic model (`session_id`, `event_type`, `timestamp`, `payload`, `actor`). `seq` is an auto-incremented ordering column not present in `SessionEvent` — `load_events()` omits it when deserializing. `append_event()` assigns `seq` via `MAX(seq) + 1`.

WAL mode enabled on connection: `PRAGMA journal_mode=WAL`. **Connection strategy:** per-request independent connection. Each `append_event()` and `load_events()` call opens a new SQLite connection, sets PRAGMAs (`journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`), executes, and closes. For single-process local mode, connection overhead is negligible (<1ms). This avoids all connection-sharing bugs and transaction isolation issues.

---

## 4. Intent Migration (Phase 4b)

Phase 4b is larger than it appears. Three interrelated migrations must complete before `MarivoRuntime` can operate without `SemanticLayerService`:

1. **CoreEngine I/O method migration** — 12 proxy methods still delegate to `svc`
2. **Intent runner execution path migration** — `execute_compiled(engine, query)` must become `ports.data_source.execute(query)`
3. **Session lifecycle method migration** — 4 Runtime methods proxy to `svc`

### 4.1 Current State

The current execution flow for an observe intent:

```
MarivoRuntime.observe(session_id, params)
  → svc.run_intent(session_id, "observe", params)
    → intent_registry → run_observe_intent(core, ports, session_id, params)
      → core.resolve_metric_execution_context()  [svc proxy: semantic repo]
      → core.resolve_engine_for_session()         [svc proxy: routing_runtime → AnalyticsEngine]
      → core.compile_step()                       [svc proxy: compiler → CompiledQuery]
      → execute_compiled(engine, compiled_query)  [bypasses ports.data_source entirely]
      → core.commit_artifact_with_extraction()    [svc proxy: evidence store]
      → core.insert_step()                        [svc proxy: session store]
```

Intent runners receive `ports` but do not use it — all I/O goes through `CoreEngine` proxy methods back to `svc`.

### 4.2 Target State

```python
class MarivoRuntime:
    def __init__(self, ports: RuntimePorts, core: CoreEngine):
        self._ports = ports
        self._core = core

    def observe(self, session_id: SessionId, params: dict) -> dict:
        return run_observe(self._core, self._ports, session_id, params)

    def create_session(self, goal: str, **kwargs) -> SessionId:
        event = SessionCreatedEvent(goal=goal, **kwargs)
        self._ports.session_store.append_event(event.session_id, event)
        return event.session_id

    # ... other intent and lifecycle methods
```

CoreEngine holds no `svc` reference and no `ports` reference. It is pure computation only.

### 4.3 CoreEngine Method Migration Table

| Current CoreEngine method | Category | Migration target | Notes |
|--------------------------|----------|-----------------|-------|
| `normalize_intent_metric_ref` | Pure | Already in `core/semantic/typed_resolution` | Done (3c) |
| `metric_name_from_ref` | Pure | Already in `core/semantic/typed_resolution` | Done (3c) |
| `new_step_id` | Pure | Utility function in `core/intent/primitives.py` | UUID generation, no I/O |
| `make_provenance` | Pure | Move to `core/` submodule or inline in runners | Dict construction, no I/O |
| `build_step_semantic_metadata` | Pure | Move to `core/semantic/` submodule | Metadata dict, no I/O |
| `resolve_metric_execution_context` | Needs model_store | **Runtime method** — loads model via `ports.model_store`, then delegates pure resolution to `core/semantic/` | Mixed: I/O (model load) + computation |
| `resolve_metric` | Needs model_store | **Runtime method** — delegates to `ports.model_store.get()` | I/O only |
| `resolve_metric_table` | Needs model_store | **Runtime method** — loads model, extracts table | I/O + trivial computation |
| `resolve_metric_dimensions` | Needs model_store | **Runtime method** — loads model, extracts dimensions | I/O + trivial computation |
| `resolve_metric_sql_for_execution` | Needs semantic context | **Runtime method** — loads model, delegates compilation to `core/semantic/compiler` | I/O + computation |
| `resolve_metric_value_sql_for_execution` | Needs semantic context | **Runtime method** — same pattern as above | I/O + computation |
| `resolve_scope_constraint_column` | Needs semantic context | **Runtime method** — loads model, delegates to `core/semantic/scope_resolution` | I/O + computation |
| `compile_step` | Needs semantic context | **Runtime method** — delegates to `core/semantic/compiler` | I/O (model load) + computation |
| `resolve_windowed_query_time_axis` | Needs semantic context | **Runtime method** — delegates to `core/semantic/calendar` | I/O + computation |
| `build_scoped_query` | Needs semantic context | **Runtime method** — delegates to `core/semantic/scope_resolution` | I/O + computation |
| `commit_artifact_with_extraction` | I/O | **Intent runners call `ports.evidence_store` directly** | Evidence persistence |
| `insert_step` | I/O | **Intent runners call `ports.session_store` directly** | Session event persistence |
| `resolve_artifact_for_ref` | I/O | **Intent runners call `ports.evidence_store.read()` directly** | Artifact lookup |
| `resolve_artifact_id_for_step` | I/O | **Intent runners call `ports.session_store` directly** | Step-to-artifact mapping |
| `resolve_artifact_with_id` | I/O | **Intent runners call `ports.evidence_store.read()` directly** | Full artifact retrieval |
| `insert_artifact` | I/O | **Intent runners call `ports.evidence_store.write()` directly** | Artifact insertion |
| `resolve_engine_for_session` | I/O | **Intent runners use `ports.data_source` directly** | Engine resolution eliminated — DataSource port abstracts this |
| `resolve_engine` | I/O | **Intent runners use `ports.data_source` directly** | Same as above |
| `discover_catalog` | I/O | **Removed from MarivoRuntime.** MCP catalog tools call `ports.data_source.schema()` directly | Catalog discovery is a DataSource concern, not a Runtime concern |

### 4.4 The `execute_compiled` → `ports.data_source` Gap

The current execution path:
```python
engine = core.resolve_engine_for_session(session_id, [table])  # returns AnalyticsEngine
rows = list(execute_compiled(engine, compiled_query).rows)       # bypasses DataSource port
```

The target execution path:
```python
query_result = ports.data_source.execute(compiled_query.to_logical_query())  # via DataSource port
rows = query_result.rows
```

**Bridge approach:** `DuckDBDataSource.execute()` accepts a `CompiledQuery` object initially (not a formal `LogicalQuery`). The `DataSource` Protocol's `LogicalQuery` parameter type will be formalized in a later phase when the query abstraction is stable. For Phase 4, `DuckDBDataSource` wraps the existing `execute_compiled` logic internally:

```python
class DuckDBDataSource:
    def execute(self, query) -> QueryResult:
        # Phase 4: accepts CompiledQuery directly
        # Translates and executes against in-process DuckDB
        from app.analysis_core.executor import execute_compiled
        result = execute_compiled(self._engine, query)
        return QueryResult(rows=result.rows, metadata=result.metadata)
```

This avoids a big-bang refactor of the query type system while still routing all execution through the `DataSource` port.

### 4.5 Session Lifecycle Method Migration

| Runtime method | Current | Target |
|---------------|---------|--------|
| `create_session` | `svc.create_session()` | Append `session_created` event via `ports.session_store.append_event()` |
| `get_session` | `svc.get_session()` | Replay events from `ports.session_store.load_events()` |
| `terminate_session` | `svc.terminate_session()` | Append `session_terminated` event via `ports.session_store.append_event()` |
| `get_session_state` | `svc.get_session_state()` | Replay events via `ports.session_store.load_events()` + reconstruct state via `core/session/rebuild.py` |

### 4.5a Session State Reconstruction

`get_session_state()` is the most complex lifecycle method. It must produce `SessionState` from a raw event log. This reconstruction logic lives in a pure function in `core/session/rebuild.py`:

```python
def rebuild_session_state(events: list[SessionEvent]) -> SessionState:
    """Pure function: reconstruct SessionState from event log.

    Handles:
    - Session status transitions (created → active → terminated)
    - Proposition lifecycle (seeded → assessed → gap identified)
    - updated_at = timestamp of last event
    """
    ...
```

`MarivoRuntime.get_session_state()` calls:
```python
events = self._ports.session_store.load_events(session_id)
state = rebuild_session_state(events)
return state
```

This separation keeps `SqliteSessionStore` as a dumb event log and puts all business logic in `core/`.

**Event sourcing scope note:** Phase 4 uses simple append-only event log with full replay on every `load_events()` call. Local sessions are short-lived (one MCP process lifetime). Snapshots, compaction, and schema migration for long-lived sessions are deferred to Phase 6 (server profile).

### 4.5b Query Type Bridge

The `DataSource` Protocol's `execute()` is typed as `execute(self, query: LogicalQuery) -> QueryResult`. For Phase 4, `DuckDBDataSource.execute()` accepts `CompiledQuery` as a bridge type:

```python
class DuckDBDataSource:
    def execute(self, query) -> QueryResult:
        # Phase 4 bridge: accepts CompiledQuery (not LogicalQuery)
        # Type narrowing handled at adapter boundary, not Protocol level
        from app.analysis_core.executor import execute_compiled
        result = execute_compiled(self._engine, query)
        return QueryResult(rows=result.rows, metadata=result.metadata)
```

Intent runners pass the `CompiledQuery` object they have directly to `ports.data_source.execute()`. The `DataSource` Protocol stays typed as `LogicalQuery`; DuckDBDataSource satisfies it structurally via Python's duck typing. When `LogicalQuery` is formalized as a concrete type in a later phase, DuckDBDataSource's `execute()` signature will be tightened and a `CompiledQuery.to_logical_query()` conversion added. The bridge is explicitly temporary — its removal is tracked in the Phase 4 completion gate.

4b is split into three ordered sub-phases:

| Sub-phase | Name | Deliverable | Gate |
|-----------|------|-------------|------|
| 4b-1 | CoreEngine cleanup | Pure methods moved to `core/` submodules; I/O proxy methods removed from CoreEngine; CoreEngine no longer holds `svc` | `CoreEngine.__init__` takes no `svc`; pure methods tested |
| 4b-2 | Intent runner migration | All intent runners use `ports.*` directly instead of `core.*` I/O proxies; `execute_compiled` → `ports.data_source.execute()`; extract `commit_step_result()` helper to DRY up the repeated artifact+step commit pattern (currently repeated ~22 times across 10 runners) | Each runner migrated individually; existing E2E tests green per runner |
| 4b-3 | Runtime lifecycle | `MarivoRuntime` session methods use `ports.session_store` directly; `svc` reference removed from Runtime | All existing E2E tests green |

**Dependency:** 4b-1 must complete before 4b-2 and 4b-3 (CoreEngine must be svc-free before runners and Runtime can drop their svc dependencies). 4b-2 and 4b-3 can proceed in parallel.

**`create_local_runtime()` dependency note:** `CoreEngine()` with no `svc` argument is only available after 4b-1 completes. The factory in Phase 4c depends on 4b-1.

### 4.7 Existing `create_runtime_from_service`

Retained for server profile. Modified to construct `CoreEngine` without `svc` (after 4b-1), then `MarivoRuntime(ports, core)`. Server adapters continue wrapping `SemanticLayerService` storage internals.

### 4.8 Compatibility

- HTTP API and MCP HTTP mode still use `create_runtime_from_service`; behavior unchanged.
- `SemanticLayerService.run_intent` marked as deprecated; removal in Phase 6.
- All existing E2E tests must remain green after each sub-phase.

---

## 5. Factory + marivo init + Layout (Phase 4c)

### 5.1 `create_local_runtime()` Factory

```python
# profiles/local.py
@dataclass
class LocalConfig:
    workspace_root: Path
    datasource_type: str = "duckdb"      # "duckdb" | "trino" | "snowflake" | "bigquery"
    datasource_config: dict[str, Any] = field(default_factory=dict)
    telemetry_sink: str = "none"

def create_local_runtime(config: LocalConfig) -> MarivoRuntime:
    marivo_dir = config.workspace_root / ".marivo"
    data_source = create_data_source(config.datasource_type, config.datasource_config)
    ports = RuntimePorts(
        model_store=FileModelStore(marivo_dir / "models"),
        session_store=SqliteSessionStore(marivo_dir / "state.db"),
        evidence_store=FileEvidenceStore(marivo_dir / "evidence"),
        data_source=data_source,
        cache_store=SqliteCacheStore(marivo_dir / "state.db"),
        authz=NoopAuthZ(),
        audit_log=FileAuditLog(marivo_dir / "audit.jsonl"),
        telemetry=LocalTelemetry(sink=config.telemetry_sink),
        runtime_config=TomlRuntimeConfig(marivo_dir / "marivo.toml"),
    )
    core = CoreEngine()
    return MarivoRuntime(ports, core)

def create_data_source(dtype: str, config: dict[str, Any]) -> DataSource:
    if dtype == "duckdb":
        return DuckDBDataSource(config.get("path"))
    elif dtype == "trino":
        return TrinoDataSource(host=config["host"], port=config.get("port", 8080),
                               catalog=config.get("catalog"), schema=config.get("schema"))
    # ... other types
    else:
        raise ValidationError(f"Unknown datasource type: {dtype}")
```

### 5.2 Profile Selection

Priority order (from parent spec §7):

| Priority | Source |
|----------|--------|
| 1 | Explicit `--profile local` flag |
| 2 | `MARIVO_PROFILE=local` env var |
| 3 | `.marivo/marivo.toml` `[profile] mode = "local"` |
| 4 | Default: `local` |

**Safety guard:** `MARIVO_DEPLOYMENT=server` and `server.toml` are advisory hints, not hard blocks. The priority order above is authoritative: explicit `--profile local` (priority 1) overrides `MARIVO_DEPLOYMENT=server`. When `MARIVO_DEPLOYMENT=server` is set but the user explicitly requests local mode (priority 1 or 2), `create_local_runtime()` logs a warning ("Running local profile in a server-deployment environment") and proceeds. When no explicit local request is made (priority 3 or 4) and `MARIVO_DEPLOYMENT=server` is set, it raises an error with a clear message.

**`CoreEngine()` dependency note:** `CoreEngine()` with no `svc` argument is only available after Phase 4b-1 completes. The factory code above assumes 4b-1 is done.

### 5.3 `marivo init` Command

```
marivo init [--workspace-root PATH]
```

Behavior:
1. Create `<workspace_root>/.marivo/` directory structure if missing: `models/`, `evidence/`.
2. Write `VERSION` file containing `"1"` if missing.
3. Generate default `marivo.toml` if missing.
4. Initialize `state.db`: create tables, enable WAL mode.
5. If already initialized and intact, print current status; do not overwrite.
6. Check `VERSION` content — if it doesn't match expected version, raise with migration guidance.

**Idempotent:** repeated runs do not damage existing data.

### 5.4 Migration from marivo.yaml

If `.marivo/marivo.yaml` exists but `marivo.toml` does not, `marivo init` prints a migration hint but does not auto-migrate. Automatic migration deferred to Phase 7. Phase 4 users handle migration manually.

### 5.5 CLI Entry Point

```toml
# pyproject.toml
[project.scripts]
marivo = "app.transports.cli:main"
```

CLI uses `click` (consistent with `marivo-mcp`'s `init_cli.py`). Only `init` subcommand in Phase 4.

---

## 6. MCP Stdio Embedded Mode (Phase 4d)

### 6.1 Current Architecture

marivo-mcp stdio entry detects local mode → spawns `marivo serve-local` subprocess → connects via HTTP.

### 6.2 Target Architecture

stdio entry detects local profile → creates `MarivoRuntime` in-process → MCP tool handlers call Runtime methods directly.

### 6.3 Mode Determination

```python
class MarivoMcpConfig:
    mode: Literal["auto", "remote", "local"]  # existing
    embedded: bool = False  # new: True = embedded Runtime
```

Resolution:
1. `mode = "remote"` → HTTP client, no embedding
2. `mode = "local"` + `embedded = True` → embedded Runtime
3. `mode = "local"` + `embedded = False` → HTTP subprocess via `target_resolution.py` (backward compatible)
4. `mode = "auto"` + `.marivo/marivo.toml` detected → embedded Runtime
5. `mode = "auto"` + no local config → HTTP client

### 6.4 Single Handler Set with Backend Injection

One handler set serves both modes. Each handler receives a `MarivoBackend` abstraction that dispatches to either the embedded Runtime or the HTTP client:

```python
class MarivoBackend(Protocol):
    async def call(self, method: str, path: str, **kwargs) -> dict: ...

class EmbeddedBackend:
    """Calls MarivoRuntime methods directly via thread executor."""
    def __init__(self, runtime: MarivoRuntime):
        self._runtime = runtime
        self._default_session_id: str | None = None  # set during startup

    async def call(self, method: str, path: str, **kwargs) -> dict:
        """Dispatch to sync runtime method via asyncio thread executor.

        MarivoRuntime methods are synchronous. Running them in a thread
        executor prevents blocking the async MCP event loop during
        DuckDB/SQLite/file I/O operations.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_call, method, path, kwargs)

    def _sync_call(self, method: str, path: str, kwargs: dict) -> dict:
        runtime_method = getattr(self._runtime, method)
        # If session_id not provided, use the default implicit session
        if "session_id" not in kwargs and self._default_session_id is not None:
            kwargs["session_id"] = self._default_session_id
        return runtime_method(**kwargs)

class HttpBackend:
    """Proxies to Marivo server via HTTP."""
    def __init__(self, client: MarivoHttpClient): ...
    async def call(self, method: str, path: str, **kwargs) -> dict: ...
```

Tool registration is mode-agnostic:

```python
def register_tools(server, backend: MarivoBackend):
    @server.tool()
    async def observe(session_id, metric, time_scope, ...):
        return backend.call("observe", session_id=session_id, ...)

def build_server(config: MarivoMcpConfig):
    if should_embed(config):
        backend = EmbeddedBackend(create_embedded_runtime(config))
    else:
        backend = HttpBackend(MarivoHttpClient(config.base_url, ...))
    register_tools(server, backend)
```

### 6.5 Result Format Adaptation

`EmbeddedBackend.call()` converts Runtime domain dict results to `ToolEnvelope` format. `HttpBackend.call()` wraps the existing `client.request_envelope()` output, which already returns `ToolEnvelope`. MCP clients see identical response structure regardless of mode.

### 6.6 Optional Dependency

```toml
# marivo-mcp/pyproject.toml
[project.optional-dependencies]
local = ["marivo[duckdb]"]
```

Lazy import in embedded path:

```python
def create_embedded_runtime(config: LocalConfig) -> MarivoRuntime:
    from app.profiles.local import create_local_runtime
    return create_local_runtime(config)
```

When `marivo-mcp[local]` is not installed, `mode = "local"` produces a clear install instruction instead of `ImportError`.

### 6.7 Session Lifecycle

Embedded stdio process lifecycle equals one session's lifecycle:

1. stdio and HTTP MCP both require explicit `session_id` on every tool call that operates on a session. There is no implicit default.
2. The MCP client's first call must be `create_session`, after which the returned `session_id` is passed to subsequent intent calls. This makes stdio and HTTP MCP wire-identical.
3. stdin closes → `runtime.terminate_session()` → process exits

There is no implicit `session_id` storage; every tool call must provide one explicitly. MCP clients can still provide an explicit `session_id` in tool calls to override the default. MCP clients may also create additional sessions via the `create_session` tool.

### 6.8 Unchanged Components

- HTTP MCP transport: unchanged
- `mode = "remote"` behavior: unchanged
- MCP tool schemas (names, parameter structures): identical across modes
- `target_resolution.py`: retained for `mode = "local"` + `embedded = False` fallback

---

## 7. Error Handling

Every local adapter must handle failure modes explicitly. Catch-all `except Exception` is forbidden in adapter code.

### 7.1 FileModelStore

| Failure mode | Exception | Action | User sees |
|-------------|-----------|--------|-----------|
| Model file not found | Returns `None` (not an error) | — | Caller treats as "model does not exist" |
| YAML/JSON parse error | `ValidationError` wrapping parse exception | Raise with filename and line info | "Model file '{path}' is invalid: {parse_error}" |
| Revision conflict on save | `ConflictError` | Raise | "Model '{name}' was modified by another process. Re-read and retry." |
| Atomic rename fails (cross-filesystem) | `OSError` | Write falls back to in-place write with file lock; log warning | Operation succeeds with degraded safety |
| Directory missing on save | `OSError` | Create `models/` directory if missing; retry once | Transparent |

`get()` returns `None` when no model file matches the selector (legitimate absent state). `get()` raises `ValidationError` when a file exists but cannot be parsed — this is not a "model not found" case, it is a data integrity problem that must not be silently swallowed.

### 7.2 SqliteSessionStore

| Failure mode | Exception | Action | User sees |
|-------------|-----------|--------|-----------|
| Database locked | `sqlite3.OperationalError` | Retry with busy timeout (5s); raise if still locked | "Session storage is busy. Try again." |
| Disk full | `sqlite3.OperationalError` | Raise with context | "Cannot write session data: disk may be full" |
| Corrupt database | `sqlite3.DatabaseError` | Raise | "Session database is corrupt. Run `marivo init` to reinitialize." |

`busy_timeout` is set to 5000ms on every connection: `PRAGMA busy_timeout=5000`. This handles concurrent multi-process access without immediate failure.

### 7.3 FileEvidenceStore

| Failure mode | Exception | Action | User sees |
|-------------|-----------|--------|-----------|
| Evidence not found | `NotFoundError` | Raise | "Evidence '{hash}' not found" |
| Hash mismatch on read | `IntegrityError` (new class in `app.contracts.errors`, using `ErrorCode.EVIDENCE_HASH_MISMATCH`) | Raise — never return corrupt data | "Evidence file '{hash}' is corrupt: content hash does not match" |
| Temp file write fails | `OSError` | Clean up temp file; raise | "Cannot write evidence: {os_error}" |
| Evidence already exists | — | `write()` is idempotent — if hash matches existing file, return existing ref silently | Transparent |

Hash verification on read is mandatory. `read()` recomputes the SHA-256 of the file contents and compares against the requested hash. Mismatch raises `IntegrityError`.

### 7.4 DuckDBDataSource

| Failure mode | Exception | Action | User sees |
|-------------|-----------|--------|-----------|
| SQL parse error | `ValidationError` wrapping `duckdb.ParserException` | Raise with SQL snippet | "Query could not be parsed: {detail}" |
| Table/file not found | `NotFoundError` wrapping `duckdb.CatalogException` | Raise with table name | "Table '{name}' not found in data source" |
| OOM during execution | `RuntimeError` wrapping `duckdb.OutOfMemoryException` | Raise | "Query exceeded available memory. Try a smaller time range or add filters." |
| Query timeout | `TimeoutError` | Raise (DuckDB queries are synchronous; timeout enforced by caller) | "Query timed out" |

### 7.5 TomlRuntimeConfig

| Failure mode | Exception | Action | User sees |
|-------------|-----------|--------|-----------|
| TOML parse error | `ValidationError` wrapping `tomllib.TOMLDecodeError` | Raise at Runtime construction time | "Configuration file 'marivo.toml' is invalid: {detail}" |
| Missing key | — | `get()` returns `None` (by design) | Transparent |

### 7.6 Trivial Adapters

- **NoopAuthZ**: cannot fail — always returns allowed.
- **FileAuditLog**: on write failure, log to stderr as fallback. Audit log failure must not crash the analysis.
- **LocalTelemetry**: on write failure, silently skip. Telemetry failure must not crash the analysis.
- **SqliteCacheStore**: on read failure, return `None` (cache miss is safe). On write failure, log warning and skip. Cache failure degrades performance, not correctness.

### 7.7 EmbeddedBackend Error Mapping

`EmbeddedBackend.call()` maps `DomainError` subclasses from Runtime to `ToolEnvelope` error format:

```python
try:
    result = runtime_method(**kwargs)
    return ToolEnvelope(data=result, error=None)
except NotFoundError as e:
    return ToolEnvelope(data=None, error=ToolError(code="NOT_FOUND", message=str(e)))
except ConflictError as e:
    return ToolEnvelope(data=None, error=ToolError(code="CONFLICT", message=str(e)))
except ValidationError as e:
    return ToolEnvelope(data=None, error=ToolError(code="VALIDATION", message=str(e)))
except IntegrityError as e:
    return ToolEnvelope(data=None, error=ToolError(code="INTEGRITY", message=str(e)))
```

Unmapped exceptions are wrapped as `ToolError(code="INTERNAL", message=str(e))`. No exception escapes the backend boundary.

### 7.8 marivo init

| Failure mode | Action | User sees |
|-------------|--------|-----------|
| `.marivo/` exists but is corrupted (missing subdirs) | Repair: create missing subdirs, reinitialize `state.db` if tables missing | "Repaired missing directories" |
| `state.db` exists but schema is wrong | Raise — do not auto-migrate or overwrite | "state.db has an incompatible schema. Back up and reinitialize." |
| `marivo.toml` exists | Skip generation | "marivo.toml already exists" |
| `VERSION` content doesn't match expected version | Raise with migration guidance | "Layout version {found} is not supported (expected {expected}). Run `marivo migrate` or reinitialize." |

---

## 8. Testing Strategy

### 8.1 Contract Tests (Phase 4a)

Each local adapter passes its Port's contract test suite. Tests are parametrized so Phase 6 server adapters slot in without new test files.

**Parametrize pattern:** each test file defines a `pytest` fixture `adapter_factories` that returns a list of `(name, factory_callable)` tuples. The test module uses `@pytest.mark.parametrize("adapter_name,adapter_factory", adapter_factories)` at the class or function level. Phase 4a registers local adapter factories; Phase 6 adds server adapter factories to the same list. Example:

```python
# tests/contracts/conftest.py
model_store_factories = [
    ("FileModelStore", lambda: FileModelStore(tmp_path / "models")),
    # Phase 6: ("SqlModelStore", lambda: SqlModelStore(db_connection)),
]

# tests/contracts/test_model_store.py
@pytest.mark.parametrize("name,factory", model_store_factories)
class TestModelStoreContract:
    def test_get_returns_none_for_absent(self, name, factory): ...
    def test_save_and_get_roundtrip(self, name, factory): ...
    def test_list_returns_all_models(self, name, factory): ...
```

### 8.2 Integration Tests (Phase 4c)

- `marivo init` creates valid `.marivo/` layout with VERSION file; `create_local_runtime()` succeeds on initialized layout
- `create_local_runtime()` respects priority rules: explicit `--profile local` overrides `MARIVO_DEPLOYMENT=server` (with warning); implicit local mode blocked by `MARIVO_DEPLOYMENT=server`
- `create_local_runtime()` with `MARIVO_DEPLOYMENT=server` + no explicit local flag raises clear error

### 8.3 E2E Tests (Phase 4d)

- Local stdio MCP session completes observe → compare → decompose cycle
- Embedded mode response structure matches HTTP mode response structure (format parity)
- Session lifecycle: session events persisted to `state.db`; evidence files in `evidence/`
- Evidence hash determinism: same input produces same SHA-256 evidence hash
- **Session state reconstruction:** create session → run observe+compare → call `get_session_state()` → verify `SessionState` has correct status, proposition counts, and `updated_at`. Tests the `core/session/rebuild.py` pure function end-to-end via the event log.

### 8.4 CI Rules

- `import-linter` continues enforcing `core/` import boundary
- Phase 4a contract tests run in every CI pass
- Phase 4d E2E tests run with DuckDB available (no MySQL required)

### 8.5 Concurrency Tests

SQLite WAL mode supports concurrent multi-process access. These tests verify that guarantee:

- **Concurrent `append_event`**: two processes append 100 events each to the same session; all 200 events are present and correctly ordered after both complete. No events lost.
- **Concurrent model read during write**: one process writes a model file (via atomic rename), another reads the same model simultaneously; reader never sees a partially-written file (always gets the old or new version, never a mix).
- **Concurrent evidence write**: two processes write evidence with the same content hash; both writes succeed, only one file exists on disk (write is idempotent by hash).

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Intent migration breaks existing HTTP E2E tests | Medium | High | 4b gate requires all existing tests green; migrate one intent at a time with per-intent verification |
| `FileModelStore` optimistic concurrency is too restrictive for single-user local mode | Low | Low | Fallback: if no `expected_revision` provided, allow write (local-only relaxation, documented) |
| `marivo-mcp[local]` dependency resolution conflicts with existing `marivo-mcp` installs | Low | Medium | Test fresh install and upgrade paths; pin dependency versions |
| `FileEvidenceStore.read()` hash mismatch due to non-canonical serialization | Medium | High | Canonical form enforced at write time (sorted-key UTF-8 JSON, integer-typed numbers); hash verification at read time catches corruption |
| Evidence file accumulation (no GC) | Low | Low | Hash-addressed files are never deleted; unreferenced evidence accumulates. In local single-user mode, volume is modest. GC deferred to Phase 6. Manual cleanup: delete `.marivo/evidence/` contents. |
| Event log growth without compaction | Low | Medium | Local sessions are short-lived (one MCP process lifetime), so full replay is acceptable. Snapshots and compaction deferred to Phase 6 for server profile with long-lived sessions. |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 3 | CLEAN | 5 proposals, 0 accepted, 0 deferred |
| Codex Review | `/codex review` | Independent 2nd opinion | 2 | ISSUES_FOUND | 30 findings (4 addressed as cross-model tensions) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | CLEAN | 15 issues found and amended |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

CROSS-MODEL: Codex flagged 30 issues; 4 presented as cross-model tensions. All resolved: (1) keep current sub-phase ordering, (2) simple event log + defer snapshots/compaction, (3) add .marivo/VERSION file, (4) defer evidence GC.

UNRESOLVED: 0

VERDICT: CEO CLEARED + ENG CLEARED — all 15 findings and 4 cross-model tensions amended into spec. Ready to implement.
