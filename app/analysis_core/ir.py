from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.analysis_core.primitives import (
    COMPOSITE_STEP_TYPES,
    step_category_for,
)


DEFAULT_SLICE_DIMENSIONS = ("platform", "app_version", "network_type", "content_type")
PERIOD_CONTEXT_STEP_TYPES = frozenset(
    {
        "compare_metric",
    }
)
STEP_METRICS: dict[str, str] = {}
STEP_OBSERVATION_TYPES = {
    "profile_table": ["table_profile"],
    "sample_rows": ["sample_rows"],
    "synthesize_findings": ["root_cause_candidate", "recommendation"],
}
STEP_ARTIFACT_KINDS = {
    "compare_metric": "table",
    "profile_table": "profile",
    "profile_table_row_count": "profile",
    "profile_table_columns": "profile",
    "profile_table_column_profile": "profile",
    "sample_rows": "rows",
    "synthesize_findings": "synthesis",
}


@dataclass(slots=True)
class AnalysisRequest:
    """Normalized request context for analysis execution."""

    goal: str = ""
    session_id: str | None = None
    plan_id: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    requested_step_types: list[str] = field(default_factory=list)
    requested_metrics: list[str] = field(default_factory=list)
    requested_tables: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SemanticIntent:
    """Semantic-level intent inferred for a step."""

    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    source_table: str | None = None
    date_column: str | None = None


@dataclass(slots=True)
class ArtifactExpectation:
    """Expected artifact and evidence shape for a step."""

    artifact_kind: str
    artifact_key: str | None = None
    observation_types: list[str] = field(default_factory=list)
    summary_required: bool = True


@dataclass(slots=True)
class ResolvedMetricIR:
    """Execution-facing semantic contract for a resolved metric."""

    name: str
    grain: str | None = None
    measure_type: str | None = None
    dimensions: list[str] = field(default_factory=list)
    allowed_dimensions: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedEntityIR:
    """Execution-facing semantic contract for a resolved entity."""

    name: str
    keys: list[str] = field(default_factory=list)
    level: str | None = None
    join_constraints: dict[str, Any] = field(default_factory=dict)
    upstream_dependencies: list[str] = field(default_factory=list)
    lineage: list[str] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SemanticResolutionIR:
    """Resolved semantic envelope for a step inside an execution plan."""

    step_index: int
    requested_metrics: list[str] = field(default_factory=list)
    requested_dimensions: list[str] = field(default_factory=list)
    supported_dimensions: list[str] = field(default_factory=list)
    compatible_dimensions: list[str] = field(default_factory=list)
    legal_grains: list[str] = field(default_factory=list)
    source_table: str | None = None
    date_column: str | None = None
    metrics: list[ResolvedMetricIR] = field(default_factory=list)
    entities: list[ResolvedEntityIR] = field(default_factory=list)
    quality_expectations: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionTargetIR:
    """Execution-target metadata for a step after routing/default resolution."""

    step_index: int
    table_names: list[str] = field(default_factory=list)
    routing_table_names: list[str] = field(default_factory=list)
    qualified_names: dict[str, str] = field(default_factory=dict)
    engine_id: str | None = None
    engine_type: str | None = None
    engine_locality: str = "unknown"
    routing_strategy: str | None = None
    routing_error: str | None = None
    routing_reason: str | None = None
    routing_detail: dict[str, Any] = field(default_factory=dict)
    capability_profile: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PolicyTransformIR:
    """Structured request- or plan-level policy transform placeholder."""

    transform_type: str
    source: str
    target: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisStepIR:
    """Minimal, execution-oriented representation of a typed analysis step."""

    index: int
    step_type: str
    params: dict[str, Any] = field(default_factory=dict)
    dependencies: list[int] = field(default_factory=list)
    step_category: str = "primitive"
    semantic_intent: SemanticIntent | None = None
    artifact_expectation: ArtifactExpectation | None = None
    execution_hints: dict[str, Any] = field(default_factory=dict)
    evidence_hints: dict[str, Any] = field(default_factory=dict)

    def table_name(self) -> str | None:
        explicit = self.params.get("table_name")
        if explicit:
            return str(explicit)
        hinted = self.execution_hints.get("default_table_name")
        if hinted:
            return str(hinted)
        if self.semantic_intent is not None and self.semantic_intent.source_table:
            return self.semantic_intent.source_table
        return None

    def routing_table_name(self) -> str | None:
        table_name = self.table_name()
        if table_name is None:
            return None
        return table_name.split(".")[-1]

    def metric_names(self) -> list[str]:
        if self.semantic_intent is None:
            return []
        return list(self.semantic_intent.metrics)

    def primary_metric_name(self) -> str | None:
        metric_names = self.metric_names()
        if not metric_names:
            return None
        return metric_names[0]

    def observation_types(self) -> list[str]:
        if self.artifact_expectation is None:
            return []
        return list(self.artifact_expectation.observation_types)

    def is_optional(self) -> bool:
        return bool(self.execution_hints.get("optional", False))


