from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, cast

from marivo.time_axis_metadata import normalize_time_capabilities
from marivo.time_contracts import (
    normalize_hour_boundary,
    normalize_timestamp_format,
    window_length_in_grain,
)

CompareKind = Literal["semantic_metric", "ad_hoc_aggregate"]
TimeScopeMode = Literal["single_window", "compare"]
TimeScopeGrain = Literal["hour", "day", "week", "month", "quarter", "year"]
_TIME_SCOPE_GRAINS = frozenset({"hour", "day", "week", "month", "quarter", "year"})
_DATE_LIKE_GRAINS = frozenset({"day", "week", "month", "quarter", "year"})
_TIME_GRANULARITY_ORDER = {
    "hour": 0,
    "day": 1,
    "week": 2,
    "month": 3,
    "quarter": 4,
    "year": 5,
}
_TIME_SCOPE_GRAIN_MESSAGE = (
    "time_scope.grain must be one of 'hour', 'day', 'week', 'month', 'quarter', 'year'"
)


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
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedScope:
    constraints: dict[str, Any] = field(default_factory=dict)
    predicate: str | None = None
    predicate_ref: str | None = None


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
    analysis_time_format: str | None = None
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


_METRIC_QUERY_FIELDS = frozenset(
    {
        "table",
        "metric",
        "dimensions",
        "time_scope",
        "scope",
        "time_axis",
        "time_scope_field",
        "scoped_query",
        "order",
        "limit",
    }
)

_AGGREGATE_QUERY_FIELDS = frozenset(
    {
        "table",
        "group_by",
        "measures",
        "time_scope",
        "scope",
        "time_axis",
        "scoped_query",
        "order",
        "limit",
    }
)

_TIME_PREDICATE_PATTERN = re.compile(
    r"(?ix)"
    r"("
    r"\b(?:current_date|current_timestamp|date_trunc|strftime)\s*\("
    r"|"
    r"\bextract\s*\("
    r"|"
    r"\btimestamp\b"
    r")"
)


def normalize_metric_query_request(params: Mapping[str, Any]) -> ResolvedWindowedQueryRequest:
    _reject_unknown_fields(params, _METRIC_QUERY_FIELDS, "metric_query")
    table = _required_str(params, "table", "metric_query")
    metric = _required_str(params, "metric", "metric_query")
    time_scope = _normalize_time_scope(params.get("time_scope"), "metric_query")
    scope = _normalize_scope(params.get("scope"))
    time_axis = _normalize_time_axis(params.get("time_axis"), time_scope.grain)
    time_scope_field = _optional_str(params.get("time_scope_field"))
    if time_scope_field is not None:
        time_axis.override_analysis_time_column = time_scope_field
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
    _reject_unknown_fields(params, _AGGREGATE_QUERY_FIELDS, "aggregate_query")
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
    return TimeScopeResolver(step_type=step_type).resolve(payload)


