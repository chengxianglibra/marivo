"""Pydantic request/response models for the Factum HTTP API (legacy non-semantic models).

Signal vs Decision design principle
-------------------------------------
Factum returns **signals** (canonical evidence: findings, propositions,
assessments, action proposals) and enforces **decisions** (governance
constraints, budget limits). Agents retain full control over what to do
with signals; governance decisions are hard stops that Factum enforces on
behalf of the operator.

Session constraints summary:
- constraints : Scalar key/value filters injected into step WHERE clauses.
                Signal-shaping input — narrows the analysis scope.
- budget      : Hard resource limits (scan bytes, latency).
                System-enforced decision — steps that exceed budget are blocked.
- policy      : Governance rules (aggregate_only, min_group_size).
                System-enforced decision — violations block step execution.

Semantic layer models:
----------------------
Typed semantic object models (entity, metric, dimension, etc.) are now
in the app/api/models/ subpackage (entity.py, metric.py, etc.). Import from
app.api.models for backward compatibility.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.models.base import validate_ref_prefix
from app.time_contracts import normalize_hour_boundary

# =============================================================================
# DEPRECATED: Legacy semantic models (for backward compatibility with semantic.py)
# These will be removed when semantic.py is updated to use typed models.
# See docs/semantic/ for new typed contract schemas.
# =============================================================================


class EntityCreateRequest(BaseModel):
    """DEPRECATED: Legacy entity create request. Use typed EntityCreateRequest from app.api.models.entity."""

    name: str
    display_name: str
    description: str = ""
    keys: list[str]
    level: str | None = None
    join_constraints: dict[str, Any] | None = None
    upstream_dependencies: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class EntityUpdateRequest(BaseModel):
    """DEPRECATED: Legacy entity update request. Use typed EntityUpdateRequest from app.api.models.entity."""

    display_name: str | None = None
    description: str | None = None
    keys: list[str] | None = None
    level: str | None = None
    join_constraints: dict[str, Any] | None = None
    upstream_dependencies: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None


class EntityPropertiesPatchRequest(BaseModel):
    """DEPRECATED: Legacy entity properties patch request."""

    properties: dict[str, Any] = Field(
        ...,
        description="Properties keys to merge into the entity's existing properties_json.",
    )


class MetricCreateRequest(BaseModel):
    """DEPRECATED: Legacy metric create request. Use typed MetricCreateRequest from app.api.models.metric."""

    name: str
    display_name: str
    description: str = ""
    definition_sql: str
    dimensions: list[str]
    entity_id: str | None = None
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    desired_direction: str | None = None


class MetricUpdateRequest(BaseModel):
    """DEPRECATED: Legacy metric update request. Use typed MetricUpdateRequest from app.api.models.metric."""

    display_name: str | None = None
    description: str | None = None
    definition_sql: str | None = None
    dimensions: list[str] | None = None
    entity_id: str | None = None
    grain: str | None = None
    measure_type: str | None = None
    allowed_dimensions: list[str] | None = None
    lineage: list[str] | None = None
    quality_expectations: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None
    desired_direction: str | None = None


class MappingCreateRequest(BaseModel):
    """DEPRECATED: Legacy mapping create request. Use TypedBindingCreateRequest from app.api.models.binding."""

    semantic_type: str
    semantic_id: str
    object_id: str
    mapping_type: str
    mapping_json: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Non-semantic API models (kept as-is)
# =============================================================================


class SessionTerminateRequest(BaseModel):
    terminal_reason: str = "user_closed"


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str
    budget: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_scan_bytes": 500_000_000_000,
            "max_latency_sec": 120,
        },
        description=(
            "Hard resource limits enforced by Factum. Steps that would exceed "
            "max_scan_bytes or max_latency_sec are blocked before execution. "
            "This is a system decision constraint, not a suggestion."
        ),
    )
    policy: dict[str, Any] = Field(
        default_factory=lambda: {
            "aggregate_only": True,
            "min_group_size": 100,
        },
        description=(
            "Governance rules enforced by Factum (e.g. aggregate_only blocks raw row access, "
            "min_group_size enforces k-anonymity). System-enforced decision constraints — "
            "violations block step execution regardless of agent intent."
        ),
    )


class SourceRegisterRequest(BaseModel):
    source_type: str
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None


class SourceUpdateRequest(BaseModel):
    display_name: str | None = None
    connection: dict[str, Any] | None = None
    sync_mode: str | None = None


class ColumnPropertiesUpdateRequest(BaseModel):
    unit: str | None = None


class EngineRegisterRequest(BaseModel):
    engine_type: str
    display_name: str
    connection: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] | None = None


# Source-Engine Binding (not typed binding)
# This is for binding a source to an execution engine, not semantic binding
class SourceEngineBindingCreateRequest(BaseModel):
    source_id: str
    engine_id: str
    priority: int = 0
    namespace: dict[str, Any] = Field(default_factory=dict)


# Alias for backward compatibility
BindingCreateRequest = SourceEngineBindingCreateRequest


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


class MetricQueryStep(BaseModel):
    table: str = Field(description="Physical table that backs the semantic metric.")
    metric: str = Field(description="Published semantic metric name.")
    dimensions: list[str] = Field(default_factory=list, description="Optional grouping dimensions.")
    time_scope: TimeScope
    scope: Scope | None = Field(default=None, description="Optional non-time row/entity scope.")
    time_axis: TimeAxis | None = Field(
        default=None,
        description=(
            "Advanced time-axis override. If omitted, Factum resolves from metadata or heuristics. "
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


class AutoFlagRequest(BaseModel):
    risk_threshold: str = "P0"


class ReadinessSignal(BaseModel):
    """Readiness signal returned in each primitive step response (M-04).

    All dimensions are in [0.0, 1.0]. Factum computes these deterministically
    from the current session evidence state — no LLM involvement.
    The agent decides how to act on these signals; Factum never auto-triggers
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