@dataclass(slots=True)
class ExecutionPlanIR:
    """Shared step container for planning and execution paths."""

    plan_id: str | None = None
    session_id: str | None = None
    status: str = "draft"
    request: AnalysisRequest = field(default_factory=AnalysisRequest)
    steps: list[AnalysisStepIR] = field(default_factory=list)
    semantic_resolutions: list[SemanticResolutionIR] = field(default_factory=list)
    execution_targets: list[ExecutionTargetIR] = field(default_factory=list)
    policy_transforms: list[PolicyTransformIR] = field(default_factory=list)

    def semantic_resolution_for_step(self, step_index: int) -> SemanticResolutionIR | None:
        for resolution in self.semantic_resolutions:
            if resolution.step_index == step_index:
                return resolution
        return None

    def execution_target_for_step(self, step_index: int) -> ExecutionTargetIR | None:
        for target in self.execution_targets:
            if target.step_index == step_index:
                return target
        return None


def request_from_legacy_session(
    session: Mapping[str, Any],
    *,
    plan_id: str | None = None,
    steps: Sequence[AnalysisStepIR | Mapping[str, Any]] | None = None,
) -> AnalysisRequest:
    """Adapt current session/step payloads into the richer request IR."""

    step_irs = _coerce_step_irs(steps or ())
    return AnalysisRequest(
        goal=str(session.get("goal", "")),
        session_id=_optional_str(session.get("session_id")),
        plan_id=plan_id,
        constraints=dict(session.get("constraints", {})),
        budget=dict(session.get("budget", {})),
        policy=dict(session.get("policy", {})),
        requested_step_types=[step.step_type for step in step_irs],
        requested_metrics=_dedupe_preserve_order(
            metric_name
            for step in step_irs
            for metric_name in step.metric_names()
        ),
        requested_tables=_dedupe_preserve_order(
            table_name
            for step in step_irs
            if (table_name := step.table_name()) is not None
        ),
    )


def from_legacy_step(index: int, step: Mapping[str, Any]) -> AnalysisStepIR:
    """Build IR from the current plan/step payload structure."""

    step_type = str(step["step_type"])
    params = dict(step.get("params", {}))
    return AnalysisStepIR(
        index=index,
        step_type=step_type,
        params=params,
        dependencies=list(step.get("dependencies", [])),
        step_category=_infer_step_category(step_type),
        semantic_intent=_infer_semantic_intent(step_type, params),
        artifact_expectation=_infer_artifact_expectation(step_type, params),
        execution_hints=_infer_execution_hints(step_type, params),
        evidence_hints=_infer_evidence_hints(step_type, params),
    )


def _coerce_step_irs(steps: Sequence[AnalysisStepIR | Mapping[str, Any]]) -> list[AnalysisStepIR]:
    coerced: list[AnalysisStepIR] = []
    for index, step in enumerate(steps):
        if isinstance(step, AnalysisStepIR):
            coerced.append(step)
            continue
        step_index = int(step.get("index", index))
        coerced.append(from_legacy_step(step_index, step))
    return coerced


