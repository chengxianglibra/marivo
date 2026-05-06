# Phase 2: Contracts & Ports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the `contracts/` and `ports/` packages inside `app/` as a pure definition layer — typed IDs, value objects, domain aggregates, error hierarchy, and Port Protocol interfaces.

**Architecture:** New `app/contracts/` and `app/ports/` packages define the shared type seams and Port Protocol interfaces for the five-layer architecture. No existing code is modified or migrated. Import-linter enforces boundary isolation.

**Tech Stack:** Python 3.12+, Pydantic v2, typing.Protocol, typing.NewType, import-linter

**Spec:** `docs/superpowers/specs/2026-05-06-phase2-contracts-ports-design.md`

---

## File Structure

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

tests/
  test_contracts_ids.py
  test_contracts_values.py
  test_contracts_errors.py
  test_contracts_domain.py
  test_contracts_init.py
  test_ports_protocols.py
  test_ports_init.py

.importlinter          # Import boundary configuration
```

---

### Task 1: Create contracts/ids.py — Typed ID Definitions

**Files:**
- Create: `app/contracts/ids.py`
- Create: `app/contracts/__init__.py` (placeholder, re-exports added in Task 4)
- Test: `tests/test_contracts_ids.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_ids.py
from __future__ import annotations

from app.contracts.ids import (
    SessionId,
    StepId,
    ArtifactId,
    AttemptId,
    FindingId,
    PropositionId,
    AssessmentId,
    ActionProposalId,
    GapId,
    InferenceRecordId,
    ModelId,
    RevisionId,
    DatasetName,
    MetricName,
    RelationshipName,
    DatasourceId,
    EngineId,
    RouteId,
    UserId,
    Action,
    ResourceId,
    EvidenceRef,
    CacheKey,
)


def test_session_ids_are_str_at_runtime() -> None:
    sid = SessionId("sess-001")
    assert isinstance(sid, str)
    assert sid == "sess-001"


def test_model_id_is_int_at_runtime() -> None:
    mid = ModelId(42)
    assert isinstance(mid, int)
    assert mid == 42


def test_revision_id_is_str_at_runtime() -> None:
    rid = RevisionId("abc123")
    assert isinstance(rid, str)
    assert rid == "abc123"


def test_all_str_ids_construct_from_str() -> None:
    str_ids = [
        SessionId("s"),
        StepId("step"),
        ArtifactId("art"),
        AttemptId("att"),
        FindingId("f"),
        PropositionId("p"),
        AssessmentId("a"),
        ActionProposalId("ap"),
        GapId("g"),
        InferenceRecordId("ir"),
        RevisionId("r"),
        DatasetName("ds"),
        MetricName("m"),
        RelationshipName("rel"),
        DatasourceId("d"),
        EngineId("e"),
        RouteId("rt"),
        UserId("u"),
        Action("act"),
        ResourceId("res"),
        EvidenceRef("eref"),
        CacheKey("ck"),
    ]
    for typed_id in str_ids:
        assert isinstance(typed_id, str)


def test_int_ids_construct_from_int() -> None:
    int_ids = [ModelId(1)]
    for typed_id in int_ids:
        assert isinstance(typed_id, int)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_contracts_ids.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.contracts'`

- [ ] **Step 3: Create app/contracts/ directory and write ids.py**

Create the directory:
```bash
mkdir -p app/contracts
```

Write `app/contracts/__init__.py` (placeholder, re-exports added in Task 4):
```python
# app/contracts/__init__.py
```

Write `app/contracts/ids.py`:
```python
from __future__ import annotations

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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_contracts_ids.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/contracts/__init__.py app/contracts/ids.py tests/test_contracts_ids.py
git commit -m "$(cat <<'EOF'
feat(contracts): add typed ID definitions

NewType wrappers for all domain IDs: Session, Evidence, Semantic,
Infrastructure, and Auth domains. Runtime-transparent but type-distinct.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 2: Create contracts/values.py — Value Objects

**Files:**
- Create: `app/contracts/values.py`
- Test: `tests/test_contracts_values.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_values.py
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.contracts.ids import CacheKey, DatasourceId, SessionId, StepId, UserId
from app.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    ColumnInfo,
    CompareRef,
    Granularity,
    LogicalQuery,
    ObservationRef,
    ObserveScope,
    QueryResult,
    ScopeConstraints,
    SourceRef,
    SourceSchema,
    TelemetryEvent,
    TimeScopeAsOf,
    TimeScopeLatestAvailable,
    TimeScopeRange,
    TimeScopeSnapshotNow,
)


class TestTimeScopeRange:
    def test_construct_and_serialize(self) -> None:
        ts = TimeScopeRange(start="2024-03-01", end="2024-04-01")
        assert ts.kind == "range"
        assert ts.start == "2024-03-01"
        assert ts.end == "2024-04-01"
        data = ts.model_dump()
        assert data["kind"] == "range"

    def test_round_trip(self) -> None:
        ts = TimeScopeRange(start="2024-03-01", end="2024-04-01")
        restored = TimeScopeRange.model_validate(ts.model_dump())
        assert restored == ts