# ── Intent API models ────────────────────────────────────────────────────────
# Typed request models for the new intent-based write surface.
# Path (/intents/<intent_type>) acts as the discriminator; no step_type field.


class ObservationRef(BaseModel):
    """Typed reference to an upstream `observe` step artifact."""

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream observe step. Defaults to the path session when omitted.",
    )
    step_id: str
    step_type: Literal["observe"]


class CorrelateObservationRef(ObservationRef):
    """Full typed artifact reference for `correlate` — adds artifact identity fields
    required by the correlate.md Reference Contract."""

    artifact_id: str | None = Field(
        default=None,
        description="Artifact ID of the upstream observe artifact (optional; resolved from step_id if omitted).",
    )
    observation_type: Literal["time_series"] = Field(
        default="time_series",
        description="Must be 'time_series'.  v1 does not support scalar or segmented correlations.",
    )


class TestObservationRef(ObservationRef):
    """Full typed artifact reference for `test`.

    Matches the typed reference contract from docs/analysis/intents/atomic/test.md.
    """

    artifact_id: str = Field(
        description="Artifact ID of the upstream committed observe artifact.",
    )
    observation_type: Literal["numeric_sample_summary", "rate_sample_summary"] = Field(
        description=(
            "Inferential-ready observation artifact type. "
            "Must be 'numeric_sample_summary' or 'rate_sample_summary'."
        ),
    )


class ArtifactRef(BaseModel):
    """Typed reference to any upstream intent step artifact."""

    session_id: str | None = Field(
        default=None,
        description="Session containing the upstream step. Defaults to the path session when omitted.",
    )
    step_id: str
    step_type: str


# ObserveTimeScope — discriminated union keyed on `kind`


class ObserveTimeScopeRange(BaseModel):
    kind: Literal["range"]
    start: str = Field(description="Inclusive start of the range (ISO-8601 date or datetime).")
    end: str = Field(description="Exclusive end of the range (ISO-8601 date or datetime).")


class ObserveTimeScopeSnapshotNow(BaseModel):
    kind: Literal["snapshot_now"]


class ObserveTimeScopeLatestAvailable(BaseModel):
    kind: Literal["latest_available"]


class ObserveTimeScopeAsOf(BaseModel):
    kind: Literal["as_of"]
    at: str = Field(description="Point-in-time snapshot (ISO-8601 datetime).")


ObserveTimeScope = Annotated[
    ObserveTimeScopeRange
    | ObserveTimeScopeSnapshotNow
    | ObserveTimeScopeLatestAvailable
    | ObserveTimeScopeAsOf,
    Field(discriminator="kind"),
]


