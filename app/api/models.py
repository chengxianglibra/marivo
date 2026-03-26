"""Pydantic request/response models for the Factum HTTP API.

Signal vs Decision design principle
-------------------------------------
Factum returns **signals** (evidence, claims, recommendations) and enforces
**decisions** (governance constraints, budget limits). Agents retain full
control over what to do with signals; governance decisions are hard stops
that Factum enforces on behalf of the operator.

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
from typing import Any

from pydantic import BaseModel, Field
from pydantic import field_validator
from pydantic import model_validator


class SessionCreateRequest(BaseModel):
    goal: str
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Scalar key/value filters auto-injected into step WHERE clauses "
            "(e.g. {\"region\": \"us-east\"}). Narrows analysis scope — a signal-shaping input, "
            "not a governance constraint."
        ),
    )
    raw_filter: str | None = Field(
        default=None,
        description=(
            "Raw SQL filter expression appended (AND) to session constraint filters. "
            "Supports IN, BETWEEN, IS NOT NULL, and any valid SQL predicate. "
            "Example: \"cluster IN ('k8sbi-bi1', 'k8sbi-bi2') AND log_date >= '20260301'\""
        ),
    )
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


class EntityCreateRequest(BaseModel):
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
    """G-5d: Incrementally patch properties_json on a published entity.

    Only the keys present in `properties` are merged into the existing
    properties dict.  Bumps revision and updated_at.

    Supported patch shapes: any key/value pairs in `properties`.
    Use `{"unit": "milliseconds"}` to apply a unit hint suggestion.
    """
    properties: dict[str, Any] = Field(
        ...,
        description="Properties keys to merge into the entity's existing properties_json.",
    )


class MetricCreateRequest(BaseModel):
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
    semantic_type: str
    semantic_id: str
    object_id: str
    mapping_type: str
    mapping_json: dict[str, Any] = Field(default_factory=dict)


class BindingCreateRequest(BaseModel):
    source_id: str
    engine_id: str
    priority: int = 0
    namespace: dict[str, Any] = Field(default_factory=dict)


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


class EvidenceStepResponse(BaseModel):
    step_id: str
    step_type: str
    status: str
    summary: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class EvidenceObservationResponse(BaseModel):
    observation_id: str
    type: str
    subject: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    significance: dict[str, Any] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    observed_window: dict[str, Any] | None = None
    temporal_order: int = 0


class EvidenceClaimResponse(BaseModel):
    claim_id: str
    claim_type: str
    text: str
    scope: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    status: str
    supporting_observations: list[str] = Field(default_factory=list)
    contradicting_observations: list[str] = Field(default_factory=list)
    confidence_breakdown: dict[str, Any] = Field(default_factory=dict)
    inference_level: str
    inference_justification: list[str] = Field(default_factory=list)


class EvidenceEdgeResponse(BaseModel):
    edge_id: str
    from_node_id: str
    from_node_type: str
    to_node_id: str
    to_node_type: str
    edge_type: str
    weight: float
    explanation: str
    match_basis: dict[str, Any] = Field(default_factory=dict)
    score_components: dict[str, Any] = Field(default_factory=dict)
    supporting_observation_ids: list[str] = Field(default_factory=list)


class EvidenceRecommendationResponse(BaseModel):
    rec_id: str
    type: str
    claim_id: str
    action_text: str
    template_id: str | None = None
    priority: str
    expected_impact: str
    risk: str
    validation_metric: dict[str, Any] = Field(default_factory=dict)
    causal_basis: dict[str, Any] | None = None
    entity_patch: dict[str, Any] | None = None
    supporting_claims: list[str] | None = None
    action: str | None = None


class EvidenceGraphResponse(BaseModel):
    session_id: str
    steps: list[EvidenceStepResponse] = Field(default_factory=list)
    observations: list[EvidenceObservationResponse] = Field(default_factory=list)
    claims: list[EvidenceClaimResponse] = Field(default_factory=list)
    edges: list[EvidenceEdgeResponse] = Field(default_factory=list)
    recommendations: list[EvidenceRecommendationResponse] = Field(default_factory=list)
    debug: dict[str, Any] | None = None


class SessionDebugResponse(BaseModel):
    session_id: str
    relation_discovery: dict[str, Any] = Field(default_factory=dict)
    checker_logs: list[dict[str, Any]] = Field(default_factory=list)


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
        if not _is_datetime_string(value):
            raise ValueError("hour-grain boundaries must be datetime-compatible strings")


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
    as_: str = Field(serialization_alias="as", validation_alias="as", description="Required output alias.")

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
    order: str | None = Field(default=None, description="Optional ordering expression for output rows.")
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
    order: str | None = Field(default=None, description="Optional ordering expression for output rows.")
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
    top_k: int = Field(default=5, ge=1, description="Number of top contributors to return per dimension.")
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
            "synthesize_findings steps are excluded from the count. 0.0 = budget exhausted."
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