class TestTimeScopeVariants:
    def test_snapshot_now(self) -> None:
        ts = TimeScopeSnapshotNow()
        assert ts.kind == "snapshot_now"

    def test_latest_available(self) -> None:
        ts = TimeScopeLatestAvailable()
        assert ts.kind == "latest_available"

    def test_as_of(self) -> None:
        ts = TimeScopeAsOf(at="2024-06-15T00:00:00")
        assert ts.kind == "as_of"
        assert ts.at == "2024-06-15T00:00:00"


class TestGranularity:
    def test_valid_values(self) -> None:
        for g in ("hour", "day", "week", "month"):
            assert g in {"hour", "day", "week", "month"}


class TestObserveScope:
    def test_with_constraints(self) -> None:
        scope = ObserveScope(
            constraints=ScopeConstraints(region="us", segment="enterprise")
        )
        assert scope.constraints is not None
        assert scope.constraints.region == "us"

    def test_with_predicate_ref(self) -> None:
        scope = ObserveScope(predicate_ref="predicate.active_users")
        assert scope.predicate_ref == "predicate.active_users"

    def test_default_none(self) -> None:
        scope = ObserveScope()
        assert scope.constraints is None
        assert scope.predicate_ref is None


class TestObservationRef:
    def test_minimal(self) -> None:
        ref = ObservationRef(step_id=StepId("step-1"))
        assert ref.step_id == "step-1"
        assert ref.step_type == "observe"
        assert ref.session_id is None

    def test_with_session(self) -> None:
        ref = ObservationRef(session_id=SessionId("sess-1"), step_id=StepId("step-1"))
        assert ref.session_id == "sess-1"


class TestCompareRef:
    def test_minimal(self) -> None:
        ref = CompareRef(step_id=StepId("cmp-1"))
        assert ref.step_type == "compare"


class TestAuthZDecision:
    def test_allowed(self) -> None:
        d = AuthZDecision(allowed=True)
        assert d.allowed
        assert d.code is None
        assert d.message is None

    def test_denied(self) -> None:
        d = AuthZDecision(allowed=False, code="forbidden", message="no access")
        assert not d.allowed
        assert d.code == "forbidden"


class TestAuditEntry:
    def test_construct(self) -> None:
        entry = AuditEntry(actor=UserId("u1"), action="read", resource_type="model", resource_id="m1")
        assert entry.actor == "u1"
        assert entry.action == "read"


class TestTelemetryEvent:
    def test_construct(self) -> None:
        event = TelemetryEvent(name="session_created", properties={"count": 1})
        assert event.name == "session_created"


class TestLogicalQueryAndResult:
    def test_query(self) -> None:
        q = LogicalQuery(sql="SELECT 1", params={"key": "val"})
        assert q.sql == "SELECT 1"
        assert q.params == {"key": "val"}

    def test_result(self) -> None:
        r = QueryResult(columns=["a"], rows=[{"a": 1}], row_count=1)
        assert r.columns == ["a"]
        assert r.row_count == 1


class TestSourceRefAndSchema:
    def test_source_ref(self) -> None:
        ref = SourceRef(datasource_id=DatasourceId("ds-1"), schema_name="public", table_name="events")
        assert ref.datasource_id == "ds-1"
        assert ref.schema_name == "public"

    def test_source_schema(self) -> None:
        schema = SourceSchema(columns=[ColumnInfo(name="id", dtype="INT", nullable=False)])
        assert len(schema.columns) == 1
        assert schema.columns[0].name == "id"


class TestCacheValue:
    def test_is_bytes(self) -> None:
        cv = CacheValue(b"data")
        assert isinstance(cv, bytes)
        assert cv == b"data"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_contracts_values.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.contracts.values'`

- [ ] **Step 3: Write values.py**

Write `app/contracts/values.py`:
```python
from __future__ import annotations

from typing import Any, Literal, NewType

from pydantic import BaseModel, ConfigDict

from .ids import CacheKey, DatasourceId, SessionId, StepId, UserId

# --- Time Scoping ---


class TimeScopeRange(BaseModel):
    """Half-open [start, end) time range."""

    kind: Literal["range"] = "range"
    start: str  # ISO-8601 date or datetime
    end: str  # ISO-8601, exclusive


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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_contracts_values.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/contracts/values.py tests/test_contracts_values.py
git commit -m "$(cat <<'EOF'
feat(contracts): add value objects

TimeScope variants, Granularity, ObserveScope, ObservationRef,
CompareRef, AuthZDecision, AuditEntry, TelemetryEvent, LogicalQuery,
QueryResult, SourceRef, SourceSchema, CacheValue.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 3: Create contracts/errors.py — Domain Error Hierarchy

**Files:**
- Create: `app/contracts/errors.py`
- Test: `tests/test_contracts_errors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts_errors.py
from __future__ import annotations