class ObserveScope(BaseModel):
    """Non-time population scope for an observe intent.

    `constraints` holds scalar equality filters; `predicate` holds a
    structured predicate AST (dict).  Time conditions must not appear here.
    """

    constraints: dict[str, Any] | None = Field(
        default=None,
        description="Scalar equality constraints on semantic dimensions.",
    )
    predicate: dict[str, Any] | None = Field(
        default=None,
        description="Structured non-time predicate AST.  Must not contain time conditions.",
    )


class ObserveRequest(BaseModel):
    """Atomic intent: read a typed observation for a semantic metric."""

    metric: str = Field(description="Canonical semantic metric ref (e.g., 'metric.watch_time').")
    result_mode: Literal["standard", "numeric_sample_summary", "rate_sample_summary"] = Field(
        default="standard",
        description=(
            "Observation contract type.  'standard' returns scalar/time-series/segmented output "
            "depending on granularity and dimensions.  'numeric_sample_summary' and "
            "'rate_sample_summary' return inferential-ready summaries for downstream `test`."
        ),
    )
    time_scope: ObserveTimeScope
    scope: ObserveScope | None = Field(default=None)
    granularity: Literal["hour", "day", "week", "month"] | None = Field(
        default=None,
        description="Time-series bucket size.  Only valid when result_mode='standard'.",
    )
    dimensions: list[str] | None = Field(
        default=None,
        description="Semantic dimensions for segmented output.  Only valid when result_mode='standard'.",
    )

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")

    @model_validator(mode="after")
    def _validate_mode_combinations(self) -> ObserveRequest:
        if self.granularity is not None and self.dimensions is not None:
            raise ValueError("granularity and dimensions are mutually exclusive")
        if self.result_mode != "standard":
            if self.granularity is not None:
                raise ValueError("granularity is only valid when result_mode='standard'")
            if self.dimensions is not None:
                raise ValueError("dimensions is only valid when result_mode='standard'")
        kind = self.time_scope.kind if hasattr(self.time_scope, "kind") else None
        if kind in {"snapshot_now", "latest_available", "as_of"} and self.granularity is not None:
            raise ValueError(f"granularity is not valid when time_scope.kind='{kind}'")
        if self.granularity == "hour" and isinstance(self.time_scope, ObserveTimeScopeRange):
            normalize_hour_boundary(self.time_scope.start, label="time_scope.start")
            normalize_hour_boundary(self.time_scope.end, label="time_scope.end")
        if self.dimensions == []:
            self.dimensions = None
        return self


class CompareRequest(BaseModel):
    """Atomic intent: compute a typed delta between two observations."""

    left_ref: ObservationRef = Field(description="Reference to the 'current' observe artifact.")
    right_ref: ObservationRef = Field(description="Reference to the 'baseline' observe artifact.")
    mode: Literal["auto", "scalar", "segmented"] = Field(
        default="auto",
        description=(
            "'auto' selects scalar or segmented based on the input observation types. "
            "'scalar' and 'segmented' enforce a specific delta type."
        ),
    )


class DecomposeRequest(BaseModel):
    """Atomic intent: attribute a scalar delta across a single semantic dimension."""

    compare_ref: ArtifactRef = Field(
        description="Reference to an upstream `compare` step artifact (step_type='compare')."
    )
    dimension: str = Field(
        min_length=1,
        description="Single semantic dimension to decompose the delta across.",
    )
    method: str = Field(
        default="delta_share",
        description="Attribution method. Only 'delta_share' is supported in v1.",
    )

    @field_validator("compare_ref")
    @classmethod
    def _validate_compare_ref_type(cls, ref: ArtifactRef) -> ArtifactRef:
        if ref.step_type != "compare":
            raise ValueError(f"compare_ref.step_type must be 'compare', got '{ref.step_type}'")
        return ref


class CorrelateRequest(BaseModel):
    """Atomic intent: estimate statistical association between two time-series."""

    left_ref: CorrelateObservationRef = Field(
        description="Reference to a time-series observe artifact (left series)."
    )
    right_ref: CorrelateObservationRef = Field(
        description="Reference to a time-series observe artifact (right series)."
    )
    method: Literal["spearman", "pearson"] = Field(
        default="spearman",
        description="Correlation method ('spearman' or 'pearson').  v1 supports one method per request.",
    )
    min_pairs: int = Field(
        default=5,
        ge=1,
        description="Minimum number of aligned time-bucket pairs required.  Requests with fewer aligned pairs are rejected.",
    )


