from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal, cast

from marivo.time_contracts import (
    normalize_hour_boundary,
    window_length_in_grain,
)

CompareKind = Literal["semantic_metric", "ad_hoc_aggregate"]
TimeScopeMode = Literal["single_window", "compare"]
TimeScopeGrain = Literal["hour", "day", "week", "month", "quarter", "year"]
TimeScopeBoundaryMode = Literal["grain", "exact"]
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
    grain: TimeScopeGrain | None
    current: ResolvedTimeWindow
    baseline: ResolvedTimeWindow | None = None
    boundary_mode: TimeScopeBoundaryMode = "grain"
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

    observation_grain: TimeScopeGrain | None
    analysis_time_kind: str | None = None
    analysis_time_expr: str | None = None
    analysis_time_format: str | None = None
    analysis_time_data_type: str | None = None
    partition_pruning_predicate: str | None = None
    partition_date_column: str | None = None
    partition_date_format: str | None = None
    partition_date_data_type: str | None = None
    partition_hour_column: str | None = None
    partition_hour_format: str | None = None
    partition_hour_data_type: str | None = None
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
        boundary_mode_raw = _optional_str(payload.get("boundary_mode")) or "grain"
        if boundary_mode_raw not in {"grain", "exact"}:
            raise ValueError("time_scope.boundary_mode must be 'grain' or 'exact'")
        if mode not in {"single_window", "compare"}:
            raise ValueError("time_scope.mode must be 'single_window' or 'compare'")
        boundary_mode = cast("TimeScopeBoundaryMode", boundary_mode_raw)
        grain_raw = payload.get("grain")
        if boundary_mode == "exact":
            if mode != "single_window":
                raise ValueError(
                    "time_scope.boundary_mode='exact' is only allowed with mode='single_window'"
                )
            if grain_raw is not None:
                raise ValueError("time_scope.grain must be omitted when boundary_mode='exact'")
            mode_typed = cast("TimeScopeMode", mode)
            current = self._normalize_exact_time_window(
                payload.get("current"), "time_scope.current"
            )
            if payload.get("baseline") is not None:
                raise ValueError("time_scope.baseline is only allowed when mode='compare'")
            return ResolvedTimeScope(
                mode=mode_typed,
                grain=None,
                current=current,
                baseline=None,
                boundary_mode=boundary_mode,
            )

        grain = _required_str(payload, "grain", self.step_type)
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
            boundary_mode=boundary_mode,
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

    def _normalize_exact_time_window(
        self,
        payload: Any,
        label: str,
    ) -> ResolvedTimeWindow:
        if not isinstance(payload, Mapping):
            raise ValueError(f"{label} is required")
        start = self._normalize_exact_boundary(
            _required_str(payload, "start", label), f"{label}.start"
        )
        end = self._normalize_exact_boundary(_required_str(payload, "end", label), f"{label}.end")
        if _parse_exact_boundary(start) >= _parse_exact_boundary(end):
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
    def _normalize_exact_boundary(value: str, label: str) -> str:
        normalized = value.strip()
        if "T" not in normalized and " " not in normalized:
            try:
                return date.fromisoformat(normalized).isoformat()
            except ValueError as exc:
                raise ValueError(f"{label} must be a date or datetime string") from exc
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{label} must be a date or datetime string") from exc
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed.isoformat()

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


def _parse_exact_boundary(value: str) -> datetime:
    if "T" not in value and " " not in value:
        return datetime.combine(date.fromisoformat(value), time.min)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _exact_boundary_is_subday(value: str) -> bool:
    if "T" not in value and " " not in value:
        return False
    parsed = _parse_exact_boundary(value)
    return parsed.time() != time.min


def _exact_window_requires_subday_precision(time_scope: ResolvedTimeScope) -> bool:
    if time_scope.boundary_mode != "exact":
        return False
    return _exact_boundary_is_subday(time_scope.current.start) or _exact_boundary_is_subday(
        time_scope.current.end
    )


def _effective_time_scope_grain(time_scope: ResolvedTimeScope) -> TimeScopeGrain:
    if time_scope.grain is not None:
        return time_scope.grain
    return "hour" if _exact_window_requires_subday_precision(time_scope) else "day"


