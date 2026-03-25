from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal, Mapping


CompareKind = Literal["semantic_metric", "ad_hoc_aggregate"]
TimeScopeMode = Literal["single_window", "compare"]
TimeScopeGrain = Literal["day", "hour"]


@dataclass(slots=True)
class ResolvedTimeWindow:
    start: str
    end: str


@dataclass(slots=True)
class ResolvedTimeScope:
    mode: TimeScopeMode
    grain: TimeScopeGrain
    current: ResolvedTimeWindow
    baseline: ResolvedTimeWindow | None = None


@dataclass(slots=True)
class ResolvedScope:
    constraints: dict[str, Any] = field(default_factory=dict)
    predicate: str | None = None


@dataclass(slots=True)
class ResolvedTimeAxis:
    """Resolved time-axis contract shared by service and later compiler layers.

    `analysis_time_expr` controls correctness. `partition_pruning_predicate`
    controls pruning, executability, and cost. Both fields may remain unresolved
    in TSU-02 and will be populated by later resolver work.
    """

    observation_grain: TimeScopeGrain
    analysis_time_kind: str | None = None
    analysis_time_expr: str | None = None
    partition_pruning_predicate: str | None = None
    override_analysis_time_column: str | None = None
    override_partition_date_column: str | None = None
    override_partition_hour_column: str | None = None


@dataclass(slots=True)
class ResolvedMeasure:
    expr: str
    alias: str


@dataclass(slots=True)
class SemanticMetricValueSpec:
    metric: str


@dataclass(slots=True)
class AdHocAggregateValueSpec:
    measures: list[ResolvedMeasure]


@dataclass(slots=True)
class ResolvedWindowedQueryRequest:
    table: str
    compare_kind: CompareKind
    grouping: list[str]
    value_spec: SemanticMetricValueSpec | AdHocAggregateValueSpec
    time_scope: ResolvedTimeScope
    scope: ResolvedScope
    resolved_time_axis: ResolvedTimeAxis
    order: str | None = None
    limit: int | None = None


_COMPARE_METRIC_LEGACY_FIELDS = frozenset(
    {
        "metric_name",
        "table_name",
        "period_start",
        "period_end",
        "baseline_start",
        "baseline_end",
        "comparison_type",
        "date_column",
        "where",
        "filter",
    }
)

_AGGREGATE_QUERY_LEGACY_FIELDS = frozenset(
    {
        "table_name",
        "select",
        "where",
        "filter",
        "compare_period",
        "date_column",
        "order_by",
    }
)

_TIME_PREDICATE_PATTERN = re.compile(
    r"(?ix)"
    r"("
    r"\b(?:event_time|created_at|updated_at|event_date|log_date|event_hour|log_hour|dt_hour)\b"
    r"|"
    r"\b(?:current_date|current_timestamp|date_trunc|strftime)\s*\("
    r"|"
    r"\bextract\s*\("
    r"|"
    r"\btimestamp\b"
    r")"
)


def normalize_compare_metric_request(params: Mapping[str, Any]) -> ResolvedWindowedQueryRequest:
    _reject_legacy_fields(params, _COMPARE_METRIC_LEGACY_FIELDS, "compare_metric")
    table = _required_str(params, "table", "compare_metric")
    metric = _required_str(params, "metric", "compare_metric")
    time_scope = _normalize_time_scope(params.get("time_scope"), "compare_metric")
    scope = _normalize_scope(params.get("scope"))
    time_axis = _normalize_time_axis(params.get("time_axis"), time_scope.grain)
    return ResolvedWindowedQueryRequest(
        table=table,
        compare_kind="semantic_metric",
        grouping=_normalize_string_list(params.get("dimensions")),
        value_spec=SemanticMetricValueSpec(metric=metric),
        time_scope=time_scope,
        scope=scope,
        resolved_time_axis=time_axis,
        order=_optional_str(params.get("order")),
        limit=_optional_int(params.get("limit")),
    )


def normalize_aggregate_query_request(params: Mapping[str, Any]) -> ResolvedWindowedQueryRequest:
    _reject_legacy_fields(params, _AGGREGATE_QUERY_LEGACY_FIELDS, "aggregate_query")
    table = _required_str(params, "table", "aggregate_query")
    time_scope = _normalize_time_scope(params.get("time_scope"), "aggregate_query")
    scope = _normalize_scope(params.get("scope"))
    time_axis = _normalize_time_axis(params.get("time_axis"), time_scope.grain)
    raw_measures = params.get("measures")
    if not isinstance(raw_measures, list) or not raw_measures:
        raise ValueError("aggregate_query requires 'measures'")
    measures = [_normalize_measure(measure) for measure in raw_measures]
    return ResolvedWindowedQueryRequest(
        table=table,
        compare_kind="ad_hoc_aggregate",
        grouping=_normalize_string_list(params.get("group_by")),
        value_spec=AdHocAggregateValueSpec(measures=measures),
        time_scope=time_scope,
        scope=scope,
        resolved_time_axis=time_axis,
        order=_optional_str(params.get("order")),
        limit=_optional_int(params.get("limit")),
    )