def _dedupe_preserve_order(values: Sequence[str] | Any) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _infer_step_category(step_type: str) -> str:
    return step_category_for(step_type)


def _infer_semantic_intent(step_type: str, params: Mapping[str, Any]) -> SemanticIntent | None:
    metric_name = str(params.get("metric_name") or STEP_METRICS.get(step_type) or "").strip()
    raw_dimensions = params.get("dimensions")
    if isinstance(raw_dimensions, list):
        dimensions = [str(dimension) for dimension in raw_dimensions]
    else:
        dimensions = []
    filters = {
        key: params[key]
        for key in DEFAULT_SLICE_DIMENSIONS
        if key in params and params[key] is not None
    }
    source_table = str(params.get("table_name") or "") or None
    date_column = str(params.get("date_column", "event_date")) if step_type in PERIOD_CONTEXT_STEP_TYPES else None
    if not any([metric_name, dimensions, filters, source_table, date_column]):
        return None
    metrics = [metric_name] if metric_name else []
    return SemanticIntent(
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        source_table=source_table,
        date_column=date_column,
    )


def _infer_artifact_expectation(step_type: str, params: Mapping[str, Any]) -> ArtifactExpectation | None:
    artifact_kind = STEP_ARTIFACT_KINDS.get(step_type)
    if artifact_kind is None:
        return None
    observation_types = _infer_observation_types(step_type, params)
    artifact_key = _infer_artifact_key(step_type, params)
    return ArtifactExpectation(
        artifact_kind=artifact_kind,
        artifact_key=artifact_key,
        observation_types=observation_types,
        summary_required=step_type != "profile_table_columns",
    )


def _infer_execution_hints(step_type: str, params: Mapping[str, Any]) -> dict[str, Any]:
    default_table_name = None
    hints: dict[str, Any] = {
        "default_table_name": default_table_name,
        "requires_period_context": step_type in PERIOD_CONTEXT_STEP_TYPES,
        "optional": False,
    }
    explicit_table_name = params.get("table_name")
    if explicit_table_name:
        hints["explicit_table_name"] = str(explicit_table_name)
        hints["routing_table_name"] = str(explicit_table_name).split(".")[-1]
    elif default_table_name:
        hints["routing_table_name"] = str(default_table_name).split(".")[-1]
    if "limit" in params:
        hints["limit"] = params["limit"]
    return hints


def _infer_evidence_hints(step_type: str, params: Mapping[str, Any]) -> dict[str, Any]:
    observation_types = _infer_observation_types(step_type, params)
    hints: dict[str, Any] = {}
    if observation_types:
        hints["observation_types"] = observation_types
    metric_name = str(params.get("metric_name") or STEP_METRICS.get(step_type) or "").strip()
    if metric_name:
        hints["primary_metric"] = metric_name
    return hints


def _infer_observation_types(step_type: str, params: Mapping[str, Any]) -> list[str]:
    if step_type == "compare_metric":
        observation_type = str(params.get("observation_type", "metric_change")).strip()
        return [observation_type] if observation_type else []
    return list(STEP_OBSERVATION_TYPES.get(step_type, []))


def _infer_artifact_key(step_type: str, params: Mapping[str, Any]) -> str | None:
    if step_type == "compare_metric":
        metric_name = str(params.get("metric_name", "")).strip()
        if metric_name:
            return f"{metric_name}_comparison"
    if step_type == "profile_table":
        table_name = str(params.get("table_name", "")).strip()
        if table_name:
            return f"{table_name.split('.')[-1]}_profile"
    if step_type == "sample_rows":
        table_name = str(params.get("table_name", "")).strip()
        if table_name:
            return f"{table_name.split('.')[-1]}_sample"
    if step_type == "synthesize_findings":
        return "workflow_synthesis"
    return step_type
