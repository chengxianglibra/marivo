"""Pydantic request/response models for the Marivo HTTP API (infrastructure models).

Signal vs Decision design principle
-------------------------------------
Marivo returns **signals** (canonical evidence: findings, propositions,
assessments, action proposals) and enforces **decisions** (governance
constraints, budget limits). Agents retain full control over what to do
with signals; governance decisions are hard stops that Marivo enforces on
behalf of the operator.

Session constraints summary:
- constraints : Scalar key/value filters injected into step WHERE clauses.
                Signal-shaping input — narrows the analysis scope.
- budget      : Hard resource limits (scan bytes, latency).
                System-enforced decision — steps that exceed budget are blocked.
- policy      : Governance rules (aggregate_only, min_group_size).
                System-enforced decision — violations block step execution.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.time_contracts import normalize_hour_boundary

# =============================================================================
# Source models
# =============================================================================


class SourceAuthorityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_system: Literal["duckdb", "trino"]
    connection: dict[str, Any] = Field(default_factory=dict)
    synthetic_catalog: str | None = None

    @model_validator(mode="after")
    def normalize_duckdb_catalog(self) -> SourceAuthorityPayload:
        if self.catalog_system == "duckdb":
            if self.synthetic_catalog is None:
                self.synthetic_catalog = "main"
            elif self.synthetic_catalog != "main":
                raise ValueError("duckdb authority.synthetic_catalog must be 'main'")
        elif self.synthetic_catalog is not None:
            raise ValueError("synthetic_catalog is only supported for duckdb sources")
        return self


class SourceSyncPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["selected", "all", "none"] = "selected"


class SourcePolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_browse: bool = True
    allow_sync: bool = True


class SourceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["duckdb", "trino"]
    display_name: str
    authority: SourceAuthorityPayload
    sync: SourceSyncPayload = Field(default_factory=SourceSyncPayload)
    policy: SourcePolicyPayload = Field(default_factory=SourcePolicyPayload)

    @model_validator(mode="after")
    def validate_authority_catalog_system(self) -> SourceRegisterRequest:
        if self.authority.catalog_system != self.source_type:
            raise ValueError("authority.catalog_system must match source_type")
        return self


class SourceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    authority: SourceAuthorityPayload | None = None
    sync: SourceSyncPayload | None = None
    policy: SourcePolicyPayload | None = None


class SourceAuthorityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_system: Literal["duckdb", "trino"] = Field(
        description="Underlying metadata authority system type."
    )
    connection: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata authority connection parameters.",
    )
    synthetic_catalog: str | None = Field(
        default=None,
        description="Stable logical catalog name when the source lacks a native catalog layer.",
    )


class SourceSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["selected", "all", "none"] = Field(description="Metadata sync scope policy.")


class SourceIntrinsicCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supports_partitions: bool = Field(
        description="Whether the source adapter can enumerate partition metadata."
    )


class SourcePolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_live_browse: bool = Field(
        description="Whether operator policy allows live catalog browse."
    )
    allow_sync: bool = Field(description="Whether operator policy allows sync jobs.")


class SourceMappingSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: str = Field(description="Stable mapping identifier.")
    engine_id: str = Field(description="Execution engine targeted by this mapping.")
    status: Literal["active", "inactive", "deprecated"] = Field(
        description="Operator lifecycle status for the mapping."
    )
    readiness_status: Literal["not_ready", "ready"] = Field(
        description="Derived readiness status for the mapping."
    )
    failure_code: str | None = Field(
        default=None,
        description="Stable blocker code when the mapping is not ready.",
    )
    catalog_mappings: list[MappingCatalogEntryResponse] = Field(
        default_factory=list,
        description="Authority-to-execution catalog projection entries owned by the mapping.",
    )


class SourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Stable source identifier.")
    source_type: Literal["duckdb", "trino"] = Field(description="Metadata authority adapter type.")
    display_name: str = Field(description="Human-readable source name.")
    authority: SourceAuthorityResponse = Field(description="Metadata authority contract.")
    sync: SourceSyncResponse = Field(description="Metadata sync policy.")
    intrinsic_capabilities: SourceIntrinsicCapabilitiesResponse = Field(
        description="Read-only source implementation capabilities."
    )
    policy: SourcePolicyResponse = Field(description="Operator control-plane policy.")
    status: Literal["active", "inactive", "deprecated"] = Field(
        description="Operator lifecycle status for the source."
    )
    readiness_status: Literal["not_ready", "ready"] = Field(
        description="Derived readiness status based on source validation."
    )
    failure_code: str | None = Field(
        default=None,
        description="Stable blocker code when the source is not ready.",
    )
    mappings: list[SourceMappingSummaryResponse] = Field(
        default_factory=list,
        description="Mappings currently registered for this source.",
    )
    created_at: str = Field(description="Creation timestamp (ISO-8601).")
    updated_at: str = Field(description="Last update timestamp (ISO-8601).")


class ColumnPropertiesUpdateRequest(BaseModel):
    unit: str | None = None


# =============================================================================
# Engine models
# =============================================================================


class EngineDefaultNamespacePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    catalog: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")


class EngineDeploymentCapabilitiesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_step_types: list[str] = Field(default_factory=list)
    min_staleness_minutes: int | None = None


class EnginePolicyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_step_types: list[str] = Field(default_factory=list)
    required_policy_support: list[str] = Field(default_factory=list)


class EngineRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_type: Literal["duckdb", "trino"]
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    default_namespace: EngineDefaultNamespacePayload | None = None
    deployment_capabilities: EngineDeploymentCapabilitiesPayload = Field(
        default_factory=EngineDeploymentCapabilitiesPayload
    )
    policy: EnginePolicyPayload = Field(default_factory=EnginePolicyPayload)

    @model_validator(mode="after")
    def validate_engine_defaults(self) -> EngineRegisterRequest:
        if self.engine_type == "duckdb":
            namespace = self.default_namespace
            if namespace is not None and (
                namespace.catalog is not None or namespace.schema_name is not None
            ):
                raise ValueError("duckdb default_namespace must be null for catalog and schema")
        return self


class EngineDefaultNamespaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    catalog: str | None = Field(
        default=None,
        description="Engine-local default catalog fallback.",
    )
    schema_name: str | None = Field(
        default=None,
        alias="schema",
        description="Engine-local default schema fallback.",
    )


class EngineIntrinsicCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materialization_support: str = Field(
        description="Built-in materialization mode exposed by the engine implementation."
    )
    performance_class: str = Field(description="Built-in execution performance class.")
    federation_support: str = Field(
        description="Built-in federation mode exposed by the engine implementation."
    )


class EngineDeploymentCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_step_types: list[str] = Field(
        default_factory=list,
        description="Deployment-specific allowed step types when explicitly overridden.",
    )
    min_staleness_minutes: int | None = Field(
        default=None,
        description="Deployment-specific freshness floor in minutes.",
    )

    @model_serializer(mode="plain")
    def serialize_non_default_fields(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.supported_step_types:
            payload["supported_step_types"] = self.supported_step_types
        if self.min_staleness_minutes is not None:
            payload["min_staleness_minutes"] = self.min_staleness_minutes
        return payload


class EnginePolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_step_types: list[str] = Field(
        default_factory=list,
        description="Operator-allowed step types for this engine.",
    )
    required_policy_support: list[str] = Field(
        default_factory=list,
        description="Policy capabilities required before this engine may be selected.",
    )


class EngineMappingSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: str = Field(description="Stable mapping identifier.")
    source_id: str = Field(description="Source governed by this mapping.")
    status: Literal["active", "inactive", "deprecated"] = Field(
        description="Operator lifecycle status for the mapping."
    )
    readiness_status: Literal["not_ready", "ready"] = Field(
        description="Derived readiness status for the mapping."
    )
    failure_code: str | None = Field(
        default=None,
        description="Stable blocker code when the mapping is not ready.",
    )
    catalog_mappings: list[MappingCatalogEntryResponse] = Field(
        default_factory=list,
        description="Authority-to-execution catalog projection entries owned by the mapping.",
    )


class EngineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_id: str = Field(description="Stable engine identifier.")
    engine_type: Literal["duckdb", "trino"] = Field(description="Execution engine type.")
    display_name: str = Field(description="Human-readable engine name.")
    connection: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution engine connection parameters.",
    )
    default_namespace: EngineDefaultNamespaceResponse = Field(
        description="Engine-local default namespace fallback."
    )
    intrinsic_capabilities: EngineIntrinsicCapabilitiesResponse = Field(
        description="Read-only engine implementation capabilities."
    )
    deployment_capabilities: EngineDeploymentCapabilitiesResponse = Field(
        description="Deployment-scoped engine capability overrides."
    )
    policy: EnginePolicyResponse = Field(description="Operator control-plane policy.")
    status: Literal["active", "inactive", "deprecated"] = Field(
        description="Operator lifecycle status for the engine."
    )
    readiness_status: Literal["not_ready", "ready"] = Field(
        description="Derived readiness status based on engine validation."
    )
    failure_code: str | None = Field(
        default=None,
        description="Stable blocker code when the engine is not ready.",
    )
    mappings: list[EngineMappingSummaryResponse] = Field(
        default_factory=list,
        description="Mappings currently targeting this engine.",
    )
    created_at: str = Field(description="Creation timestamp (ISO-8601).")
    updated_at: str = Field(description="Last update timestamp (ISO-8601).")


# =============================================================================
# Mapping models
# =============================================================================


class MappingCatalogEntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority_catalog: str
    execution_catalog: str
    default_schema: str | None = None

    @field_validator("authority_catalog", "execution_catalog")
    @classmethod
    def validate_required_catalog_fields(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("mapping catalog fields must not be blank")
        return normalized

    @field_validator("default_schema")
    @classmethod
    def validate_default_schema(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("default_schema must not be blank")
        return normalized


class MappingCatalogEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority_catalog: str = Field(
        description="Authority-side catalog name frozen in source metadata."
    )
    execution_catalog: str = Field(description="Execution-side catalog projected by the mapping.")
    default_schema: str | None = Field(
        default=None,
        description="Fallback schema to apply only when the authority locator omits schema.",
    )


class MappingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    engine_id: str
    priority: int = 0
    catalog_mappings: list[MappingCatalogEntryPayload] = Field(default_factory=list)
    status: Literal["active", "inactive", "deprecated"] = "active"

    @model_validator(mode="after")
    def validate_catalog_mappings(self) -> MappingCreateRequest:
        _validate_unique_authority_catalogs(self.catalog_mappings)
        return self


class MappingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int | None = None
    catalog_mappings: list[MappingCatalogEntryPayload] | None = None
    status: Literal["active", "inactive", "deprecated"] | None = None

    @model_validator(mode="after")
    def validate_catalog_mappings(self) -> MappingUpdateRequest:
        if self.catalog_mappings is not None:
            _validate_unique_authority_catalogs(self.catalog_mappings)
        return self


class MappingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: str = Field(description="Stable mapping identifier.")
    source_id: str = Field(description="Source governed by this mapping.")
    engine_id: str = Field(description="Execution engine targeted by this mapping.")
    priority: int = Field(
        description="Routing priority. Higher values win among otherwise eligible mappings."
    )
    catalog_mappings: list[MappingCatalogEntryResponse] = Field(
        default_factory=list,
        description="Explicit authority-to-execution catalog projection entries.",
    )
    status: Literal["active", "inactive", "deprecated"] = Field(
        description="Operator lifecycle status for the mapping."
    )
    readiness_status: Literal["not_ready", "ready"] = Field(
        description="Derived readiness status based on dependency and catalog validation."
    )
    failure_code: str | None = Field(
        default=None,
        description="Stable blocker code when the mapping is not ready.",
    )
    created_at: str = Field(description="Creation timestamp (ISO-8601).")
    updated_at: str = Field(description="Last update timestamp (ISO-8601).")


class MappingDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["deleted"] = Field(description="Deletion status.")
    mapping_id: str = Field(description="Deleted mapping identifier.")


def _validate_unique_authority_catalogs(
    catalog_mappings: list[MappingCatalogEntryPayload],
) -> None:
    seen_authority_catalogs: set[str] = set()
    for entry in catalog_mappings:
        if entry.authority_catalog in seen_authority_catalogs:
            raise ValueError(
                f"catalog_mappings contains duplicate authority_catalog: {entry.authority_catalog}"
            )
        seen_authority_catalogs.add(entry.authority_catalog)


SourceResponse.model_rebuild()
EngineResponse.model_rebuild()
SourceRegisterRequest.model_rebuild()
SourceUpdateRequest.model_rebuild()
EngineRegisterRequest.model_rebuild()


# =============================================================================
# Routing models
# =============================================================================


class RouteIntentRequest(BaseModel):
    step_type: str | None = None
    metric_names: list[str] = Field(default_factory=list)
    requested_dimensions: list[str] = Field(default_factory=list)
    compatible_dimensions: list[str] = Field(default_factory=list)
    legal_grains: list[str] = Field(default_factory=list)
    policy_hints: list[str] = Field(default_factory=list)


class RouteResolveRequest(BaseModel):
    table_names: list[str]
    routing_intent: RouteIntentRequest | None = None


class RouteEngineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_id: str = Field(description="Resolved execution engine identifier.")
    engine_type: str = Field(description="Resolved execution engine type.")
    display_name: str = Field(description="Resolved execution engine display name.")


class RouteCapabilityProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine_type: str = Field(description="Engine type associated with this capability profile.")
    supported_sql_features: list[str] = Field(default_factory=list)
    supported_step_types: list[str] = Field(default_factory=list)
    materialization_support: str = Field(
        description="Materialization mode advertised by the engine."
    )
    policy_support: list[str] = Field(default_factory=list)
    performance_class: str = Field(description="Performance class used during routing.")
    min_staleness_minutes: int | None = Field(
        default=None,
        description="Minimum freshness lag tolerated by the engine profile.",
    )
    federation_support: str = Field(description="Federation capability advertised by the engine.")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved: bool = Field(description="Whether routing resolved to a concrete execution engine.")
    failure_code: str | None = Field(
        default=None,
        description="Stable routing blocker code when resolution fails.",
    )
    table_names: list[str] = Field(
        default_factory=list,
        description="Original table names supplied to the routing request.",
    )
    engine: RouteEngineResponse | None = Field(
        default=None,
        description="Resolved engine summary when routing succeeds.",
    )
    qualified_names: dict[str, str] = Field(
        default_factory=dict,
        description="Execution-qualified table names keyed by the requested table name.",
    )
    selection_reason: str | None = Field(
        default=None,
        description="Primary explanation for the selected engine or routing failure.",
    )
    routing_detail: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured routing evidence covering mappings, candidates, and blockers.",
    )
    capability_profile: RouteCapabilityProfileResponse | None = Field(
        default=None,
        description="Capability profile of the resolved engine when routing succeeds.",
    )


# =============================================================================
# Sync / Policy / Quality / Governance / Job / Approval models
# =============================================================================


class SyncSelectionItem(BaseModel):
    schema_name: str
    table_name: str


class SyncSelectionRequest(BaseModel):
    selections: list[SyncSelectionItem]


class PolicyCreateRequest(BaseModel):
    name: str
    policy_type: str
    definition: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdateRequest(BaseModel):
    enabled: bool | None = None
    definition: dict[str, Any] | None = None


class QualityRuleCreateRequest(BaseModel):
    name: str
    rule_type: str
    table_name: str
    threshold: dict[str, Any]
    severity: str = "warn"


class GovernanceCheckRequest(BaseModel):
    session_id: str
    step_type: str
    params: dict[str, Any] = Field(default_factory=dict)


class JobSubmitRequest(BaseModel):
    session_id: str
    job_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalCreateRequest(BaseModel):
    session_id: str
    rec_id: str


class ApprovalDecisionRequest(BaseModel):
    reviewer: str
    reason: str = ""


# =============================================================================
# Time / Scope / Measure models
# =============================================================================


class TimeWindow(BaseModel):
    start: str = Field(description="Window start boundary. Interpreted as inclusive.")
    end: str = Field(description="Window end boundary. Interpreted as exclusive.")


class TimeScope(BaseModel):
    mode: str = Field(description="Time-scope mode: 'single_window' or 'compare'.")
    grain: str = Field(description="Observation grain: 'day' or 'hour'.")
    current: TimeWindow
    baseline: TimeWindow | None = Field(
        default=None,
        description="Required only when mode='compare'. Omit when mode='single_window'.",
    )

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        if value not in {"single_window", "compare"}:
            raise ValueError("time_scope.mode must be 'single_window' or 'compare'")
        return value

    @field_validator("grain")
    @classmethod
    def _validate_grain(cls, value: str) -> str:
        if value not in {"day", "hour"}:
            raise ValueError("time_scope.grain must be 'day' or 'hour'")
        return value

    @model_validator(mode="after")
    def _validate_windows(self) -> TimeScope:
        if self.mode == "compare" and self.baseline is None:
            raise ValueError("time_scope.baseline is required when mode='compare'")
        if self.mode == "single_window" and self.baseline is not None:
            raise ValueError("time_scope.baseline is only allowed when mode='compare'")

        windows = [self.current]
        if self.baseline is not None:
            windows.append(self.baseline)
        for window in windows:
            self._validate_boundary(window.start)
            self._validate_boundary(window.end)
        return self

    def _validate_boundary(self, value: str) -> None:
        if self.grain == "day":
            if not _is_date_or_datetime_string(value):
                raise ValueError("day-grain boundaries must be date or datetime strings")
            return
        normalize_hour_boundary(value, label="time_scope boundary")


class Scope(BaseModel):
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="Typed non-time equality constraints for row/entity scoping.",
    )
    predicate: str | None = Field(
        default=None,
        description="Optional non-time SQL predicate. Time-axis conditions are not allowed.",
    )


class AnalysisTimeOverride(BaseModel):
    column: str = Field(description="Column to use as the semantic analysis time axis.")


class PartitionPruningOverride(BaseModel):
    date_column: str | None = Field(
        default=None,
        description="Optional partition date column used only for pruning.",
    )
    hour_column: str | None = Field(
        default=None,
        description="Optional partition hour column used only for pruning.",
    )

    @model_validator(mode="after")
    def _validate_partition_columns(self) -> PartitionPruningOverride:
        if self.date_column is None and self.hour_column is None:
            raise ValueError("partition_pruning must include date_column or hour_column")
        return self


class TimeAxis(BaseModel):
    analysis_time: AnalysisTimeOverride | None = Field(
        default=None,
        description="Advanced override for the semantic analysis time axis.",
    )
    partition_pruning: PartitionPruningOverride | None = Field(
        default=None,
        description="Advanced override for partition pruning columns.",
    )

    @model_validator(mode="after")
    def _validate_non_empty(self) -> TimeAxis:
        if self.analysis_time is None and self.partition_pruning is None:
            raise ValueError("time_axis must include analysis_time or partition_pruning")
        return self


class Measure(BaseModel):
    expr: str = Field(description="Aggregate SQL expression.")
    as_: str = Field(
        serialization_alias="as", validation_alias="as", description="Required output alias."
    )

    @field_validator("expr")
    @classmethod
    def _validate_aggregate_expr(cls, value: str) -> str:
        expr = value.strip()
        if not expr:
            raise ValueError("measure.expr must not be empty")
        if not _looks_like_aggregate_expression(expr):
            raise ValueError("measure.expr must be an aggregate expression")
        return expr

    @field_validator("as_")
    @classmethod
    def _validate_alias(cls, value: str) -> str:
        alias = value.strip()
        if not alias:
            raise ValueError("measure.as must not be empty")
        return alias


# =============================================================================
# Step models
# =============================================================================


class MetricQueryStep(BaseModel):
    table: str = Field(description="Physical table that backs the semantic metric.")
    metric: str = Field(description="Published semantic metric name.")
    dimensions: list[str] = Field(default_factory=list, description="Optional grouping dimensions.")
    time_scope: TimeScope
    scope: Scope | None = Field(default=None, description="Optional non-time row/entity scope.")
    time_axis: TimeAxis | None = Field(
        default=None,
        description=(
            "Advanced time-axis override. If omitted, Marivo resolves from metadata or heuristics. "
            "Legacy fields period_start, period_end, baseline_start, baseline_end, comparison_type, "
            "date_column, where, and filter are no longer supported."
        ),
    )
    order: str | None = Field(
        default=None, description="Optional ordering expression for output rows."
    )
    limit: int | None = Field(default=None, ge=1, description="Optional row limit.")


class AggregateQueryStep(BaseModel):
    table: str = Field(description="Physical table to aggregate.")
    group_by: list[str] = Field(default_factory=list, description="Optional grouping columns.")
    measures: list[Measure] = Field(
        min_length=1,
        description=(
            "Aggregate measures. Each item must be an aggregate expression and must include an explicit alias. "
            "Legacy fields select, where, compare_period, and date_column are no longer supported."
        ),
    )
    time_scope: TimeScope
    scope: Scope | None = Field(default=None, description="Optional non-time row/entity scope.")
    time_axis: TimeAxis | None = Field(
        default=None,
        description="Advanced time-axis override resolved ahead of execution.",
    )
    order: str | None = Field(
        default=None, description="Optional ordering expression for output rows."
    )
    limit: int | None = Field(default=None, ge=1, description="Optional row limit.")


class AttributeChangeStep(BaseModel):
    metric_name: str = Field(description="Published semantic metric to attribute.")
    table_name: str = Field(description="Physical table that backs the metric.")
    period_start: str | None = Field(
        default=None,
        description="Current window start date (YYYY-MM-DD). Defaults to period_end when omitted.",
    )
    period_end: str = Field(description="Current window end date (YYYY-MM-DD).")
    baseline_start: str = Field(description="Baseline window start date (YYYY-MM-DD).")
    baseline_end: str = Field(description="Baseline window end date (YYYY-MM-DD).")
    candidate_dimensions: list[str] = Field(
        default_factory=list,
        min_length=1,
        description="Candidate attribution dimensions to compare one-by-one.",
    )
    anomaly_observation_id: str | None = Field(
        default=None,
        description="Optional upstream anomaly observation to link with a justifies edge.",
    )
    top_k: int = Field(
        default=5, ge=1, description="Number of top contributors to return per dimension."
    )
    min_contribution_pct: float = Field(
        default=5.0,
        ge=0.0,
        description="Minimum contribution percentage required to keep a contributor.",
    )
    date_column: str | None = Field(
        default=None,
        description="Optional explicit date column. When omitted, the service infers one.",
    )
    where: str | None = Field(
        default=None,
        description="Optional SQL filter merged with session constraints before attribution queries.",
    )


# =============================================================================
# Signal / Plan models
# =============================================================================


class AutoFlagRequest(BaseModel):
    risk_threshold: str = "P0"


class ReadinessSignal(BaseModel):
    """Readiness signal returned in each primitive step response (M-04).

    All dimensions are in [0.0, 1.0]. Marivo computes these deterministically
    from the current session evidence state — no LLM involvement.
    The agent decides how to act on these signals; Marivo never auto-triggers
    next steps.
    """

    goal_coverage: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of session goal covered by claims with confidence >= 0.5. "
            "Denominator is 5 (heuristic target claim count). Reaches 1.0 when >= 5 "
            "high-confidence claims exist."
        ),
    )
    evidence_sufficiency: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Average (supporting_observations / 3) across all claims, clipped to [0,1]. "
            "Reaches 1.0 when every claim has >= 3 supporting observations."
        ),
    )
    contradiction_resolution: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of claims with no contradicting observations. "
            "1.0 = no unresolved contradictions; 0.0 = all claims are contradicted."
        ),
    )
    budget_remaining: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Remaining budget fraction: (max_steps - primitive_step_count) / max_steps. "
            "Only primitive execution steps count toward this budget. 0.0 = budget exhausted."
        ),
    )
    diminishing_returns: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of the last 3 primitive steps that produced at least one new claim. "
            "0.0 = recent steps yielded no new claims (diminishing returns detected)."
        ),
    )
    suggested_action: str = Field(
        description=(
            "Agent guidance signal — one of four values: "
            "'resolve_contradiction' (unresolved contradictions detected), "
            "'synthesize' (goal_coverage >= 0.7 AND evidence_sufficiency >= 0.7), "
            "'stop' (budget nearly exhausted OR diminishing returns with sufficient evidence), "
            "'continue_exploring' (none of the above conditions met). "
            "This is a signal, not a command. The agent decides whether to act on it."
        ),
    )


class ModifyStepOperation(BaseModel):
    """A single step parameter modification in a plan patch."""

    index: int = Field(description="Zero-based index of the step to modify.")
    params: dict[str, Any] = Field(
        description="Params to merge into the step. Existing params not listed here are preserved.",
    )


class PlanPatchRequest(BaseModel):
    """Incremental patch for an existing plan.

    Agents submit patches to add steps, modify step params, or skip steps.
    The plan is reset to 'draft', the patch applied, and the plan re-validated
    (which may auto-approve if no governance/budget issues are found).
    """

    add_steps: list[dict[str, Any]] = Field(
        default_factory=list,
        description="New step dicts to append. Each must include a valid 'step_type'.",
    )
    modify_steps: list[ModifyStepOperation] = Field(
        default_factory=list,
        description="Step parameter updates keyed by step index.",
    )
    skip_steps: list[int] = Field(
        default_factory=list,
        description="Indices of steps to mark as 'skipped'.",
    )


# =============================================================================
# Internal helpers
# =============================================================================

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?([zZ]|[+-]\d{2}:\d{2})?$"
)
_AGGREGATE_FN_RE = re.compile(
    r"\b(count|sum|avg|min|max|approx_distinct|count_if|stddev|stddev_samp|stddev_pop|variance|var_samp|var_pop)\s*\(",
    re.IGNORECASE,
)


def _is_date_or_datetime_string(value: str) -> bool:
    stripped = value.strip()
    return bool(_DATE_ONLY_RE.fullmatch(stripped) or _DATETIME_RE.fullmatch(stripped))


def _looks_like_aggregate_expression(value: str) -> bool:
    return bool(_AGGREGATE_FN_RE.search(value))
