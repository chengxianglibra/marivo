# Phase 3: Runtime Decoupling Design

**Date:** 2026-05-07
**Status:** Approved
**Parent spec:** `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`

---

## 1. Scope & Objective

Phase 3 introduces the shared Runtime facade and extracts pure domain logic into `core/`, decoupling HTTP/MCP surfaces from direct `SemanticLayerService` orchestration. The `SemanticLayerService` god object (3,440 lines) is progressively dismantled; intent runners migrate from `(svc, session_id, params)` to `(core, ports, session_id, params)`.

**Acceptance criteria** (from parent spec):

1. Service and MCP handlers have no direct core orchestration logic — they call Runtime use-case methods only.
2. `core/` import-linter rules pass — no I/O imports, no port imports.
3. Existing E2E tests remain green.

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Call granularity | Use-case level (`runtime.observe()`, `runtime.diagnose()`) | Surfaces should not know about steps, plans, or internal modules |
| Domain logic placement | Core owns domain logic, accepts pure inputs | Core is I/O-free; Runtime orchestrates core + ports |
| Intent runner interface | Runtime as context with narrow public API | Intent runners receive `(core, ports, session_id, params)` — 2 typed objects + 2 params instead of 50+ method god object. Narrowness comes from Runtime's own API design, not a separate Protocol |
| Evidence pipeline | Core owns logic, Runtime owns I/O | Finding extraction, proposition seeding, assessment recompute are pure computation; persistence is I/O |
| Migration pace | Incremental sub-phases (3a→3b→3c→3d) | Strangler strategy: each sub-phase has green-test gate |
| core/ directory | New `core/` facade, gradual drain from `analysis_core/` | No big-bang rename; pure logic copied to `core/` first, `analysis_core/` deprecated then deleted |
| Dependency injection | Constructor injection | `MarivoRuntime(ports, core)` — matches Phase 1 spec factory pattern, no DI framework |

---

## 3. RuntimePorts Container

`RuntimePorts` is a typed container holding all port instances. Phase 3 constructs it from existing infrastructure via adapter wrappers; Phase 6 profile factories will construct it with real adapter implementations.

```python
# app/runtime/ports.py
from app.ports.model_store import ModelStore
from app.ports.session_store import SessionStore
from app.ports.evidence_store import EvidenceStore
from app.ports.data_source import DataSource
from app.ports.cache_store import CacheStore
from app.ports.authz import AuthZ
from app.ports.audit_log import AuditLog
from app.ports.telemetry import Telemetry
from app.ports.runtime_config import RuntimeConfig

class RuntimePorts:
    """Typed container for all port implementations."""
    def __init__(
        self,
        model_store: ModelStore,
        session_store: SessionStore,
        evidence_store: EvidenceStore,
        data_source: DataSource,
        cache_store: CacheStore,
        authz: AuthZ,
        audit_log: AuditLog,
        telemetry: Telemetry,
        runtime_config: RuntimeConfig,
    ) -> None:
        self.model_store = model_store
        self.session_store = session_store
        self.evidence_store = evidence_store
        self.data_source = data_source
        self.cache_store = cache_store
        self.authz = authz
        self.audit_log = audit_log
        self.telemetry = telemetry
        self.runtime_config = runtime_config
```

---

## 4. MarivoRuntime Class

`MarivoRuntime` holds `RuntimePorts` + `CoreEngine`. All use-case methods are on this class. HTTP/MCP surfaces call these methods only.

