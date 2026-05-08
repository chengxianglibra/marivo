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
- Local and enterprise expose the same MCP tool names and schemas. Authorization-driven differences (e.g. enterprise `AuthZ` rejecting an action) appear as standard error responses, not new tools or new request shapes.
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

The tree below is the **target logical structure**. Under the contract-first strangler strategy, physical package names may remain under `app/` during the early extraction phases. The repo-wide `app/` -> `marivo/` rename is a later mechanical cutover after the shared Runtime facade and profile seams are already proven. All MCP entry-points live in `marivo/transports/mcp/` from Phase 5 onward; no separate compatibility distribution exists. `marivo-skill/` (a Claude Code skill definition file, not Python code) stays out of scope of this spec.

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
  transports/        # MCP (stdio + HTTP) + existing surface code
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

**Migration rule:** existing modules are extracted toward the above structure by responsibility. Early strangler phases prioritize new seams (`contracts/`, `ports/`, `runtime/`) over physical renames. Mechanical namespace moves must not be bundled with boundary-defining refactors in the same phase.

---

## 5. Core Engine Design

**Responsibilities:**

| Area | Module |
|------|--------|
| Semantic model parsing, validation, publish-state determination | `core/semantic/` |
| Typed intent compilation | `core/intent/` |
| Logical plan generation and execution-independent plan transforms | `core/planner/` |
| Evidence / finding / proposition / assessment generation | `core/evidence/` |
| Lineage, hash, replay semantics | `core/lineage/` |
| Policy rule evaluation | `core/policy/` |

**Non-responsibilities (hard boundaries):**
- Does not read or write files.
- Does not connect to databases.
- Does not call `DataSource` or any other Port directly.
- Does not handle HTTP / MCP / CLI protocols.
- Does not know whether it is running in local or enterprise mode.
- Does not import any adapter or transport module.

**Execution boundary:** Core produces typed plans, validation results, and pure transformations. Runtime owns execution orchestration over Ports: selecting the `DataSource`, issuing queries, committing session events, persisting evidence, and handling retries / authorization / telemetry side effects. This separation is mandatory because `core/` is not allowed to import `ports/`.

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
class ModelSelector(Protocol):
    model_id: ModelId | None
    name: str | None
    revision: RevisionId | None

class ModelListQuery(Protocol):
    owner: UserId | None
    visibility: str | None
    include_public: bool
    include_private: bool

class ModelStore(Protocol):
    def get(self, selector: ModelSelector) -> SemanticModel | None: ...
    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId: ...
    def list(self, query: ModelListQuery) -> list[ModelSummary]: ...

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
class AuthZDecision(Protocol):
    allowed: bool
    code: str | None
    message: str | None
    detail: dict[str, Any]

class AuthZ(Protocol):
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision: ...

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
- `EvidenceStore.write` returns a hash-bearing `EvidenceRef`, ensuring referenceability and replayability. Hash algorithm: **sha256**. Hash input must be canonicalized: JSON with sorted keys, UTF-8, no whitespace, integer-typed numbers where possible (no float ambiguity). The same canonical form is used for replay-stability tests.
- `AuthZ` is implemented by `NoopAuthZ` in local profile (returns an allowed decision with empty reason fields).
- `ModelStore` read semantics must support public + private visibility, revision-aware reads, and approval/publish workflows implemented in Runtime. Runtime must not need adapter-specific escape hatches to express model visibility rules.
- Every Port has an adapter contract test suite; all implementations must pass it.

---

## 7. Adapters & Profiles

**Adapter implementation matrix (storage / execution Ports only):**

| Port | local adapter | server adapter |
|------|--------------|----------------|
| `ModelStore` | `FileModelStore` | `SqlModelStore` (mysql) |
| `SessionStore` | `SqliteSessionStore` | `SqlSessionStore` (mysql) |
| `EvidenceStore` | `FileEvidenceStore` | `S3EvidenceStore` |
| `DataSource` | `DuckDBDataSource` | `Trino/Snowflake/BQDataSource` |
| `CacheStore` | `SqliteCacheStore` | `RedisCacheStore` |
| `AuthZ` | `NoopAuthZ` | `OidcRbacAuthZ` |
| `AuditLog` | `FileAuditLog` | `CentralizedAuditLog` |
| `Telemetry` | `LocalTelemetry` | `OtelTelemetry` |

