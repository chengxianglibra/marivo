# Phase 2: Contracts & Ports Design

**Date:** 2026-05-06
**Status:** Approved
**Parent spec:** `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`

---

## 1. Scope & Objective

Phase 2 lands the `contracts/` and `ports/` packages inside the current `app/` package root, establishing the shared type seams and Port Protocol definitions required by the five-layer architecture. No repo-wide rename, no adapter implementation, and no caller migration — pure definition layer.

**Acceptance criteria** (from parent spec):

1. `import-linter` rules pass for the new seams.
2. All existing tests remain green.
3. Adding `contracts/` and `ports/` packages does not break existing HTTP/MCP compilation (no existing code is required to import from the new packages yet).

---

## 2. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Contracts scope | Contract alignment — IDs + value objects + request/response types for Port signatures | Port Protocols need a closed type system; referencing `app/api/models/` would violate isolation |
| Port coverage | Progressive — 3 full Ports (ModelStore, SessionStore, DataSource) + 6 skeleton Ports | Reduces first-pass surface while establishing the full Protocol set |
| Adapter implementation | None in Phase 2 — pure definitions | Matches "pure definition layer" decision; adapter work belongs in Phase 3 |
| Import-linter | Introduced in Phase 2 | New seams must be clean from day one |
| Domain types vs HTTP models | New types in `contracts/`, existing `api/models/` untouched | Coexistence until Phase 3 progressively switches callers; avoids big-bang migration |

---

## 3. Package Structure

```
app/contracts/
  __init__.py          # Re-exports key symbols
  ids.py               # NewType ID wrappers
  values.py            # Cross-cutting value objects
  errors.py            # Domain error codes and typed exceptions
  evidence.py          # Evidence domain types
  session.py           # Session domain types
  semantic.py          # Semantic model domain types

app/ports/
  __init__.py           # Re-exports all Port Protocols
  model_store.py        # ModelStore + ModelSelector + ModelListQuery
  session_store.py      # SessionStore
  evidence_store.py     # EvidenceStore
  data_source.py        # DataSource
  cache_store.py        # CacheStore
  authz.py              # AuthZ
  audit_log.py          # AuditLog
  telemetry.py          # Telemetry
  runtime_config.py     # RuntimeConfig
```

**`__init__.py` conventions:**

- `app/contracts/__init__.py` re-exports all ID types, key value objects, and domain aggregates so that `from app.contracts import SessionId, TimeScope, Evidence` works.
- `app/ports/__init__.py` re-exports all Port Protocols so that `from app.ports import ModelStore, SessionStore` works.
- Individual `ports/*.py` files also re-export their companion request/response types (e.g., `from app.ports.model_store import ModelStore, ModelSelector, ModelListQuery`).

---

## 4. Contracts: IDs

All ID types use `NewType` for type-level distinction. No runtime validation — IDs are opaque identifiers.

```python
# app/contracts/ids.py
from typing import NewType

# Session domain
SessionId = NewType("SessionId", str)
StepId = NewType("StepId", str)
ArtifactId = NewType("ArtifactId", str)
AttemptId = NewType("AttemptId", str)

# Evidence domain
FindingId = NewType("FindingId", str)
PropositionId = NewType("PropositionId", str)
AssessmentId = NewType("AssessmentId", str)
ActionProposalId = NewType("ActionProposalId", str)
GapId = NewType("GapId", str)
InferenceRecordId = NewType("InferenceRecordId", str)

# Semantic domain
ModelId = NewType("ModelId", int)
RevisionId = NewType("RevisionId", str)
DatasetName = NewType("DatasetName", str)

# RevisionId conversion rule:
# - SqlModelStore (MySQL): DB uses INTEGER auto-increment; adapter converts
#   via str(revision) on read and int(revision_id) on write.
# - FileModelStore (local): uses content-hash strings directly.
# Both adapters produce/consume RevisionId as str.
MetricName = NewType("MetricName", str)
RelationshipName = NewType("RelationshipName", str)

# Infrastructure
DatasourceId = NewType("DatasourceId", str)
EngineId = NewType("EngineId", str)
RouteId = NewType("RouteId", str)

# Auth domain
UserId = NewType("UserId", str)
Action = NewType("Action", str)
ResourceId = NewType("ResourceId", str)

# Evidence referencing
EvidenceRef = NewType("EvidenceRef", str)
CacheKey = NewType("CacheKey", str)
```