```python
# app/runtime/runtime.py
from app.contracts.ids import SessionId, UserId, ModelId, DatasourceId
from app.contracts.semantic import SemanticModel, ModelSummary
from app.contracts.session import SessionState

class MarivoRuntime:
    def __init__(self, ports: RuntimePorts, core: CoreEngine) -> None:
        self._ports = ports
        self._core = core

    # --- Intent use-cases (HTTP/MCP entry points) ---
    def observe(self, session_id: SessionId, params: dict) -> dict: ...
    def compare(self, session_id: SessionId, params: dict) -> dict: ...
    def decompose(self, session_id: SessionId, params: dict) -> dict: ...
    def detect(self, session_id: SessionId, params: dict) -> dict: ...
    def correlate(self, session_id: SessionId, params: dict) -> dict: ...
    def test(self, session_id: SessionId, params: dict) -> dict: ...
    def forecast(self, session_id: SessionId, params: dict) -> dict: ...
    def attribute(self, session_id: SessionId, params: dict) -> dict: ...
    def diagnose(self, session_id: SessionId, params: dict) -> dict: ...
    def validate(self, session_id: SessionId, params: dict) -> dict: ...

    # --- Session lifecycle ---
    def create_session(self, goal: str, *, actor: UserId) -> SessionState: ...
    def get_session(self, session_id: SessionId) -> SessionState: ...
    def terminate_session(self, session_id: SessionId) -> None: ...
    def get_session_state(self, session_id: SessionId, **filters) -> dict: ...

    # --- Semantic model ops ---
    def get_semantic_model(self, selector) -> SemanticModel | None: ...
    def save_semantic_model(self, model: SemanticModel, *, actor: UserId) -> ModelId: ...
    def list_semantic_models(self, query) -> list[ModelSummary]: ...

    # --- Datasource ops ---
    def discover_catalog(self, datasource_id: DatasourceId, ...) -> dict: ...
```

### 4.1 Phase 3a CoreEngine Proxy

During Phase 3a, `CoreEngine` proxies to `SemanticLayerService` for domain computation. This avoids a big-bang extraction while allowing Runtime to have a clean internal API.

```python
# app/core/engine.py (Phase 3a)
class CoreEngine:
    """Phase 3a: proxies to SemanticLayerService.
    Phase 3c: replaced with real core modules."""
    def __init__(self, svc: SemanticLayerService) -> None:
        self._svc = svc

    # Pure domain computation proxies
    def resolve_metric_execution_context(self, *args, **kwargs):
        return self._svc._resolve_metric_execution_context(*args, **kwargs)

    def compile_step(self, *args, **kwargs):
        return self._svc._compile_step_with_feedback(*args, **kwargs)

    def build_step_semantic_metadata(self, *args, **kwargs):
        return self._svc.build_step_semantic_metadata(*args, **kwargs)

    # Note: _commit_artifact_with_extraction and _insert_step are I/O
    # operations and go through ports, not core. During 3a they are
    # called directly on Runtime (which proxies to svc).
```

### 4.2 Phase 3c CoreEngine (Real Core)

After core extraction, `CoreEngine` delegates to extracted pure modules:

```python
# app/core/engine.py (Phase 3c)
class CoreEngine:
    """Real core: delegates to extracted pure modules."""
    def __init__(self, semantic_repo_data: SemanticRepoData) -> None:
        self._semantic = SemanticModule(semantic_repo_data)
        self._evidence = EvidenceModule()

    def resolve_metric_execution_context(self, metric_name, model, ...):
        return metric_resolution.resolve(metric_name, model, ...)

    def compile_step(self, step_ir, ...):
        return compiler.compile(step_ir, ...)

    def extract_findings(self, artifact_type, artifact_data, ...):
        return finding_extraction.extract(artifact_type, artifact_data, ...)
```

---

## 5. Port Adapter Wrappers

Phase 3a wraps existing infrastructure into port implementations. These are thin adapters that proxy to existing classes and perform type conversion between `app.api.models.*` and `app.contracts.*`.

### 5.1 Wrapper Definitions

