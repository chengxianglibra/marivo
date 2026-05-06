# Marivo Platform Architecture Design

**Date:** 2026-05-06
**Status:** Approved
**Scope:** North-star architecture for Marivo as a dual-mode data analysis platform (local agentic + enterprise). This document is the Phase 1 deliverable. Subsequent phases each produce their own implementation plan.

---

## 1. Background & Goals

Marivo supports two deployment scenarios with a single codebase:

- **Local agentic data analysis**: installed locally, no daemon, no external services, short-lived processes, SQLite + DuckDB.
- **Enterprise intelligent analytics platform**: shared semantic layer, centralized governance, audit trail, ChatBI / agent / UI integration.

Core goals:
- One canonical semantic / intent / evidence contract across both scenarios.
- Local and enterprise differ only in deployment profile, not agent interaction syntax.
- Marivo core is a library; service / MCP / CLI / SDK are wrappers.

---

## 2. Architecture Principles

1. Core Engine is a pure library — no I/O implementations.
2. All external dependencies injected through Ports.
3. Surfaces only do protocol translation — no business logic.
4. Profile is a composed set of adapters, assembled by an explicit factory function.
5. Local mode does not start an HTTP service.
6. Enterprise mode reuses the same Runtime, exposed via HTTP / MCP / SDK.
7. Canonical contract takes priority over transport contract.
8. MCP is not the canonical business contract.
9. CLI is not the primary agent analysis protocol.

**Core invariants (must not be violated in any phase):**

1. `core/` must not import any adapter, transport, or storage library.
2. Ports return domain objects and domain IDs — not ORM rows or SQL cursors.
3. Surfaces call Runtime only — not Core Engine directly.
4. Profile Factory is the only place that knows which adapter wires to which port.
5. Local profile does not start HTTP service; enterprise profile does not maintain independent business logic.

---

## 3. Five-Layer Architecture

```
┌─────────────────────────────────────────────────────┐
│  Surfaces                                           │
│  CLI │ MCP (stdio + HTTP) │ HTTP API │ SDK │ Admin  │
│  Responsibility: protocol translation only.         │
├─────────────────────────────────────────────────────┤
│  Runtime / Use Cases                                │
│  session │ semantic ops │ intent execution          │
│  evidence ops │ governance                          │
│  Responsibility: orchestrate core + ports.          │
├─────────────────────────────────────────────────────┤
│  Core Engine                                        │
│  semantic │ intent compiler │ plan executor          │
│  evidence │ lineage │ policy                        │
│  Responsibility: pure domain logic, zero I/O.       │
├─────────────────────────────────────────────────────┤
│  Ports                                              │
│  ModelStore │ SessionStore │ EvidenceStore           │
│  DataSource │ CacheStore │ AuthZ │ AuditLog          │
│  Telemetry │ RuntimeConfig                          │
│  Responsibility: domain-defined abstract interfaces.│
├─────────────────────────────────────────────────────┤
│  Adapters / Profiles                                │
│  local │ server │ client                            │
│  Responsibility: port implementations + factory.   │
└─────────────────────────────────────────────────────┘
```

---

## 4. Package Structure

`app/` is renamed to `marivo/`. `marivo-mcp/` and `marivo-skill/` are merged into `marivo/transports/`.

```
marivo/
  contracts/         # Shared domain types: IDs, value objects, error codes
  core/              # Pure domain logic, zero I/O
    semantic/
    intent/
    planner/
    evidence/
    lineage/
    policy/
  runtime/           # Use case orchestration layer
    session.py
    semantic_ops.py
    intent_execution.py
    evidence_ops.py
    governance.py
  ports/             # Python Protocol definitions, one file per port
    model_store.py
    session_store.py
    evidence_store.py
    data_source.py
    cache_store.py
    authz.py
    audit_log.py
    telemetry.py
    runtime_config.py
  adapters/
    local/           # FileModelStore, SqliteSessionStore, DuckDBDataSource…
    server/          # SqlModelStore, SqlSessionStore, S3EvidenceStore…
    client/          # HTTP proxy adapters for client profile
  transports/        # Merged from marivo-mcp/ + existing surface code
    cli/
    mcp/             # stdio + HTTP MCP, embedded and client modes
    http/            # FastAPI routes
    sdk/
  profiles/
    local.py         # create_local_runtime() factory
    server.py        # create_server_runtime() factory
    client.py        # create_client_runtime() factory
  local/             # Local-mode utilities: state layout, init, WAL helpers
```