import pytest

from app.contracts.errors import (
    ConflictError,
    DomainError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)


class TestErrorCode:
    def test_all_codes_defined(self) -> None:
        expected = {
            "not_found",
            "conflict",
            "forbidden",
            "validation",
            "session_closed",
            "session_not_found",
            "model_not_found",
            "model_revision_conflict",
            "evidence_not_found",
            "evidence_hash_mismatch",
            "query_execution_failed",
            "datasource_unavailable",
        }
        actual = {code.value for code in ErrorCode}
        assert actual == expected


class TestDomainError:
    def test_str_returns_message(self) -> None:
        err = DomainError(ErrorCode.NOT_FOUND, "thing not found")
        assert str(err) == "thing not found"

    def test_code_and_message(self) -> None:
        err = DomainError(ErrorCode.VALIDATION, "bad input", detail={"field": "x"})
        assert err.code == ErrorCode.VALIDATION
        assert err.message == "bad input"
        assert err.detail == {"field": "x"}

    def test_default_detail_is_empty(self) -> None:
        err = DomainError(ErrorCode.CONFLICT, "conflict")
        assert err.detail == {}

    def test_is_exception(self) -> None:
        with pytest.raises(DomainError, match="not found"):
            raise DomainError(ErrorCode.NOT_FOUND, "not found")


class TestErrorSubclasses:
    def test_not_found(self) -> None:
        err = NotFoundError(ErrorCode.NOT_FOUND, "session missing")
        assert isinstance(err, DomainError)
        assert isinstance(err, Exception)
        assert str(err) == "session missing"

    def test_conflict(self) -> None:
        err = ConflictError(ErrorCode.CONFLICT, "revision conflict")
        assert isinstance(err, DomainError)

    def test_forbidden(self) -> None:
        err = ForbiddenError(ErrorCode.FORBIDDEN, "no access")
        assert isinstance(err, DomainError)

    def test_validation(self) -> None:
        err = ValidationError(ErrorCode.VALIDATION, "bad input")
        assert isinstance(err, DomainError)

    def test_subclass_raises(self) -> None:
        with pytest.raises(NotFoundError):
            raise NotFoundError(ErrorCode.NOT_FOUND, "gone")

        with pytest.raises(DomainError):
            raise ConflictError(ErrorCode.CONFLICT, "clash")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_contracts_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.contracts.errors'`

- [ ] **Step 3: Write errors.py**

Write `app/contracts/errors.py`:
```python
from __future__ import annotations

from enum import Enum
from typing import Any


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

    def __init__(
        self, code: ErrorCode, message: str, detail: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


class NotFoundError(DomainError): ...


class ConflictError(DomainError): ...


class ForbiddenError(DomainError): ...


class ValidationError(DomainError): ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_contracts_errors.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/contracts/errors.py tests/test_contracts_errors.py
git commit -m "$(cat <<'EOF'
feat(contracts): add domain error hierarchy

ErrorCode enum, DomainError base with code/message/detail,
and NotFoundError, ConflictError, ForbiddenError, ValidationError
subclasses.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 4: Create Domain Aggregates + contracts/__init__.py Re-exports

**Files:**
- Create: `app/contracts/session.py`
- Create: `app/contracts/evidence.py`
- Create: `app/contracts/semantic.py`
- Modify: `app/contracts/__init__.py` (add re-exports)
- Test: `tests/test_contracts_domain.py`
- Test: `tests/test_contracts_init.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_contracts_domain.py
from __future__ import annotations

from app.contracts.evidence import Assessment, Evidence, Finding, Proposition
from app.contracts.ids import ArtifactId, EvidenceRef, FindingId, PropositionId, SessionId, AssessmentId
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionEvent, SessionState
from app.contracts.ids import ModelId, RevisionId, UserId


class TestSessionTypes:
    def test_session_event(self) -> None:
        event = SessionEvent(
            session_id=SessionId("s1"),
            event_type="created",
            timestamp="2024-01-01T00:00:00Z",
        )
        assert event.session_id == "s1"
        assert event.event_type == "created"
        assert event.actor is None

    def test_session_event_with_actor(self) -> None:
        event = SessionEvent(
            session_id=SessionId("s1"),
            event_type="closed",
            timestamp="2024-01-01T00:00:00Z",
            actor=UserId("user-1"),
        )
        assert event.actor == "user-1"

    def test_session_state(self) -> None:
        state = SessionState(
            session_id=SessionId("s1"),
            status="active",
            goal="investigate revenue drop",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-02T00:00:00Z",
        )
        assert state.status == "active"
        assert state.goal == "investigate revenue drop"

    def test_session_state_optional_goal(self) -> None:
        state = SessionState(
            session_id=SessionId("s1"),
            status="closed",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-02T00:00:00Z",
        )
        assert state.goal is None