class DetectTimeScopeCurrentWindow(BaseModel):
    start: str = Field(description="Inclusive start of the window (ISO-8601 date or datetime).")
    end: str = Field(description="Exclusive end of the window (ISO-8601 date or datetime).")


class DetectTimeScope(BaseModel):
    """time_scope for detect: single_window mode with explicit grain and current window."""

    mode: Literal["single_window"]
    grain: Literal["hour", "day", "week", "month"]
    current: DetectTimeScopeCurrentWindow

    @model_validator(mode="after")
    def _validate_current_window(self) -> DetectTimeScope:
        if self.grain == "hour":
            normalize_hour_boundary(self.current.start, label="time_scope.current.start")
            normalize_hour_boundary(self.current.end, label="time_scope.current.end")
        return self


class DetectRequest(BaseModel):
    """Atomic intent: scan a metric time range for anomaly candidates."""

    metric: str = Field(
        description="Canonical semantic metric ref to scan (e.g., 'metric.watch_time')."
    )
    time_scope: DetectTimeScope
    scope: ObserveScope | None = Field(default=None)
    split_by: str | None = Field(
        default=None,
        description="Optional semantic dimension to split the metric into independent series.",
    )
    profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = Field(
        default="auto",
        description="Detection profile preset.",
    )
    sensitivity: Literal["conservative", "balanced", "aggressive"] = Field(
        default="balanced",
        description="Detection sensitivity preset.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of candidates to return.",
    )
    max_series: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of series to scan when split_by is set.",
    )

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")


class HypothesisContract(BaseModel):
    """Hypothesis definition for the `test` atomic intent."""

    family: Literal["difference"] = Field(
        default="difference",
        description="Hypothesis family.  v1 only supports 'difference'.",
    )
    alternative: Literal["two_sided", "greater", "less"] = Field(
        default="two_sided",
        description="Direction of the alternative hypothesis.",
    )
    alpha: float = Field(
        default=0.05,
        gt=0.0,
        lt=1.0,
        description="Significance level α ∈ (0, 1).",
    )
    label: str | None = Field(
        default=None,
        description="Human-readable label.  Does not affect artifact identity.",
    )


class IntentTestRequest(BaseModel):
    """Atomic intent: evaluate a typed statistical hypothesis.

    Named IntentTestRequest to avoid collision with Python's built-in `test` usage.
    Exposed via the /intents/test endpoint.
    """

    left_ref: TestObservationRef = Field(
        description=(
            "Reference to an inferential-ready observe artifact "
            "(numeric_sample_summary or rate_sample_summary)."
        )
    )
    right_ref: TestObservationRef = Field(
        description="Reference to a second inferential-ready observe artifact."
    )
    hypothesis: HypothesisContract = Field(
        description="Hypothesis contract defining the statistical test to run.",
    )
    method: Literal["auto", "welch_t", "two_proportion_z"] = Field(
        default="auto",
        description=(
            "'auto' selects welch_t for numeric_sample_summary and "
            "two_proportion_z for rate_sample_summary."
        ),
    )


class ForecastRequest(BaseModel):
    """Atomic intent: project a time-series into future buckets."""

    source_ref: ObservationRef = Field(
        description="Reference to a completed time-series observe artifact."
    )
    horizon: int = Field(ge=1, le=90, description="Number of future buckets to forecast.")
    profile: Literal["auto", "level", "trend", "seasonal", "seasonal_trend"] = Field(
        default="auto",
        description=(
            "Forecast profile.  'auto' selects the best available v1 algorithm. "
            "'level' uses last-value carry-forward; 'trend' uses OLS linear extrapolation. "
            "'seasonal' and 'seasonal_trend' are accepted but unsupported in v1."
        ),
    )
    interval_level: float | None = Field(
        default=None,
        gt=0.0,
        lt=1.0,
        description="Prediction interval confidence level in (0, 1).  Defaults to 0.95.",
    )


class AttributeObservationInput(BaseModel):
    """One side of an attribute request — canonical observe scalar profile."""

    time_scope: ObserveTimeScope
    scope: ObserveScope | None = Field(default=None)