**Server SQL adapters:** `SqlModelStore` and `SqlSessionStore` use SQLAlchemy Core, accepting a `db_url` connection string. Current supported backend: MySQL only. The adapter contract suite runs against MySQL in CI.

**No client profile:** There is no client profile. Remote agents connect to the enterprise server via HTTP MCP transport (mounted on the same FastAPI app at `/mcp`). The MCP tool schema is identical between local stdio and enterprise HTTP MCP because both surfaces target the same `MarivoRuntime` semantics. Authorization differences (e.g. enterprise `AuthZ` rejecting an action) appear as standard error responses.

**Local-mode safety guard:** The local profile factory must refuse to construct a `MarivoRuntime` when the runtime detects an enterprise deployment context (e.g. `MARIVO_DEPLOYMENT=server` env var, or presence of a `server.toml` config). This prevents `NoopAuthZ` from accidentally bypassing enterprise authorization.

**LocalTelemetry default:** `LocalTelemetry` is no-op by default. Local users opt-in via `marivo.toml` (`[telemetry] sink = "file"`), which writes to `.marivo/telemetry.jsonl`. No telemetry leaves the host without explicit opt-in.

**FileModelStore concurrency rule:** local semantic model writes use optimistic concurrency. Each write must include an `expected_revision` (or equivalent content digest) captured from the last read. The adapter writes to a temp file and atomically renames into place only when the on-disk revision still matches the expected one; otherwise it raises a conflict that the caller must surface. Silent last-writer-wins overwrites are forbidden.

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

**Profile selection authority:** profile resolution is explicit and entry-point-specific.

| Entry point | Precedence order |
|------------|------------------|
| local CLI / local stdio MCP | explicit flag > env var > workspace `.marivo/marivo.toml` > default `local` |
| client CLI / client stdio MCP | explicit flag > env var > user client config > workspace `.marivo/marivo.toml` |
| server HTTP / server HTTP MCP | explicit flag > env var > service config file; ambient workspace `.marivo/` is ignored unless explicitly pointed to |

Profile auto-detection must never let an enterprise server bind itself from a nearby developer workspace config by accident.

---

## 8. Local No-Service Mode

**Process model:**

```
Agent (Claude / any MCP client)
  └─ spawn marivo-stdio (short-lived child process for one MCP client session)
       └─ embedded MarivoRuntime
            ├─ SqliteSessionStore  (state.db, WAL mode)
            ├─ FileModelStore      (models/)
            ├─ FileEvidenceStore   (evidence/)
            └─ DuckDBDataSource    (in-process)
```

One child process per MCP client session. The process may serve many tool calls until stdin closes. No daemon. No background service.

**Local state layout:**

```
.marivo/
  marivo.toml        # profile = "local", datasource config, etc.
  models/            # semantic model files (yaml / json)
  state.db           # SQLite WAL: canonical session event log + cache metadata
  evidence/          # hash-addressed evidence files
  sessions/          # optional exported session jsonl projections (derived from state.db)
  cache/             # optional local query cache
```