class TestEvidenceTypes:
    def test_finding(self) -> None:
        f = Finding(
            finding_id=FindingId("f1"),
            session_id=SessionId("s1"),
            artifact_id=ArtifactId("a1"),
            finding_type="anomaly",
            content={"metric": "revenue", "delta": -0.15},
        )
        assert f.finding_type == "anomaly"
        assert f.invalidated is False
        assert f.proposition_id is None

    def test_proposition(self) -> None:
        p = Proposition(
            proposition_id=PropositionId("p1"),
            session_id=SessionId("s1"),
            identity_key="rev_drop_us_region",
        )
        assert p.identity_key == "rev_drop_us_region"
        assert p.invalidated is False

    def test_assessment(self) -> None:
        a = Assessment(
            assessment_id=AssessmentId("a1"),
            proposition_id=PropositionId("p1"),
            status="confirmed",
        )
        assert a.status == "confirmed"
        assert a.snapshot_seq == 0

    def test_evidence_container(self) -> None:
        e = Evidence(
            ref=EvidenceRef("sha256:abc"),
            findings=[
                Finding(
                    finding_id=FindingId("f1"),
                    session_id=SessionId("s1"),
                    artifact_id=ArtifactId("a1"),
                    finding_type="anomaly",
                    content={"delta": -0.1},
                )
            ],
        )
        assert e.ref == "sha256:abc"
        assert len(e.findings) == 1
        assert e.proposition is None
        assert e.assessment is None


class TestSemanticTypes:
    def test_semantic_model(self) -> None:
        m = SemanticModel(name="my_model")
        assert m.name == "my_model"
        assert m.model_id is None
        assert m.visibility == "private"

    def test_semantic_model_full(self) -> None:
        m = SemanticModel(
            model_id=ModelId(1),
            name="my_model",
            revision=RevisionId("v1"),
            description="test model",
            visibility="public",
            owner=UserId("user-1"),
        )
        assert m.model_id == 1
        assert m.revision == "v1"

    def test_model_summary(self) -> None:
        s = ModelSummary(
            model_id=ModelId(1),
            name="my_model",
            updated_at="2024-01-01",
        )
        assert s.model_id == 1
        assert s.updated_at == "2024-01-01"
```

```python
# tests/test_contracts_init.py
from __future__ import annotations

from app.contracts import (
    Assessment,
    AssessmentId,
    ArtifactId,
    CacheKey,
    CacheValue,
    DatasourceId,
    DomainError,
    Evidence,
    EvidenceRef,
    Finding,
    FindingId,
    Granularity,
    LogicalQuery,
    ModelId,
    ModelSummary,
    Proposition,
    PropositionId,
    QueryResult,
    RevisionId,
    SemanticModel,
    SessionEvent,
    SessionId,
    SessionState,
    StepId,
    TimeScope,
    TimeScopeAsOf,
    TimeScopeLatestAvailable,
    TimeScopeRange,
    TimeScopeSnapshotNow,
    UserId,
)


def test_key_ids_importable() -> None:
    assert SessionId("s") == "s"
    assert ModelId(1) == 1
    assert StepId("step") == "step"


def test_key_value_objects_importable() -> None:
    ts = TimeScopeRange(start="2024-01-01", end="2024-02-01")
    assert ts.kind == "range"


def test_domain_types_importable() -> None:
    state = SessionState(
        session_id=SessionId("s1"),
        status="active",
        created_at="2024-01-01",
        updated_at="2024-01-01",
    )
    assert state.status == "active"


def test_errors_importable() -> None:
    err = DomainError(code="not_found", message="missing")
    assert str(err) == "missing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_contracts_domain.py tests/test_contracts_init.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.contracts.session'`

- [ ] **Step 3: Write session.py**

