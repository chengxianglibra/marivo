from __future__ import annotations

from typing import Any, Literal, NewType

from pydantic import BaseModel, ConfigDict

from .ids import DatasourceId, SessionId, StepId, UserId

LAYOUT_VERSION: int = 1
"""Current .marivo/ layout schema version. Increment on breaking layout changes."""

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