**Invariants:**
- SQLite WAL mode enabled; supports concurrent multi-process reads and writes without lock files.
- WAL relies on local POSIX file locking. `.marivo/` on a network filesystem (NFS / SMB / CIFS) is unsupported; `marivo doctor` warns when it detects one.
- `state.db` is the canonical append-only session system of record. `sessions/` is a derived export/debug projection only; it must be rebuildable from `state.db` and must never be read as the primary source of truth.
- Startup opens SQLite first and relies on SQLite's own crash-recovery path. Pre-open deletion of `state.db-wal` / `state.db-shm` is forbidden. Any post-recovery maintenance must happen only after the database is opened successfully and recovery has completed.
- Evidence file writes: write to `tmp-<uuid>` first, then atomic rename — no partial writes.
- Session events are append-only; no update, no delete.
- No long transactions held across MCP requests; each request opens and commits its own transaction.
- `marivo init` creates the directory structure and default `marivo.toml` when `.marivo/` does not exist.
- Cold-start cost: starting a fresh stdio session adds ~200–500 ms (Python import + DuckDB init). Per-tool-call cost within the same MCP child process should not assume a full process restart. A warm-cache / daemon-mode opt-in is a candidate for post-Phase-7 work, not core Phase 4 scope.

---

## 9. Enterprise Mode

**Deployment topology:**