Write `app/contracts/session.py`:
```python
from __future__ import annotations

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

- [ ] **Step 4: Write evidence.py**

Write `app/contracts/evidence.py`:
```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .ids import (
    ArtifactId,
    AssessmentId,
    EvidenceRef,
    FindingId,
    PropositionId,
    SessionId,
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

- [ ] **Step 5: Write semantic.py**

Write `app/contracts/semantic.py`:
```python
from __future__ import annotations

from typing import Any

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

- [ ] **Step 6: Write contracts/__init__.py with re-exports**

Replace the placeholder `app/contracts/__init__.py`:
```python
from __future__ import annotations

from .evidence import Assessment, Evidence, Finding, Proposition
from .errors import ConflictError, DomainError, ForbiddenError, NotFoundError, ValidationError
from .ids import (
    Action,
    ActionProposalId,
    ArtifactId,
    AssessmentId,
    AttemptId,
    CacheKey,
    DatasourceId,
    EngineId,
    EvidenceRef,
    FindingId,
    GapId,
    InferenceRecordId,
    MetricName,
    ModelId,
    PropositionId,
    ResourceId,
    RevisionId,
    RouteId,
    SessionId,
    StepId,
    UserId,
)
from .semantic import ModelSummary, SemanticModel
from .session import SessionEvent, SessionState
from .values import (
    Granularity,
    LogicalQuery,
    ObserveScope,
    QueryResult,
    TimeScope,
    TimeScopeAsOf,
    TimeScopeLatestAvailable,
    TimeScopeRange,
    TimeScopeSnapshotNow,
)

__all__ = [
    # IDs
    "Action",
    "ActionProposalId",
    "ArtifactId",
    "AssessmentId",
    "AttemptId",
    "CacheKey",
    "DatasourceId",
    "EngineId",
    "EvidenceRef",
    "FindingId",
    "GapId",
    "InferenceRecordId",
    "MetricName",
    "ModelId",
    "PropositionId",
    "ResourceId",
    "RevisionId",
    "RouteId",
    "SessionId",
    "StepId",
    "UserId",
    # Value objects
    "Granularity",
    "LogicalQuery",
    "ObserveScope",
    "QueryResult",
    "TimeScope",
    "TimeScopeAsOf",
    "TimeScopeLatestAvailable",
    "TimeScopeRange",
    "TimeScopeSnapshotNow",
    # Domain aggregates
    "Assessment",
    "Evidence",
    "Finding",
    "ModelSummary",
    "Proposition",
    "SemanticModel",
    "SessionEvent",
    "SessionState",
    # Errors
    "ConflictError",
    "DomainError",
    "ForbiddenError",
    "NotFoundError",
    "ValidationError",
]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_contracts_domain.py tests/test_contracts_init.py -v`
Expected: All tests PASS

- [ ] **Step 8: Run all contracts tests together**

Run: `.venv/bin/pytest tests/test_contracts_ids.py tests/test_contracts_values.py tests/test_contracts_errors.py tests/test_contracts_domain.py tests/test_contracts_init.py -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add app/contracts/session.py app/contracts/evidence.py app/contracts/semantic.py app/contracts/__init__.py tests/test_contracts_domain.py tests/test_contracts_init.py
git commit -m "$(cat <<'EOF'
feat(contracts): add domain aggregates and __init__ re-exports

SessionEvent/SessionState, Finding/Proposition/Assessment/Evidence,
SemanticModel/ModelSummary, plus __init__.py re-exporting all key
symbols from the contracts package.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 5: Create Full Ports — ModelStore, SessionStore, DataSource

**Files:**
- Create: `app/ports/model_store.py`
- Create: `app/ports/session_store.py`
- Create: `app/ports/data_source.py`
- Create: `app/ports/__init__.py` (placeholder, re-exports added in Task 6)
- Test: `tests/test_ports_protocols.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ports_protocols.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.contracts.evidence import Evidence
from app.contracts.ids import (
    Action,
    CacheKey,
    DatasourceId,
    EvidenceRef,
    ModelId,
    ResourceId,
    RevisionId,
    SessionId,
    UserId,
)
from app.contracts.semantic import ModelSummary, SemanticModel
from app.contracts.session import SessionEvent
from app.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    LogicalQuery,
    QueryResult,
    SourceRef,
    SourceSchema,
    TelemetryEvent,
)

# --- Concrete implementations for Protocol satisfaction testing ---


class InMemoryModelSelector(BaseModel):
    model_id: ModelId | None = None
    name: str | None = None
    revision: RevisionId | None = None


class InMemoryModelListQuery(BaseModel):
    owner: UserId | None = None
    visibility: str | None = None
    include_public: bool = True
    include_private: bool = False


class InMemoryModelStore:
    def __init__(self) -> None:
        self._models: dict[ModelId, SemanticModel] = {}

    def get(self, selector: InMemoryModelSelector) -> SemanticModel | None:
        if selector.model_id is not None:
            return self._models.get(selector.model_id)
        return None

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None,
    ) -> ModelId:
        mid = ModelId(len(self._models) + 1)
        self._models[mid] = model
        return mid

    def list(self, query: InMemoryModelListQuery) -> list[ModelSummary]:
        return []


class InMemorySessionStore:
    def __init__(self) -> None:
        self._events: dict[SessionId, list[SessionEvent]] = {}

    def append_event(self, session_id: SessionId, event: SessionEvent) -> None:
        self._events.setdefault(session_id, []).append(event)

    def load_events(self, session_id: SessionId) -> list[SessionEvent]:
        return self._events.get(session_id, [])


class InMemoryDataSource:
    def execute(self, query: LogicalQuery) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def schema(self, source_ref: SourceRef) -> SourceSchema:
        return SourceSchema(columns=[])


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._store: dict[EvidenceRef, Evidence] = {}

    def write(self, evidence: Evidence) -> EvidenceRef:
        self._store[evidence.ref] = evidence
        return evidence.ref

    def read(self, ref: EvidenceRef) -> Evidence:
        return self._store[ref]


class InMemoryCacheStore:
    def __init__(self) -> None:
        self._cache: dict[CacheKey, CacheValue] = {}

    def get(self, key: CacheKey) -> CacheValue | None:
        return self._cache.get(key)

    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None:
        self._cache[key] = value


class InMemoryAuthZ:
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision:
        return AuthZDecision(allowed=True)


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class InMemoryTelemetry:
    def __init__(self) -> None:
        self.events: list[TelemetryEvent] = []

    def emit(self, event: TelemetryEvent) -> None:
        self.events.append(event)


class InMemoryRuntimeConfig:
    def __init__(self) -> None:
        self._config: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._config.get(key)


# --- Tests ---


class TestModelStore:
    def test_import_protocol(self) -> None:
        from app.ports.model_store import ModelListQuery, ModelSelector, ModelStore

        assert ModelStore is not None
        assert ModelSelector is not None
        assert ModelListQuery is not None

    def test_concrete_get(self) -> None:
        store = InMemoryModelStore()
        result = store.get(InMemoryModelSelector())
        assert result is None

    def test_concrete_save(self) -> None:
        store = InMemoryModelStore()
        mid = store.save(
            SemanticModel(name="test"),
            actor=UserId("u1"),
            expected_revision=None,
        )
        assert isinstance(mid, int)

    def test_concrete_list(self) -> None:
        store = InMemoryModelStore()
        result = store.list(InMemoryModelListQuery())
        assert result == []


class TestSessionStore:
    def test_import_protocol(self) -> None:
        from app.ports.session_store import SessionStore

        assert SessionStore is not None

    def test_append_and_load(self) -> None:
        store = InMemorySessionStore()
        event = SessionEvent(
            session_id=SessionId("s1"),
            event_type="created",
            timestamp="2024-01-01T00:00:00Z",
        )
        store.append_event(SessionId("s1"), event)
        events = store.load_events(SessionId("s1"))
        assert len(events) == 1
        assert events[0].event_type == "created"


class TestDataSource:
    def test_import_protocol(self) -> None:
        from app.ports.data_source import DataSource

        assert DataSource is not None

    def test_execute(self) -> None:
        ds = InMemoryDataSource()
        result = ds.execute(LogicalQuery(sql="SELECT 1"))
        assert result.row_count == 0

    def test_schema(self) -> None:
        ds = InMemoryDataSource()
        result = ds.schema(SourceRef(
            datasource_id=DatasourceId("ds1"),
            schema_name="public",
            table_name="t",
        ))
        assert result.columns == []


class TestEvidenceStore:
    def test_import_protocol(self) -> None:
        from app.ports.evidence_store import EvidenceStore

        assert EvidenceStore is not None

    def test_write_and_read(self) -> None:
        store = InMemoryEvidenceStore()
        evidence = Evidence(ref=EvidenceRef("ref1"), findings=[])
        ref = store.write(evidence)
        assert ref == "ref1"
        result = store.read(EvidenceRef("ref1"))
        assert result.ref == "ref1"


class TestCacheStore:
    def test_import_protocol(self) -> None:
        from app.ports.cache_store import CacheStore

        assert CacheStore is not None

    def test_get_and_set(self) -> None:
        store = InMemoryCacheStore()
        key = CacheKey("k1")
        assert store.get(key) is None
        store.set(key, CacheValue(b"v1"))
        assert store.get(key) == b"v1"


class TestAuthZ:
    def test_import_protocol(self) -> None:
        from app.ports.authz import AuthZ

        assert AuthZ is not None

    def test_check(self) -> None:
        authz = InMemoryAuthZ()
        decision = authz.check(UserId("u1"), Action("read"), ResourceId("r1"))
        assert decision.allowed


class TestAuditLog:
    def test_import_protocol(self) -> None:
        from app.ports.audit_log import AuditLog

        assert AuditLog is not None

    def test_record(self) -> None:
        log = InMemoryAuditLog()
        log.record(AuditEntry(
            actor=UserId("u1"),
            action="read",
            resource_type="model",
            resource_id="m1",
        ))
        assert len(log.entries) == 1


class TestTelemetry:
    def test_import_protocol(self) -> None:
        from app.ports.telemetry import Telemetry

        assert Telemetry is not None

    def test_emit(self) -> None:
        tel = InMemoryTelemetry()
        tel.emit(TelemetryEvent(name="test"))
        assert len(tel.events) == 1


class TestRuntimeConfig:
    def test_import_protocol(self) -> None:
        from app.ports.runtime_config import RuntimeConfig

        assert RuntimeConfig is not None

    def test_get(self) -> None:
        cfg = InMemoryRuntimeConfig()
        assert cfg.get("missing") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ports_protocols.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ports'`

- [ ] **Step 3: Create app/ports/ directory and write all port files**

Create the directory:
```bash
mkdir -p app/ports
```

Write `app/ports/__init__.py` (placeholder, re-exports added in Task 6):
```python
# app/ports/__init__.py
```

Write `app/ports/model_store.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.ids import ModelId, RevisionId, UserId
from app.contracts.semantic import ModelSummary, SemanticModel


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

Write `app/ports/session_store.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.ids import SessionId
from app.contracts.session import SessionEvent


class SessionStore(Protocol):
    def append_event(self, session_id: SessionId, event: SessionEvent) -> None: ...
    def load_events(self, session_id: SessionId) -> list[SessionEvent]: ...
```

Write `app/ports/data_source.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.values import LogicalQuery, QueryResult, SourceRef, SourceSchema


class DataSource(Protocol):
    def execute(self, query: LogicalQuery) -> QueryResult: ...
    def schema(self, source_ref: SourceRef) -> SourceSchema: ...
```

Write `app/ports/evidence_store.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.ids import EvidenceRef
from app.contracts.evidence import Evidence


class EvidenceStore(Protocol):
    def write(self, evidence: Evidence) -> EvidenceRef: ...
    def read(self, ref: EvidenceRef) -> Evidence: ...
```

Write `app/ports/cache_store.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.ids import CacheKey
from app.contracts.values import CacheValue


class CacheStore(Protocol):
    def get(self, key: CacheKey) -> CacheValue | None: ...
    def set(self, key: CacheKey, value: CacheValue, ttl: int | None = None) -> None: ...
```

Write `app/ports/authz.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.ids import Action, ResourceId, UserId
from app.contracts.values import AuthZDecision


class AuthZ(Protocol):
    def check(self, actor: UserId, action: Action, resource: ResourceId) -> AuthZDecision: ...
```

Write `app/ports/audit_log.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.values import AuditEntry


class AuditLog(Protocol):
    def record(self, entry: AuditEntry) -> None: ...
```

Write `app/ports/telemetry.py`:
```python
from __future__ import annotations

from typing import Protocol

from app.contracts.values import TelemetryEvent


class Telemetry(Protocol):
    def emit(self, event: TelemetryEvent) -> None: ...
```

Write `app/ports/runtime_config.py`:
```python
from __future__ import annotations

from typing import Protocol


class RuntimeConfig(Protocol):
    def get(self, key: str) -> str | None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ports_protocols.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/ports/ tests/test_ports_protocols.py
git commit -m "$(cat <<'EOF'
feat(ports): add all Port Protocol definitions

3 full Ports (ModelStore, SessionStore, DataSource) and 6 skeleton
Ports (EvidenceStore, CacheStore, AuthZ, AuditLog, Telemetry,
RuntimeConfig). All use typing.Protocol for structural subtyping.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 6: Add ports/__init__.py Re-exports

**Files:**
- Modify: `app/ports/__init__.py`
- Test: `tests/test_ports_init.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ports_init.py
from __future__ import annotations

from app.ports import (
    AuditLog,
    AuthZ,
    CacheStore,
    DataSource,
    EvidenceStore,
    ModelStore,
    RuntimeConfig,
    SessionStore,
    Telemetry,
)


def test_all_protocols_importable() -> None:
    assert ModelStore is not None
    assert SessionStore is not None
    assert DataSource is not None
    assert EvidenceStore is not None
    assert CacheStore is not None
    assert AuthZ is not None
    assert AuditLog is not None
    assert Telemetry is not None
    assert RuntimeConfig is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ports_init.py -v`
Expected: FAIL — `ImportError: cannot import name 'ModelStore' from 'app.ports'`

- [ ] **Step 3: Write ports/__init__.py with re-exports**

Replace the placeholder `app/ports/__init__.py`:
```python
from __future__ import annotations

from .audit_log import AuditLog
from .authz import AuthZ
from .cache_store import CacheStore
from .data_source import DataSource
from .evidence_store import EvidenceStore
from .model_store import ModelListQuery, ModelSelector, ModelStore
from .runtime_config import RuntimeConfig
from .session_store import SessionStore
from .telemetry import Telemetry

__all__ = [
    "AuditLog",
    "AuthZ",
    "CacheStore",
    "DataSource",
    "EvidenceStore",
    "ModelListQuery",
    "ModelSelector",
    "ModelStore",
    "RuntimeConfig",
    "SessionStore",
    "Telemetry",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_ports_init.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ports/__init__.py tests/test_ports_init.py
git commit -m "$(cat <<'EOF'
feat(ports): add __init__ re-exports for all Port Protocols

Re-exports all Protocol classes plus ModelSelector and
ModelListQuery so that `from app.ports import ModelStore`
works.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 7: Add import-linter Dependency and Configuration

**Files:**
- Modify: `pyproject.toml` (add import-linter to dev deps)
- Create: `.importlinter`
- Modify: `Makefile` (add lint-imports to lint target)
- Test: manual verification via `lint-imports`

- [ ] **Step 1: Add import-linter to dev dependencies**

In `pyproject.toml`, add `import-linter` to the `dev` optional dependencies. Edit the `dev` list:

```toml
dev = [
    "pytest>=8",
    "pytest-xdist>=3",
    "pytest-cov>=5.0",
    "ruff>=0.3.0",
    "mypy>=1.9",
    "types-PyYAML>=6.0",
    "import-linter>=2.0",
]
```

- [ ] **Step 2: Install the new dependency**

```bash
.venv/bin/pip install "import-linter>=2.0"
```

- [ ] **Step 3: Create .importlinter configuration**

Write `.importlinter`:
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

- [ ] **Step 4: Wire lint-imports into Makefile**

Add `VENV_LINT_IMPORTS` variable and update the `lint` target. Edit `Makefile`:

```makefile
.PHONY: test typecheck lint format check

VENV_PYTHON := .venv/bin/python
VENV_PYTEST := .venv/bin/pytest
VENV_MYPY := .venv/bin/mypy
VENV_RUFF := .venv/bin/ruff
VENV_LINT_IMPORTS := .venv/bin/lint-imports

test:
	@./scripts/require-venv.sh pytest
	@$(VENV_PYTEST) $(TESTS)

typecheck:
	@./scripts/require-venv.sh mypy
	@$(VENV_MYPY) app

lint:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) check .
	@$(VENV_LINT_IMPORTS)