@dataclass(slots=True)
class TimeScopeResolver:
    step_type: str

    def resolve(self, payload: Any) -> ResolvedTimeScope:
        if not isinstance(payload, Mapping):
            raise ValueError(f"{self.step_type} requires 'time_scope'")
        mode = _required_str(payload, "mode", self.step_type)
        grain = _required_str(payload, "grain", self.step_type)
        if mode not in {"single_window", "compare"}:
            raise ValueError("time_scope.mode must be 'single_window' or 'compare'")
        if grain not in _TIME_SCOPE_GRAINS:
            raise ValueError(_TIME_SCOPE_GRAIN_MESSAGE)
        mode_typed = cast("TimeScopeMode", mode)
        grain_typed = cast("TimeScopeGrain", grain)

        current = self._normalize_time_window(
            payload.get("current"), "time_scope.current", grain_typed
        )
        baseline_payload = payload.get("baseline")
        if mode == "compare":
            if not isinstance(baseline_payload, Mapping):
                raise ValueError("time_scope.baseline is required when mode='compare'")
            baseline = self._normalize_time_window(
                payload.get("baseline"), "time_scope.baseline", grain_typed
            )
        else:
            if baseline_payload is not None:
                raise ValueError("time_scope.baseline is only allowed when mode='compare'")
            baseline = None

        warnings: list[dict[str, Any]] = []
        if baseline is not None:
            current_duration = self._window_duration(current, grain_typed)
            baseline_duration = self._window_duration(baseline, grain_typed)
            if current_duration != baseline_duration:
                warnings.append(
                    {
                        "code": "window_length_mismatch",
                        "message": "current and baseline windows have different lengths",
                        "grain": grain,
                        "current_duration": current_duration,
                        "baseline_duration": baseline_duration,
                    }
                )
        return ResolvedTimeScope(
            mode=mode_typed,
            grain=grain_typed,
            current=current,
            baseline=baseline,
            warnings=warnings,
        )

    def _normalize_time_window(
        self,
        payload: Any,
        label: str,
        grain: TimeScopeGrain,
    ) -> ResolvedTimeWindow:
        if not isinstance(payload, Mapping):
            raise ValueError(f"{label} is required")
        start = self._normalize_boundary(
            _required_str(payload, "start", label), f"{label}.start", grain
        )
        end = self._normalize_boundary(_required_str(payload, "end", label), f"{label}.end", grain)
        self._ensure_aligned_boundary(start, f"{label}.start", grain)
        self._ensure_aligned_boundary(end, f"{label}.end", grain)
        if grain in _DATE_LIKE_GRAINS:
            if date.fromisoformat(start) >= date.fromisoformat(end):
                raise ValueError(f"{label} requires start < end")
        else:
            if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
                raise ValueError(f"{label} requires start < end")
        return ResolvedTimeWindow(start=start, end=end)

    @staticmethod
    def _normalize_boundary(value: str, label: str, grain: TimeScopeGrain) -> str:
        normalized = value.strip()
        if grain in _DATE_LIKE_GRAINS:
            try:
                return date.fromisoformat(normalized).isoformat()
            except ValueError:
                pass
            try:
                return datetime.fromisoformat(normalized).date().isoformat()
            except ValueError as exc:
                raise ValueError(f"{label} must be a date or datetime string") from exc

        normalized_hour = normalize_hour_boundary(normalized, label=label)
        hour_input = normalized
        if "T" not in hour_input and " " not in hour_input:
            hour_input = f"{hour_input}T00:00:00"
        parsed_input = datetime.fromisoformat(hour_input.replace("Z", "+00:00"))
        if parsed_input.minute != 0 or parsed_input.second != 0 or parsed_input.microsecond != 0:
            raise ValueError(f"{label} must align to hour grain (whole hour)")
        return normalized_hour

    @staticmethod
    def _ensure_aligned_boundary(value: str, label: str, grain: TimeScopeGrain) -> None:
        if grain == "hour":
            parsed = datetime.fromisoformat(value)
            if parsed.minute != 0 or parsed.second != 0 or parsed.microsecond != 0:
                raise ValueError(f"{label} must align to hour grain (whole hour)")
            return

        parsed_date = date.fromisoformat(value)
        if grain == "day":
            return
        if grain == "week" and parsed_date.weekday() == 0:
            return
        if grain == "month" and parsed_date.day == 1:
            return
        if grain == "quarter" and parsed_date.day == 1 and parsed_date.month in {1, 4, 7, 10}:
            return
        if grain == "year" and parsed_date.month == 1 and parsed_date.day == 1:
            return
        hints = {
            "week": "Monday",
            "month": "the first day of a month",
            "quarter": "the first day of Jan, Apr, Jul, or Oct",
            "year": "Jan 1",
        }
        raise ValueError(f"{label} must align to {grain} grain ({hints.get(grain, grain)})")

    @staticmethod
    def _window_duration(window: ResolvedTimeWindow, grain: TimeScopeGrain) -> int:
        return window_length_in_grain(window.start, window.end, grain=grain)


@dataclass(slots=True)
class _TimeCapabilities:
    timestamp_column: str | None = None
    timestamp_format: str | None = None
    fallback_date_column: str | None = None
    fallback_hour_column: str | None = None
    partition_date_column: str | None = None
    partition_date_format: str | None = None
    partition_hour_column: str | None = None
    partition_hour_format: str | None = None
    default_compare_grain: TimeScopeGrain | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> _TimeCapabilities:
        normalized_payload = normalize_time_capabilities(payload)
        if not isinstance(normalized_payload, Mapping):
            return cls()
        analysis_time = normalized_payload.get("analysis_time")
        partition_time = normalized_payload.get("partition_time")
        if not isinstance(analysis_time, Mapping):
            analysis_time = {}
        if not isinstance(partition_time, Mapping):
            partition_time = {}
        return cls(
            timestamp_column=_optional_str(analysis_time.get("timestamp_column")),
            timestamp_format=_normalize_timestamp_format(analysis_time.get("timestamp_format")),
            fallback_date_column=_optional_str(analysis_time.get("fallback_date_column")),
            fallback_hour_column=_optional_str(analysis_time.get("fallback_hour_column")),
            partition_date_column=_optional_str(partition_time.get("date_column")),
            partition_date_format=_normalize_date_format(partition_time.get("date_format")),
            partition_hour_column=_optional_str(partition_time.get("hour_column")),
            partition_hour_format=_normalize_hour_format(partition_time.get("hour_format")),
            default_compare_grain=cast(
                "TimeScopeGrain | None",
                _optional_str(normalized_payload.get("default_compare_grain")),
            ),
        )


@dataclass(slots=True)
class _AnalysisAxis:
    kind: str
    expr: str
    column: str | None = None
    date_column: str | None = None
    date_format: str | None = None
    hour_column: str | None = None
    hour_format: str | None = None