```
ChatBI / Agent / UI / SDK
  └─ HTTP API / MCP Gateway
       └─ Marivo Server
            ├─ SqlModelStore        (mysql)
            ├─ SqlSessionStore      (mysql)
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
| Session-level user identity | `X-Marivo-User` header is a trusted propagation header; the authenticated edge injects it and `AuthZ` validates the canonical actor |
| Audit trail | Every runtime operation recorded to `AuditLog` |
| Centralized policy | `AuthZ` + `policy/` rules managed centrally |
| Engine routing | Routes to the appropriate `DataSource` adapter by source type |
| Observability | OTel traces and metrics, integrated with existing monitoring stack |
| Multi-agent integration | Multiple agents share a session with identity isolation |

**Identity trust boundary:** User authentication is out of scope for the current phase set. `X-Marivo-User` is a propagation header trusted from the calling environment; marivo passes it through to `current_user` ContextVar without validation. A trusted-edge design (Bearer + token introspection + strip-and-reinject) is deferred to a dedicated future phase. Deploying environments that need real authentication MUST gate marivo behind their own authenticated proxy.

---

## 10. Surfaces Design

All surfaces do protocol translation only. No business logic.

**CLI** (`marivo/transports/cli/`)
- `marivo init` — initialize `.marivo/` directory structure and default `marivo.toml`
- `marivo doctor` — check environment, configuration, and connectivity
- `marivo profile` — view or switch current profile
- `marivo push/pull` — sync semantic models with enterprise server
- `marivo import/export` — import/export datasources and models

**MCP** (`marivo/transports/mcp/`)

Supports two transport protocols:
- **stdio** — local agent default; short-lived process; embedded runtime
- **HTTP MCP** — enterprise scenario or persistent MCP server (multi-agent sharing, remote MCP clients)

Both transports share the same MCP tool schema and handler implementations. Runtime mode (embedded vs. client) is orthogonal to transport protocol, determined by `profile` in `marivo.toml`:

| Transport | Runtime mode | Scenario |
|-----------|-------------|----------|
| stdio | embedded | Local single-user |
| HTTP MCP | server | Enterprise managed MCP gateway |

**HTTP API** (`marivo/transports/http/`)
Enterprise canonical remote API, FastAPI implementation. Integration entry point for UI / ChatBI / SDK. Target endpoint for MCP client mode proxy.

**SDK** (`marivo/transports/sdk/`)
- Embedded: `from marivo.profiles.local import create_local_runtime` — use runtime in-process
- Remote use: connect to enterprise `marivo serve` via HTTP API or HTTP MCP transport. There is no `create_client_runtime()` factory.

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
- Same suite runs against all implementations: `SqliteSessionStore`, `SqlSessionStore(mysql)`, `FileEvidenceStore`, `S3EvidenceStore`, etc.
- A new adapter may not merge without passing its Port's contract test.

**Surface tests** (`tests/surfaces/`)
- CLI: argument parsing, `init` / `doctor` behavior.
- MCP: tool schema correctness, stdio and HTTP transport handlers.
- HTTP API: OpenAPI schema compliance, route-level behavior.

**E2E golden tests** (`tests/e2e/`)
- Session jsonl replay: record a complete session; replay produces zero evidence diff.
- Local / server profile behavior parity: same intent produces equivalent results under both profiles.
- Evidence hash stability: cross-version replay does not change hash values. Stability is defined against the canonical form in §6 (sha256 over sorted-key UTF-8 JSON), not over Python pickle or library-default serialization.

**CI rules:**
- `import-linter` enforces `core/` import boundary; violation fails the build.
- Local adapter contract tests run in every CI pass.
- Server adapter contract tests and local/server parity tests are mandatory Phase 6 CI jobs with provisioned MySQL infrastructure (testcontainers or an approved equivalent). Phase 6 cannot close while server-profile CI is "best effort" or externally optional.

---

## 12. Migration Path

Sequencing follows a **contract-first strangler** strategy: define the shared contracts and Runtime seam first, move existing HTTP and MCP paths behind that seam, then add local embedded mode and client/server profiles on top of the stabilized boundary.

| Order | Phase | Name | Deliverable | Acceptance Criteria |
|-------|-------|------|-------------|---------------------|
| 1 | 1 | Target Architecture | This spec document | Spec merged to main branch |
| 2 | 2 | Contracts & Ports | `contracts/` + `ports/` Protocol definitions land inside the current package root; no repo-wide rename required yet | import-linter rules pass for the new seams; all existing tests green; HTTP and MCP can compile against the new contract types |
| 3 | 3 | Runtime Decoupling | Shared Runtime facade introduced; HTTP service and MCP both call Runtime use-case layer; `core/` is I/O-free and execution over Ports lives in Runtime | Service and MCP handlers have no direct core orchestration logic; `core/` import-linter rules pass; existing E2E tests green |
| 4 | 4 | Local Embedded Runtime | `create_local_runtime()` factory; MCP stdio embedded mode operational against the shared Runtime seam | An agent + Marivo can finish a sample analysis end-to-end in local mode against `.marivo/`. MCP session-lifetime behavior is documented and tested. |
| 5 | 6 | Profile System | local / server / client three profile factories; adapter contract tests; behavior parity test infrastructure (testcontainers or chosen alternative) | Contract tests pass across all adapter implementations; parity tests run in CI |
| 6 | 5 | MCP Dual Mode | HTTP MCP transport mounted on enterprise FastAPI app; stdio + HTTP MCP share an identical tool registration; legacy `marivo-mcp/` package and all client/proxy abstractions removed | HTTP MCP end-to-end integration test passes; tool schema parity test passes between stdio and HTTP MCP |
| 7 | 7 | Namespace Cutover & Convergence | `app/` -> `marivo/` mechanical rename, packaging/documentation cutover, E2E golden tests, import boundary enforced in CI | All tests green; architectural invariants documented; no remaining `from app.*` imports; no `marivo-mcp` distribution remains (removed in Phase 5); only `app/` -> `marivo/` mechanical rename and import boundary enforcement |

**Migration principles:**
- Existing functionality must not regress at the end of each phase (green tests are the gate).
- Phases 2 and 3 establish the strangler seam first; local embedded mode is not allowed to bypass or preempt that seam.
- Repository-wide namespace renames are mechanical follow-on work, not the vehicle for boundary design.
- Phase 6 (Profile System) precedes Phase 5 (MCP Dual Mode) because dual-mode MCP requires a working profile factory to choose between embedded and client runtimes.

> **Note on numbering:** the *Phase* column preserves the original phase identities used elsewhere in this document (and in tooling). The *Order* column is the actual execution sequence.

---

## 13. Non-Goals

- Local mode does not start an HTTP service by default.
- MCP does not become the canonical business contract.
- CLI does not become the primary agent analysis protocol.
- The service layer does not own core business rules.
- Local and enterprise do not maintain separate analysis logic.
- No Rust migration design — this spec is purely Python architecture.
- User authentication and authorization in marivo are out of scope for the current phase set. `X-Marivo-User` is a trusted propagation header injected by the deploying environment; marivo does not validate it. A trusted-edge auth design is deferred to a dedicated future phase.