format:
	@./scripts/require-venv.sh ruff
	@$(VENV_RUFF) format .
	@$(VENV_RUFF) check --fix .

check: lint typecheck test
```

- [ ] **Step 5: Run lint-imports to verify it passes**

```bash
.venv/bin/lint-imports
```

Expected: Output shows both contracts pass:
```
----
contracts: app.contracts must not import app internals
KEPT
----
ports: app.ports must not import app internals
KEPT
----
```

- [ ] **Step 6: Run make lint to verify the full lint target works**

```bash
make lint
```

Expected: Both ruff and lint-imports pass with no errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .importlinter Makefile
git commit -m "$(cat <<'EOF'
feat: add import-linter for contracts/ and ports/ boundary enforcement

Adds import-linter>=2.0 as a dev dependency, configures .importlinter
with two forbidden-import contracts (contracts/ and ports/ must not
import app internals), and wires lint-imports into the make lint
target.

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

---

### Task 8: Run Full Verification Suite

This task verifies all acceptance criteria from the spec.

- [ ] **Step 1: Run make test**

```bash
make test
```

Expected: All existing tests plus new contracts/ports tests pass.

- [ ] **Step 2: Run make typecheck**

```bash
make typecheck
```

Expected: mypy passes for all of `app/`, including the new `app/contracts/` and `app/ports/` packages, with no errors.

- [ ] **Step 3: Run make lint**

```bash
make lint
```

Expected: ruff and lint-imports both pass.

- [ ] **Step 4: Run make check (all three)**

```bash
make check
```

Expected: lint + typecheck + test all pass.

- [ ] **Step 5: Verify no circular imports**

```bash
.venv/bin/python -c "import app.contracts; import app.ports; print('No circular imports')"
```

Expected: `No circular imports`

- [ ] **Step 6: Verify contracts/ has zero I/O imports**

```bash
.venv/bin/python -c "
import ast, sys
from pathlib import Path