```python
# app/adapters/server/wrappers.py

class SqlModelStoreAdapter:
    """Wraps MetadataStore + SemanticModelV2Service → ModelStore."""
    def __init__(self, metadata: MetadataStore, semantic_svc: SemanticModelV2Service) -> None: ...

class SqlSessionStoreAdapter:
    """Wraps MetadataStore + SessionManager → SessionStore."""
    def __init__(self, metadata: MetadataStore, session_mgr: SessionManager) -> None: ...

class DataSourceAdapter:
    """Wraps AnalyticsEngine instances + RoutingRuntime → DataSource."""
    def __init__(self, engines: dict[EngineId, AnalyticsEngine], routing: RoutingRuntime) -> None: ...

class MetadataEvidenceStoreAdapter:
    """Wraps evidence repositories → EvidenceStore."""
    def __init__(self, repos: EvidenceRepositories) -> None: ...

class NoopAuthZAdapter:
    """Phase 3a: always-allow. Phase 6: replaced by OidcRbacAuthZ."""
    def check(self, actor, action, resource) -> AuthZDecision:
        return AuthZDecision(allowed=True)

class MetadataCacheStoreAdapter:
    """Wraps MetadataStore → CacheStore."""
    def __init__(self, metadata: MetadataStore) -> None: ...

class FileAuditLogAdapter:
    """Phase 3a: logs to Python logger."""
    def record(self, entry: AuditEntry) -> None: ...

class LocalTelemetryAdapter:
    """Phase 3a: no-op."""
    def emit(self, event: TelemetryEvent) -> None: ...

class TomlRuntimeConfigAdapter:
    """Wraps MarivoConfig → RuntimeConfig."""
    def __init__(self, config: MarivoConfig) -> None: ...
```

### 5.2 Type Conversion Rules

Each wrapper is responsible for bidirectional type conversion between HTTP models and domain contracts:
- `to_domain()`: convert `app.api.models.*` → `app.contracts.*` on read
- `from_domain()`: convert `app.contracts.*` → `app.api.models.*` on write

This conversion boundary is the reconciliation point between the two type systems (as noted in the Phase 2 spec §11 "Drift mitigation").

### 5.3 Wrapper Location

Phase 3a: `app/adapters/server/wrappers.py`. Phase 6 will reorganize into `app/adapters/server/` with proper module structure.

### 5.4 Not Wrapped

`SessionManager`'s full session lifecycle (create/get/terminate/state derivation) is not wrapped through the `SessionStore` port in Phase 3. `SessionManager` does CRUD + ownership checks + status derivation, which is richer than the event-sourced `append_event/load_events` port. The `SessionStore` port gets a full implementation in Phase 6. Phase 3a's `SqlSessionStoreAdapter` provides a minimal bridge.

---

## 6. Core Extraction Boundaries

### 6.1 Principle

Code in `core/` accepts only pure data inputs and returns pure data outputs. No port calls, no file reads, no database connections, no HTTP. If it needs I/O, it doesn't belong in core.

### 6.2 Core Module Layout

```
app/core/
  __init__.py
  engine.py           # CoreEngine facade (3a: proxy, 3c: real)
  semantic/
    __init__.py
    metric_resolution.py    # From service.py: ~700 lines
    scope_resolution.py     # From service.py: ~400 lines
    compiler.py             # From analysis_core/compiler.py (gradual)
    ir.py                   # From analysis_core/ir.py (gradual)
    validator.py            # From analysis_core/validator.py (gradual)
    typed_resolution.py     # From analysis_core/typed_resolution.py (gradual)
    calendar.py             # From analysis_core/calendar_*.py (merged)
    additivity.py           # From analysis_core/additivity_capabilities.py
  intent/
    __init__.py
    primitives.py           # From analysis_core/primitives.py
    step_registry.py        # From analysis_core/step_registry.py
    intent_registry.py      # From analysis_core/intent_registry.py
  evidence/
    __init__.py
    finding_extraction.py   # From evidence_engine/ extractors
    proposition_seeding.py  # From evidence_engine/proposition_seeding_run.py
    assessment.py           # From evidence_engine/assessment_recompute.py
    proposal.py             # From evidence_engine/proposal_refresh_run.py
    publish.py              # From evidence_engine/publish_switch.py
```

### 6.3 Extraction Rules

