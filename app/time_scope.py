from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
import re
from typing import Any, Literal, Mapping, Sequence

from app.time_axis_metadata import normalize_time_capabilities


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
    warnings: list[dict[str, Any]] = field(default_factory=list)


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

_TIMESTAMP_CANDIDATES = ("event_time", "timestamp", "created_at", "updated_at", "time")
_DAY_CANDIDATES = ("log_date", "event_date", "dt", "date", "day")
_HOUR_CANDIDATES = ("log_hour", "event_hour", "hour", "dt_hour")
_EMPTY_SCHEMA_DAY_CANDIDATES = ("event_date", "date", "day", "dt", "log_date")


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
        if grain not in {"day", "hour"}:
            raise ValueError("time_scope.grain must be 'day' or 'hour'")

        current = self._normalize_time_window(payload.get("current"), "time_scope.current", grain)
        baseline_payload = payload.get("baseline")
        if mode == "compare":
            if not isinstance(baseline_payload, Mapping):
                raise ValueError("time_scope.baseline is required when mode='compare'")
            baseline = self._normalize_time_window(payload.get("baseline"), "time_scope.baseline", grain)
        else:
            if baseline_payload is not None:
                raise ValueError("time_scope.baseline is only allowed when mode='compare'")
            baseline = None

        warnings: list[dict[str, Any]] = []
        if baseline is not None:
            current_duration = self._window_duration(current, grain)
            baseline_duration = self._window_duration(baseline, grain)
            if current_duration != baseline_duration:
                warnings.append({
                    "code": "window_length_mismatch",
                    "message": "current and baseline windows have different lengths",
                    "grain": grain,
                    "current_duration": current_duration,
                    "baseline_duration": baseline_duration,
                })
        return ResolvedTimeScope(
            mode=mode,
            grain=grain,
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
        start = self._normalize_boundary(_required_str(payload, "start", label), f"{label}.start", grain)
        end = self._normalize_boundary(_required_str(payload, "end", label), f"{label}.end", grain)
        if grain == "day":
            if date.fromisoformat(start) >= date.fromisoformat(end):
                raise ValueError(f"{label} requires start < end")
        else:
            if datetime.fromisoformat(start) >= datetime.fromisoformat(end):
                raise ValueError(f"{label} requires start < end")
        return ResolvedTimeWindow(start=start, end=end)

    @staticmethod
    def _normalize_boundary(value: str, label: str, grain: TimeScopeGrain) -> str:
        normalized = value.strip()
        if grain == "day":
            try:
                return date.fromisoformat(normalized).isoformat()
            except ValueError:
                pass
            try:
                return datetime.fromisoformat(normalized).date().isoformat()
            except ValueError as exc:
                raise ValueError(f"{label} must be a date or datetime string") from exc

        if "T" not in normalized and " " not in normalized:
            raise ValueError(f"{label} must be a datetime string for hour grain")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError(f"{label} must be a datetime string for hour grain") from exc
        if parsed.tzinfo is not None:
            raise ValueError(f"{label} must be a naive datetime string without timezone")
        return parsed.replace(microsecond=0).isoformat(timespec="seconds")

    @staticmethod
    def _window_duration(window: ResolvedTimeWindow, grain: TimeScopeGrain) -> int:
        if grain == "day":
            return (date.fromisoformat(window.end) - date.fromisoformat(window.start)).days
        return int((datetime.fromisoformat(window.end) - datetime.fromisoformat(window.start)).total_seconds())


@dataclass(slots=True)
class _TimeCapabilities:
    timestamp_column: str | None = None
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
            fallback_date_column=_optional_str(analysis_time.get("fallback_date_column")),
            fallback_hour_column=_optional_str(analysis_time.get("fallback_hour_column")),
            partition_date_column=_optional_str(partition_time.get("date_column")),
            partition_date_format=_normalize_date_format(partition_time.get("date_format")),
            partition_hour_column=_optional_str(partition_time.get("hour_column")),
            partition_hour_format=_normalize_hour_format(partition_time.get("hour_format")),
            default_compare_grain=_optional_str(normalized_payload.get("default_compare_grain")),
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

    def resolve(self) -> ResolvedTimeAxis:
        columns = _normalize_columns(self.available_columns)
        metadata_chain = [
            _TimeCapabilities.from_mapping(self.entity_time_capabilities),
            _TimeCapabilities.from_mapping(self.source_time_capabilities),
        ]
        analysis = self._resolve_analysis_axis(columns, metadata_chain)
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
            if self.request.time_scope.grain == "hour" and _looks_like_day_column(override):
                raise ValueError(
                    "time_axis.analysis_time.column must be hour-compatible for hour grain; "
                    "provide a timestamp column or rely on partition date/hour metadata"
                )
            if _looks_like_day_column(override):
                return _AnalysisAxis(
                    kind="date_field",
                    expr=override,
                    column=override,
                    date_column=override,
                    date_format=self._analysis_date_format(metadata_chain, override),
                )
            return _AnalysisAxis(kind="timestamp", expr=override, column=override)

        for caps in metadata_chain:
            if caps.timestamp_column is not None:
                self._ensure_known_column(caps.timestamp_column, columns, "time_capabilities.analysis_time.timestamp_column")
                return _AnalysisAxis(kind="timestamp", expr=caps.timestamp_column, column=caps.timestamp_column)

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
                        date_format=self._analysis_date_format(metadata_chain, caps.fallback_date_column),
                        hour_column=caps.fallback_hour_column,
                        hour_format=self._analysis_hour_format(metadata_chain, caps.fallback_hour_column),
                    )
            elif caps.fallback_date_column:
                self._ensure_known_column(
                    caps.fallback_date_column,
                    columns,
                    "time_capabilities.analysis_time.fallback_date_column",
                )
                return _AnalysisAxis(
                    kind="date_field",
                    expr=caps.fallback_date_column,
                    column=caps.fallback_date_column,
                    date_column=caps.fallback_date_column,
                    date_format=self._analysis_date_format(metadata_chain, caps.fallback_date_column),
                )

        if not columns and self.request.time_scope.grain == "day":
            day_column = self._first_candidate(columns, _EMPTY_SCHEMA_DAY_CANDIDATES)
            if day_column is not None:
                return _AnalysisAxis(
                    kind="date_field",
                    expr=day_column,
                    column=day_column,
                    date_column=day_column,
                    date_format=self._analysis_date_format(metadata_chain, day_column),
                )

        timestamp_column = self._first_candidate(columns, _TIMESTAMP_CANDIDATES)
        if timestamp_column is not None:
            return _AnalysisAxis(kind="timestamp", expr=timestamp_column, column=timestamp_column)

        day_column = self._first_candidate(columns, _DAY_CANDIDATES)
        hour_column = self._first_candidate(columns, _HOUR_CANDIDATES)
        if self.request.time_scope.grain == "hour":
            if day_column is not None and hour_column is not None:
                return _AnalysisAxis(
                    kind="partition_fields",
                    expr=self._partition_hour_analysis_expr(
                        day_column,
                        hour_column,
                        self._analysis_date_format(metadata_chain, day_column),
                    ),
                    date_column=day_column,
                    date_format=self._analysis_date_format(metadata_chain, day_column),
                    hour_column=hour_column,
                    hour_format=self._analysis_hour_format(metadata_chain, hour_column),
                )
            raise ValueError(
                f"could not resolve an hour-compatible time axis for {self.request.table}; "
                "provide time_axis override or metadata-backed timestamp/date+hour columns"
            )
        if day_column is not None:
            return _AnalysisAxis(
                kind="date_field",
                expr=day_column,
                column=day_column,
                date_column=day_column,
                date_format=self._analysis_date_format(metadata_chain, day_column),
            )
        raise ValueError(
            f"could not resolve a time axis for {self.request.table}; provide time_axis override or metadata"
        )

    def _resolve_partition_axis(
        self,
        columns: tuple[str, ...],
        metadata_chain: list[_TimeCapabilities],
        analysis: _AnalysisAxis,
    ) -> _PartitionAxis | None:
        override_date = self.request.resolved_time_axis.override_partition_date_column
        override_hour = self.request.resolved_time_axis.override_partition_hour_column
        if override_date is not None:
            self._ensure_known_column(override_date, columns, "time_axis.partition_pruning.date_column")
        if override_hour is not None:
            self._ensure_known_column(override_hour, columns, "time_axis.partition_pruning.hour_column")

        date_column = override_date
        hour_column = override_hour
        date_format = _default_date_format_for_column(date_column) if date_column else None
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
                    date_format = caps.partition_date_format or _default_date_format_for_column(date_column)
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
                    hour_format = caps.partition_hour_format or _default_hour_format_for_column(hour_column)
                    break

        if date_column is None and analysis.date_column is not None:
            date_column = analysis.date_column
            date_format = analysis.date_format or _default_date_format_for_column(date_column)
        if hour_column is None and analysis.hour_column is not None:
            hour_column = analysis.hour_column
            hour_format = analysis.hour_format or _default_hour_format_for_column(hour_column)

        if date_column is None:
            date_column = self._first_candidate(columns, _DAY_CANDIDATES)
            if date_column is not None:
                date_format = _default_date_format_for_column(date_column)
        if hour_column is None:
            hour_column = self._first_candidate(columns, _HOUR_CANDIDATES)
            if hour_column is not None:
                hour_format = _default_hour_format_for_column(hour_column)

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
        if self.request.time_scope.grain == "day":
            start_day, end_day = self._day_envelope()
            return (
                f"{axis.date_column} >= '{_format_partition_date(start_day, axis.date_format)}' "
                f"AND {axis.date_column} < '{_format_partition_date(end_day, axis.date_format)}'"
            )

        start_dt, end_dt = self._hour_envelope()
        if axis.hour_column is None:
            last_day = (end_dt - timedelta(seconds=1)).date()
            return (
                f"{axis.date_column} >= '{_format_partition_date(start_dt.date(), axis.date_format)}' "
                f"AND {axis.date_column} < '{_format_partition_date(last_day + timedelta(days=1), axis.date_format)}'"
            )

        start_day = start_dt.date()
        last_day = (end_dt - timedelta(seconds=1)).date()
        start_hour = _format_partition_hour(start_dt.hour, axis.hour_format)
        end_hour = _format_partition_hour(end_dt.hour, axis.hour_format)

        if start_day == last_day:
            parts = [
                f"{axis.date_column} = '{_format_partition_date(start_day, axis.date_format)}'",
                f"{axis.hour_column} >= '{start_hour}'",
            ]
            if end_dt.date() == start_day:
                parts.append(f"{axis.hour_column} < '{end_hour}'")
            return " AND ".join(parts)

        clauses = [
            (
                f"{axis.date_column} = '{_format_partition_date(start_day, axis.date_format)}' "
                f"AND {axis.hour_column} >= '{start_hour}'"
            )
        ]
        if start_day + timedelta(days=1) <= last_day - timedelta(days=1):
            clauses.append(
                f"{axis.date_column} > '{_format_partition_date(start_day, axis.date_format)}' "
                f"AND {axis.date_column} < '{_format_partition_date(last_day, axis.date_format)}'"
            )
        if end_dt.time() == time(0, 0):
            clauses.append(f"{axis.date_column} = '{_format_partition_date(last_day, axis.date_format)}'")
        else:
            clauses.append(
                f"{axis.date_column} = '{_format_partition_date(last_day, axis.date_format)}' "
                f"AND {axis.hour_column} < '{end_hour}'"
            )
        return "(" + ") OR (".join(clauses) + ")"

    def _partition_hour_analysis_expr(self, date_column: str, hour_column: str, date_format: str | None) -> str:
        date_text_expr = _partition_date_text_expr(date_column, date_format)
        hour_text_expr = f"LPAD(CAST({hour_column} AS VARCHAR), 2, '0')"
        return f"CAST(CONCAT({date_text_expr}, ' ', {hour_text_expr}, ':00:00') AS TIMESTAMP)"

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
    def _first_candidate(columns: tuple[str, ...], candidates: Sequence[str]) -> str | None:
        if columns:
            column_set = set(columns)
            for candidate in candidates:
                if candidate in column_set:
                    return candidate
            return None
        return candidates[0] if candidates else None

    @staticmethod
    def _ensure_known_column(column: str, columns: tuple[str, ...], label: str) -> None:
        if columns and column not in set(columns):
            raise ValueError(f"{label} references unknown column '{column}'")

    @staticmethod
    def _analysis_date_format(metadata_chain: list[_TimeCapabilities], date_column: str | None) -> str | None:
        for caps in metadata_chain:
            if caps.partition_date_column == date_column and caps.partition_date_format is not None:
                return caps.partition_date_format
        return _default_date_format_for_column(date_column)

    @staticmethod
    def _analysis_hour_format(metadata_chain: list[_TimeCapabilities], hour_column: str | None) -> str | None:
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


def _looks_like_day_column(column: str) -> bool:
    return column in _DAY_CANDIDATES


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


def _default_date_format_for_column(column: str | None) -> str | None:
    if column in {"log_date", "dt"}:
        return "yyyymmdd"
    return None


def _default_hour_format_for_column(column: str | None) -> str | None:
    if column is None:
        return None
    return "hh"


def _partition_date_text_expr(column: str, date_format: str | None) -> str:
    raw = f"CAST({column} AS VARCHAR)"
    if date_format == "yyyymmdd":
        return (
            f"CONCAT(SUBSTR({raw}, 1, 4), '-', SUBSTR({raw}, 5, 2), '-', SUBSTR({raw}, 7, 2))"
        )
    return raw


def _format_partition_date(value: date, date_format: str | None) -> str:
    if date_format == "yyyymmdd":
        return value.strftime("%Y%m%d")
    return value.isoformat()


def _format_partition_hour(value: int, hour_format: str | None) -> str:
    if hour_format in {"h", "int"}:
        return str(value)
    return f"{value:02d}"