class AttributeRequest(BaseModel):
    """Derived intent: attribute a metric change (expands to observe+observe+compare+decompose)."""

    metric: str = Field(
        description="Canonical semantic metric ref to attribute (e.g., 'metric.watch_time')."
    )
    left: AttributeObservationInput = Field(
        description="Current / treatment side observation scope."
    )
    right: AttributeObservationInput = Field(
        description="Baseline / control side observation scope."
    )
    dimensions: list[str] = Field(
        min_length=1,
        description="Attribution dimensions (deduped in order).",
    )
    decomposition_method: Literal["delta_share"] = Field(default="delta_share")
    decomposition_limit: int = Field(default=5, ge=1)

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")


class DiagnoseRequest(BaseModel):
    """Derived intent: diagnose anomalies (expands to detect+compare+decompose on top-K)."""

    metric: str = Field(
        description="Canonical semantic metric ref to diagnose (e.g., 'metric.watch_time')."
    )
    time_scope: DetectTimeScope
    scope: ObserveScope | None = Field(default=None)
    detect_split_by: str | None = Field(
        default=None,
        description="Optional semantic dimension to split detect into independent series.",
    )
    candidate_dimensions: list[str] = Field(
        min_length=1,
        description="Attribution dimensions to decompose each followed candidate over.",
    )
    profile: Literal["auto", "spike_dip", "level_shift", "seasonal_residual"] = Field(
        default="auto",
        description="Detection profile preset.",
    )
    sensitivity: Literal["conservative", "balanced", "aggressive"] = Field(
        default="balanced",
        description="Detection sensitivity preset.",
    )
    candidate_limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of candidates returned by the internal detect step.",
    )
    followup_limit: int | None = Field(
        default=3,
        ge=1,
        description="Number of top-ranked candidates to follow up with compare+decompose.",
    )
    decomposition_limit: int | None = Field(
        default=5,
        ge=1,
        description="Maximum driver rows per dimension per candidate.",
    )

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")


class ValidateObservationInput(BaseModel):
    """One side (left/right) of a validate intent: time scope + optional non-time scope."""

    time_scope: ObserveTimeScope
    scope: ObserveScope | None = None


class ValidateHypothesis(BaseModel):
    """Hypothesis specification for a validate intent (difference family only in v1)."""

    family: Literal["difference"] = "difference"
    alternative: Literal["two_sided", "greater", "less"] = "two_sided"
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    label: str | None = None


class ValidateRequest(BaseModel):
    """Derived intent: validate a hypothesis (expands to observe×2 + test)."""

    metric: str = Field(
        description="Canonical semantic metric ref to validate (e.g., 'metric.watch_time')."
    )
    left: ValidateObservationInput = Field(description="Primary / treatment population.")
    right: ValidateObservationInput = Field(description="Comparison / control population.")
    sample_kind: Literal["auto", "numeric", "rate"] | None = Field(
        default=None,
        description=(
            "Inferential summary mode. 'auto' fails in v1; use 'numeric' or 'rate' explicitly."
        ),
    )
    hypothesis: ValidateHypothesis | None = Field(default=None)
    method: Literal["auto", "welch_t", "two_proportion_z"] | None = Field(default=None)

    @field_validator("metric")
    @classmethod
    def _validate_metric_ref(cls, value: str) -> str:
        return validate_ref_prefix(value, "metric", "metric")


class SessionStateQueryRequest(BaseModel):
    """Request body for ``POST /sessions/{session_id}/state/query`` (Phase 5b).

    All fields are optional.  Omitted fields apply no filter.
    Mirrors the ``SessionStateQuery`` canonical contract from
    ``docs/analysis/evidence-engine/schemas/state-surface-schema.md``.

    ``page_token`` is intentionally absent: it is a transport concern, not part
    of the canonical query contract.  When cursor pagination is implemented it
    will be a separate HTTP query parameter on both GET and POST endpoints.
    """

    metric: str | None = None
    entity: str | None = None
    slice: dict[str, Any] | None = None
    proposition_types: list[str] | None = None
    origin_kinds: list[str] | None = None
    assessment_presence: Literal["assessed", "unassessed"] | None = None
    assessment_statuses: list[str] | None = None
    has_blocking_gaps: bool | None = None
    limit: int | None = None


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


def _is_datetime_string(value: str) -> bool:
    return bool(_DATETIME_RE.fullmatch(value.strip()))


def _looks_like_aggregate_expression(value: str) -> bool:
    return bool(_AGGREGATE_FN_RE.search(value))