**Notes:**
- `ModelId` is `NewType("ModelId", int)` because it is currently an auto-increment integer PK.
- `EvidenceRef` is a string containing a sha256 hash — the hash algorithm and canonicalization rules are `EvidenceStore` implementation details, not ID-type concerns.
- Existing code continues using bare `str` / `int` for IDs. Only `contracts/` and `ports/` reference these typed IDs.

---

## 5. Contracts: Value Objects

Core value objects shared across Ports and domain types. All are Pydantic `BaseModel` subclasses.

```python
# app/contracts/values.py
from __future__ import annotations
from typing import Any, Literal, NewType
from pydantic import BaseModel, ConfigDict
from .ids import UserId, CacheKey, SessionId, StepId, DatasourceId

# --- Time Scoping ---

class TimeScopeRange(BaseModel):
    """Half-open [start, end) time range."""
    kind: Literal["range"] = "range"
    start: str  # ISO-8601 date or datetime
    end: str    # ISO-8601, exclusive

class TimeScopeSnapshotNow(BaseModel):
    kind: Literal["snapshot_now"] = "snapshot_now"

class TimeScopeLatestAvailable(BaseModel):
    kind: Literal["latest_available"] = "latest_available"

class TimeScopeAsOf(BaseModel):
    kind: Literal["as_of"] = "as_of"
    at: str  # ISO-8601

TimeScope = TimeScopeRange | TimeScopeSnapshotNow | TimeScopeLatestAvailable | TimeScopeAsOf

# --- Granularity ---
Granularity = Literal["hour", "day", "week", "month"]

# --- Observation Scoping ---

class ScopeConstraints(BaseModel):
    """Scalar equality constraints on semantic dimensions."""
    model_config = ConfigDict(extra="allow")

class ObserveScope(BaseModel):
    constraints: ScopeConstraints | None = None
    predicate_ref: str | None = None

# --- Observation References ---

class ObservationRef(BaseModel):
    session_id: SessionId | None = None
    step_id: StepId
    step_type: Literal["observe"] = "observe"

class CompareRef(BaseModel):
    session_id: SessionId | None = None
    step_id: StepId
    step_type: Literal["compare"] = "compare"

# --- AuthZ ---

class AuthZDecision(BaseModel):
    allowed: bool
    code: str | None = None
    message: str | None = None
    detail: dict[str, Any] = {}

# --- Audit ---

class AuditEntry(BaseModel):
    actor: UserId
    action: str
    resource_type: str
    resource_id: str
    detail: dict[str, Any] = {}

# --- Telemetry ---

class TelemetryEvent(BaseModel):
    name: str
    properties: dict[str, Any] = {}

# --- Query ---

class LogicalQuery(BaseModel):
    """Logical query produced by core/planner, consumed by DataSource port."""
    sql: str
    params: dict[str, Any] = {}

class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    query_sql: str | None = None

# --- Source Schema ---

class SourceRef(BaseModel):
    datasource_id: DatasourceId
    schema_name: str
    table_name: str

class ColumnInfo(BaseModel):
    name: str
    dtype: str
    nullable: bool = True

class SourceSchema(BaseModel):
    columns: list[ColumnInfo]
    row_count: int | None = None

# --- Cache ---

CacheValue = NewType("CacheValue", bytes)
```

---

## 6. Contracts: Domain Aggregates

### 6.1 Session Types