**Migration rule:** existing modules under `app/` are relocated into the above structure by responsibility. No functional changes during the move.

---

## 5. Core Engine Design

**Responsibilities:**

| Area | Module |
|------|--------|
| Semantic model parsing, validation, publish-state determination | `core/semantic/` |
| Typed intent compilation | `core/intent/` |
| Logical plan generation and execution orchestration | `core/planner/` |
| Evidence / finding / proposition / assessment generation | `core/evidence/` |
| Lineage, hash, replay semantics | `core/lineage/` |
| Policy rule evaluation | `core/policy/` |

**Non-responsibilities (hard boundaries):**
- Does not read or write files.
- Does not connect to databases.
- Does not handle HTTP / MCP / CLI protocols.
- Does not know whether it is running in local or enterprise mode.
- Does not import any adapter or transport module.

**Import rules (enforced in CI via `import-linter`):**

```
core/ may only import:
  - marivo/contracts/
  - Python standard library
  - Pure computation dependencies (e.g. pydantic model definitions)

core/ must never import:
  - marivo/ports/
  - marivo/adapters/
  - marivo/runtime/
  - marivo/transports/
  - Any I/O library (sqlalchemy, duckdb, httpx, fastapi, …)
```

---

## 6. Ports Design

Each Port is a Python `Protocol` in `marivo/ports/`, containing only the domain contract.

```python
# ports/model_store.py
class ModelStore(Protocol):
    def get(self, model_id: ModelId) -> SemanticModel: ...
    def save(self, model: SemanticModel) -> ModelId: ...
    def list(self, owner: UserId) -> list[ModelSummary]: ...

# ports/session_store.py — append-only
class SessionStore(Protocol):
    def append_event(self, session_id: SessionId, event: SessionEvent) -> None: ...
    def load_events(self, session_id: SessionId) -> list[SessionEvent]: ...

# ports/evidence_store.py — hash-addressed
class EvidenceStore(Protocol):
    def write(self, evidence: Evidence) -> EvidenceRef: ...  # ref contains hash
    def read(self, ref: EvidenceRef) -> Evidence: ...

# ports/data_source.py
class DataSource(Protocol):
    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...

# ports/authz.py
class AuthZ(Protocol):
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> bool: ...

# ports/audit_log.py
class AuditLog(Protocol):
    def record(self, entry: AuditEntry) -> None: ...

# ports/cache_store.py
class CacheStore(Protocol):
    def get(self, key: CacheKey) -> CacheValue | None: ...
    def set(self, key: CacheKey, value: CacheValue, ttl: int | None) -> None: ...

# ports/telemetry.py
class Telemetry(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...

# ports/runtime_config.py
class RuntimeConfig(Protocol):
    def get(self, key: str) -> str | None: ...
```

**Design rules:**
- Port methods accept and return only types defined in `marivo/contracts/`.
- `SessionStore` is append-only: `append_event` only, no update or delete.
- `EvidenceStore.write` returns a hash-bearing `EvidenceRef`, ensuring referenceability and replayability.
- `AuthZ` is implemented by `NoopAuthZ` in local profile (always returns `True`).
- Every Port has an adapter contract test suite; all implementations must pass it.

---

## 7. Adapters & Profiles

**Adapter implementation matrix:**

| Port | local adapter | server adapter | client adapter |
|------|--------------|----------------|----------------|
| `ModelStore` | `FileModelStore` | `SqlModelStore` (pg / mysql) | `HttpModelStore` |
| `SessionStore` | `SqliteSessionStore` | `SqlSessionStore` (pg / mysql) | `HttpSessionStore` |
| `EvidenceStore` | `FileEvidenceStore` | `S3EvidenceStore` | `HttpEvidenceStore` |
| `DataSource` | `DuckDBDataSource` | `Trino/Snowflake/BQDataSource` | `HttpDataSource` |
| `CacheStore` | `SqliteCacheStore` | `RedisCacheStore` | `HttpCacheStore` |
| `AuthZ` | `NoopAuthZ` | `OidcRbacAuthZ` | `HttpAuthZ` |
| `AuditLog` | `FileAuditLog` | `CentralizedAuditLog` | `HttpAuditLog` |
| `Telemetry` | `LocalTelemetry` | `OtelTelemetry` | `HttpTelemetry` |