@dataclass(slots=True)
class _PartitionAxis:
    date_column: str | None = None
    date_format: str | None = None
    hour_column: str | None = None
    hour_format: str | None = None


@dataclass(slots=True)
class TimeAxisResolver:
    request: ResolvedWindowedQueryRequest
    engine_type: str
    available_columns: Sequence[str] = ()
    entity_time_capabilities: Mapping[str, Any] | None = None
    source_time_capabilities: Mapping[str, Any] | None = None
    time_field_expressions: Mapping[str, str] | None = None
    time_field_data_types: Mapping[str, str] | None = None
    time_field_support_min_granularities: Mapping[str, str] | None = None

    def resolve(self) -> ResolvedTimeAxis:
        columns = _normalize_columns(self.available_columns)
        metadata_chain = [
            _TimeCapabilities.from_mapping(self.entity_time_capabilities),
            _TimeCapabilities.from_mapping(self.source_time_capabilities),
        ]
        analysis = self._resolve_analysis_axis(columns, metadata_chain)
        self._validate_supported_granularity(analysis)
        pruning = self._resolve_partition_axis(columns, metadata_chain, analysis)
        return ResolvedTimeAxis(
            observation_grain=self.request.time_scope.grain,
            analysis_time_kind=analysis.kind,
            analysis_time_expr=analysis.expr,
            analysis_time_format=analysis.date_format,
            partition_pruning_predicate=self._build_partition_pruning_predicate(pruning),
            override_analysis_time_column=self.request.resolved_time_axis.override_analysis_time_column,
            override_partition_date_column=self.request.resolved_time_axis.override_partition_date_column,
            override_partition_hour_column=self.request.resolved_time_axis.override_partition_hour_column,
        )

    def _resolve_analysis_axis(
        self,
        columns: tuple[str, ...],
        metadata_chain: list[_TimeCapabilities],
    ) -> _AnalysisAxis:
        override = self.request.resolved_time_axis.override_analysis_time_column
        if override is not None:
            self._ensure_known_column(override, columns, "time_axis.analysis_time.column")
            self._validate_field_granularity(override)
            field_expression = _mapping_optional_str(self.time_field_expressions, override)
            field_data_type = _mapping_optional_str(self.time_field_data_types, override)
            if field_expression is not None and field_expression != override:
                return self._analysis_axis_from_time_field_expression(
                    field_name=override,
                    expression=field_expression,
                    data_type=field_data_type,
                    columns=columns,
                    metadata_chain=metadata_chain,
                )
            if _sql_type_is_date(field_data_type):
                if self.request.time_scope.grain == "hour":
                    raise ValueError(
                        "time_axis.analysis_time.column must be hour-compatible for hour grain; "
                        "select a time field whose support_min_granularity is 'hour'"
                    )
                date_format = self._analysis_date_format(metadata_chain, override)
                return _AnalysisAxis(
                    kind="date_field",
                    expr=self._date_field_analysis_expr(override, date_format),
                    column=override,
                    date_column=override,
                    date_format=date_format,
                )
            if _sql_type_is_timestamp(field_data_type):
                return _AnalysisAxis(kind="timestamp", expr=override, column=override)
            for caps in metadata_chain:
                if caps.timestamp_column == override:
                    timestamp_expr = _timestamp_field_expr(
                        override,
                        caps.timestamp_format,
                        engine_type=self.engine_type,
                    )
                    return _AnalysisAxis(kind="timestamp", expr=timestamp_expr, column=override)
                if caps.fallback_date_column == override:
                    date_format = self._analysis_date_format(metadata_chain, override)
                    return _AnalysisAxis(
                        kind="date_field",
                        expr=self._date_field_analysis_expr(override, date_format),
                        column=override,
                        date_column=override,
                        date_format=date_format,
                    )
            raise ValueError(
                f"time_axis.analysis_time.column '{override}' requires explicit "
                "time field data_type or time_capabilities metadata"
            )

        for caps in metadata_chain:
            if caps.timestamp_column is not None:
                self._ensure_known_column(
                    caps.timestamp_column,
                    columns,
                    "time_capabilities.analysis_time.timestamp_column",
                )
                timestamp_expr = _timestamp_field_expr(
                    caps.timestamp_column,
                    caps.timestamp_format,
                    engine_type=self.engine_type,
                )
                return _AnalysisAxis(
                    kind="timestamp",
                    expr=timestamp_expr,
                    column=caps.timestamp_column,
                )

        for caps in metadata_chain:
            if self.request.time_scope.grain == "hour":
                if caps.fallback_date_column and caps.fallback_hour_column:
                    self._ensure_known_column(
                        caps.fallback_date_column,
                        columns,
                        "time_capabilities.analysis_time.fallback_date_column",
                    )
                    self._ensure_known_column(
                        caps.fallback_hour_column,
                        columns,
                        "time_capabilities.analysis_time.fallback_hour_column",
                    )
                    return _AnalysisAxis(
                        kind="partition_fields",
                        expr=self._partition_hour_analysis_expr(
                            caps.fallback_date_column,
                            caps.fallback_hour_column,
                            self._analysis_date_format(metadata_chain, caps.fallback_date_column),
                        ),
                        date_column=caps.fallback_date_column,
                        date_format=self._analysis_date_format(
                            metadata_chain, caps.fallback_date_column
                        ),
                        hour_column=caps.fallback_hour_column,
                        hour_format=self._analysis_hour_format(
                            metadata_chain, caps.fallback_hour_column
                        ),
                    )
            elif caps.fallback_date_column:
                self._ensure_known_column(
                    caps.fallback_date_column,
                    columns,
                    "time_capabilities.analysis_time.fallback_date_column",
                )
                date_format = self._analysis_date_format(metadata_chain, caps.fallback_date_column)
                return _AnalysisAxis(
                    kind="date_field",
                    expr=self._date_field_analysis_expr(caps.fallback_date_column, date_format),
                    column=caps.fallback_date_column,
                    date_column=caps.fallback_date_column,
                    date_format=date_format,
                )

        raise ValueError(
            f"could not resolve a time axis for {self.request.table}; "
            "provide time_axis override or explicit time_capabilities metadata"
        )

    def _analysis_axis_from_time_field_expression(
        self,
        *,
        field_name: str,
        expression: str,
        data_type: str | None,
        columns: tuple[str, ...],
        metadata_chain: list[_TimeCapabilities],
    ) -> _AnalysisAxis:
        if _is_simple_identifier(expression):
            if _sql_type_is_date(data_type):
                if self.request.time_scope.grain == "hour":
                    raise ValueError(
                        "time_scope.field expression must be hour-compatible for hour grain; "
                        "select a time field whose support_min_granularity is 'hour'"
                    )
                date_format = self._analysis_date_format(metadata_chain, expression)
                return _AnalysisAxis(
                    kind="date_field",
                    expr=self._date_field_analysis_expr(expression, date_format),
                    column=field_name,
                    date_column=expression,
                    date_format=date_format,
                )
            if _sql_type_is_timestamp(data_type):
                return _AnalysisAxis(kind="timestamp", expr=expression, column=field_name)
            raise ValueError(f"time field '{field_name}' expression requires explicit data_type")

        if _sql_type_is_timestamp(data_type) or _expression_returns_timestamp(expression):
            return _AnalysisAxis(
                kind="timestamp",
                expr=_timestamp_expression_for_engine(expression, engine_type=self.engine_type),
                column=field_name,
            )
        if _sql_type_is_date(data_type) or _expression_returns_date(expression):
            return _AnalysisAxis(kind="date_expression", expr=expression, column=field_name)
        raise ValueError(
            f"time field '{field_name}' expression requires explicit data_type "
            "or a timestamp/date cast"
        )

    def _validate_supported_granularity(self, analysis: _AnalysisAxis) -> None:
        field_name = analysis.column or analysis.date_column
        if field_name is None:
            raise ValueError(
                f"time field for {self.request.table} must declare support_min_granularity"
            )
        self._validate_field_granularity(field_name)

    def _validate_field_granularity(self, field_name: str) -> None:
        support_min_granularity = _mapping_optional_str(
            self.time_field_support_min_granularities,
            field_name,
        )
        if support_min_granularity is None:
            raise ValueError(f"time field '{field_name}' must declare support_min_granularity")
        if support_min_granularity not in _TIME_GRANULARITY_ORDER:
            raise ValueError(
                f"time field '{field_name}' has invalid support_min_granularity "
                f"'{support_min_granularity}'"
            )
        requested = self.request.time_scope.grain
        if _TIME_GRANULARITY_ORDER[requested] < _TIME_GRANULARITY_ORDER[support_min_granularity]:
            raise ValueError(
                f"time field '{field_name}' supports minimum granularity "
                f"'{support_min_granularity}' and cannot satisfy requested granularity "
                f"'{requested}'"
            )

    def _partition_hour_axis_for_date_column(
        self,
        date_column: str,
        columns: tuple[str, ...],
        metadata_chain: list[_TimeCapabilities],
        *,
        field_name: str | None = None,
    ) -> _AnalysisAxis | None:
        hour_column = self._hour_column_for_date_column(date_column, columns, metadata_chain)
        if hour_column is None:
            return None
        self._ensure_known_column(hour_column, columns, "time_axis.analysis_time.hour_column")
        date_format = self._analysis_date_format(metadata_chain, date_column)
        return _AnalysisAxis(
            kind="partition_fields",
            expr=self._partition_hour_analysis_expr(date_column, hour_column, date_format),
            column=field_name or date_column,
            date_column=date_column,
            date_format=date_format,
            hour_column=hour_column,
            hour_format=self._analysis_hour_format(metadata_chain, hour_column),
        )

    @staticmethod
    def _hour_column_for_date_column(
        date_column: str,
        columns: tuple[str, ...],
        metadata_chain: list[_TimeCapabilities],
    ) -> str | None:
        for caps in metadata_chain:
            if caps.fallback_date_column == date_column and caps.fallback_hour_column is not None:
                return caps.fallback_hour_column
            if caps.partition_date_column == date_column and caps.partition_hour_column is not None:
                return caps.partition_hour_column
        return None

    def _resolve_partition_axis(
        self,
        columns: tuple[str, ...],
        metadata_chain: list[_TimeCapabilities],
        analysis: _AnalysisAxis,
    ) -> _PartitionAxis | None:
        override_date = self.request.resolved_time_axis.override_partition_date_column
        override_hour = self.request.resolved_time_axis.override_partition_hour_column
        if override_date is not None:
            self._ensure_known_column(
                override_date, columns, "time_axis.partition_pruning.date_column"
            )
        if override_hour is not None:
            self._ensure_known_column(
                override_hour, columns, "time_axis.partition_pruning.hour_column"
            )

        date_column = override_date
        hour_column = override_hour
        date_format = None
        hour_format = _default_hour_format_for_column(hour_column) if hour_column else None

        if date_column is None:
            for caps in metadata_chain:
                if caps.partition_date_column is not None:
                    self._ensure_known_column(
                        caps.partition_date_column,
                        columns,
                        "time_capabilities.partition_time.date_column",
                    )
                    date_column = caps.partition_date_column
                    date_format = caps.partition_date_format
                    break
        if hour_column is None:
            for caps in metadata_chain:
                if caps.partition_hour_column is not None:
                    self._ensure_known_column(
                        caps.partition_hour_column,
                        columns,
                        "time_capabilities.partition_time.hour_column",
                    )
                    hour_column = caps.partition_hour_column
                    hour_format = caps.partition_hour_format or _default_hour_format_for_column(
                        hour_column
                    )
                    break

        if date_column is None and analysis.date_column is not None:
            date_column = analysis.date_column
            date_format = analysis.date_format
        if hour_column is None and analysis.hour_column is not None:
            hour_column = analysis.hour_column
            hour_format = analysis.hour_format or _default_hour_format_for_column(hour_column)

        if date_column is None:
            return None
        if self.request.time_scope.grain == "hour" and hour_column is None:
            return _PartitionAxis(date_column=date_column, date_format=date_format)
        if hour_column is not None and date_column is None:
            raise ValueError("partition pruning hour_column requires a date_column")
        return _PartitionAxis(
            date_column=date_column,
            date_format=date_format,
            hour_column=hour_column,
            hour_format=hour_format,
        )

    def _build_partition_pruning_predicate(self, axis: _PartitionAxis | None) -> str | None:
        if axis is None or axis.date_column is None:
            return None
        if self.request.time_scope.grain != "hour":
            return self._build_day_partition_pruning_predicate(axis)

        return self._build_hour_partition_pruning_predicate(axis)

    def _build_day_partition_pruning_predicate(self, axis: _PartitionAxis) -> str:
        start_day, end_day = self._day_envelope()
        return (
            f"{axis.date_column} >= '{self._format_partition_date_literal(start_day, axis.date_format)}' "
            f"AND {axis.date_column} < '{self._format_partition_date_literal(end_day, axis.date_format)}'"
        )

    def _build_hour_partition_pruning_predicate(self, axis: _PartitionAxis) -> str:
        start_dt, end_dt = self._hour_envelope()
        if axis.hour_column is None:
            last_day = (end_dt - timedelta(seconds=1)).date()
            return (
                f"{axis.date_column} >= '{self._format_partition_date_literal(start_dt.date(), axis.date_format)}' "
                f"AND {axis.date_column} < '{self._format_partition_date_literal(last_day + timedelta(days=1), axis.date_format)}'"
            )

        start_day = start_dt.date()
        last_day = (end_dt - timedelta(seconds=1)).date()
        if start_day == last_day:
            return self._build_same_day_hour_partition_pruning(
                axis,
                start_day=start_day,
                start_hour=start_dt.hour,
                end_day=end_dt.date(),
                end_hour=end_dt.hour,
            )
        return self._build_cross_day_hour_partition_pruning(
            axis,
            start_day=start_day,
            start_hour=start_dt.hour,
            last_day=last_day,
            end_dt=end_dt,
        )

    def _build_same_day_hour_partition_pruning(
        self,
        axis: _PartitionAxis,
        *,
        start_day: date,
        start_hour: int,
        end_day: date,
        end_hour: int,
    ) -> str:
        parts = [
            f"{axis.date_column} = '{self._format_partition_date_literal(start_day, axis.date_format)}'",
            f"{axis.hour_column} >= '{self._format_partition_hour_literal(start_hour, axis.hour_format)}'",
        ]
        if end_day == start_day:
            parts.append(
                f"{axis.hour_column} < '{self._format_partition_hour_literal(end_hour, axis.hour_format)}'"
            )
        return " AND ".join(parts)

    def _build_cross_day_hour_partition_pruning(
        self,
        axis: _PartitionAxis,
        *,
        start_day: date,
        start_hour: int,
        last_day: date,
        end_dt: datetime,
    ) -> str:
        clauses = [
            (
                f"{axis.date_column} = '{self._format_partition_date_literal(start_day, axis.date_format)}' "
                f"AND {axis.hour_column} >= '{self._format_partition_hour_literal(start_hour, axis.hour_format)}'"
            )
        ]
        if start_day + timedelta(days=1) <= last_day - timedelta(days=1):
            clauses.append(
                f"{axis.date_column} > '{self._format_partition_date_literal(start_day, axis.date_format)}' "
                f"AND {axis.date_column} < '{self._format_partition_date_literal(last_day, axis.date_format)}'"
            )
        if end_dt.time() == time(0, 0):
            clauses.append(
                f"{axis.date_column} = '{self._format_partition_date_literal(last_day, axis.date_format)}'"
            )
        else:
            clauses.append(
                f"{axis.date_column} = '{self._format_partition_date_literal(last_day, axis.date_format)}' "
                f"AND {axis.hour_column} < '{self._format_partition_hour_literal(end_dt.hour, axis.hour_format)}'"
            )
        return "(" + ") OR (".join(clauses) + ")"

    def _partition_hour_analysis_expr(
        self, date_column: str, hour_column: str, date_format: str | None
    ) -> str:
        date_text_expr = _partition_date_text_expr(
            date_column,
            date_format,
            engine_type=self.engine_type,
        )
        hour_text_expr = _partition_hour_text_expr(hour_column, engine_type=self.engine_type)
        return _partition_hour_timestamp_expr(
            date_text_expr,
            hour_text_expr,
            engine_type=self.engine_type,
        )

    def _date_field_analysis_expr(self, date_column: str, date_format: str | None) -> str:
        """Build a DATE expression for analysis time axis from a date column.

        Handles yyyymmdd format by converting to ISO format before casting to DATE.
        """
        return _date_field_expr(date_column, date_format, engine_type=self.engine_type)

    def _format_partition_date_literal(self, value: date, date_format: str | None) -> str:
        return _format_partition_date(value, date_format, engine_type=self.engine_type)

    def _format_partition_hour_literal(self, value: int, hour_format: str | None) -> str:
        return _format_partition_hour(value, hour_format, engine_type=self.engine_type)

    def _day_envelope(self) -> tuple[date, date]:
        windows = [self.request.time_scope.current]
        if self.request.time_scope.baseline is not None:
            windows.append(self.request.time_scope.baseline)
        starts = [date.fromisoformat(window.start) for window in windows]
        ends = [date.fromisoformat(window.end) for window in windows]
        return min(starts), max(ends)

    def _hour_envelope(self) -> tuple[datetime, datetime]:
        windows = [self.request.time_scope.current]
        if self.request.time_scope.baseline is not None:
            windows.append(self.request.time_scope.baseline)
        starts = [datetime.fromisoformat(window.start) for window in windows]
        ends = [datetime.fromisoformat(window.end) for window in windows]
        return min(starts), max(ends)

    @staticmethod
    def _ensure_known_column(column: str, columns: tuple[str, ...], label: str) -> None:
        if columns and column not in set(columns):
            raise ValueError(f"{label} references unknown column '{column}'")

    @staticmethod
    def _analysis_date_format(
        metadata_chain: list[_TimeCapabilities], date_column: str | None
    ) -> str | None:
        for caps in metadata_chain:
            if caps.partition_date_column == date_column and caps.partition_date_format is not None:
                return caps.partition_date_format
        return None

    @staticmethod
    def _analysis_hour_format(
        metadata_chain: list[_TimeCapabilities], hour_column: str | None
    ) -> str | None:
        for caps in metadata_chain:
            if caps.partition_hour_column == hour_column and caps.partition_hour_format is not None:
                return caps.partition_hour_format
        return _default_hour_format_for_column(hour_column)