```python
# app/contracts/session.py
from typing import Any
from pydantic import BaseModel
from .ids import SessionId, UserId

class SessionEvent(BaseModel):
    """Append-only event in the session event log."""
    session_id: SessionId
    event_type: str
    timestamp: str  # ISO-8601
    payload: dict[str, Any] = {}
    actor: UserId | None = None

class SessionState(BaseModel):
    """Derived view of session state, rebuilt from events."""
    session_id: SessionId
    status: str
    goal: str | None = None
    created_at: str
    updated_at: str
```

### 6.2 Evidence Types

```python
# app/contracts/evidence.py
from typing import Any
from pydantic import BaseModel
from .ids import (
    SessionId, ArtifactId, FindingId, PropositionId,
    AssessmentId, EvidenceRef,
)

class Finding(BaseModel):
    finding_id: FindingId
    session_id: SessionId
    artifact_id: ArtifactId
    proposition_id: PropositionId | None = None
    finding_type: str
    content: dict[str, Any]
    invalidated: bool = False

class Proposition(BaseModel):
    proposition_id: PropositionId
    session_id: SessionId
    identity_key: str
    description: str | None = None
    externally_visible_assessment: str | None = None
    invalidated: bool = False

class Assessment(BaseModel):
    assessment_id: AssessmentId
    proposition_id: PropositionId
    status: str
    rationale: str | None = None
    snapshot_seq: int = 0

class Evidence(BaseModel):
    """Container for a coherent evidence unit."""
    ref: EvidenceRef
    findings: list[Finding]
    proposition: Proposition | None = None
    assessment: Assessment | None = None
```

### 6.3 Semantic Types

```python
# app/contracts/semantic.py
from pydantic import BaseModel
from .ids import ModelId, RevisionId, UserId

class SemanticModel(BaseModel):
    """Domain-level semantic model, aligned with OSI but not coupled to HTTP shapes."""
    model_id: ModelId | None = None
    name: str
    revision: RevisionId | None = None
    description: str | None = None
    osi_document: dict[str, Any] = {}
    visibility: str = "private"
    owner: UserId | None = None

class ModelSummary(BaseModel):
    model_id: ModelId
    name: str
    revision: RevisionId | None = None
    description: str | None = None
    visibility: str = "private"
    owner: UserId | None = None
    updated_at: str | None = None
```

**Design note:** `SemanticModel` embeds `osi_document: dict[str, Any]` rather than re-defining the full OSI schema. The OSI schema is already stable in `api/models/osi.py` and is the HTTP API contract. Unifying the full OSI schema into `contracts/` is Phase 7 work.

---

## 7. Contracts: Errors

```python
# app/contracts/errors.py
from typing import Any
from enum import Enum

class ErrorCode(str, Enum):
    # General
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    VALIDATION = "validation"
    # Session
    SESSION_CLOSED = "session_closed"
    SESSION_NOT_FOUND = "session_not_found"
    # Semantic model
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_REVISION_CONFLICT = "model_revision_conflict"
    # Evidence
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    EVIDENCE_HASH_MISMATCH = "evidence_hash_mismatch"
    # DataSource
    QUERY_EXECUTION_FAILED = "query_execution_failed"
    DATASOURCE_UNAVAILABLE = "datasource_unavailable"

class DomainError(Exception):
    """Base domain error raised by Runtime/Core."""
    code: ErrorCode
    message: str
    detail: dict[str, Any]

    def __init__(self, code: ErrorCode, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

class NotFoundError(DomainError): ...
class ConflictError(DomainError): ...
class ForbiddenError(DomainError): ...
class ValidationError(DomainError): ...
```

---

## 8. Ports: Protocol Definitions

### 8.1 Full Ports (with real adapters)

```python
# app/ports/model_store.py
from typing import Protocol
from app.contracts.ids import ModelId, RevisionId, UserId
from app.contracts.semantic import SemanticModel, ModelSummary

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
```

```python
# app/ports/session_store.py
from typing import Protocol
from app.contracts.ids import SessionId
from app.contracts.session import SessionEvent

class SessionStore(Protocol):
    def append_event(self, session_id: SessionId, event: SessionEvent) -> None: ...
    def load_events(self, session_id: SessionId) -> list[SessionEvent]: ...
```