| Current location | Logic | Target | Extraction method |
|-----------------|-------|--------|-------------------|
| `service.py._resolve_metric_execution_context` (~700 lines) | Metric binding, route preflight, execution context | `core/semantic/metric_resolution.py` | Extract method body; replace `self._metadata` etc. with pre-loaded parameters |
| `service.py._resolve_scope_constraint_column` + `_constraints_dict_to_filter` + `_resolved_scope_filter` + `_resolve_predicate_ref_to_filter` + `_predicate_expression_to_sql` + `_build_scoped_query` (~400 lines) | Scope constraint resolution, predicate SQL generation | `core/semantic/scope_resolution.py` | Extract pure computation; metadata-dependent parts accept pre-loaded semantic model as parameter |
| `service.py._compile_step_with_feedback` | SQL orchestration | `core/semantic/compiler.py` | Proxy to `analysis_core/compiler.compile_step`, inline gradually |
| `analysis_core/compiler.py` (~1970 lines) | SQL compilation | `core/semantic/compiler.py` | Migrate gradually; current `compiler.py` accepts `SemanticRuntimeRepository` (has I/O), refactor to accept pre-resolved data |
| `analysis_core/ir.py` (~659 lines) | IR definitions | `core/semantic/ir.py` | Direct migration (pure data definitions) |
| `analysis_core/validator.py` (~1138 lines) | Input validation | `core/semantic/validator.py` | Migrate gradually; most is pure validation, few repository lookups need refactoring |
| `analysis_core/typed_resolution.py` (~862 lines) | Semantic resolution | `core/semantic/typed_resolution.py` | Migrate gradually; current `SemanticRuntimeRepository` dependency needs refactoring |
| `evidence_engine/canonical_finding.py` | Finding ID generation, extraction logic | `core/evidence/finding_extraction.py` | Extract `StepRef`, finding ID generation as pure functions |
| `evidence_engine/observe_extractor.py` + other extractors (~1900 lines) | Finding extraction | `core/evidence/finding_extraction.py` | Extract extraction logic as pure functions (input: artifact dict → output: findings) |
| `evidence_engine/proposition_seeding_run.py` (~1073 lines) | Proposition seeding | `core/evidence/proposition_seeding.py` | Extract seed rules and proposition generation as pure functions |
| `evidence_engine/assessment_recompute.py` (~1661 lines) | Assessment computation | `core/evidence/assessment.py` | Extract evaluation logic as pure functions |
| `evidence_engine/proposal_refresh_run.py` (~852 lines) | Action proposal generation | `core/evidence/proposal.py` | Extract proposal generation logic |

### 6.4 Logic That Stays in Runtime

| Logic | Reason |
|-------|--------|
| Step execution orchestration (core compile → port execute → core extract → port persist) | Orchestration = Runtime responsibility |
| Engine routing (select datasource, resolve route) | Needs port/state access |
| Artifact + evidence persistence (write repos, call `MetadataStore`) | I/O |
| Session lifecycle (create/terminate/status derivation) | I/O + business orchestration |
| Calendar data loading (read holiday data) | I/O |
| Provenance construction | Needs current actor, timestamp, runtime context |

### 6.5 Gradual Migration from analysis_core/

`analysis_core/` is not migrated in one shot. Strategy:

1. **Phase 3a:** `core/` has only `engine.py` (proxy to service.py)
2. **Phase 3b:** When intent runners need a method, copy it from `analysis_core/` to `core/semantic/` (original `analysis_core/` files unchanged)
3. **Phase 3c:** When `core/semantic/` version is stable and all callers have switched, delete corresponding `analysis_core/` files
4. **Phase 3d:** `analysis_core/` fully drained, delete directory

This ensures existing `analysis_core/` callers (service.py, intent runners) continue working during migration without a single cutover point.

---

## 7. Sub-Phase Breakdown

### Phase 3a — Runtime Shell + Port Wiring

**Goal:** Runtime facade in place, HTTP/MCP switch to calling Runtime. Runtime internally proxies to `SemanticLayerService`.

**Deliverables:**

| Deliverable | Description |
|------------|-------------|
| `app/runtime/__init__.py` | Package init |
| `app/runtime/ports.py` | `RuntimePorts` container |
| `app/runtime/runtime.py` | `MarivoRuntime` class, all use-case methods proxy to `svc` |
| `app/core/__init__.py` | Package init |
| `app/core/engine.py` | `CoreEngine` proxy class |
| `app/adapters/server/wrappers.py` | Port adapter wrappers |
| `app/runtime/session_ops.py` | Session lifecycle proxy (calls `SessionManager`) |
| `app/runtime/semantic_ops.py` | Semantic model ops proxy (calls `SemanticModelV2Service`) |

