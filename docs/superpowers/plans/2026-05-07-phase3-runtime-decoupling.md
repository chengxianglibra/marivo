# Phase 3: Runtime Decoupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the 3,440-line `SemanticLayerService` god object into a `MarivoRuntime` facade + `CoreEngine` pure logic + `RuntimePorts` container, so that HTTP/MCP surfaces call Runtime use-case methods only and core/ is I/O-free.

**Architecture:** Facade-First Strangler — build the Runtime shell (3a), migrate intent runners (3b), extract pure core logic (3c), then delete the old service (3d). Each sub-phase has a green-test gate. Port adapters wrap existing infrastructure; CoreEngine starts as a proxy to service.py, then becomes real pure logic.

**Tech Stack:** Python 3.12+, Pydantic v2, typing.Protocol, import-linter, pytest

**Spec:** `docs/superpowers/specs/2026-05-07-phase3-runtime-decoupling-design.md`

---

## File Structure

```
app/
  runtime/                    # NEW: use-case orchestration layer
    __init__.py               # Re-exports MarivoRuntime, RuntimePorts, create_runtime_from_service
    ports.py                  # RuntimePorts container
    runtime.py                # MarivoRuntime class
    session_ops.py            # Session lifecycle delegation
    semantic_ops.py           # Semantic model ops delegation
    intent_execution.py       # Intent runner dispatch (replaces service.run_intent)
  core/                       # NEW: pure domain logic (I/O-free)
    __init__.py               # Re-exports CoreEngine
    engine.py                 # CoreEngine (3a: proxy, 3c: real)
  adapters/
    server/                   # NEW: port adapter wrappers
      __init__.py
      wrappers.py             # All 9 adapter classes
  api/
    deps.py                   # MODIFIED: add runtime to AppServices
    sessions.py               # MODIFIED: call runtime instead of service
    app_factory.py            # MODIFIED: construct runtime alongside service

tests/
  runtime/                    # NEW: runtime tests
    __init__.py
    test_runtime_construction.py
    test_port_wrappers.py
    test_runtime_session_ops.py
    test_runtime_semantic_ops.py
    test_runtime_intent_dispatch.py
  core/                       # NEW: core tests
    __init__.py
    test_core_engine_proxy.py

.importlinter                 # MODIFIED: add runtime and core isolation rules
```

---

## Phase 3a — Runtime Shell + Port Wiring

Goal: `MarivoRuntime` facade in place, HTTP endpoints switch to calling Runtime. Runtime internally proxies to `SemanticLayerService`. Zero behavior change — pure structural refactoring.

---

### Task 1: Create `app/runtime/ports.py` — RuntimePorts Container

**Files:**
- Create: `app/runtime/__init__.py`
- Create: `app/runtime/ports.py`
- Test: `tests/runtime/test_runtime_construction.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/runtime/__init__.py
# (empty)
```

```python
# tests/runtime/test_runtime_construction.py
"""Test RuntimePorts construction and attribute access."""
from app.runtime.ports import RuntimePorts
from app.ports.model_store import ModelStore
from app.ports.session_store import SessionStore
from app.ports.evidence_store import EvidenceStore
from app.ports.data_source import DataSource
from app.ports.cache_store import CacheStore
from app.ports.authz import AuthZ
from app.ports.audit_log import AuditLog
from app.ports.telemetry import Telemetry
from app.ports.runtime_config import RuntimeConfig


class _StubModelStore:
    """Minimal stub satisfying ModelStore Protocol."""

    def get(self, selector): ...
    def save(self, model, *, actor, expected_revision): ...
    def list(self, query): ...


class _StubSessionStore:
    def append_event(self, session_id, event): ...
    def load_events(self, session_id): ...


class _StubEvidenceStore:
    def write(self, evidence): ...
    def read(self, ref): ...


class _StubDataSource:
    def execute(self, query): ...
    def schema(self, source_ref): ...


class _StubCacheStore:
    def get(self, key): ...
    def set(self, key, value, ttl=None): ...


class _StubAuthZ:
    def check(self, actor, action, resource): ...


class _StubAuditLog:
    def record(self, entry): ...


class _StubTelemetry:
    def emit(self, event): ...


class _StubRuntimeConfig:
    def get(self, key): ...


def _make_ports() -> RuntimePorts:
    return RuntimePorts(
        model_store=_StubModelStore(),
        session_store=_StubSessionStore(),
        evidence_store=_StubEvidenceStore(),
        data_source=_StubDataSource(),
        cache_store=_StubCacheStore(),
        authz=_StubAuthZ(),
        audit_log=_StubAuditLog(),
        telemetry=_StubTelemetry(),
        runtime_config=_StubRuntimeConfig(),
    )


def test_runtime_ports_construction():
    ports = _make_ports()
    assert ports.model_store is not None
    assert ports.session_store is not None
    assert ports.evidence_store is not None
    assert ports.data_source is not None
    assert ports.cache_store is not None
    assert ports.authz is not None
    assert ports.audit_log is not None
    assert ports.telemetry is not None
    assert ports.runtime_config is not None


def test_runtime_ports_attribute_types():
    ports = _make_ports()
    # Each attribute satisfies its corresponding Protocol
    assert isinstance(ports.model_store, ModelStore)
    assert isinstance(ports.session_store, SessionStore)
    assert isinstance(ports.evidence_store, EvidenceStore)
    assert isinstance(ports.data_source, DataSource)
    assert isinstance(ports.cache_store, CacheStore)
    assert isinstance(ports.authz, AuthZ)
    assert isinstance(ports.audit_log, AuditLog)
    assert isinstance(ports.telemetry, Telemetry)
    assert isinstance(ports.runtime_config, RuntimeConfig)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/runtime/test_runtime_construction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.runtime'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/runtime/__init__.py
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime

__all__ = ["MarivoRuntime", "RuntimePorts"]
```

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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/runtime/test_runtime_construction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/runtime/__init__.py app/runtime/ports.py tests/runtime/__init__.py tests/runtime/test_runtime_construction.py
git commit -m "$(cat <<'EOF'
feat(runtime): add RuntimePorts container

Typed container holding all port implementations. Phase 3a foundation.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 2: Create `app/core/engine.py` — CoreEngine Proxy

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/engine.py`
- Test: `tests/core/test_core_engine_proxy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/__init__.py
# (empty)
```

```python
# tests/core/test_core_engine_proxy.py
"""Test CoreEngine proxy delegates to SemanticLayerService."""
from unittest.mock import MagicMock

from app.core.engine import CoreEngine


def test_core_engine_proxy_holds_service_reference():
    svc = MagicMock(name="SemanticLayerService")
    core = CoreEngine(svc)
    assert core._svc is svc


def test_core_engine_proxy_delegates_resolve_metric_execution_context():
    svc = MagicMock(name="SemanticLayerService")
    expected = MagicMock(name="MetricExecutionContext")
    svc._resolve_metric_execution_context.return_value = expected

    core = CoreEngine(svc)
    result = core.resolve_metric_execution_context("metric.ref", session_id="s1")

    svc._resolve_metric_execution_context.assert_called_once_with(
        "metric.ref", session_id="s1"
    )
    assert result is expected


def test_core_engine_proxy_delegates_compile_step():
    svc = MagicMock(name="SemanticLayerService")
    expected = MagicMock(name="CompiledStep")
    svc._compile_step_with_feedback.return_value = expected

    core = CoreEngine(svc)
    result = core.compile_step(step_ir="dummy", session_id="s1")

    svc._compile_step_with_feedback.assert_called_once_with(
        step_ir="dummy", session_id="s1"
    )
    assert result is expected


def test_core_engine_proxy_delegates_build_step_semantic_metadata():
    svc = MagicMock(name="SemanticLayerService")
    expected = MagicMock(name="StepMetadata")
    svc.build_step_semantic_metadata.return_value = expected

    core = CoreEngine(svc)
    result = core.build_step_semantic_metadata(intent_type="observe", step_id="step_1")

    svc.build_step_semantic_metadata.assert_called_once_with(
        intent_type="observe", step_id="step_1"
    )
    assert result is expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/core/test_core_engine_proxy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/__init__.py
from app.core.engine import CoreEngine

__all__ = ["CoreEngine"]
```

```python
# app/core/engine.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.service import SemanticLayerService