**Server SQL adapters:** `SqlModelStore` and `SqlSessionStore` use SQLAlchemy Core, accepting a `db_url` connection string. Supported backends: PostgreSQL, MySQL, and any SQLAlchemy-compatible database. Not bound to a specific dialect. Adapter contract tests run against both pg and mysql in CI.

**Client profile:** All port calls are transparently proxied to the enterprise server's HTTP API. The MCP tool schema exposed to agents is identical to the local profile — switching profiles does not change agent interaction syntax.

**Profile Factory (selected approach):**

```python
# profiles/local.py
def create_local_runtime(config: LocalConfig) -> MarivoRuntime:
    return MarivoRuntime(
        model_store=FileModelStore(config.models_dir),
        session_store=SqliteSessionStore(config.state_db),
        evidence_store=FileEvidenceStore(config.evidence_dir),
        data_source=DuckDBDataSource(),
        cache_store=SqliteCacheStore(config.state_db),
        authz=NoopAuthZ(),
        audit_log=FileAuditLog(config.audit_dir),
        telemetry=LocalTelemetry(),
        runtime_config=TomlRuntimeConfig(config.config_file),
    )

# profiles/server.py
def create_server_runtime(config: ServerConfig) -> MarivoRuntime:
    return MarivoRuntime(
        model_store=SqlModelStore(config.db_url),
        session_store=SqlSessionStore(config.db_url),
        evidence_store=S3EvidenceStore(config.s3_config),
        ...
    )
```

**Profile selection:** entry points (CLI / MCP startup) read `profile` from `marivo.toml` and call the corresponding factory to obtain a fully-wired `MarivoRuntime`.

---

## 8. Local No-Service Mode

**Process model:**

```
Agent (Claude / any MCP client)
  └─ spawn marivo-stdio (short-lived process)
       └─ embedded MarivoRuntime
            ├─ SqliteSessionStore  (state.db, WAL mode)
            ├─ FileModelStore      (models/)
            ├─ FileEvidenceStore   (evidence/)
            └─ DuckDBDataSource    (in-process)
```

One process per MCP invocation. No daemon. No background service.

**Local state layout:**

```
.marivo/
  marivo.toml        # profile = "local", datasource config, etc.
  models/            # semantic model files (yaml / json)
  state.db           # SQLite WAL: session events + cache
  evidence/          # hash-addressed evidence files
  sessions/          # session event jsonl (append-only)
  cache/             # optional local query cache
```

**Invariants:**
- SQLite WAL mode enabled; supports concurrent multi-process reads and writes without lock files.
- Evidence file writes: write to `tmp-<uuid>` first, then atomic rename — no partial writes.
- Session events are append-only; no update, no delete.
- No long transactions held across MCP requests; each request opens and commits its own transaction.
- `marivo init` creates the directory structure and default `marivo.toml` when `.marivo/` does not exist.

---

## 9. Enterprise Mode

**Deployment topology:**

```
ChatBI / Agent / UI / SDK
  └─ HTTP API / MCP Gateway
       └─ Marivo Server
            ├─ SqlModelStore        (pg / mysql)
            ├─ SqlSessionStore      (pg / mysql)
            ├─ S3EvidenceStore
            ├─ Trino / Snowflake / BQ DataSource
            ├─ OidcRbacAuthZ
            ├─ CentralizedAuditLog
            └─ OtelTelemetry
```

**Enterprise-only capabilities** (not implemented by local profile):

| Capability | Description |
|-----------|-------------|
| Semantic model registry | Public model publication + private workspace isolation |
| Approval / publish workflow | Model publication requires approval; triggered via a publish operation through the Runtime layer, not exposed in the base `ModelStore` Protocol |
| Session-level user identity | `X-Marivo-User` header propagated; `AuthZ` validates |
| Audit trail | Every runtime operation recorded to `AuditLog` |
| Centralized policy | `AuthZ` + `policy/` rules managed centrally |
| Engine routing | Routes to the appropriate `DataSource` adapter by source type |
| Observability | OTel traces and metrics, integrated with existing monitoring stack |
| Multi-agent integration | Multiple agents share a session with identity isolation |

---

## 10. Surfaces Design

All surfaces do protocol translation only. No business logic.

**CLI** (`marivo/transports/cli/`)
- `marivo init` — initialize `.marivo/` directory structure and default `marivo.toml`
- `marivo doctor` — check environment, configuration, and connectivity
- `marivo profile` — view or switch current profile
- `marivo push/pull` — sync semantic models with enterprise server
- `marivo import/export` — import/export datasources and models