**Factory function:**

```python
def create_runtime_from_service(
    svc: SemanticLayerService,
    session_mgr: SessionManager,
    semantic_svc: SemanticModelV2Service,
    datasource_svc: DatasourceService,
    config: MarivoConfig,
) -> MarivoRuntime:
    """Phase 3a factory: wraps existing infrastructure into Runtime."""
    ports = RuntimePorts(
        model_store=SqlModelStoreAdapter(semantic_svc, svc._metadata),
        session_store=SqlSessionStoreAdapter(svc._metadata, session_mgr),
        evidence_store=MetadataEvidenceStoreAdapter(svc._evidence_repos),
        data_source=DataSourceAdapter(svc._engines, svc._routing_runtime),
        cache_store=MetadataCacheStoreAdapter(svc._metadata),
        authz=NoopAuthZAdapter(),
        audit_log=FileAuditLogAdapter(),
        telemetry=LocalTelemetryAdapter(),
        runtime_config=TomlRuntimeConfigAdapter(config),
    )
    core = CoreEngine(svc)
    return MarivoRuntime(ports, core)
```

**HTTP/MCP switch:**

```python
# app/api/deps.py — add runtime to AppServices
class AppServices:
    runtime: MarivoRuntime         # New
    service: SemanticLayerService   # Retained for wrapper references
    datasource_service: DatasourceService
    ...
```

HTTP endpoints change from `services.service.run_intent(...)` to `services.runtime.observe(...)` / `services.runtime.diagnose(...)`.

**Acceptance criteria:**

- [ ] `MarivoRuntime` class exists with all use-case methods callable
- [ ] All HTTP intent endpoints call through Runtime, not `SemanticLayerService` directly
- [ ] All MCP intent tools call through Runtime
- [ ] All existing tests green
- [ ] `app/runtime/` and `app/core/` import-linter rules pass
- [ ] `core/engine.py` only proxies, contains no domain logic

---

### Phase 3b — Intent Runner Migration

**Goal:** Migrate 10 intent runners from `(svc, session_id, params)` to `(core, ports, session_id, params)`. Each migrated intent removes corresponding methods from `service.py`.

**Migration order (simple → complex):**

| Order | Intent | Lines | Complexity | Reason |
|-------|--------|-------|------------|--------|
| 1 | correlate | 435 | Low | Simplest atomic intent, only references artifact + commit |
| 2 | forecast | 471 | Low | Similar to correlate, simple artifact reference |
| 3 | test | 582 | Medium | References observe artifact, no sub-intent calls |
| 4 | observe | 1191 | High | Core atomic intent, all derived intents depend on it |
| 5 | compare | 751 | Medium | References observe artifact |
| 6 | decompose | 766 | Medium | References observe + compare artifact |
| 7 | detect | 806 | Medium | Anomaly detection orchestration |
| 8 | attribute | 674 | Medium | Derived: calls observe + compare + decompose |
| 9 | validate | 367 | Low | Derived: calls observe + test |
| 10 | diagnose | 917 | High | Derived: calls detect + observe + compare + decompose |

**Migration pattern (per intent runner):**

```python
# Step 1: New signature, old implementation via core proxy
def run_observe(core: CoreEngine, ports: RuntimePorts, session_id: SessionId, params: dict) -> dict:
    # Internally still calls core._svc._resolve_metric_execution_context etc.
    ...

# Step 2: Replace proxy calls gradually
# core._svc._resolve_metric_execution_context(...)
#   → core.resolve_metric_execution_context(...)  (proxies to svc)
#   → pure function call (after 3c extraction)

# Step 3: Runtime use-case method calls intent runner directly
class MarivoRuntime:
    def observe(self, session_id, params):
        return run_observe(self._core, self._ports, session_id, params)
```