class CoreEngine:
    """Phase 3a: proxies to SemanticLayerService for domain computation.
    Phase 3c: replaced with real core modules."""

    def __init__(self, svc: SemanticLayerService) -> None:
        self._svc = svc

    # --- Pure domain computation proxies ---

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc._resolve_metric_execution_context(*args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc._compile_step_with_feedback(*args, **kwargs)

    def build_step_semantic_metadata(self, *args: Any, **kwargs: Any) -> Any:
        return self._svc.build_step_semantic_metadata(*args, **kwargs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/core/test_core_engine_proxy.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/__init__.py app/core/engine.py tests/core/__init__.py tests/core/test_core_engine_proxy.py
git commit -m "$(cat <<'EOF'
feat(core): add CoreEngine proxy to SemanticLayerService

Phase 3a: CoreEngine proxies domain computation methods. Pure
structural seam — no behavior change yet.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 3: Create `app/adapters/server/wrappers.py` — Port Adapter Wrappers

**Files:**
- Create: `app/adapters/server/__init__.py`
- Create: `app/adapters/server/wrappers.py`
- Test: `tests/runtime/test_port_wrappers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/runtime/test_port_wrappers.py
"""Test port adapter wrappers satisfy their Protocol interfaces."""
from app.contracts.errors import DomainError, ErrorCode
from app.contracts.ids import CacheKey, UserId, Action, ResourceId
from app.contracts.values import AuthZDecision, AuditEntry, TelemetryEvent
from app.ports.authz import AuthZ
from app.ports.audit_log import AuditLog
from app.ports.cache_store import CacheStore
from app.ports.telemetry import Telemetry
from app.ports.runtime_config import RuntimeConfig
from app.adapters.server.wrappers import (
    NoopAuthZAdapter,
    FileAuditLogAdapter,
    LocalTelemetryAdapter,
    TomlRuntimeConfigAdapter,
)


# --- NoopAuthZAdapter ---


def test_noop_authz_allows_all():
    adapter = NoopAuthZAdapter()
    decision = adapter.check(
        actor=UserId("anyone"),
        action=Action("anything"),
        resource=ResourceId("any_resource"),
    )
    assert decision.allowed is True
    assert isinstance(decision, AuthZDecision)


def test_noop_authz_satisfies_protocol():
    adapter = NoopAuthZAdapter()
    assert isinstance(adapter, AuthZ)


# --- FileAuditLogAdapter ---


def test_file_audit_log_records_without_error():
    adapter = FileAuditLogAdapter()
    entry = AuditEntry(
        actor=UserId("user1"),
        action="create_session",
        resource_type="session",
        resource_id="s1",
    )
    # Should not raise
    adapter.record(entry)


def test_file_audit_log_satisfies_protocol():
    adapter = FileAuditLogAdapter()
    assert isinstance(adapter, AuditLog)


# --- LocalTelemetryAdapter ---


def test_local_telemetry_emits_without_error():
    adapter = LocalTelemetryAdapter()
    event = TelemetryEvent(name="test_event", properties={"key": "value"})
    # Should not raise
    adapter.emit(event)


def test_local_telemetry_satisfies_protocol():
    adapter = LocalTelemetryAdapter()
    assert isinstance(adapter, Telemetry)


# --- TomlRuntimeConfigAdapter ---


def test_toml_runtime_config_delegates_to_config():
    from app.config import MarivoConfig

    config = MarivoConfig()
    adapter = TomlRuntimeConfigAdapter(config)
    # Should not raise, returns str or None
    result = adapter.get("nonexistent_key")
    assert result is None or isinstance(result, str)


def test_toml_runtime_config_satisfies_protocol():
    from app.config import MarivoConfig

    adapter = TomlRuntimeConfigAdapter(MarivoConfig())
    assert isinstance(adapter, RuntimeConfig)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/runtime/test_port_wrappers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.server'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/adapters/server/__init__.py
# (empty)
```

```python
# app/adapters/server/wrappers.py
from __future__ import annotations

import logging
from typing import Any

from app.contracts.ids import CacheKey, UserId, Action, ResourceId
from app.contracts.values import AuthZDecision, AuditEntry, CacheValue, TelemetryEvent

logger = logging.getLogger(__name__)


class NoopAuthZAdapter:
    """Phase 3a: always-allow. Phase 6: replaced by OidcRbacAuthZ."""

    def check(
        self, actor: UserId, action: Action, resource: ResourceId
    ) -> AuthZDecision:
        return AuthZDecision(allowed=True)


class FileAuditLogAdapter:
    """Phase 3a: logs to Python logger."""

    def record(self, entry: AuditEntry) -> None:
        logger.info(
            "AUDIT actor=%s action=%s resource=%s/%s detail=%s",
            entry.actor,
            entry.action,
            entry.resource_type,
            entry.resource_id,
            entry.detail,
        )


class LocalTelemetryAdapter:
    """Phase 3a: no-op telemetry."""

    def emit(self, event: TelemetryEvent) -> None:
        pass


class TomlRuntimeConfigAdapter:
    """Wraps MarivoConfig → RuntimeConfig."""

    def __init__(self, config: Any) -> None:
        self._config = config

    def get(self, key: str) -> str | None:
        value = getattr(self._config, key, None)
        if value is None:
            return None
        return str(value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/runtime/test_port_wrappers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/adapters/server/__init__.py app/adapters/server/wrappers.py tests/runtime/test_port_wrappers.py
git commit -m "$(cat <<'EOF'
feat(adapters): add noop/stub port adapters for authz, audit, telemetry, config

Phase 3a stub adapters that satisfy Protocol interfaces. Real
implementations replace these in Phase 6.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 4: Add infrastructure-backed port adapters (ModelStore, SessionStore, DataSource, EvidenceStore, CacheStore)

**Files:**
- Modify: `app/adapters/server/wrappers.py`
- Test: `tests/runtime/test_port_wrappers.py`

This task adds the 5 adapters that wrap existing infrastructure classes. These are more complex because they need type conversion between `app.api.models.*` and `app.contracts.*`.

- [ ] **Step 1: Investigate existing infrastructure classes**

Before writing adapters, identify the exact method signatures and return types of:
- `SemanticModelV2Service` (used by `SqlModelStoreAdapter`)
- `SessionManager` (used by `SqlSessionStoreAdapter`)
- `AnalyticsEngine` + `RoutingRuntime` (used by `DataSourceAdapter`)
- `FindingRepository`, `PropositionRepository`, etc. (used by `MetadataEvidenceStoreAdapter`)
- `MetadataStore` (used by `MetadataCacheStoreAdapter`)

Read these files to understand the exact signatures. Document findings in comments in wrappers.py.

- [ ] **Step 2: Write the failing tests**

```python
# Add to tests/runtime/test_port_wrappers.py

from unittest.mock import MagicMock

from app.contracts.ids import ModelId, SessionId, EvidenceRef, DatasourceId, EngineId
from app.contracts.semantic import SemanticModel, ModelSummary
from app.contracts.evidence import Evidence, Finding, Proposition
from app.contracts.values import LogicalQuery, QueryResult, SourceRef, SourceSchema
from app.ports.model_store import ModelStore
from app.ports.session_store import SessionStore
from app.ports.evidence_store import EvidenceStore
from app.ports.data_source import DataSource
from app.ports.cache_store import CacheStore
from app.adapters.server.wrappers import (
    SqlModelStoreAdapter,
    SqlSessionStoreAdapter,
    DataSourceAdapter,
    MetadataEvidenceStoreAdapter,
    MetadataCacheStoreAdapter,
)


# --- SqlModelStoreAdapter ---


def test_sql_model_store_satisfies_protocol():
    metadata = MagicMock()
    semantic_svc = MagicMock()
    adapter = SqlModelStoreAdapter(semantic_svc, metadata)
    assert isinstance(adapter, ModelStore)


# --- SqlSessionStoreAdapter ---


def test_sql_session_store_satisfies_protocol():
    metadata = MagicMock()
    session_mgr = MagicMock()
    adapter = SqlSessionStoreAdapter(metadata, session_mgr)
    assert isinstance(adapter, SessionStore)


# --- DataSourceAdapter ---


def test_data_source_adapter_satisfies_protocol():
    engines: dict[EngineId, MagicMock] = {}
    routing = MagicMock()
    adapter = DataSourceAdapter(engines, routing)
    assert isinstance(adapter, DataSource)


# --- MetadataEvidenceStoreAdapter ---


def test_evidence_store_adapter_satisfies_protocol():
    repos = MagicMock()
    adapter = MetadataEvidenceStoreAdapter(repos)
    assert isinstance(adapter, EvidenceStore)


# --- MetadataCacheStoreAdapter ---


def test_cache_store_adapter_satisfies_protocol():
    metadata = MagicMock()
    adapter = MetadataCacheStoreAdapter(metadata)
    assert isinstance(adapter, CacheStore)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/runtime/test_port_wrappers.py -v -k "satisfies_protocol"`
Expected: FAIL — `ImportError` for the adapter classes

- [ ] **Step 4: Implement infrastructure-backed adapters**

Add to `app/adapters/server/wrappers.py`. Each adapter wraps its existing infrastructure and performs error translation (catching infrastructure exceptions and raising `DomainError`).

**Important:** The exact method implementations depend on the infrastructure class signatures discovered in Step 1. The structure below is the template — fill in the actual delegation calls based on what you find.

```python
# Add to app/adapters/server/wrappers.py

from app.contracts.ids import (
    ModelId, RevisionId, UserId, SessionId, EvidenceRef,
    DatasourceId, EngineId, CacheKey, DatasetName, MetricName,
)
from app.contracts.errors import DomainError, ErrorCode
from app.contracts.semantic import SemanticModel, ModelSummary
from app.contracts.session import SessionEvent
from app.contracts.evidence import Evidence
from app.contracts.values import CacheValue, LogicalQuery, QueryResult, SourceRef, SourceSchema


class SqlModelStoreAdapter:
    """Wraps SemanticModelV2Service + MetadataStore → ModelStore."""

    def __init__(self, semantic_svc: Any, metadata: Any) -> None:
        self._semantic_svc = semantic_svc
        self._metadata = metadata

    def get(self, selector: Any) -> SemanticModel | None:
        # Delegate to semantic_svc.get_model(name=...) or metadata query
        # Translate exceptions → DomainError
        # Convert api.models → contracts.SemanticModel
        raise NotImplementedError("Fill in after investigating SemanticModelV2Service signatures")

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId:
        raise NotImplementedError("Fill in after investigating SemanticModelV2Service signatures")

    def list(self, query: Any) -> list[ModelSummary]:
        raise NotImplementedError("Fill in after investigating SemanticModelV2Service signatures")


class SqlSessionStoreAdapter:
    """Wraps MetadataStore + SessionManager → SessionStore."""

    def __init__(self, metadata: Any, session_mgr: Any) -> None:
        self._metadata = metadata
        self._session_mgr = session_mgr

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        # Minimal bridge — SessionManager is CRUD, not event-sourced.
        # Phase 6 replaces with full event-sourced implementation.
        raise NotImplementedError("Fill in after investigating SessionManager signatures")

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        raise NotImplementedError("Fill in after investigating SessionManager signatures")


class DataSourceAdapter:
    """Wraps AnalyticsEngine instances + RoutingRuntime → DataSource."""

    def __init__(self, engines: dict[EngineId, Any], routing: Any) -> None:
        self._engines = engines
        self._routing = routing

    def execute(self, query: LogicalQuery) -> QueryResult:
        raise NotImplementedError("Fill in after investigating AnalyticsEngine + RoutingRuntime signatures")

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        raise NotImplementedError("Fill in after investigating AnalyticsEngine.schema signatures")


class MetadataEvidenceStoreAdapter:
    """Wraps evidence repositories → EvidenceStore."""

    def __init__(self, repos: Any) -> None:
        self._repos = repos

    def write(self, evidence: Evidence) -> EvidenceRef:
        raise NotImplementedError("Fill in after investigating FindingRepository etc. signatures")

    def read(self, ref: EvidenceRef) -> Evidence:
        raise NotImplementedError("Fill in after investigating repository read signatures")


class MetadataCacheStoreAdapter:
    """Wraps MetadataStore → CacheStore."""

    def __init__(self, metadata: Any) -> None:
        self._metadata = metadata

    def get(self, key: CacheKey) -> CacheValue | None:
        raise NotImplementedError("Fill in after investigating MetadataStore cache capability")

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        raise NotImplementedError("Fill in after investigating MetadataStore cache capability")
```

**After investigating the infrastructure:** Replace each `raise NotImplementedError(...)` with actual delegation code that:
1. Converts contract types to infrastructure types (if needed)
2. Calls the underlying infrastructure method
3. Catches infrastructure exceptions and translates them to `DomainError`
4. Converts infrastructure return types back to contract types

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime/test_port_wrappers.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/adapters/server/wrappers.py tests/runtime/test_port_wrappers.py
git commit -m "$(cat <<'EOF'
feat(adapters): add infrastructure-backed port adapters

SqlModelStoreAdapter, SqlSessionStoreAdapter, DataSourceAdapter,
MetadataEvidenceStoreAdapter, MetadataCacheStoreAdapter wrap
existing infrastructure classes with error translation.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 5: Create `app/runtime/runtime.py` — MarivoRuntime Class

**Files:**
- Create: `app/runtime/runtime.py`
- Modify: `app/runtime/__init__.py` (update re-exports)
- Test: `tests/runtime/test_runtime_construction.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/runtime/test_runtime_construction.py

from unittest.mock import MagicMock

from app.core.engine import CoreEngine
from app.runtime.runtime import MarivoRuntime


def test_runtime_construction():
    ports = _make_ports()
    svc = MagicMock(name="SemanticLayerService")
    core = CoreEngine(svc)
    runtime = MarivoRuntime(ports, core)
    assert runtime._ports is ports
    assert runtime._core is core


def test_runtime_has_intent_methods():
    ports = _make_ports()
    svc = MagicMock(name="SemanticLayerService")
    core = CoreEngine(svc)
    runtime = MarivoRuntime(ports, core)

    # All intent use-case methods must exist
    intent_methods = [
        "observe", "compare", "decompose", "correlate",
        "detect", "test", "forecast", "attribute",
        "diagnose", "validate",
    ]
    for method_name in intent_methods:
        assert hasattr(runtime, method_name), f"Missing intent method: {method_name}"
        assert callable(getattr(runtime, method_name))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/runtime/test_runtime_construction.py -v`
Expected: FAIL — `ImportError: cannot import name 'MarivoRuntime' from 'app.runtime'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/runtime/runtime.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.contracts.ids import SessionId, UserId, ModelId, DatasourceId
from app.contracts.semantic import SemanticModel, ModelSummary
from app.contracts.session import SessionState

if TYPE_CHECKING:
    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts


class MarivoRuntime:
    """Use-case facade for the Marivo platform.

    All HTTP/MCP surfaces call methods on this class only.
    Internally delegates to CoreEngine (domain logic) and RuntimePorts (I/O).
    """

    def __init__(self, ports: RuntimePorts, core: CoreEngine) -> None:
        self._ports = ports
        self._core = core

    # --- Intent use-cases (HTTP/MCP entry points) ---

    def observe(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def compare(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def decompose(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def correlate(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def detect(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def test(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def forecast(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def attribute(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def diagnose(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    def validate(self, session_id: SessionId, params: dict) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service.run_intent")

    # --- Session lifecycle ---

    def create_session(self, goal: str, *, actor: UserId) -> SessionState:
        raise NotImplementedError("Phase 3a: will proxy to SessionManager")

    def get_session(self, session_id: SessionId) -> SessionState:
        raise NotImplementedError("Phase 3a: will proxy to SessionManager")

    def terminate_session(self, session_id: SessionId) -> None:
        raise NotImplementedError("Phase 3a: will proxy to SessionManager")

    def get_session_state(self, session_id: SessionId, **filters: Any) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to service")

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        raise NotImplementedError("Phase 3a: will proxy to SemanticModelV2Service")

    def save_semantic_model(
        self, model: SemanticModel, *, actor: UserId
    ) -> ModelId:
        raise NotImplementedError("Phase 3a: will proxy to SemanticModelV2Service")

    def list_semantic_models(self, query: Any) -> list[ModelSummary]:
        raise NotImplementedError("Phase 3a: will proxy to SemanticModelV2Service")

    # --- Datasource ops ---

    def discover_catalog(self, datasource_id: DatasourceId, **kwargs: Any) -> dict:
        raise NotImplementedError("Phase 3a: will proxy to DatasourceService")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/runtime/test_runtime_construction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/runtime/runtime.py app/runtime/__init__.py tests/runtime/test_runtime_construction.py
git commit -m "$(cat <<'EOF'
feat(runtime): add MarivoRuntime facade with use-case method stubs

All 10 intent methods + session + semantic + datasource ops defined.
Raise NotImplementedError until wired in subsequent tasks.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 6: Wire MarivoRuntime to proxy through SemanticLayerService

**Files:**
- Modify: `app/runtime/runtime.py`
- Create: `app/runtime/session_ops.py`
- Create: `app/runtime/semantic_ops.py`
- Test: `tests/runtime/test_runtime_session_ops.py`
- Test: `tests/runtime/test_runtime_semantic_ops.py`
- Test: `tests/runtime/test_runtime_intent_dispatch.py`

This task fills in the `NotImplementedError` stubs so MarivoRuntime actually proxies to the existing service.

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_runtime_session_ops.py
"""Test session lifecycle delegation through Runtime."""
from unittest.mock import MagicMock, patch

from app.contracts.ids import SessionId, UserId
from app.contracts.session import SessionState
from app.core.engine import CoreEngine
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime


def _make_runtime() -> tuple[MarivoRuntime, MagicMock]:
    svc = MagicMock(name="SemanticLayerService")
    core = CoreEngine(svc)
    ports = MagicMock(spec=RuntimePorts)
    runtime = MarivoRuntime(ports, core)
    return runtime, svc


def test_create_session_proxies_to_service():
    runtime, svc = _make_runtime()
    expected = MagicMock()
    svc.create_session.return_value = expected

    result = runtime.create_session("test goal", actor=UserId("user1"))

    svc.create_session.assert_called_once()
    assert result is expected


def test_get_session_proxies_to_service():
    runtime, svc = _make_runtime()
    expected = MagicMock()
    svc.get_session.return_value = expected

    result = runtime.get_session(SessionId("s1"))

    svc.get_session.assert_called_once_with("s1")
    assert result is expected


def test_terminate_session_proxies_to_service():
    runtime, svc = _make_runtime()

    runtime.terminate_session(SessionId("s1"))

    svc.terminate_session.assert_called_once_with("s1")


def test_get_session_state_proxies_to_service():
    runtime, svc = _make_runtime()
    expected = {"status": "open"}
    svc.get_session_state.return_value = expected

    result = runtime.get_session_state(SessionId("s1"))

    svc.get_session_state.assert_called_once_with("s1")
    assert result is expected
```

```python
# tests/runtime/test_runtime_semantic_ops.py
"""Test semantic model ops delegation through Runtime."""
from unittest.mock import MagicMock

from app.contracts.ids import ModelId, UserId
from app.contracts.semantic import SemanticModel
from app.core.engine import CoreEngine
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime


def _make_runtime() -> tuple[MarivoRuntime, MagicMock]:
    svc = MagicMock(name="SemanticLayerService")
    core = CoreEngine(svc)
    ports = MagicMock(spec=RuntimePorts)
    runtime = MarivoRuntime(ports, core)
    return runtime, svc


def test_get_semantic_model_proxies_to_ports():
    runtime, svc = _make_runtime()
    expected = MagicMock(spec=SemanticModel)
    runtime._ports.model_store.get.return_value = expected

    result = runtime.get_semantic_model(selector=MagicMock())

    runtime._ports.model_store.get.assert_called_once()
    assert result is expected


def test_list_semantic_models_proxies_to_ports():
    runtime, svc = _make_runtime()
    expected = [MagicMock()]
    runtime._ports.model_store.list.return_value = expected

    result = runtime.list_semantic_models(query=MagicMock())

    runtime._ports.model_store.list.assert_called_once()
    assert result is expected
```

```python
# tests/runtime/test_runtime_intent_dispatch.py
"""Test intent dispatch through Runtime proxies to service.run_intent."""
from unittest.mock import MagicMock

from app.contracts.ids import SessionId
from app.core.engine import CoreEngine
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime


def _make_runtime() -> tuple[MarivoRuntime, MagicMock]:
    svc = MagicMock(name="SemanticLayerService")
    core = CoreEngine(svc)
    ports = MagicMock(spec=RuntimePorts)
    runtime = MarivoRuntime(ports, core)
    return runtime, svc


def test_observe_proxies_to_service():
    runtime, svc = _make_runtime()
    expected = {"artifact_id": "a1"}
    svc.run_intent.return_value = expected

    result = runtime.observe(SessionId("s1"), {"metric": "revenue"})

    svc.run_intent.assert_called_once_with("s1", "observe", {"metric": "revenue"})
    assert result is expected


def test_diagnose_proxies_to_service():
    runtime, svc = _make_runtime()
    expected = {"propositions": []}
    svc.run_intent.return_value = expected

    result = runtime.diagnose(SessionId("s1"), {"metric": "revenue"})

    svc.run_intent.assert_called_once_with("s1", "diagnose", {"metric": "revenue"})
    assert result is expected


def test_all_intent_methods_proxy_to_service():
    runtime, svc = _make_runtime()
    svc.run_intent.return_value = {"ok": True}

    intent_methods = [
        "observe", "compare", "decompose", "correlate",
        "detect", "test", "forecast", "attribute",
        "diagnose", "validate",
    ]
    for method_name in intent_methods:
        method = getattr(runtime, method_name)
        result = method(SessionId("s1"), {"metric": "revenue"})
        assert result == {"ok": True}

    # All 10 calls went through run_intent
    assert svc.run_intent.call_count == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/runtime/test_runtime_session_ops.py tests/runtime/test_runtime_semantic_ops.py tests/runtime/test_runtime_intent_dispatch.py -v`
Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement the proxying**

Update `app/runtime/runtime.py` to hold a reference to `SemanticLayerService` (Phase 3a only) and proxy all methods:

```python
# app/runtime/runtime.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.contracts.ids import SessionId, UserId, ModelId, DatasourceId
from app.contracts.semantic import SemanticModel, ModelSummary
from app.contracts.session import SessionState

if TYPE_CHECKING:
    from app.core.engine import CoreEngine
    from app.runtime.ports import RuntimePorts
    from app.service import SemanticLayerService


class MarivoRuntime:
    """Use-case facade for the Marivo platform.

    Phase 3a: proxies to SemanticLayerService.
    Phase 3b+: intent runners use (core, ports, session_id, params).
    """

    def __init__(
        self,
        ports: RuntimePorts,
        core: CoreEngine,
        svc: SemanticLayerService | None = None,
    ) -> None:
        self._ports = ports
        self._core = core
        self._svc = svc  # Phase 3a: retained for proxying

    # --- Intent use-cases ---

    def observe(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "observe", params)

    def compare(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "compare", params)

    def decompose(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "decompose", params)

    def correlate(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "correlate", params)

    def detect(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "detect", params)

    def test(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "test", params)

    def forecast(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "forecast", params)

    def attribute(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "attribute", params)

    def diagnose(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "diagnose", params)

    def validate(self, session_id: SessionId, params: dict) -> dict:
        return self._svc.run_intent(session_id, "validate", params)

    # --- Session lifecycle ---

    def create_session(self, goal: str, *, actor: UserId | None = None, **kwargs: Any) -> Any:
        return self._svc.create_session(goal, **kwargs)

    def get_session(self, session_id: SessionId) -> Any:
        return self._svc.get_session(session_id)

    def terminate_session(self, session_id: SessionId) -> None:
        self._svc.terminate_session(session_id)

    def get_session_state(self, session_id: SessionId, **filters: Any) -> dict:
        return self._svc.get_session_state(session_id, **filters)

    # --- Semantic model ops ---

    def get_semantic_model(self, selector: Any) -> SemanticModel | None:
        return self._ports.model_store.get(selector)

    def save_semantic_model(
        self, model: SemanticModel, *, actor: UserId
    ) -> ModelId:
        return self._ports.model_store.save(model, actor=actor, expected_revision=None)

    def list_semantic_models(self, query: Any) -> list[ModelSummary]:
        return self._ports.model_store.list(query)

    # --- Datasource ops ---

    def discover_catalog(self, datasource_id: DatasourceId, **kwargs: Any) -> dict:
        return self._svc.discover_catalog(datasource_id, **kwargs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/runtime/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/runtime/runtime.py tests/runtime/test_runtime_session_ops.py tests/runtime/test_runtime_semantic_ops.py tests/runtime/test_runtime_intent_dispatch.py
git commit -m "$(cat <<'EOF'
feat(runtime): wire MarivoRuntime to proxy through SemanticLayerService

Phase 3a: all intent, session, semantic, and datasource methods proxy
to the existing service. Zero behavior change — pure structural seam.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 7: Create factory function and wire into AppServices

**Files:**
- Create: `app/runtime/factory.py`
- Modify: `app/api/deps.py` (add `runtime` field)
- Modify: `app/api/app_factory.py` (construct runtime)
- Modify: `app/runtime/__init__.py` (update re-exports)

- [ ] **Step 1: Write the factory function**

```python
# app/runtime/factory.py
from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.server.wrappers import (
    DataSourceAdapter,
    FileAuditLogAdapter,
    LocalTelemetryAdapter,
    MetadataCacheStoreAdapter,
    MetadataEvidenceStoreAdapter,
    NoopAuthZAdapter,
    SqlModelStoreAdapter,
    SqlSessionStoreAdapter,
    TomlRuntimeConfigAdapter,
)
from app.contracts.ids import EngineId
from app.core.engine import CoreEngine
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime

if TYPE_CHECKING:
    from app.config import MarivoConfig
    from app.datasources import DatasourceService
    from app.routing import QueryRouter
    from app.service import SemanticLayerService
    from app.session import SessionManager
    from app.semantic_service_v2 import SemanticModelV2Service
    from app.storage import MetadataStore
    from app.adapters.base import AnalyticsEngine


def create_runtime_from_service(
    svc: SemanticLayerService,
    session_mgr: SessionManager,
    semantic_svc: SemanticModelV2Service,
    datasource_svc: DatasourceService,
    config: MarivoConfig,
) -> MarivoRuntime:
    """Phase 3a factory: wraps existing infrastructure into Runtime."""
    ports = RuntimePorts(
        model_store=SqlModelStoreAdapter(semantic_svc, svc.metadata),
        session_store=SqlSessionStoreAdapter(svc.metadata, session_mgr),
        evidence_store=MetadataEvidenceStoreAdapter(svc._evidence_repos),
        data_source=DataSourceAdapter(
            {EngineId(eid): eng for eid, eng in svc._engines.items()},
            svc.routing_runtime,
        ),
        cache_store=MetadataCacheStoreAdapter(svc.metadata),
        authz=NoopAuthZAdapter(),
        audit_log=FileAuditLogAdapter(),
        telemetry=LocalTelemetryAdapter(),
        runtime_config=TomlRuntimeConfigAdapter(config),
    )
    core = CoreEngine(svc)
    return MarivoRuntime(ports, core, svc=svc)
```

**Note:** The exact attribute names on `svc` (like `svc._evidence_repos`, `svc._engines`) must be verified against the actual `SemanticLayerService.__init__` code. Adjust as needed.

- [ ] **Step 2: Update AppServices to include runtime**

```python
# In app/api/deps.py, add runtime field to AppServices dataclass:

# Before:
@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str
    config: MarivoConfig
    service: SemanticLayerService
    datasource_service: DatasourceService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    semantic_v2_service: SemanticModelV2Service

# After:
@dataclass(slots=True)
class AppServices:
    resolved_path: Path | str
    config: MarivoConfig
    runtime: MarivoRuntime          # NEW
    service: SemanticLayerService   # Retained for wrapper references
    datasource_service: DatasourceService
    query_router: QueryRouter
    metadata_store: MetadataStore
    analytics_engine: AnalyticsEngine
    metrics: MetricsCollector | None
    semantic_v2_service: SemanticModelV2Service
```

- [ ] **Step 3: Update app_factory.py to construct runtime**

```python
# In app/api/app_factory.py, after constructing the service and query_router:

from app.runtime.factory import create_runtime_from_service

# After: service.query_router = query_router
runtime = create_runtime_from_service(
    svc=service,
    session_mgr=service.session_manager,
    semantic_svc=semantic_v2_service,
    datasource_svc=datasource_service,
    config=config,
)

# In the AppServices construction, add runtime:
services = AppServices(
    resolved_path=resolved_path,
    config=config,
    runtime=runtime,  # NEW
    service=service,
    datasource_service=datasource_service,
    query_router=query_router,
    metadata_store=metadata_store,
    analytics_engine=analytics_engine,
    metrics=metrics_collector,
    semantic_v2_service=semantic_v2_service,
)
```

- [ ] **Step 4: Update `app/runtime/__init__.py`**

```python
# app/runtime/__init__.py
from app.runtime.factory import create_runtime_from_service
from app.runtime.ports import RuntimePorts
from app.runtime.runtime import MarivoRuntime

__all__ = ["MarivoRuntime", "RuntimePorts", "create_runtime_from_service"]
```

- [ ] **Step 5: Run full test suite**

Run: `make test`
Expected: All existing tests green. Runtime construction happens in app factory but no endpoints use it yet.

- [ ] **Step 6: Commit**

```bash
git add app/runtime/factory.py app/runtime/__init__.py app/api/deps.py app/api/app_factory.py
git commit -m "$(cat <<'EOF'
feat(runtime): add factory function and wire Runtime into AppServices

create_runtime_from_service() wraps existing infrastructure into
MarivoRuntime. AppServices now holds both runtime and service.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 8: Switch HTTP intent endpoints to call Runtime

**Files:**
- Modify: `app/api/sessions.py`

- [ ] **Step 1: Update the `_run_intent` helper**

In `app/api/sessions.py`, the dispatch helper `_run_intent` currently calls `get_services(request).service.run_intent(...)`. Change it to call through Runtime:

```python
# Before:
def _run_intent(session_id, intent_type, params, request):
    return get_services(request).service.run_intent(session_id, intent_type, params)

# After:
def _run_intent(session_id, intent_type, params, request):
    return get_services(request).runtime._svc.run_intent(session_id, intent_type, params)
```

Wait — this just adds an indirection through runtime's internal svc reference. Better: use the Runtime's named methods instead. But Runtime currently proxies all intents through `svc.run_intent()` anyway. So the cleanest approach is:

```python
# After (proper):
def _run_intent(session_id, intent_type, params, request):
    runtime = get_services(request).runtime
    method = getattr(runtime, intent_type, None)
    if method is None:
        raise ValueError(f"Unknown intent type: '{intent_type}'")
    return method(session_id, params)
```

This routes all intent calls through Runtime's named methods, which proxy to `svc.run_intent()`. Zero behavior change, but the call path now goes through Runtime.

- [ ] **Step 2: Update session lifecycle endpoints**

Each session endpoint currently calls `get_services(request).service.<method>(...)`. Switch to `get_services(request).runtime.<method>(...)`:

- `service.create_session(...)` → `runtime.create_session(...)`
- `service.get_session(...)` → `runtime.get_session(...)`
- `service.terminate_session(...)` → `runtime.terminate_session(...)`
- `service.get_session_state(...)` → `runtime.get_session_state(...)`
- `service.query_session_state(...)` → `runtime.query_session_state(...)` (add this method to Runtime if needed)

**Note:** Not all service methods may have Runtime proxies yet. Add any missing ones to `MarivoRuntime` as needed. For methods that don't map cleanly, keep the direct `service.*` call for now and add a `# TODO(phase3b): migrate to runtime` comment.

- [ ] **Step 3: Run full test suite**

Run: `make test`
Expected: All green. The HTTP→Runtime→service proxy chain works identically to the old HTTP→service direct path.

- [ ] **Step 4: Commit**

```bash
git add app/api/sessions.py app/runtime/runtime.py
git commit -m "$(cat <<'EOF'
refactor(api): switch HTTP endpoints to call through MarivoRuntime

Intent dispatch and session lifecycle endpoints now route through
Runtime facade. Zero behavior change — Runtime proxies to service.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 9: Add import-linter rules for runtime and core

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Add the two Phase 3a import-linter contracts**

```ini
# Add to .importlinter:

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

- [ ] **Step 2: Run import-linter to verify**

Run: `.venv/bin/lint-imports`
Expected: PASS (no violations yet — runtime only imports from ports/contracts/service, core only imports from service via TYPE_CHECKING)

- [ ] **Step 3: Commit**

```bash
git add .importlinter
git commit -m "$(cat <<'EOF'
chore: add Phase 3a import-linter rules for runtime and core isolation

runtime-no-direct-core-orchestration: runtime must not import
analysis_core or evidence_engine. core-no-io: core must not import
I/O libraries or ports.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 10: Run full verification gate for Phase 3a

- [ ] **Step 1: Run full test suite**

Run: `make test`
Expected: All green

- [ ] **Step 2: Run type checking**

Run: `make typecheck`
Expected: Pass (may need to add `app/runtime/` and `app/core/` to mypy paths if not automatically included)

- [ ] **Step 3: Run lint + import-linter**

Run: `make lint`
Expected: All green, including new import-linter rules

- [ ] **Step 4: Verify acceptance criteria**

Check against Phase 3a acceptance criteria from the spec:
- [ ] `MarivoRuntime` class exists with all use-case methods callable
- [ ] All HTTP intent endpoints call through Runtime, not `SemanticLayerService` directly
- [ ] All MCP intent tools call through Runtime (verify: MCP already uses HTTP, so no changes needed)
- [ ] All existing tests green
- [ ] `app/runtime/` and `app/core/` import-linter rules pass
- [ ] `core/engine.py` only proxies, contains no domain logic

- [ ] **Step 5: Tag the 3a completion**

```bash
git tag phase-3a-complete
```

---

## Phase 3b — Intent Runner Migration

Goal: Migrate 10 intent runners from `(svc, session_id, params)` to `(core, ports, session_id, params)`. Each migration is followed by a green test run.

**Migration order (from spec):** correlate → forecast → test → observe → compare → decompose → detect → attribute → validate → diagnose

**Pattern for each intent runner migration:**

1. Change the function signature from `run_X_intent(svc: SemanticLayerService, session_id, params)` to `run_X_intent(core: CoreEngine, ports: RuntimePorts, session_id, params)`
2. Replace `svc.some_method(...)` calls with either `core.some_method(...)` (for domain computation) or `ports.some_port.method(...)` (for I/O)
3. For methods not yet on CoreEngine, add a proxy method to CoreEngine that delegates to `self._svc`
4. Update the intent registration in `SemanticLayerService.__init__` to pass `core` and `ports` instead of `self`
5. For derived intents, replace direct function calls with `runtime.<intent>()` calls
6. Run tests, commit

**IMPORTANT: Method deletion rule:** Do NOT delete methods from `SemanticLayerService` until ALL consumers of that method have been migrated. The CoreEngine proxy may still reference service methods for unmigrated runners.

Because each intent runner has unique logic, the tasks below provide the pattern and list of `svc.*` calls to replace, but the exact code changes depend on what each runner actually does. The engineer must read each intent file, identify every `svc.` reference, and determine whether it maps to `core.*` or `ports.*`.

---

### Task 11: Migrate `correlate` intent runner

**Files:**
- Modify: `app/intents/correlate.py`
- Modify: `app/service.py` (update registration lambda)
- Test: existing `tests/test_intent_correlate.py` (should still pass)

**correlate** is the simplest: ~435 lines, only references artifact + commit.

- [ ] **Step 1: Identify all `svc.` references in correlate.py**

Read `app/intents/correlate.py` and list every `svc.` method call. Classify each as:
- **core** (domain computation) → will become `core.some_method(...)`
- **ports** (I/O) → will become `ports.some_port.method(...)`
- **runtime** (orchestration that uses both) → will become `runtime.some_method(...)`

- [ ] **Step 2: Change the function signature**

```python
# Before:
def run_correlate_intent(
    svc: SemanticLayerService, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:

# After:
from app.core.engine import CoreEngine
from app.runtime.ports import RuntimePorts

def run_correlate_intent(
    core: CoreEngine, ports: RuntimePorts, session_id: str, params: dict[str, Any] | None
) -> dict[str, Any]:
```

- [ ] **Step 3: Replace svc references**

Replace each `svc.some_call(...)` with the appropriate `core.*` or `ports.*` call based on the classification from Step 1. For any method not yet available on CoreEngine, add a proxy method to `app/core/engine.py`.

- [ ] **Step 4: Update registration in service.py**

```python
# Before (in SemanticLayerService.__init__):
self.intent_registry.register(
    "correlate", lambda sid, p: run_correlate_intent(self, sid, p)
)

# After:
self.intent_registry.register(
    "correlate", lambda sid, p: run_correlate_intent(
        self._core_engine, self._runtime_ports, sid, p
    )
)
```

This requires `SemanticLayerService.__init__` to have `self._core_engine` and `self._runtime_ports` attributes, which will be set by the factory function.

**Alternative:** Instead of adding attributes to SemanticLayerService, have the Runtime's intent dispatch call the runner directly (bypassing the registry). This is cleaner but requires updating how Runtime dispatches intents. Choose whichever matches the current architecture better.

- [ ] **Step 5: Run tests**

Run: `make test`
Expected: All green

- [ ] **Step 6: Commit**

```bash
git add app/intents/correlate.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate correlate runner to (core, ports) signature

First intent runner migrated from (svc, session_id, params) to
(core, ports, session_id, params). CoreEngine gains proxy methods
as needed.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 12: Migrate `forecast` intent runner

**Files:**
- Modify: `app/intents/forecast.py`
- Test: existing `tests/test_intent_forecast.py` (should still pass)

Same pattern as Task 11. Forecast is similar to correlate — simple artifact reference.

- [ ] **Step 1-6:** Same pattern as Task 11, applied to `forecast.py`

```bash
git add app/intents/forecast.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate forecast runner to (core, ports) signature

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 13: Migrate `test` intent runner

**Files:**
- Modify: `app/intents/test.py`
- Test: existing `tests/test_intent_test.py` (should still pass)

Same pattern. `test` references observe artifact, no sub-intent calls.

- [ ] **Step 1-6:** Same pattern as Task 11, applied to `test.py`

```bash
git add app/intents/test.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate test runner to (core, ports) signature

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 14: Migrate `observe` intent runner

**Files:**
- Modify: `app/intents/observe.py`
- Test: existing intent tests (should still pass)

**observe** is the core atomic intent (~1191 lines). All derived intents depend on it. This is the highest-complexity migration.

- [ ] **Step 1:** Read the full `app/intents/observe.py` and classify every `svc.` reference

- [ ] **Step 2-6:** Apply the migration pattern. This will likely require adding several proxy methods to CoreEngine (metric resolution, scope resolution, etc.)

**Key concern:** observe is called by many derived intents. After migration, derived intents that call `run_observe_intent(core, ports, ...)` directly should instead call `runtime.observe(...)` to ensure the call goes through Runtime's dispatch (authz, audit, telemetry hooks).

```bash
git add app/intents/observe.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate observe runner to (core, ports) signature

Core atomic intent migration. Derived intents now call
runtime.observe() instead of run_observe_intent() directly.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 15: Migrate `compare` intent runner

**Files:**
- Modify: `app/intents/compare.py`

References observe artifact. Same pattern.

- [ ] **Step 1-6:** Apply migration pattern

```bash
git add app/intents/compare.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate compare runner to (core, ports) signature

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 16: Migrate `decompose` intent runner

**Files:**
- Modify: `app/intents/decompose.py`

References observe + compare artifact.

- [ ] **Step 1-6:** Apply migration pattern

```bash
git add app/intents/decompose.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate decompose runner to (core, ports) signature

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 17: Migrate `detect` intent runner

**Files:**
- Modify: `app/intents/detect.py`

Anomaly detection orchestration.

- [ ] **Step 1-6:** Apply migration pattern

```bash
git add app/intents/detect.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate detect runner to (core, ports) signature

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 18: Migrate `attribute` intent runner

**Files:**
- Modify: `app/intents/attribute.py`

Derived intent: calls observe + compare + decompose. Sub-intent calls must go through `runtime.observe()`, `runtime.compare()`, `runtime.decompose()`.

- [ ] **Step 1-6:** Apply migration pattern. Ensure sub-intent calls use `runtime.*()` not direct function calls.

```bash
git add app/intents/attribute.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate attribute runner to (core, ports) signature

Derived intent: sub-intent calls now go through runtime.observe(),
runtime.compare(), runtime.decompose().

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 19: Migrate `validate` intent runner

**Files:**
- Modify: `app/intents/validate.py`

Derived: calls observe + test.

- [ ] **Step 1-6:** Apply migration pattern

```bash
git add app/intents/validate.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate validate runner to (core, ports) signature

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 20: Migrate `diagnose` intent runner

**Files:**
- Modify: `app/intents/diagnose.py`

Most complex derived intent: calls detect + observe + compare + decompose.

- [ ] **Step 1-6:** Apply migration pattern

```bash
git add app/intents/diagnose.py app/core/engine.py app/service.py
git commit -m "$(cat <<'EOF'
refactor(intents): migrate diagnose runner to (core, ports) signature

Last intent runner migrated. All 10 runners now use (core, ports,
session_id, params) signature.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 21: Clean up SemanticLayerService — delete migrated methods

**Files:**
- Modify: `app/service.py`

Apply the **method deletion rule**: for each private method on `SemanticLayerService` that is now only referenced by `CoreEngine` proxy (and no longer by any intent runner), evaluate whether to:

1. Keep it on service.py (if other code still calls it)
2. Delete it from service.py (if only the CoreEngine proxy references it, and the proxy is about to become real in Phase 3c)

**Be conservative.** When in doubt, keep the method. Phase 3c will handle the full cleanup when CoreEngine transitions from proxy to real.

- [ ] **Step 1:** Search for all remaining `svc.` references in intent files

Run: `grep -rn "svc\." app/intents/`
Expected: Zero results (all intent runners have been migrated)

- [ ] **Step 2:** Search for all remaining direct calls to SemanticLayerService private methods

Run: `grep -rn "_resolve_\|_compile_\|_build_\|_run_\|_insert_\|_commit_" app/ --include="*.py" | grep -v "app/service.py" | grep -v "app/core/" | grep -v "__pycache__"`
Expected: Only references from `app/core/engine.py` proxy methods

- [ ] **Step 3:** Delete any methods from service.py that have no remaining consumers

For each method identified in Step 2 as only referenced by CoreEngine proxy:
- If the proxy will be replaced in Phase 3c, keep the method for now
- If the method is truly dead code (no references at all), delete it

- [ ] **Step 4: Run tests**

Run: `make test`
Expected: All green

- [ ] **Step 5: Commit**

```bash
git add app/service.py
git commit -m "$(cat <<'EOF'
refactor(service): delete methods fully migrated to core/ports

Remove SemanticLayerService methods that have no remaining consumers
after intent runner migration. Conservative: only delete methods
with zero references.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 22: Run full verification gate for Phase 3b

- [ ] **Step 1:** Run full test suite: `make test`
- [ ] **Step 2:** Run type checking: `make typecheck`
- [ ] **Step 3:** Run lint + import-linter: `make lint`
- [ ] **Step 4:** Verify acceptance criteria:
  - [ ] All 10 intent runners use `(core, ports, session_id, params)` signature
  - [ ] Migrated methods deleted from SemanticLayerService (only when last consumer has migrated)
  - [ ] Each intent migration followed by full green test run
  - [ ] Derived intents call sub-intents through `runtime.*()` methods

- [ ] **Step 5:** Tag: `git tag phase-3b-complete`

---

## Phase 3c — Core Extraction

Goal: Extract pure computation logic from service.py and analysis_core/ into `core/`. CoreEngine transitions from proxy to real facade. `core/` becomes I/O-free.

This is the most complex sub-phase. The extraction must be done incrementally, one module at a time, with green tests after each.

---

### Task 23: Create `core/semantic/` package and migrate `analysis_core/ir.py`

**Files:**
- Create: `app/core/semantic/__init__.py`
- Create: `app/core/semantic/ir.py` (copy from `app/analysis_core/ir.py`)
- Test: `tests/core/test_semantic_ir.py`

`ir.py` (~659 lines) contains pure data definitions — the safest first extraction.

- [ ] **Step 1:** Read `app/analysis_core/ir.py` fully. Identify any I/O dependencies or imports from `SemanticRuntimeRepository`.

- [ ] **Step 2:** Copy the file to `app/core/semantic/ir.py`, removing any I/O imports. If it imports `SemanticRuntimeRepository`, refactor to accept pre-loaded data as parameters.

- [ ] **Step 3:** Write tests for the extracted module:

```python
# tests/core/test_semantic_ir.py
"""Test core IR definitions are pure (no I/O)."""
import importlib

def test_ir_module_has_no_io_imports():
    """Verify core.semantic.ir does not import any I/O modules."""
    ir = importlib.import_module("app.core.semantic.ir")
    source = open(ir.__file__).read()
    forbidden = ["sqlalchemy", "duckdb", "httpx", "MetadataStore", "Session"]
    for word in forbidden:
        assert word not in source, f"Forbidden import '{word}' found in core.semantic.ir"
```

- [ ] **Step 4:** Mark the original as deprecated:

Add to top of `app/analysis_core/ir.py`:
```python
# DEPRECATED: use app.core.semantic.ir
```

- [ ] **Step 5:** Run tests: `make test`

- [ ] **Step 6:** Commit

```bash
git add app/core/semantic/__init__.py app/core/semantic/ir.py app/analysis_core/ir.py tests/core/test_semantic_ir.py
git commit -m "$(cat <<'EOF'
feat(core): extract ir.py to core/semantic/ as pure data definitions

analysis_core/ir.py marked deprecated. core/semantic/ir.py has
no I/O imports, verified by import-linter.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 24: Create `core/intent/` package and migrate primitives + registries

**Files:**
- Create: `app/core/intent/__init__.py`
- Create: `app/core/intent/primitives.py` (from `app/analysis_core/primitives.py`)
- Create: `app/core/intent/step_registry.py` (from `app/analysis_core/step_registry.py`)
- Create: `app/core/intent/intent_registry.py` (from `app/analysis_core/intent_registry.py`)
- Test: `tests/core/test_intent_primitives.py`

`primitives.py` (~139 lines) is pure taxonomy definitions. Both registries are pure `dict[str, Callable]`.

- [ ] **Step 1-6:** Same copy-verify-deprecate pattern as Task 23.

```bash
git add app/core/intent/ app/analysis_core/primitives.py app/analysis_core/step_registry.py app/analysis_core/intent_registry.py tests/core/
git commit -m "$(cat <<'EOF'
feat(core): extract intent primitives and registries to core/intent/

primitives.py, step_registry.py, intent_registry.py are pure
data/callable definitions with no I/O.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 25: Extract scope resolution pure functions to `core/semantic/scope_resolution.py`

**Files:**
- Create: `app/core/semantic/scope_resolution.py`
- Modify: `app/service.py` (reference extracted functions)
- Test: `tests/core/test_scope_resolution.py`

~400 lines of scope constraint resolution, predicate SQL generation from service.py private methods.

- [ ] **Step 1:** Read the scope resolution methods from service.py:
  - `_resolve_scope_constraint_column`
  - `_constraints_dict_to_filter`
  - `_resolved_scope_filter`
  - `_resolve_predicate_ref_to_filter`
  - `_predicate_expression_to_sql`
  - `_build_scoped_query`

- [ ] **Step 2:** Extract each method body into a pure function in `scope_resolution.py`. Replace `self._metadata` / `self.semantic_repository` references with pre-loaded parameters.

- [ ] **Step 3:** Write tests using pure function inputs (no mocks for infrastructure):

```python
# tests/core/test_scope_resolution.py
def test_constraints_dict_to_filter_converts_simple_equality():
    from app.core.semantic.scope_resolution import constraints_dict_to_filter
    result = constraints_dict_to_filter({"country": "US", "region": "CA"})
    assert "country" in result
    assert result["country"] == "US"
```

- [ ] **Step 4:** Update CoreEngine to delegate to extracted functions instead of service proxy

- [ ] **Step 5:** Run tests: `make test`

- [ ] **Step 6:** Commit

```bash
git add app/core/semantic/scope_resolution.py app/core/engine.py tests/core/test_scope_resolution.py
git commit -m "$(cat <<'EOF'
feat(core): extract scope resolution pure functions from service.py

Predicate SQL generation and scope constraint resolution extracted
as pure functions with no I/O dependencies.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 26: Extract metric resolution pure functions to `core/semantic/metric_resolution.py`

**Files:**
- Create: `app/core/semantic/metric_resolution.py`
- Modify: `app/core/engine.py`
- Test: `tests/core/test_metric_resolution.py`

~700 lines — the largest extraction. Methods from service.py that resolve metric binding, route preflight, and execution context.

- [ ] **Step 1:** Read `_resolve_metric_execution_context` and all its helper methods from service.py

- [ ] **Step 2:** Extract as pure functions. Replace `self._metadata` / `self.semantic_repository` with pre-loaded `SemanticModel`, `relationships`, etc. as parameters.

- [ ] **Step 3:** Write pure function tests:

```python
# tests/core/test_metric_resolution.py
def test_resolve_metric_context_returns_binding():
    from app.core.semantic.metric_resolution import resolve_metric_execution_context
    result = resolve_metric_execution_context(
        metric_name="revenue",
        model=sample_semantic_model,
        entity=sample_entity,
        relationships=sample_relationships,
    )
    assert result.metric_binding is not None
```

- [ ] **Step 4:** Update CoreEngine to use real pure functions instead of proxy

- [ ] **Step 5:** Run tests: `make test`

- [ ] **Step 6:** Commit

```bash
git add app/core/semantic/metric_resolution.py app/core/engine.py tests/core/test_metric_resolution.py
git commit -m "$(cat <<'EOF'
feat(core): extract metric resolution pure functions from service.py

CoreEngine now uses real pure functions for metric resolution
instead of proxying to SemanticLayerService.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 27: Migrate `analysis_core/typed_resolution.py` pure parts

**Files:**
- Create: `app/core/semantic/typed_resolution.py`
- Test: `tests/core/test_typed_resolution.py`

~862 lines. Current `SemanticRuntimeRepository` dependency needs refactoring to accept pre-loaded data.

- [ ] **Step 1-6:** Same pattern. Identify I/O parts, refactor to accept pre-loaded data.

```bash
git add app/core/semantic/typed_resolution.py tests/core/test_typed_resolution.py app/analysis_core/typed_resolution.py
git commit -m "$(cat <<'EOF'
feat(core): extract typed_resolution pure parts to core/semantic/

I/O-dependent parts refactored to accept pre-loaded semantic data.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 28: Migrate `analysis_core/compiler.py`

**Files:**
- Create: `app/core/semantic/compiler.py`
- Test: `tests/core/test_compiler.py`

~1970 lines. The largest single file migration. Currently accepts `SemanticRuntimeRepository` (has I/O).

- [ ] **Step 1:** Read `app/analysis_core/compiler.py` fully. Identify which methods need `SemanticRuntimeRepository` and what data they load.

- [ ] **Step 2:** Refactor to accept pre-loaded data. This is the most complex extraction — may need to be broken into sub-steps within this task.

- [ ] **Step 3-6:** Extract, test, deprecate original, commit.

```bash
git add app/core/semantic/compiler.py tests/core/test_compiler.py app/analysis_core/compiler.py
git commit -m "$(cat <<'EOF'
feat(core): extract compiler.py to core/semantic/ with pre-loaded data

SemanticRuntimeRepository I/O replaced with pre-loaded data
parameters. Original marked deprecated.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 29: Migrate `analysis_core/validator.py`

**Files:**
- Create: `app/core/semantic/validator.py`
- Test: `tests/core/test_validator.py`

~1138 lines. Most is pure validation, few repository lookups need refactoring.

- [ ] **Step 1-6:** Same pattern.

```bash
git add app/core/semantic/validator.py tests/core/test_validator.py app/analysis_core/validator.py
git commit -m "$(cat <<'EOF'
feat(core): extract validator.py to core/semantic/

Repository lookups refactored to accept pre-loaded data.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 30: Extract evidence engine pure functions to `core/evidence/`

**Files:**
- Create: `app/core/evidence/__init__.py`
- Create: `app/core/evidence/finding_extraction.py`
- Create: `app/core/evidence/proposition_seeding.py`
- Create: `app/core/evidence/assessment.py`
- Create: `app/core/evidence/proposal.py`
- Test: `tests/core/test_evidence_*.py`

~3000 lines total across evidence engine extractors. Finding extraction, proposition seeding, assessment computation, and proposal generation are all pure computation that can be extracted.

- [ ] **Step 1:** Read each evidence engine file and classify I/O vs pure computation

- [ ] **Step 2:** Extract pure functions. For each module:
  - `finding_extraction.py`: Extract `StepRef`, finding ID generation, extractor logic from `observe_extractor.py`, `compare_extractor.py`, etc.
  - `proposition_seeding.py`: Extract seed rules and proposition generation from `proposition_seeding_run.py`
  - `assessment.py`: Extract evaluation logic from `assessment_recompute.py`
  - `proposal.py`: Extract proposal generation from `proposal_refresh_run.py`

- [ ] **Step 3:** Write zero-I/O, zero-mock tests

- [ ] **Step 4:** Mark originals as deprecated

- [ ] **Step 5:** Run tests: `make test`

- [ ] **Step 6:** Commit

```bash
git add app/core/evidence/ tests/core/ app/evidence_engine/
git commit -m "$(cat <<'EOF'
feat(core): extract evidence engine pure computation to core/evidence/

Finding extraction, proposition seeding, assessment, and proposal
generation extracted as pure functions. I/O orchestration stays
in runtime/. Originals marked deprecated.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 31: Migrate calendar pure functions to `core/semantic/calendar.py`

**Files:**
- Create: `app/core/semantic/calendar.py`
- Test: `tests/core/test_calendar.py`

~800 lines from `analysis_core/calendar_*.py` (merged).

- [ ] **Step 1-6:** Same pattern.

```bash
git add app/core/semantic/calendar.py tests/core/test_calendar.py app/analysis_core/calendar_alignment_baseline.py app/analysis_core/calendar_alignment_pairing.py app/analysis_core/calendar_policy.py
git commit -m "$(cat <<'EOF'
feat(core): extract calendar pure functions to core/semantic/calendar.py

Merged from calendar_alignment_baseline, calendar_alignment_pairing,
and calendar_policy. Calendar data loading (I/O) stays in runtime.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 32: Migrate additivity capabilities to `core/semantic/additivity.py`

**Files:**
- Create: `app/core/semantic/additivity.py`
- Test: `tests/core/test_additivity.py`

From `analysis_core/additivity_capabilities.py`.

- [ ] **Step 1-6:** Same pattern.

```bash
git add app/core/semantic/additivity.py tests/core/test_additivity.py app/analysis_core/additivity_capabilities.py
git commit -m "$(cat <<'EOF'
feat(core): extract additivity capabilities to core/semantic/

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 33: Transition CoreEngine from proxy to real facade

**Files:**
- Modify: `app/core/engine.py`
- Modify: `app/runtime/runtime.py` (remove svc reference)

Now that all pure logic is extracted to `core/`, replace the CoreEngine proxy with a real facade that delegates to extracted modules.

- [ ] **Step 1:** Replace proxy methods with real delegation:

```python
# app/core/engine.py (Phase 3c)
from __future__ import annotations

from typing import Any

from app.core.semantic.metric_resolution import resolve_metric_execution_context
from app.core.semantic.scope_resolution import constraints_dict_to_filter
from app.core.semantic.compiler import compile_step
# ... other extracted modules


class CoreEngine:
    """Real core: delegates to extracted pure modules."""

    def resolve_metric_execution_context(self, *args: Any, **kwargs: Any) -> Any:
        return resolve_metric_execution_context(*args, **kwargs)

    def compile_step(self, *args: Any, **kwargs: Any) -> Any:
        return compile_step(*args, **kwargs)

    def build_step_semantic_metadata(self, *args: Any, **kwargs: Any) -> Any:
        # ... delegate to extracted module
        ...
```

- [ ] **Step 2:** Remove `svc` parameter from `MarivoRuntime.__init__`:

```python
# Before:
def __init__(self, ports: RuntimePorts, core: CoreEngine, svc: SemanticLayerService | None = None) -> None:
    self._ports = ports
    self._core = core
    self._svc = svc

# After:
def __init__(self, ports: RuntimePorts, core: CoreEngine) -> None:
    self._ports = ports
    self._core = core
```

Update all methods that previously proxied through `self._svc` to use `self._core` and `self._ports` instead.

- [ ] **Step 3:** Update factory function — `CoreEngine` no longer needs `svc`

- [ ] **Step 4:** Run tests: `make test`

- [ ] **Step 5:** Commit

```bash
git add app/core/engine.py app/runtime/runtime.py app/runtime/factory.py
git commit -m "$(cat <<'EOF'
refactor(core): transition CoreEngine from proxy to real facade

CoreEngine now delegates to extracted pure modules. MarivoRuntime
no longer holds SemanticLayerService reference. Core is I/O-free.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 34: Run full verification gate for Phase 3c

- [ ] **Step 1:** Run full test suite: `make test`
- [ ] **Step 2:** Run type checking: `make typecheck`
- [ ] **Step 3:** Run lint + import-linter: `make lint`
- [ ] **Step 4:** Verify acceptance criteria:
  - [ ] `core/` imports no I/O library or adapter (import-linter verified)
  - [ ] `core/` imports no `app.ports` (import-linter verified)
  - [ ] All intent runners use core pure functions + ports I/O
  - [ ] All existing tests green
  - [ ] Deprecated files in `analysis_core/` marked

- [ ] **Step 5:** Tag: `git tag phase-3c-complete`

---

## Phase 3d — Service Shell Removal & Boundary Enforcement

Goal: Remove `SemanticLayerService` remnants, tighten import-linter rules, delete deprecated files.

---

### Task 35: Delete deprecated files in `analysis_core/`

**Files:**
- Delete: all `app/analysis_core/*.py` files marked as deprecated
- Verify: no remaining imports of deleted modules

- [ ] **Step 1:** Search for remaining imports of deprecated modules

Run: `grep -rn "from app.analysis_core" app/ --include="*.py" | grep -v "__pycache__" | grep -v "DEPRECATED"`
Expected: Only references from `app/core/` (which re-exports) and `app/service.py`

- [ ] **Step 2:** For each deprecated file, verify all callers have switched to `app.core.*` equivalent

- [ ] **Step 3:** Delete the deprecated files

- [ ] **Step 4:** Run tests: `make test`

- [ ] **Step 5:** Commit

```bash
git add -A app/analysis_core/
git commit -m "$(cat <<'EOF'
chore: delete deprecated analysis_core/ files migrated to core/

All callers now reference app.core.semantic.* and app.core.intent.*.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 36: Reduce or delete `SemanticLayerService`

**Files:**
- Modify: `app/service.py`

- [ ] **Step 1:** Search for remaining references to `SemanticLayerService`

Run: `grep -rn "SemanticLayerService" app/ --include="*.py" | grep -v "__pycache__" | grep -v "service.py"`

- [ ] **Step 2:** For each reference, migrate to use `MarivoRuntime` or `CoreEngine` directly

- [ ] **Step 3:** If `service.py` is down to < 100 lines of init glue, keep it as a thin compatibility shim. If it's empty, delete it.

- [ ] **Step 4:** Update test fixtures that construct `SemanticLayerService`:

In `tests/semantic_test_helpers.py`, update `build_semantic_layer_service` to construct `MarivoRuntime` instead (or alongside, with the service construction as a transitional helper).

- [ ] **Step 5:** Run tests: `make test`

- [ ] **Step 6:** Commit

```bash
git add app/service.py tests/semantic_test_helpers.py
git commit -m "$(cat <<'EOF'
refactor: reduce SemanticLayerService to init glue or delete

All business logic moved to core/ and runtime/. Service reduced
to thin compatibility shim or deleted entirely.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 37: Tighten import-linter rules for Phase 3d

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1:** Add the Phase 3c+ and 3d import-linter rules:

```ini
# Add to .importlinter:

[importlinter:contract:runtime-must-use-core]
name = runtime/ must use core/ for domain logic
type = forbidden
source_modules =
    app.runtime
forbidden_modules =
    app.analysis_core
    app.evidence_engine

[importlinter:contract:surfaces-must-use-runtime]
name = api/ and cli/ must go through runtime
type = forbidden
source_modules =
    app.api
    app.cli
forbidden_modules =
    app.analysis_core
    app.evidence_engine
    app.semantic_runtime
```

- [ ] **Step 2:** Update the `core-no-io` rule to also forbid `app.service`:

```ini
# Update core-no-io forbidden_modules to add:
    app.service
```

- [ ] **Step 3:** Run import-linter: `.venv/bin/lint-imports`

- [ ] **Step 4:** Commit

```bash
git add .importlinter
git commit -m "$(cat <<'EOF'
chore: tighten import-linter rules for Phase 3d

Add runtime-must-use-core, surfaces-must-use-runtime, and extend
core-no-io to forbid app.service imports.

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```

---

### Task 38: Run final verification gate for Phase 3d

- [ ] **Step 1:** Run full test suite: `make test`
- [ ] **Step 2:** Run type checking: `make typecheck`
- [ ] **Step 3:** Run lint + import-linter: `make lint`
- [ ] **Step 4:** Verify all acceptance criteria:
  - [ ] `SemanticLayerService` deleted or reduced to empty shell
  - [ ] `CoreEngine` does not reference `SemanticLayerService`
  - [ ] `core/` import-linter rules pass
  - [ ] `runtime/` does not import `analysis_core/` or `evidence_engine/`
  - [ ] All existing tests green
  - [ ] HTTP and MCP handlers do not import `SemanticLayerService`

- [ ] **Step 5:** Tag: `git tag phase-3d-complete`

- [ ] **Step 6:** Final commit with spec reference

```bash
git commit --allow-empty -m "$(cat <<'EOF'
milestone: Phase 3 Runtime Decoupling complete

All 4 sub-phases delivered:
- 3a: Runtime shell + port wiring
- 3b: Intent runner migration (10 runners)
- 3c: Core extraction (I/O-free pure logic)
- 3d: Service shell removal + boundary enforcement

Spec: docs/superpowers/specs/2026-05-07-phase3-runtime-decoupling-design.md

Co-Authored-By: Claude Code:glm-5.1 [Edit] [Bash]
EOF
)"
```