**MCP** (`marivo/transports/mcp/`, merged from `marivo-mcp/`)

Supports two transport protocols:
- **stdio** — local agent default; short-lived process; embedded runtime
- **HTTP MCP** — enterprise scenario or persistent MCP server (multi-agent sharing, remote MCP clients)

Both transports share the same MCP tool schema and handler implementations. Runtime mode (embedded vs. client) is orthogonal to transport protocol, determined by `profile` in `marivo.toml`:

| Transport | Runtime mode | Scenario |
|-----------|-------------|----------|
| stdio | embedded | Local single-user |
| stdio | client | Local agent proxying to enterprise server |
| HTTP MCP | server | Enterprise managed MCP gateway |
| HTTP MCP | client | Bridge scenario |

**HTTP API** (`marivo/transports/http/`)
Enterprise canonical remote API, FastAPI implementation. Integration entry point for UI / ChatBI / SDK. Target endpoint for MCP client mode proxy.

**SDK** (`marivo/transports/sdk/`)
- Embedded: `from marivo.profiles.local import create_local_runtime` — use runtime in-process
- Remote client: `create_client_runtime()` — connect to enterprise server

**Web Admin** (`frontend/`)
Management UI. Continues with existing frontend implementation; not in scope for this spec.

---

## 11. Testing Strategy

**Core tests** (`tests/core/`)
- Pure unit tests, zero I/O, no mocks.
- Property-based tests: intent + model → plan stability; evidence hash determinism.
- Invariant verification: same input produces same plan hash; evidence replay produces identical result.

**Adapter contract tests** (`tests/contracts/`)
- Each Port defines a contract test suite (pytest parametrize).
- Same suite runs against all implementations: `SqliteSessionStore`, `SqlSessionStore(pg)`, `SqlSessionStore(mysql)`, `FileEvidenceStore`, `S3EvidenceStore`, etc.
- A new adapter may not merge without passing its Port's contract test.

**Surface tests** (`tests/surfaces/`)
- CLI: argument parsing, `init` / `doctor` behavior.
- MCP: tool schema correctness, stdio and HTTP transport handlers.
- HTTP API: OpenAPI schema compliance, route-level behavior.

**E2E golden tests** (`tests/e2e/`)
- Session jsonl replay: record a complete session; replay produces zero evidence diff.
- Local / server profile behavior parity: same intent produces equivalent results under both profiles.
- Evidence hash stability: cross-version replay does not change hash values.

**CI rules:**
- `import-linter` enforces `core/` import boundary; violation fails the build.
- Adapter contract tests run on local adapters by default; server adapter tests run in CI environments with the corresponding service available.

---

## 12. Migration Path

| Phase | Name | Deliverable | Acceptance Criteria |
|-------|------|-------------|---------------------|
| 1 | Target Architecture | This spec document | Spec merged to main branch |
| 2 | Contracts & Ports | `marivo/contracts/` + `marivo/ports/` Protocol definitions; `app/` renamed to `marivo/` | import-linter rules pass; all existing tests green |
| 3 | Runtime Decoupling | HTTP service calls Runtime use-case layer; no direct core imports from service layer | Service layer has no direct core imports; existing E2E tests green |
| 4 | Local Embedded Runtime | `create_local_runtime()` factory; MCP stdio embedded mode operational | Local profile E2E golden tests pass |
| 5 | MCP Dual Mode | MCP supports embedded / client dual mode; HTTP MCP transport | Client mode proxy integration tests pass |
| 6 | Profile System | local / server / client three profile factories; adapter contract tests | Contract tests pass across all adapter implementations |
| 7 | Convergence & Lock-in | E2E golden tests; behavior parity tests; documentation updated; import boundary enforced in CI | All tests green; architectural invariants documented; no import violations |

**Migration principles:**
- Existing functionality must not regress at the end of each phase (green tests are the gate).
- Phases 2–3 are pure refactoring; no observable behavior change.
- Phase 4 introduces new capability (local no-service mode).
- Phases may overlap, but Phase 3 must complete before Phase 4 begins.

---

## 13. Non-Goals

- Local mode does not start an HTTP service by default.
- MCP does not become the canonical business contract.
- CLI does not become the primary agent analysis protocol.
- The service layer does not own core business rules.
- Local and enterprise do not maintain separate analysis logic.
- No Rust migration design — this spec is purely Python architecture.