**Derived intent sub-intent calls:** Derived intents call `runtime.observe()`, `runtime.detect()` etc. for sub-intents, not intent runner functions directly. This ensures sub-intent execution goes through the same Runtime path (authz, audit, telemetry).

**Acceptance criteria:**

- [ ] All 10 intent runners use `(core, ports, session_id, params)` signature
- [ ] Migrated methods deleted from `SemanticLayerService`
- [ ] Each intent migration followed by full green test run
- [ ] Derived intents call sub-intents through `runtime.*()` methods

---

### Phase 3c — Core Extraction

**Goal:** Extract pure computation logic from service.py and analysis_core/ into `core/`. `CoreEngine` transitions from proxy to real facade.

**Extraction order:**

| Order | Target | Destination | Est. lines |
|-------|--------|-------------|------------|
| 1 | `analysis_core/ir.py` | `core/semantic/ir.py` | ~659 |
| 2 | `analysis_core/primitives.py` | `core/intent/primitives.py` | ~139 |
| 3 | `analysis_core/step_registry.py` + `intent_registry.py` | `core/intent/` | ~70 |
| 4 | Scope resolution pure functions | `core/semantic/scope_resolution.py` | ~400 |
| 5 | Metric resolution pure functions | `core/semantic/metric_resolution.py` | ~700 |
| 6 | `analysis_core/typed_resolution.py` pure parts | `core/semantic/typed_resolution.py` | ~500 |
| 7 | `analysis_core/compiler.py` | `core/semantic/compiler.py` | ~1970 |
| 8 | `analysis_core/validator.py` | `core/semantic/validator.py` | ~1138 |
| 9 | Evidence extraction pure functions | `core/evidence/` | ~3000 |
| 10 | Calendar pure functions | `core/semantic/calendar.py` | ~800 |

**Extraction rules:**
- Methods that query `MetadataStore` / `SemanticRuntimeRepository`: pre-load data, pass as parameters
- Methods that need current time: time as parameter
- Methods that need actor: actor as parameter
- Migrated files in `analysis_core/` marked `# DEPRECATED: use app.core.semantic.xxx`

**Acceptance criteria:**

- [ ] `core/` imports no I/O library or adapter (import-linter verified)
- [ ] `core/` imports no `app.ports` (import-linter verified)
- [ ] All intent runners use core pure functions + ports I/O
- [ ] All existing tests green
- [ ] Deprecated files in `analysis_core/` marked

---

### Phase 3d — Service Shell Removal & Boundary Enforcement

**Goal:** Remove `SemanticLayerService` remnants, tighten import-linter rules.

**Deliverables:**
- `service.py` reduced to 0 or < 100 lines (init glue only) or deleted
- `CoreEngine` no longer holds `SemanticLayerService` reference
- import-linter `core-isolation` rule added
- Deprecated files in `analysis_core/` deleted

**Final import-linter rules (Phase 3d):**

```ini
[importlinter:contract:core-isolation]
name = core/ must not import app internals or I/O
type = forbidden
source_modules =
    app.core
forbidden_modules =
    app.api
    app.storage
    app.analysis_core
    app.evidence_engine
    app.semantic_runtime
    app.semantic_service_v2
    app.execution
    app.session
    app.registry
    app.adapters
    app.cli
    app.ports

[importlinter:contract:runtime-must-use-core]
name = runtime/ must use core/ for domain logic
type = forbidden
source_modules =
    app.runtime
forbidden_modules =
    app.analysis_core
    app.evidence_engine
```

**Acceptance criteria:**

- [ ] `SemanticLayerService` deleted or reduced to empty shell
- [ ] `CoreEngine` does not reference `SemanticLayerService`
- [ ] `core/` import-linter rules pass
- [ ] `runtime/` does not import `analysis_core/` or `evidence_engine/`
- [ ] All existing tests green
- [ ] HTTP and MCP handlers do not import `SemanticLayerService`

---

## 8. Testing Strategy

### 8.1 Sub-Phase Test Gates

Each sub-phase must pass `make test` (all green) before proceeding. No additional intermediate gates.