def _normalize_scope(payload: Any) -> ResolvedScope:
    if payload is None:
        return ResolvedScope()
    if not isinstance(payload, Mapping):
        raise ValueError("scope must be an object")
    constraints = payload.get("constraints") or {}
    if not isinstance(constraints, Mapping):
        raise ValueError("scope.constraints must be an object")
    predicate_ref = _optional_str(payload.get("predicate_ref"))
    predicate_raw = _optional_str(payload.get("predicate"))
    if scope_predicate_contains_time_condition(predicate_raw):
        raise ValueError(
            "scope.predicate must not contain time-axis predicates; move time conditions into time_scope"
        )
    return ResolvedScope(
        constraints={str(key): value for key, value in constraints.items()},
        predicate=predicate_raw,
        predicate_ref=predicate_ref,
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


def _reject_unknown_fields(
    params: Mapping[str, Any], allowed_fields: set[str] | frozenset[str], step_type: str
) -> None:
    unknown_fields = sorted(field for field in params if field not in allowed_fields)
    if unknown_fields:
        joined = ", ".join(unknown_fields)
        raise ValueError(f"{step_type} contains unsupported fields: {joined}")


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


def _normalize_columns(columns: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for column in columns:
        name = str(column).strip()
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return tuple(normalized)


def _is_simple_identifier(expression: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expression.strip()))


def _normalized_sql_type(data_type: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (data_type or "").lower())


def _sql_type_is_date(data_type: str | None) -> bool:
    normalized = _normalized_sql_type(data_type)
    return normalized == "date"


def _sql_type_is_timestamp(data_type: str | None) -> bool:
    normalized = _normalized_sql_type(data_type)
    return normalized in {
        "timestamp",
        "timestampwithtimezone",
        "timestamptz",
        "datetime",
    }


def _expression_returns_date(expression: str) -> bool:
    return bool(re.search(r"(?is)\bAS\s+DATE\b", expression))


def _expression_returns_timestamp(expression: str) -> bool:
    return bool(
        re.search(r"(?is)\bAS\s+TIMESTAMP(?:\s+WITH\s+TIME\s+ZONE)?\b", expression)
        or re.search(r"(?is)\b(?:DATE_PARSE|STRPTIME|TO_TIMESTAMP)\s*\(", expression)
    )


def _timestamp_expression_for_engine(expression: str, *, engine_type: str) -> str:
    if engine_type.strip().lower() != "trino":
        return expression
    match = re.fullmatch(
        r"(?is)CAST\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s+AS\s+TIMESTAMP\s*\)",
        expression.strip(),
    )
    if match is None:
        return expression
    return _custom_format_timestamp_expr(
        match.group(1),
        "%Y-%m-%d %H:%M:%S",
        engine_type="trino",
    )


def _normalize_date_format(value: Any) -> str | None:
    normalized = _optional_str(value)
    if normalized is None:
        return None
    lowered = normalized.lower().replace("_", "").replace("-", "")
    if lowered in {"yyyymmdd"}:
        return "yyyymmdd"
    if lowered in {"iso", "yyyymmddhh", "yyyymmddthh"}:
        return normalized.lower()
    if lowered in {"yyyymmdddate", "yyyymmddday"}:
        return "yyyymmdd"
    return normalized.lower()


def _normalize_hour_format(value: Any) -> str | None:
    normalized = _optional_str(value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered in {"hh", "h", "int"}:
        return lowered
    return lowered


def _normalize_timestamp_format(value: Any) -> str | None:
    return normalize_timestamp_format(value)


def _default_hour_format_for_column(column: str | None) -> str | None:
    if column is None:
        return None
    return "hh"


def _varchar_cast_expr(column: str, *, engine_type: str) -> str:
    del engine_type  # DuckDB and Trino both accept VARCHAR casts in phase 1.
    return f"CAST({column} AS VARCHAR)"


def _partition_date_text_expr(column: str, date_format: str | None, *, engine_type: str) -> str:
    raw = _varchar_cast_expr(column, engine_type=engine_type)
    if date_format == "yyyymmdd":
        return f"CONCAT(SUBSTR({raw}, 1, 4), '-', SUBSTR({raw}, 5, 2), '-', SUBSTR({raw}, 7, 2))"
    return raw


def _date_field_expr(column: str, date_format: str | None, *, engine_type: str) -> str:
    """Build a DATE expression suitable for DATE_TRUNC from a date column.

    For yyyymmdd format (e.g., "20260410"), converts to ISO date format and casts to DATE.
    For ISO format or unknown format, casts the column directly to DATE.
    """
    if date_format == "yyyymmdd":
        # yyyymmdd format needs conversion to ISO format before casting
        text_expr = _partition_date_text_expr(column, date_format, engine_type=engine_type)
        return f"CAST({text_expr} AS DATE)"
    # For ISO format or unknown format, cast directly to DATE
    return f"CAST({column} AS DATE)"


def _partition_hour_text_expr(column: str, *, engine_type: str) -> str:
    return f"LPAD({_varchar_cast_expr(column, engine_type=engine_type)}, 2, '0')"


# Format translation tables for custom timestamp formats.
# Users provide strftime-style format strings (e.g., '%Y%m%d %H:%M:%S').
# Engines have different syntax requirements.

_STRFTIME_TO_DUCKDB: dict[str, str] = {
    "%Y": "%Y",  # Year (4 digits)
    "%y": "%y",  # Year (2 digits)
    "%m": "%m",  # Month (01-12)
    "%d": "%d",  # Day (01-31)
    "%H": "%H",  # Hour 24-hour (00-23)
    "%I": "%I",  # Hour 12-hour (01-12)
    "%M": "%M",  # Minute (00-59)
    "%S": "%S",  # Second (00-59)
    "%p": "%p",  # AM/PM
    "%f": "%f",  # Microseconds
}

_STRFTIME_TO_TRINO: dict[str, str] = {
    "%Y": "%Y",  # Year (4 digits)
    "%y": "%y",  # Year (2 digits)
    "%m": "%m",  # Month (01-12)
    "%d": "%d",  # Day (01-31)
    "%H": "%H",  # Hour 24-hour (00-23)
    "%I": "%h",  # Hour 12-hour (01-12)
    "%M": "%i",  # Minute (00-59)
    "%S": "%s",  # Second (00-59)
    "%p": "%p",  # AM/PM marker
    "%f": "%f",  # Fractional seconds
}


def _translate_format_for_duckdb(strftime_format: str) -> str:
    """Translate strftime-style format to DuckDB STRPTIME format.

    DuckDB's STRPTIME uses strftime-like specifiers, so most pass through.
    """
    result = strftime_format
    for strftime_spec, duckdb_spec in _STRFTIME_TO_DUCKDB.items():
        result = result.replace(strftime_spec, duckdb_spec)
    return result


def _translate_format_for_trino(strftime_format: str) -> str:
    """Translate strftime-style format to Trino DATE_PARSE format."""
    result = strftime_format
    for strftime_spec, trino_spec in _STRFTIME_TO_TRINO.items():
        result = result.replace(strftime_spec, trino_spec)
    return result


def _custom_format_timestamp_expr(
    column: str,
    format_string: str,
    *,
    engine_type: str,
) -> str:
    """Build SQL expression for parsing custom timestamp format.

    Args:
        column: Physical column name.
        format_string: strftime-style format string (e.g., '%Y%m%d %H:%M:%S').
        engine_type: 'duckdb' or 'trino'.

    Returns:
        SQL expression that produces a TIMESTAMP value.
    """
    varchar_expr = _varchar_cast_expr(column, engine_type=engine_type)

    if engine_type == "duckdb":
        duckdb_format = _translate_format_for_duckdb(format_string)
        return f"STRPTIME({varchar_expr}, '{duckdb_format}')"

    if engine_type == "trino":
        trino_format = _translate_format_for_trino(format_string)
        # Trino: use date_parse which directly returns TIMESTAMP
        return f"DATE_PARSE({varchar_expr}, '{trino_format}')"

    raise ValueError(f"Unsupported engine_type for custom timestamp format: {engine_type}")


def _timestamp_field_expr(
    column: str,
    timestamp_format: str | None,
    *,
    engine_type: str,
) -> str:
    """Build SQL expression for a timestamp column with optional format parsing.

    Args:
        column: Physical column name.
        timestamp_format: Either a semantic convention ('native', 'iso8601_t_naive')
                          or a custom strftime-style format string.
        engine_type: 'duckdb' or 'trino'.

    Returns:
        SQL expression that produces a TIMESTAMP value.
    """
    # Semantic convention: native (timestamp-like column, no conversion)
    if timestamp_format in {None, "native"}:
        return column

    # Semantic convention: iso8601_t_naive (YYYY-MM-DDTHH:MM:SS format)
    if timestamp_format == "iso8601_t_naive":
        return f"CAST(REPLACE({_varchar_cast_expr(column, engine_type=engine_type)}, 'T', ' ') AS TIMESTAMP)"

    # Custom format: use STRPTIME family for parsing
    # At this point timestamp_format is guaranteed to be a non-None string
    assert timestamp_format is not None
    return _custom_format_timestamp_expr(column, timestamp_format, engine_type=engine_type)


def _partition_hour_timestamp_expr(
    date_text_expr: str, hour_text_expr: str, *, engine_type: str
) -> str:
    del engine_type  # DuckDB and Trino both accept CAST(CONCAT(... ) AS TIMESTAMP) in phase 1.
    return f"CAST(CONCAT({date_text_expr}, ' ', {hour_text_expr}, ':00:00') AS TIMESTAMP)"


def _format_partition_date(value: date, date_format: str | None, *, engine_type: str) -> str:
    del engine_type  # Phase-1 engines share literal formatting.
    if date_format == "yyyymmdd":
        return value.strftime("%Y%m%d")
    return value.isoformat()


def _format_partition_hour(value: int, hour_format: str | None, *, engine_type: str) -> str:
    del engine_type  # Phase-1 engines share literal formatting.
    if hour_format in {"h", "int"}:
        return str(value)
    return f"{value:02d}"