```python
# app/ports/data_source.py
from typing import Protocol
from app.contracts.values import LogicalQuery, QueryResult, SourceRef, SourceSchema

class DataSource(Protocol):
    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...
```

### 8.2 Skeleton Ports (Protocol defined, methods may evolve)

```python
# app/ports/evidence_store.py
from typing import Protocol
from app.contracts.ids import EvidenceRef
from app.contracts.evidence import Evidence

class EvidenceStore(Protocol):
    def write(self, evidence: Evidence) -> EvidenceRef: ...
    def read(self, ref: EvidenceRef) -> Evidence: ...
```

```python
# app/ports/cache_store.py
from typing import Protocol
from app.contracts.ids import CacheKey
from app.contracts.values import CacheValue

class CacheStore(Protocol):
    def get(self, key: CacheKey) -> CacheValue | None: ...
    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None: ...
```

```python
# app/ports/authz.py
from typing import Protocol
from app.contracts.ids import UserId, Action, ResourceId
from app.contracts.values import AuthZDecision

class AuthZ(Protocol):
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision: ...
```

```python
# app/ports/audit_log.py
from typing import Protocol
from app.contracts.values import AuditEntry

class AuditLog(Protocol):
    def record(self, entry: AuditEntry) -> None: ...
```

```python
# app/ports/telemetry.py
from typing import Protocol
from app.contracts.values import TelemetryEvent

class Telemetry(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...
```

```python
# app/ports/runtime_config.py
from typing import Protocol

class RuntimeConfig(Protocol):
    def get(self, key: str) -> str | None: ...
```

---

## 9. Import-Linter Configuration

File: `.importlinter` at repository root.

```ini
[importlinter]
root_package = app

[importlinter:contract:contracts-isolation]
name = contracts/ must not import app internals
type = forbidden
source_modules =
    app.contracts
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

[importlinter:contract:ports-isolation]
name = ports/ must not import app internals
type = forbidden
source_modules =
    app.ports
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
```

Integration: `make lint` (or CI equivalent) must run `lint-imports` and enforce these contracts. Violations block merge.

---

## 10. Dependency Addition

Phase 2 adds one new dev dependency:

- `import-linter` — enforces import boundary contracts at CI time

No runtime dependencies are added. All `contracts/` and `ports/` types use only `pydantic` (already a dependency) and the Python standard library.

---

## 11. What Phase 2 Does NOT Do

- Does not implement any adapter against the Port Protocols.
- Does not migrate existing `api/models/` types into `contracts/`.
- Does not change any existing service, repository, or handler code.
- Does not create `app/core/`, `app/runtime/`, `app/adapters/`, or `app/profiles/`.
- Does not start the `app/` → `marivo/` namespace migration.
- Does not enforce that existing code must reference `contracts/` types.

The definitions in `contracts/` and `ports/` coexist with the existing type system. Phase 3 (Runtime Decoupling) will progressively switch callers and implement adapters against these Ports.

**Drift mitigation:** During the Phase 2→3 transition, `contracts/` types and `api/models/` types will coexist. To prevent silent divergence:
1. Phase 3 adapter implementations are the reconciliation point — each adapter's `to_domain()` / `from_domain()` methods define the canonical mapping between HTTP models and domain types.
2. The adapter contract test suite (parent spec §11) validates that round-tripping through the Port produces equivalent results.
3. No separate drift-detection tooling is needed; the contract tests serve this purpose.

---

## 12. Verification Plan

| Check | How | Gate |
|-------|-----|------|
| Import-linter passes | `lint-imports` in CI | Must pass |
| All existing tests green | `make test` | Must pass |
| contracts/ compiles | `make typecheck` includes `app/contracts/` | Must pass |
| ports/ compiles | `make typecheck` includes `app/ports/` | Must pass |
| No circular imports | `lint-imports` + manual review | Must pass |
| contracts/ has zero I/O imports | `lint-imports` forbidden rule | Must pass |
| ports/ has zero I/O imports | `lint-imports` forbidden rule | Must pass |