forbidden = {'os', 'sys', 'io', 'pathlib', 'subprocess', 'socket', 'http', 'urllib', 'logging', 'sqlite3', 'threading', 'multiprocessing'}
for pyfile in Path('app/contracts').glob('*.py'):
    tree = ast.parse(pyfile.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in forbidden:
                    print(f'VIOLATION: {pyfile} imports {alias.name}')
                    sys.exit(1)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in forbidden:
                print(f'VIOLATION: {pyfile} imports from {node.module}')
                sys.exit(1)
print('contracts/ has zero I/O imports')
"
```

Expected: `contracts/ has zero I/O imports`

- [ ] **Step 7: Verify ports/ has zero I/O imports (same script, change path)**

```bash
.venv/bin/python -c "
import ast, sys
from pathlib import Path

forbidden = {'os', 'sys', 'io', 'pathlib', 'subprocess', 'socket', 'http', 'urllib', 'logging', 'sqlite3', 'threading', 'multiprocessing'}
for pyfile in Path('app/ports').glob('*.py'):
    tree = ast.parse(pyfile.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in forbidden:
                    print(f'VIOLATION: {pyfile} imports {alias.name}')
                    sys.exit(1)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in forbidden:
                print(f'VIOLATION: {pyfile} imports from {node.module}')
                sys.exit(1)
print('ports/ has zero I/O imports')
"
```

Expected: `ports/ has zero I/O imports`

- [ ] **Step 8: Final commit (only if any fixes were needed)**

If any issues were found and fixed in steps 1-7, commit the fixes:
```bash
git add -A
git commit -m "$(cat <<'EOF'
fix: address verification issues for contracts/ports Phase 2

Co-Authored-By: CLAUDE:claude-sonnet-4-6 [Write] [Bash]
EOF
)"
```

If no fixes needed, skip this step.
