from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.runtime_contracts import DEFAULT_STEP_TABLES


DEFAULT_SLICE_DIMENSIONS = ("platform", "app_version", "network_type", "content_type")
COMPOSITE_STEP_TYPES = frozenset(
    {
        "compare_watch_time",
        "analyze_qoe",
        "analyze_ads",
        "analyze_recommendation",
        "synthesize_findings",
    }
)
OPTIONAL_STEP_TYPES = frozenset({"analyze_ads", "analyze_recommendation"})
PERIOD_CONTEXT_STEP_TYPES = frozenset(
    {
        "compare_metric",
        "compare_watch_time_top_slices",
        "compare_watch_time_overall",
        "analyze_qoe",
        "analyze_ads",
        "analyze_recommendation",
    }
)
STEP_METRICS = {
    "compare_watch_time": "watch_time",
    "compare_watch_time_top_slices": "watch_time",
    "compare_watch_time_overall": "watch_time",
    "analyze_qoe": "first_frame_time",
    "analyze_ads": "preroll_timeout_rate",
    "analyze_recommendation": "recommendation_ctr",
}
STEP_OBSERVATION_TYPES = {
    "compare_watch_time": ["metric_change"],
    "compare_watch_time_top_slices": ["metric_change"],
    "analyze_qoe": ["qoe_regression"],
    "analyze_ads": ["ad_regression"],
    "analyze_recommendation": ["recommendation_signal"],
    "profile_table": ["table_profile"],
    "sample_rows": ["sample_rows"],
    "synthesize_findings": ["root_cause_candidate", "recommendation"],
}
STEP_ARTIFACT_KINDS = {
    "compare_watch_time": "table",
    "compare_watch_time_top_slices": "table",
    "compare_watch_time_overall": "table",
    "analyze_qoe": "table",
    "analyze_ads": "table",
    "analyze_recommendation": "table",
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
    constraints: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)


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

    steps: list[AnalysisStepIR] = field(default_factory=list)


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


def _infer_step_category(step_type: str) -> str:
    if step_type in COMPOSITE_STEP_TYPES:
        return "composite"
    return "primitive"


def _infer_semantic_intent(step_type: str, params: Mapping[str, Any]) -> SemanticIntent | None:
    metric_name = str(params.get("metric_name") or STEP_METRICS.get(step_type) or "").strip()
    raw_dimensions = params.get("dimensions")
    if isinstance(raw_dimensions, list):
        dimensions = [str(dimension) for dimension in raw_dimensions]
    elif step_type in {
        "compare_watch_time",
        "compare_watch_time_top_slices",
        "analyze_qoe",
        "analyze_ads",
        "analyze_recommendation",
    }:
        dimensions = list(DEFAULT_SLICE_DIMENSIONS)
    else:
        dimensions = []
    filters = {
        key: params[key]
        for key in DEFAULT_SLICE_DIMENSIONS
        if key in params and params[key] is not None
    }
    source_table = str(params.get("table_name") or DEFAULT_STEP_TABLES.get(step_type) or "") or None
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
    default_table_name = DEFAULT_STEP_TABLES.get(step_type)
    hints: dict[str, Any] = {
        "default_table_name": default_table_name,
        "requires_period_context": step_type in PERIOD_CONTEXT_STEP_TYPES,
        "optional": step_type in OPTIONAL_STEP_TYPES,
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