| Sub-phase | Must pass | New tests |
|-----------|----------|-----------|
| 3a | `make test` all green | Runtime construction tests, port wrapper basic tests, HTTP endpoint routing tests (confirm path through Runtime) |
| 3b | `make test` all green | Per-migrated-intent-runner unit tests (mock core + mock ports) |
| 3c | `make test` all green | Core module pure function unit tests, port boundary assertion tests |
| 3d | `make test` all green | import-linter rule tests, `SemanticLayerService` non-reference assertions |

### 8.2 Runtime Integration Tests

After 3a, a lightweight integration test verifies the HTTP → Runtime → service.py proxy → existing behavior path:

```python
# tests/runtime/test_runtime_integration.py
def test_observe_through_runtime(http_client, sample_model):
    """Verify observe intent works through Runtime facade."""
    response = http_client.post("/sessions/{sid}/intents/observe", json={...})
    assert response.status_code == 200
```

These tests validate proxy correctness in 3a, and behavioral preservation after service.py removal in 3d.

### 8.3 Intent Runner Unit Tests

Each migrated intent runner gets a unit test file in 3b:

```python
# tests/runtime/test_intent_observe.py
def test_observe_resolves_metric_and_executes(mock_core, mock_ports):
    mock_core.resolve_metric_execution_context.return_value = ...
    mock_ports.data_source.execute.return_value = QueryResult(...)

    result = run_observe(mock_core, mock_ports, session_id, params)

    mock_core.resolve_metric_execution_context.assert_called_once()
    mock_ports.data_source.execute.assert_called_once()
    mock_ports.evidence_store.write.assert_called()
```

In 3b these use mock core (proxy); in 3c they progressively use real core pure functions.

### 8.4 Core Pure Function Tests

Extracted pure logic gets zero-I/O, zero-mock tests in 3c:

```python
# tests/core/test_metric_resolution.py
def test_resolve_metric_context_returns_binding():
    result = resolve_metric_execution_context(
        metric_name="revenue",
        model=sample_semantic_model,
        entity=sample_entity,
        relationships=sample_relationships,
    )
    assert result.metric_binding is not None
```

These tests have no infrastructure dependencies, run fast, and can serve as property-test bases in the future.

### 8.5 Regression Protection

**Existing test files are not restructured.** The ~90 existing test files continue as regression protection. All Phase 3 changes must keep them green.

When tests directly construct `SemanticLayerService`, they don't need changes in 3a (Runtime and service coexist). In 3d, when service.py is removed, these tests migrate to constructing `MarivoRuntime`:

- Shared fixture changes in `tests/conftest.py` cover most tests
- Per-file migration, each followed by `make test`

### 8.6 Out of Scope

- No adapter contract test suite (Phase 6 deliverable)
- No property-based testing (future enhancement)
- No E2E golden test / replay test (Phase 7 deliverable)

---

## 9. Import-Linter Rules Evolution

Phase 2 has `contracts-isolation` and `ports-isolation`. Phase 3 adds rules progressively:

| Sub-phase | New rule | Description |
|-----------|----------|-------------|
| 3a | `runtime-no-direct-core-orchestration` | `app.runtime` must not import `app.analysis_core` or `app.evidence_engine` (Runtime proxies through service.py, must not bypass) |
| 3a | `core-no-io` | `app.core` must not import I/O libraries or `app.ports` |
| 3b | (none) | 3b is internal intent runner refactoring, no package dependency direction changes |
| 3c | `runtime-must-use-core` | `app.runtime` must not import `app.analysis_core` or `app.evidence_engine` (must use `app.core`) |
| 3d | `surfaces-must-use-runtime` | `app.api` and `app.cli` must not import `app.analysis_core`, `app.evidence_engine`, `app.semantic_runtime` (must go through `app.runtime`) |

**3a `.importlinter` additions:**

