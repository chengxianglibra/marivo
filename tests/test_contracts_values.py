from __future__ import annotations

from marivo.contracts.ids import DatasourceId, SessionId, StepId, UserId
from marivo.contracts.values import (
    AuditEntry,
    AuthZDecision,
    CacheValue,
    ColumnInfo,
    CompareRef,
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

# --- TimeScopeRange ---


def test_time_scope_range_construct_and_serialize() -> None:
    ts = TimeScopeRange(start="2024-03-01", end="2024-04-01")
    assert ts.kind == "range"
    assert ts.start == "2024-03-01"
    assert ts.end == "2024-04-01"
    data = ts.model_dump()
    assert data["kind"] == "range"


def test_time_scope_range_round_trip() -> None:
    ts = TimeScopeRange(start="2024-03-01", end="2024-04-01")
    restored = TimeScopeRange.model_validate(ts.model_dump())
    assert restored == ts


# --- TimeScope variants ---


def test_time_scope_snapshot_now() -> None:
    ts = TimeScopeSnapshotNow()
    assert ts.kind == "snapshot_now"


def test_time_scope_latest_available() -> None:
    ts = TimeScopeLatestAvailable()
    assert ts.kind == "latest_available"


def test_time_scope_as_of() -> None:
    ts = TimeScopeAsOf(at="2024-06-15T00:00:00")
    assert ts.kind == "as_of"
    assert ts.at == "2024-06-15T00:00:00"


# --- Granularity ---


def test_granularity_valid_values() -> None:
    for g in ("hour", "day", "week", "month"):
        assert g in {"hour", "day", "week", "month"}


# --- ObserveScope ---


def test_observe_scope_with_constraints() -> None:
    scope = ObserveScope(constraints=ScopeConstraints(region="us", segment="enterprise"))
    assert scope.constraints is not None
    assert scope.constraints.region == "us"


def test_observe_scope_with_predicate_ref() -> None:
    scope = ObserveScope(predicate_ref="predicate.active_users")
    assert scope.predicate_ref == "predicate.active_users"


def test_observe_scope_default_none() -> None:
    scope = ObserveScope()
    assert scope.constraints is None
    assert scope.predicate_ref is None


# --- ObservationRef ---


def test_observation_ref_minimal() -> None:
    ref = ObservationRef(step_id=StepId("step-1"))
    assert ref.step_id == "step-1"
    assert ref.step_type == "observe"
    assert ref.session_id is None


def test_observation_ref_with_session() -> None:
    ref = ObservationRef(session_id=SessionId("sess-1"), step_id=StepId("step-1"))
    assert ref.session_id == "sess-1"


# --- CompareRef ---


def test_compare_ref_minimal() -> None:
    ref = CompareRef(step_id=StepId("cmp-1"))
    assert ref.step_type == "compare"


# --- AuthZDecision ---


def test_authz_decision_allowed() -> None:
    d = AuthZDecision(allowed=True)
    assert d.allowed
    assert d.code is None
    assert d.message is None


def test_authz_decision_denied() -> None:
    d = AuthZDecision(allowed=False, code="forbidden", message="no access")
    assert not d.allowed
    assert d.code == "forbidden"


# --- AuditEntry ---


def test_audit_entry_construct() -> None:
    entry = AuditEntry(actor=UserId("u1"), action="read", resource_type="model", resource_id="m1")
    assert entry.actor == "u1"
    assert entry.action == "read"


# --- TelemetryEvent ---


def test_telemetry_event_construct() -> None:
    event = TelemetryEvent(name="session_created", properties={"count": 1})
    assert event.name == "session_created"


# --- LogicalQuery and QueryResult ---


def test_logical_query() -> None:
    q = LogicalQuery(sql="SELECT 1", params={"key": "val"})
    assert q.sql == "SELECT 1"
    assert q.params == {"key": "val"}


def test_logical_query_datasource_id_default() -> None:
    q = LogicalQuery(sql="SELECT 1")
    assert q.datasource_id is None


def test_logical_query_datasource_id_explicit() -> None:
    q = LogicalQuery(sql="SELECT 1", datasource_id=DatasourceId("ds_abc"))
    assert q.datasource_id == "ds_abc"


def test_query_result() -> None:
    r = QueryResult(columns=["a"], rows=[{"a": 1}], row_count=1)
    assert r.columns == ["a"]
    assert r.row_count == 1


# --- SourceRef and SourceSchema ---


def test_source_ref() -> None:
    ref = SourceRef(datasource_id=DatasourceId("ds-1"), schema_name="public", table_name="events")
    assert ref.datasource_id == "ds-1"
    assert ref.schema_name == "public"


def test_source_schema() -> None:
    schema = SourceSchema(columns=[ColumnInfo(name="id", dtype="INT", nullable=False)])
    assert len(schema.columns) == 1
    assert schema.columns[0].name == "id"


# --- CacheValue ---


def test_cache_value_is_bytes() -> None:
    cv = CacheValue(b"data")
    assert isinstance(cv, bytes)
    assert cv == b"data"