def _ceil_hour_for_partition_end(value: datetime) -> datetime:
    if value.minute == 0 and value.second == 0 and value.microsecond == 0:
        return value
    return value.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


@dataclass(slots=True)
class _AnalysisAxis:
    kind: str
    expr: str
    column: str | None = None
    data_type: str | None = None
    date_column: str | None = None
    date_format: str | None = None
    date_data_type: str | None = None
    hour_column: str | None = None
    hour_format: str | None = None
    hour_data_type: str | None = None


@dataclass(slots=True)
class TimeAxisResolver:
    request: ResolvedWindowedQueryRequest
    engine_type: str
    available_columns: Sequence[str] = ()
    time_field_expressions: Mapping[str, str] | None = None
    time_field_data_types: Mapping[str, str] | None = None
    time_field_formats: Mapping[str, str] | None = None
    time_field_required_prefixes: Mapping[str, str] | None = None
    time_field_support_min_granularities: Mapping[str, str] | None = None

    def resolve(self) -> ResolvedTimeAxis:
        columns = _normalize_columns(self.available_columns)
        analysis = self._resolve_analysis_axis(columns)
        self._validate_supported_granularity(analysis)

        # Populate per-column data types on analysis axis
        if analysis.date_column is not None:
            analysis.date_data_type = _mapping_optional_str(
                self.time_field_data_types, analysis.date_column
            )
        if analysis.hour_column is not None:
            analysis.hour_data_type = _mapping_optional_str(
                self.time_field_data_types, analysis.hour_column
            )

        # --- Compute effective pruning columns ---
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

        prune_date_column = override_date or analysis.date_column
        prune_hour_column = override_hour or analysis.hour_column

        # Compute format and data_type for each pruning column
        if override_date is not None:
            prune_date_format = self._analysis_date_format(override_date)
            prune_date_data_type = _mapping_optional_str(self.time_field_data_types, override_date)
        elif analysis.date_column is not None:
            prune_date_format = analysis.date_format
            prune_date_data_type = analysis.date_data_type
        else:
            prune_date_format = None
            prune_date_data_type = None

        if override_hour is not None:
            prune_hour_format = self._analysis_hour_format(
                override_hour
            ) or _default_hour_format_for_column(override_hour)
            prune_hour_data_type = _mapping_optional_str(self.time_field_data_types, override_hour)
        elif analysis.hour_column is not None:
            prune_hour_format = self._analysis_hour_format(
                analysis.hour_column
            ) or _default_hour_format_for_column(analysis.hour_column)
            prune_hour_data_type = analysis.hour_data_type
        else:
            prune_hour_format = None
            prune_hour_data_type = None

        if prune_hour_column is not None and prune_date_column is None:
            raise ValueError("partition pruning hour_column requires a date_column")

        has_partition = prune_date_column is not None

        predicate = None
        if has_partition:
            predicate = self._build_partition_pruning_predicate(
                date_column=prune_date_column,
                date_format=prune_date_format,
                date_data_type=prune_date_data_type,
                hour_column=prune_hour_column,
                hour_format=prune_hour_format,
                hour_data_type=prune_hour_data_type,
            )

        return ResolvedTimeAxis(
            observation_grain=self.request.time_scope.grain,
            analysis_time_kind=analysis.kind,
            analysis_time_expr=analysis.expr,
            analysis_time_format=analysis.date_format,
            analysis_time_data_type=analysis.data_type,
            partition_pruning_predicate=predicate,
            partition_date_column=prune_date_column if has_partition else None,
            partition_date_format=prune_date_format if has_partition else None,
            partition_date_data_type=prune_date_data_type if has_partition else None,
            partition_hour_column=prune_hour_column if has_partition else None,
            partition_hour_format=prune_hour_format if has_partition else None,
            partition_hour_data_type=prune_hour_data_type if has_partition else None,
            override_analysis_time_column=self.request.resolved_time_axis.override_analysis_time_column,
            override_partition_date_column=override_date,
            override_partition_hour_column=override_hour,
        )

    def _resolve_analysis_axis(
        self,
        columns: tuple[str, ...],
    ) -> _AnalysisAxis:
        override = self.request.resolved_time_axis.override_analysis_time_column
        if override is not None:
            self._ensure_known_column(override, columns, "time_axis.analysis_time.column")
            # Granularity validation deferred to _validate_supported_granularity which
            # considers composite axis (date + paired hour field) capability.
            field_expression = _mapping_optional_str(self.time_field_expressions, override)
            field_data_type = _mapping_optional_str(self.time_field_data_types, override)
            field_format = _mapping_optional_str(self.time_field_formats, override)
            if field_expression is not None and field_expression != override:
                return self._analysis_axis_from_time_field_expression(
                    field_name=override,
                    expression=field_expression,
                    data_type=field_data_type,
                    format=field_format,
                    columns=columns,
                )
            if _sql_type_is_date(field_data_type):
                if self._effective_grain() == "hour":
                    raise ValueError(
                        self._hour_compatibility_error("time_axis.analysis_time.column")
                    )
                date_format = field_format or self._analysis_date_format(override)
                return _AnalysisAxis(
                    kind="date_field",
                    expr=self._date_field_analysis_expr(override, date_format),
                    column=override,
                    data_type="date",
                    date_column=override,
                    date_format=date_format,
                )
            if _sql_type_is_timestamp(field_data_type):
                return _AnalysisAxis(
                    kind="timestamp", expr=override, column=override, data_type="timestamp"
                )
            if _sql_type_is_string(field_data_type):
                normalized_format = _normalize_date_format(field_format)
                if normalized_format in _HOUR_ONLY_FORMATS:
                    return self._analysis_axis_from_hour_only_field(
                        field_name=override,
                        format=normalized_format,
                        columns=columns,
                    )
                return self._analysis_axis_from_string_field(
                    field_name=override,
                    format=field_format,
                    columns=columns,
                )
            if _sql_type_is_integer(field_data_type):
                normalized = _normalize_date_format(field_format)
                if normalized in _HOUR_ONLY_FORMATS:
                    return self._analysis_axis_from_hour_only_field(
                        field_name=override,
                        format=normalized,
                        columns=columns,
                    )
                return self._analysis_axis_from_integer_field(
                    field_name=override,
                    format=field_format,
                    columns=columns,
                )
            raise ValueError(
                f"time_axis.analysis_time.column '{override}' requires explicit "
                "time field data_type"
            )

        raise ValueError(
            f"could not resolve a time axis for {self.request.table}; provide time_scope.field"
        )

    def _analysis_axis_from_time_field_expression(
        self,
        *,
        field_name: str,
        expression: str,
        data_type: str | None,
        format: str | None,
        columns: tuple[str, ...],
    ) -> _AnalysisAxis:
        if _is_simple_identifier(expression):
            if _sql_type_is_date(data_type):
                if self._effective_grain() == "hour":
                    raise ValueError(self._hour_compatibility_error("time_scope.field expression"))
                date_format = format or self._analysis_date_format(expression)
                return _AnalysisAxis(
                    kind="date_field",
                    expr=self._date_field_analysis_expr(expression, date_format),
                    column=field_name,
                    data_type="date",
                    date_column=expression,
                    date_format=date_format,
                )
            if _sql_type_is_timestamp(data_type):
                return _AnalysisAxis(
                    kind="timestamp", expr=expression, column=field_name, data_type="timestamp"
                )
            if _sql_type_is_string(data_type):
                return self._analysis_axis_from_string_field(
                    field_name=field_name,
                    format=format,
                    columns=columns,
                    column_name=expression,
                )
            if _sql_type_is_integer(data_type):
                return self._analysis_axis_from_integer_field(
                    field_name=field_name,
                    format=format,
                    columns=columns,
                    column_name=expression,
                )
            raise ValueError(f"time field '{field_name}' expression requires explicit data_type")

        if _sql_type_is_timestamp(data_type) or _expression_returns_timestamp(expression):
            return _AnalysisAxis(
                kind="timestamp",
                expr=_timestamp_expression_for_engine(expression, engine_type=self.engine_type),
                column=field_name,
                data_type=_canonical_data_type(data_type) or "timestamp",
            )
        if _sql_type_is_date(data_type) or _expression_returns_date(expression):
            return _AnalysisAxis(
                kind="date_expression",
                expr=expression,
                column=field_name,
                data_type=_canonical_data_type(data_type) or "date",
            )
        if _sql_type_is_string(data_type):
            if format is None:
                raise ValueError(
                    f"time field '{field_name}' with data_type='string' requires format"
                )
            normalized_format = _normalize_date_format(format)
            if normalized_format in _HOUR_PRECISION_FORMATS:
                return _AnalysisAxis(
                    kind="timestamp",
                    expr=_timestamp_expression_for_engine(expression, engine_type=self.engine_type),
                    column=field_name,
                    data_type="string",
                    date_column=field_name,
                    date_format=normalized_format,
                )
            return _AnalysisAxis(
                kind="date_expression",
                expr=expression,
                column=field_name,
                data_type="string",
                date_format=normalized_format,
            )
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
        requested = self._effective_grain()
        # Composite axis (date + paired hour field) satisfies hour granularity
        # even if the date field alone only supports day granularity.
        if requested == "hour" and analysis.hour_column is not None:
            hour_field = analysis.hour_column
            hour_support = _mapping_optional_str(
                self.time_field_support_min_granularities,
                hour_field,
            )
            if hour_support == "hour":
                return  # Composite capability satisfies hour granularity
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
        requested = self._effective_grain()
        if _TIME_GRANULARITY_ORDER[requested] < _TIME_GRANULARITY_ORDER[support_min_granularity]:
            if self.request.time_scope.boundary_mode == "exact":
                raise ValueError(
                    f"time field '{field_name}' supports minimum granularity "
                    f"'{support_min_granularity}' and cannot satisfy exact sub-day window"
                )
            raise ValueError(
                f"time field '{field_name}' supports minimum granularity "
                f"'{support_min_granularity}' and cannot satisfy requested granularity "
                f"'{requested}'"
            )

    def _partition_hour_axis_for_date_column(
        self,
        date_column: str,
        columns: tuple[str, ...],
        *,
        field_name: str | None = None,
    ) -> _AnalysisAxis | None:
        hour_column = self._hour_column_for_date_column(date_column, columns)
        if hour_column is None:
            return None
        self._ensure_known_column(hour_column, columns, "time_axis.analysis_time.hour_column")
        date_format = self._analysis_date_format(date_column)
        return _AnalysisAxis(
            kind="partition_fields",
            expr=self._partition_hour_analysis_expr(date_column, hour_column, date_format),
            column=field_name or date_column,
            date_column=date_column,
            date_format=date_format,
            hour_column=hour_column,
            hour_format=self._analysis_hour_format(hour_column),
        )

    def _analysis_axis_from_hour_only_field(
        self,
        *,
        field_name: str,
        format: str | None,
        columns: tuple[str, ...],
    ) -> _AnalysisAxis:
        """Handle a time field with hour-only format (hh/h) as analysis axis override.

        An hour-only field cannot be a standalone analysis axis — it needs a date-format
        field declared via required_prefix. This method finds the required_prefix field
        and resolves the composite axis (date + hour → partition_fields).
        """
        prefix_field = _mapping_optional_str(self.time_field_required_prefixes, field_name)
        if prefix_field is None:
            raise ValueError(
                f"time field '{field_name}' with format '{format}' cannot be used as "
                "a standalone analysis axis; it requires a date-format time field "
                "declared via required_prefix"
            )
        self._ensure_known_column(prefix_field, columns, "required_prefix field")
        # Resolve using the prefix (date) field with this hour field as paired
        prefix_format = _mapping_optional_str(self.time_field_formats, prefix_field)
        date_format = prefix_format or self._analysis_date_format(prefix_field)
        hour_format = "hh"  # hour-only format is always hh
        hour_column = field_name
        date_column = prefix_field
        date_text_expr = _partition_date_text_expr(
            date_column, date_format, engine_type=self.engine_type
        )
        hour_text_expr = _partition_hour_text_expr(hour_column, engine_type=self.engine_type)
        expr = _partition_hour_timestamp_expr(
            date_text_expr,
            hour_text_expr,
            engine_type=self.engine_type,
        )
        return _AnalysisAxis(
            kind="partition_fields",
            expr=expr,
            column=prefix_field,
            data_type="string",
            date_column=date_column,
            date_format=date_format,
            hour_column=hour_column,
            hour_format=hour_format,
        )

    def _analysis_axis_from_string_field(
        self,
        *,
        field_name: str,
        format: str | None,
        columns: tuple[str, ...],
        column_name: str | None = None,
    ) -> _AnalysisAxis:
        if format is None:
            raise ValueError(f"time field '{field_name}' with data_type='string' requires format")
        normalized_format = _normalize_date_format(format)
        if normalized_format in _HOUR_ONLY_FORMATS:
            raise ValueError(
                f"time field '{field_name}' with format '{format}' cannot be used as "
                "a standalone analysis axis; it must be paired with a date-format "
                "time field via required_prefix"
            )
        actual_column = column_name or field_name

        if normalized_format in {"yyyymmdd", "yyyy-mm-dd"}:
            if self._effective_grain() == "hour":
                raise ValueError(
                    f"time field '{field_name}' with format '{format}' and "
                    f"support_min_granularity 'day' cannot satisfy requested granularity 'hour'"
                )
            date_format = normalized_format
            expr = _date_field_expr(actual_column, date_format, engine_type=self.engine_type)
            return _AnalysisAxis(
                kind="date_field",
                expr=expr,
                column=field_name,
                data_type="string",
                date_column=actual_column,
                date_format=date_format,
            )

        if normalized_format in _HOUR_PRECISION_FORMATS:
            expr = _custom_format_timestamp_expr(
                actual_column,
                _format_to_strptime_pattern(normalized_format),
                engine_type=self.engine_type,
            )
            return _AnalysisAxis(
                kind="timestamp",
                expr=expr,
                column=field_name,
                data_type="string",
                date_column=actual_column,
                date_format=normalized_format,
            )

        raise ValueError(
            f"time field '{field_name}' with data_type='string' and format "
            f"'{format}' is not recognized; supported formats: "
            "yyyymmdd, yyyy-mm-dd, yyyymmddhh, yyyymmdd-hh, yyyy-mm-dd-hh, yyyymmddthh, hh"
        )

    def _analysis_axis_from_integer_field(
        self,
        *,
        field_name: str,
        format: str | None,
        columns: tuple[str, ...],
        column_name: str | None = None,
    ) -> _AnalysisAxis:
        if format is None:
            raise ValueError(f"time field '{field_name}' with data_type='integer' requires format")
        normalized_format = _normalize_date_format(format)
        actual_column = column_name or field_name

        if normalized_format == "yyyymmdd":
            if self._effective_grain() == "hour":
                hour_axis = self._partition_hour_axis_for_date_column(
                    actual_column,
                    columns,
                    field_name=field_name,
                )
                if hour_axis is not None:
                    hour_axis.data_type = "integer"
                    return hour_axis
                raise ValueError(
                    f"time field '{field_name}' with format '{format}' cannot "
                    "satisfy hour granularity without a separate hour column"
                )
            varchar_expr = _varchar_cast_expr(actual_column, engine_type=self.engine_type)
            text_expr = _partition_date_text_expr_from_varchar(varchar_expr, normalized_format)
            expr = f"CAST({text_expr} AS DATE)"
            return _AnalysisAxis(
                kind="date_field",
                expr=expr,
                column=field_name,
                data_type="integer",
                date_column=actual_column,
                date_format=normalized_format,
            )

        if normalized_format in {"epochseconds", "epochdays"}:
            if normalized_format == "epochseconds":
                expr = f"CAST({actual_column} / 86400 AS DATE)"
            else:
                expr = f"CAST({actual_column} AS DATE)"
            return _AnalysisAxis(
                kind="date_field",
                expr=expr,
                column=field_name,
                data_type="integer",
                date_format=normalized_format,
            )

        raise ValueError(
            f"time field '{field_name}' with data_type='integer' and format "
            f"'{format}' is not recognized; supported formats: "
            "yyyymmdd, epoch_seconds, epoch_days"
        )

    def _hour_column_for_date_column(
        self,
        date_column: str,
        columns: tuple[str, ...],
    ) -> str | None:
        # Discover from required_prefix declarations: find an hour-only field whose
        # required_prefix references this date column.
        if self.time_field_required_prefixes is not None:
            for field_name, prefix in self.time_field_required_prefixes.items():
                if prefix == date_column and field_name in columns:
                    return field_name
        return None

    def _build_partition_pruning_predicate(
        self,
        *,
        date_column: str | None = None,
        date_format: str | None = None,
        date_data_type: str | None = None,
        hour_column: str | None = None,
        hour_format: str | None = None,
        hour_data_type: str | None = None,
    ) -> str | None:
        if date_column is None:
            return None
        if self._effective_grain() != "hour":
            return self._build_day_partition_pruning_predicate(
                date_column=date_column,
                date_format=date_format,
                date_data_type=date_data_type,
            )

        return self._build_hour_partition_pruning_predicate(
            date_column=date_column,
            date_format=date_format,
            date_data_type=date_data_type,
            hour_column=hour_column,
            hour_format=hour_format,
            hour_data_type=hour_data_type,
        )

    def _build_day_partition_pruning_predicate(
        self,
        *,
        date_column: str,
        date_format: str | None = None,
        date_data_type: str | None = None,
    ) -> str:
        start_day, end_day = self._day_envelope()
        start_literal = self._format_partition_date_literal(start_day, date_format)
        end_literal = self._format_partition_date_literal(end_day, date_format)
        if date_data_type == "integer":
            return f"{date_column} >= {start_literal} AND {date_column} < {end_literal}"
        return f"{date_column} >= '{start_literal}' AND {date_column} < '{end_literal}'"

    def _build_hour_precision_single_column_pruning(
        self,
        *,
        date_column: str,
        date_format: str | None = None,
        date_data_type: str | None = None,
    ) -> str:
        start_dt, end_dt = self._hour_envelope()
        start_literal = _format_hour_precision_partition_literal(start_dt, date_format)
        end_literal = _format_hour_precision_partition_literal(end_dt, date_format)
        if date_data_type == "integer":
            return f"{date_column} >= {start_literal} AND {date_column} < {end_literal}"
        return f"{date_column} >= '{start_literal}' AND {date_column} < '{end_literal}'"

    def _build_hour_partition_pruning_predicate(
        self,
        *,
        date_column: str,
        date_format: str | None = None,
        date_data_type: str | None = None,
        hour_column: str | None = None,
        hour_format: str | None = None,
        hour_data_type: str | None = None,
    ) -> str:
        start_dt, end_dt = self._hour_envelope()
        if hour_column is None:
            if date_format in _HOUR_PRECISION_FORMATS:
                return self._build_hour_precision_single_column_pruning(
                    date_column=date_column,
                    date_format=date_format,
                    date_data_type=date_data_type,
                )
            last_day = (end_dt - timedelta(seconds=1)).date()
            start_literal = self._format_partition_date_literal(start_dt.date(), date_format)
            end_literal = self._format_partition_date_literal(
                last_day + timedelta(days=1), date_format
            )
            if date_data_type == "integer":
                return f"{date_column} >= {start_literal} AND {date_column} < {end_literal}"
            return f"{date_column} >= '{start_literal}' AND {date_column} < '{end_literal}'"

        start_day = start_dt.date()
        end_partition_dt = _ceil_hour_for_partition_end(end_dt)
        last_day = (end_dt - timedelta(seconds=1)).date()
        if start_day == last_day:
            return self._build_same_day_hour_partition_pruning(
                date_column=date_column,
                date_format=date_format,
                date_data_type=date_data_type,
                hour_column=hour_column,
                hour_format=hour_format,
                hour_data_type=hour_data_type,
                start_day=start_day,
                start_hour=start_dt.hour,
                end_day=end_partition_dt.date(),
                end_hour=end_partition_dt.hour,
            )
        return self._build_cross_day_hour_partition_pruning(
            date_column=date_column,
            date_format=date_format,
            date_data_type=date_data_type,
            hour_column=hour_column,
            hour_format=hour_format,
            hour_data_type=hour_data_type,
            start_day=start_day,
            start_hour=start_dt.hour,
            last_day=last_day,
            end_dt=end_partition_dt,
        )

    def _build_same_day_hour_partition_pruning(
        self,
        *,
        date_column: str,
        date_format: str | None = None,
        date_data_type: str | None = None,
        hour_column: str,
        hour_format: str | None = None,
        hour_data_type: str | None = None,
        start_day: date,
        start_hour: int,
        end_day: date,
        end_hour: int,
    ) -> str:
        date_literal = self._format_partition_date_literal(start_day, date_format)
        start_hour_literal = self._format_partition_hour_literal(start_hour, hour_format)
        end_hour_literal = self._format_partition_hour_literal(end_hour, hour_format)
        date_q = "" if date_data_type == "integer" else "'"
        hour_q = "" if hour_data_type == "integer" else "'"
        parts = [
            f"{date_column} = {date_q}{date_literal}{date_q}",
            f"{hour_column} >= {hour_q}{start_hour_literal}{hour_q}",
        ]
        if end_day == start_day:
            parts.append(f"{hour_column} < {hour_q}{end_hour_literal}{hour_q}")
        return " AND ".join(parts)

    def _build_cross_day_hour_partition_pruning(
        self,
        *,
        date_column: str,
        date_format: str | None = None,
        date_data_type: str | None = None,
        hour_column: str,
        hour_format: str | None = None,
        hour_data_type: str | None = None,
        start_day: date,
        start_hour: int,
        last_day: date,
        end_dt: datetime,
    ) -> str:
        date_q = "" if date_data_type == "integer" else "'"
        hour_q = "" if hour_data_type == "integer" else "'"
        clauses = [
            (
                f"{date_column} = {date_q}{self._format_partition_date_literal(start_day, date_format)}{date_q} "
                f"AND {hour_column} >= {hour_q}{self._format_partition_hour_literal(start_hour, hour_format)}{hour_q}"
            )
        ]
        if start_day + timedelta(days=1) <= last_day - timedelta(days=1):
            clauses.append(
                f"{date_column} > {date_q}{self._format_partition_date_literal(start_day, date_format)}{date_q} "
                f"AND {date_column} < {date_q}{self._format_partition_date_literal(last_day, date_format)}{date_q}"
            )
        if end_dt.time() == time(0, 0):
            clauses.append(
                f"{date_column} = {date_q}{self._format_partition_date_literal(last_day, date_format)}{date_q}"
            )
        else:
            clauses.append(
                f"{date_column} = {date_q}{self._format_partition_date_literal(last_day, date_format)}{date_q} "
                f"AND {hour_column} < {hour_q}{self._format_partition_hour_literal(end_dt.hour, hour_format)}{hour_q}"
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
        starts = [_parse_exact_boundary(window.start).date() for window in windows]
        ends = [_parse_exact_boundary(window.end).date() for window in windows]
        return min(starts), max(ends)

    def _hour_envelope(self) -> tuple[datetime, datetime]:
        windows = [self.request.time_scope.current]
        if self.request.time_scope.baseline is not None:
            windows.append(self.request.time_scope.baseline)
        starts = [_parse_exact_boundary(window.start) for window in windows]
        ends = [_parse_exact_boundary(window.end) for window in windows]
        return min(starts), max(ends)

    def _effective_grain(self) -> TimeScopeGrain:
        return _effective_time_scope_grain(self.request.time_scope)

    def _hour_compatibility_error(self, label: str) -> str:
        if self.request.time_scope.boundary_mode == "exact":
            return (
                f"{label} must be hour-compatible for exact sub-day scalar windows; "
                "select a time field whose support_min_granularity is 'hour'"
            )
        return (
            f"{label} must be hour-compatible for hour grain; "
            "select a time field whose support_min_granularity is 'hour'"
        )

    @staticmethod
    def _ensure_known_column(column: str, columns: tuple[str, ...], label: str) -> None:
        if columns and column not in set(columns):
            raise ValueError(f"{label} references unknown column '{column}'")

    def _analysis_date_format(self, date_column: str | None) -> str | None:
        if date_column is not None:
            field_format = _mapping_optional_str(self.time_field_formats, date_column)
            if field_format is not None:
                normalized = _normalize_date_format(field_format)
                if normalized in {"yyyymmdd", "yyyy-mm-dd"}:
                    return normalized
        return None

    def _analysis_hour_format(self, hour_column: str | None) -> str | None:
        if hour_column is not None:
            field_format = _mapping_optional_str(self.time_field_formats, hour_column)
            if field_format is not None:
                normalized = _normalize_date_format(field_format)
                if normalized in _HOUR_ONLY_FORMATS:
                    return normalized
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


def _normalize_time_axis(payload: Any, grain: TimeScopeGrain | None) -> ResolvedTimeAxis:
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


def _sql_type_is_string(data_type: str | None) -> bool:
    normalized = _normalized_sql_type(data_type)
    return normalized in {"string", "varchar", "text", "char", "nvarchar"}


def _sql_type_is_integer(data_type: str | None) -> bool:
    normalized = _normalized_sql_type(data_type)
    return normalized in {"integer", "int", "bigint", "smallint", "tinyint"}


_TIME_FIELD_DATA_TYPES = {"date", "timestamp", "string", "integer"}
_HOUR_PRECISION_FORMATS = {"yyyymmddhh", "yyyymmddthh", "yyyymmdd-hh", "yyyy-mm-dd-hh"}
_HOUR_ONLY_FORMATS = {"hh", "h"}


def _canonical_data_type(data_type: str | None) -> str | None:
    if _sql_type_is_date(data_type):
        return "date"
    if _sql_type_is_timestamp(data_type):
        return "timestamp"
    if _sql_type_is_string(data_type):
        return "string"
    if _sql_type_is_integer(data_type):
        return "integer"
    return None


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
    lowered = normalized.lower()
    stripped = lowered.replace("_", "").replace("-", "")
    if stripped in {"yyyymmdd"}:
        if lowered == "yyyy-mm-dd":
            return "yyyy-mm-dd"
        return "yyyymmdd"
    if stripped in {"yyyymmddhh"}:
        if lowered == "yyyymmdd-hh" or lowered == "yyyy-mm-dd-hh":
            return lowered
        return "yyyymmddhh"
    if stripped in {"yyyymmddthh"}:
        return "yyyymmddthh"
    if stripped in {"hh"}:
        return "hh"
    if stripped in {"int"}:
        return "h"
    if stripped in {"iso"}:
        return "iso"
    if stripped in {"epochseconds", "epochdays"}:
        return stripped
    return lowered


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


def _partition_date_text_expr_from_varchar(varchar_expr: str, date_format: str | None) -> str:
    if date_format == "yyyymmdd":
        return f"CONCAT(SUBSTR({varchar_expr}, 1, 4), '-', SUBSTR({varchar_expr}, 5, 2), '-', SUBSTR({varchar_expr}, 7, 2))"
    return varchar_expr


def _format_to_strptime_pattern(format: str) -> str:
    if format == "yyyymmddhh":
        return "%Y%m%d%H"
    if format == "yyyymmddthh":
        return "%Y%m%dT%H"
    if format == "yyyymmdd-hh":
        return "%Y%m%d-%H"
    if format == "yyyy-mm-dd-hh":
        return "%Y-%m-%d-%H"
    return format


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
    if date_format == "yyyy-mm-dd":
        return value.isoformat()
    if date_format in {"epochdays"}:
        epoch = date(1970, 1, 1)
        return str((value - epoch).days)
    return value.isoformat()


def _format_hour_precision_partition_literal(dt: datetime, date_format: str | None) -> str:
    if date_format == "yyyymmddhh":
        return dt.strftime("%Y%m%d%H")
    if date_format == "yyyymmdd-hh":
        return dt.strftime("%Y%m%d-%H")
    if date_format == "yyyy-mm-dd-hh":
        return dt.strftime("%Y-%m-%d-%H")
    if date_format == "yyyymmddthh":
        return dt.strftime("%Y%m%dT%H")
    return dt.strftime("%Y%m%d%H")


def _format_partition_hour(value: int, hour_format: str | None, *, engine_type: str) -> str:
    del engine_type  # Phase-1 engines share literal formatting.
    if hour_format in {"h", "int"}:
        return str(value)
    return f"{value:02d}"