```ini
[importlinter:contract:runtime-no-direct-core-orchestration]
name = runtime/ must not bypass service.py for core orchestration
type = forbidden
source_modules =
    app.runtime
forbidden_modules =
    app.analysis_core
    app.evidence_engine

[importlinter:contract:core-no-io]
name = core/ must not import I/O or ports
type = forbidden
source_modules =
    app.core
forbidden_modules =
    app.api
    app.storage
    app.analysis_core
    app.evidence_engine
    app.semantic_runtime
    app.semantic_service_v2
    app.execution
    app.session
    app.registry
    app.adapters
    app.cli
    app.ports
```

---

## 10. Non-Goals

| Non-goal | Belongs to |
|----------|-----------|
| Implement `FileModelStore`, `SqliteSessionStore`, `FileEvidenceStore`, `DuckDBDataSource` etc. | Phase 4 (Local Embedded Runtime) |
| `app/` → `marivo/` namespace migration | Phase 7 (Namespace Cutover) |
| Profile factory (`create_local_runtime()` / `create_server_runtime()` / `create_client_runtime()`) | Phase 6 (Profile System) |
| Adapter contract test suite | Phase 6 |
| E2E golden test / replay test | Phase 7 |
| MCP stdio embedded mode / client mode | Phase 4 + Phase 5 |
| Full SessionStore event-sourced implementation (current `SessionManager` is CRUD) | Phase 6 |
| `SemanticRuntimeRepository` refactoring (has I/O, needs pre-loaded data) | Internal to Phase 3c, not a separate deliverable |
| Unify `app.api.models/` types into `app.contracts/` | Phase 7 |
| Property-based testing | Future enhancement |
| Any `marivo-mcp` package changes | Phase 7 |

---

## 11. Phase 3 Completion State

### 11.1 Package Structure

```
app/
  contracts/         # Phase 2 deliverable, unchanged
  ports/             # Phase 2 deliverable, unchanged
  core/              # Phase 3 new: pure domain logic
    engine.py
    semantic/        # metric_resolution, scope_resolution, compiler, ir, ...
    intent/          # primitives, registries
    evidence/        # finding_extraction, proposition_seeding, assessment, ...
  runtime/           # Phase 3 new: use-case orchestration layer
    runtime.py       # MarivoRuntime
    ports.py         # RuntimePorts
    session_ops.py   # Session lifecycle
    semantic_ops.py  # Semantic model ops
    intent_execution.py  # Intent runner orchestration
  adapters/
    server/
      wrappers.py    # Phase 3a port wrappers
    base.py          # Existing catalog adapter, unchanged
    duckdb_adapter.py
    trino_adapter.py
  service.py         # Phase 3d: deleted or < 100 lines init glue
  analysis_core/     # Phase 3d: deprecated files deleted, package may remain as empty shell
  evidence_engine/   # Phase 3d: pure computation migrated to core/, I/O parts in runtime/
  api/               # HTTP handlers now call Runtime
  storage/           # Unchanged, referenced by wrappers
  session/           # Phase 3d: SessionManager may migrate into runtime/ or stay
  ...
```

### 11.2 Key Metrics (Phase 3d Complete)

- `service.py`: ~3,440 → 0 or < 100 lines
- `core/`: ~8,000 lines pure logic (metric resolution + compiler + evidence computation)
- `runtime/`: ~3,000 lines orchestration (intent runner orchestration + session ops + semantic ops)
- `analysis_core/`: drained or deleted
- `evidence_engine/`: I/O parts in `runtime/`, pure computation in `core/evidence/`

---

## 12. Verification Plan

| Check | How | Gate |
|-------|-----|------|
| All existing tests green | `make test` after each sub-phase | Must pass |
| import-linter passes | `lint-imports` in CI | Must pass |
| Type checking passes | `make typecheck` includes `app/core/` and `app/runtime/` | Must pass |
| core/ has zero I/O imports | import-linter `core-no-io` rule | Must pass |
| core/ has zero port imports | import-linter `core-no-io` rule | Must pass |
| runtime/ does not import analysis_core/ | import-linter rule | Must pass (3c+) |
| Surfaces call Runtime only | import-linter `surfaces-must-use-runtime` | Must pass (3d) |
| No circular imports | `lint-imports` + manual review | Must pass |