def scope_predicate_contains_time_condition(predicate: str | None) -> bool:
    if predicate is None:
        return False
    return bool(_TIME_PREDICATE_PATTERN.search(predicate))


def _normalize_time_scope(payload: Any, step_type: str) -> ResolvedTimeScope:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{step_type} requires 'time_scope'")
    mode = _required_str(payload, "mode", step_type)
    grain = _required_str(payload, "grain", step_type)
    if mode not in {"single_window", "compare"}:
        raise ValueError("time_scope.mode must be 'single_window' or 'compare'")
    if grain not in {"day", "hour"}:
        raise ValueError("time_scope.grain must be 'day' or 'hour'")
    current = _normalize_time_window(payload.get("current"), "time_scope.current")
    baseline_payload = payload.get("baseline")
    if mode == "compare":
        if not isinstance(baseline_payload, Mapping):
            raise ValueError("time_scope.baseline is required when mode='compare'")
        baseline = _normalize_time_window(baseline_payload, "time_scope.baseline")
    else:
        if baseline_payload is not None:
            raise ValueError("time_scope.baseline is only allowed when mode='compare'")
        baseline = None
    return ResolvedTimeScope(mode=mode, grain=grain, current=current, baseline=baseline)


def _normalize_time_window(payload: Any, label: str) -> ResolvedTimeWindow:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} is required")
    return ResolvedTimeWindow(
        start=_required_str(payload, "start", label),
        end=_required_str(payload, "end", label),
    )


def _normalize_scope(payload: Any) -> ResolvedScope:
    if payload is None:
        return ResolvedScope()
    if not isinstance(payload, Mapping):
        raise ValueError("scope must be an object")
    constraints = payload.get("constraints") or {}
    if not isinstance(constraints, Mapping):
        raise ValueError("scope.constraints must be an object")
    predicate = _optional_str(payload.get("predicate"))
    if scope_predicate_contains_time_condition(predicate):
        raise ValueError("scope.predicate must not contain time-axis predicates; move time conditions into time_scope")
    return ResolvedScope(
        constraints={str(key): value for key, value in constraints.items()},
        predicate=predicate,
    )


def _normalize_time_axis(payload: Any, grain: TimeScopeGrain) -> ResolvedTimeAxis:
    if payload is None:
        return ResolvedTimeAxis(observation_grain=grain)
    if not isinstance(payload, Mapping):
        raise ValueError("time_axis must be an object")
    analysis_time = payload.get("analysis_time")
    partition_pruning = payload.get("partition_pruning")
    if analysis_time is not None and not isinstance(analysis_time, Mapping):
        raise ValueError("time_axis.analysis_time must be an object")
    if partition_pruning is not None and not isinstance(partition_pruning, Mapping):
        raise ValueError("time_axis.partition_pruning must be an object")
    return ResolvedTimeAxis(
        observation_grain=grain,
        override_analysis_time_column=_mapping_optional_str(analysis_time, "column"),
        override_partition_date_column=_mapping_optional_str(partition_pruning, "date_column"),
        override_partition_hour_column=_mapping_optional_str(partition_pruning, "hour_column"),
    )


def _normalize_measure(payload: Any) -> ResolvedMeasure:
    if not isinstance(payload, Mapping):
        raise ValueError("aggregate_query measures must be objects")
    expr = _required_str(payload, "expr", "measure")
    alias = _required_str(payload, "as", "measure")
    return ResolvedMeasure(expr=expr, alias=alias)


def _reject_legacy_fields(params: Mapping[str, Any], fields: set[str] | frozenset[str], step_type: str) -> None:
    legacy_fields = sorted(field for field in fields if field in params)
    if legacy_fields:
        joined = ", ".join(legacy_fields)
        raise ValueError(f"{step_type} no longer accepts legacy fields: {joined}")


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    return [str(item) for item in value if str(item).strip()]


def _required_str(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = _optional_str(payload.get(key))
    if value is None:
        raise ValueError(f"{label} requires '{key}'")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _mapping_optional_str(payload: Mapping[str, Any] | None, key: str) -> str | None:
    if payload is None:
        return None
    return _optional_str(payload.get(key))
