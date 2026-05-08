"""Semantic resolution + step compilation use case functions.

Absorbs the model-loading + metric-resolution + step-compilation helpers
that previously lived on SemanticLayerService. Functions take
runtime: MarivoRuntime as the first argument; the runtime carries
ports.model_store from which the SemanticModel is loaded, and the result
is then handed to core.semantic.* pure functions for the actual work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.analysis_core.compiler import (
    CompiledQuery,
    SemanticRequestCompatibilityError,
    compile_step,
)
from app.analysis_core.compiler import build_metric_query as compile_metric_query
from app.analysis_core.ir import AnalysisStepIR
from app.contracts.errors import ErrorCode, NotFoundError
from app.contracts.semantic import SemanticModel
from app.execution.feedback import compile_failure_from_error
from app.metric_inputs import required_metric_input_slots
from app.semantic_runtime.dimensions import resolve_entity_binding_dimensions
from app.semantic_runtime.errors import (
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from app.semantic_runtime.resolution import ResolvedSemanticObject
from app.time_axis_metadata import TimeAxisMetadataContext
from app.time_scope import (
    ResolvedWindowedQueryRequest,
    SemanticMetricValueSpec,
    TimeAxisResolver,
)

if TYPE_CHECKING:
    from app.runtime.runtime import MarivoRuntime


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MetricExecutionContext:
    metric_ref: str
    table_name: str
    binding_ref: str
    carrier_binding_key: str | None = None
    source_object_ref: str | None = None
    carrier_locator: dict[str, Any] | None = None
    authority_locator: dict[str, Any] | None = None
    mapping_id: str | None = None
    execution_locator: dict[str, Any] | None = None
    routing_detail: dict[str, Any] | None = None
    input_field_map: dict[str, str] | None = None
    additivity_constraints: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MetricBindingResolution:
    metric_ref: str
    binding_ref: str
    carrier_binding_key: str | None
    source_object_ref: str | None
    carrier_locator: dict[str, Any] | None
    authority_locator: dict[str, Any] | None
    mapping_id: str | None
    execution_locator: dict[str, Any] | None
    routing_detail: dict[str, Any] | None
    table_name: str | None
    input_field_map: dict[str, str]


@dataclass(frozen=True, slots=True)
class MetricCarrierRoutePreflight:
    table_name: str | None
    mapping_id: str | None
    execution_locator: dict[str, Any] | None
    routing_detail: dict[str, Any]
    readiness_blockers: list[dict[str, Any]]


# ── Pure string helpers (no runtime dependency) ─────────────────────────


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _coerce_metric_ref(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("'metric' is required")
    if normalized.startswith("metric."):
        return normalized
    return f"metric.{normalized}"


def metric_name_from_ref(metric_ref: str) -> str:
    """Return the short metric name for display or legacy internals."""
    return metric_ref.removeprefix("metric.")


def normalize_intent_metric_ref(metric_ref: str) -> str:
    """Normalize a typed-intent metric parameter to canonical ref form for runtime use."""
    return _coerce_metric_ref(metric_ref)


def _carrier_locator_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "catalog": _optional_str(value.get("catalog")),
            "schema": _optional_str(value.get("schema")) or _optional_str(value.get("schema_name")),
            "table": _optional_str(value.get("table")),
        }
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError:
            parts = [part.strip() for part in normalized.split(".") if part.strip()]
            if len(parts) >= 3:
                return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
            if len(parts) == 2:
                return {"catalog": None, "schema": parts[0], "table": parts[1]}
            if len(parts) == 1:
                return {"catalog": None, "schema": None, "table": parts[0]}
            return None
        if isinstance(payload, dict):
            return _carrier_locator_dict(payload)
        if isinstance(payload, str):
            return _carrier_locator_dict(payload)
    return None


def _require_metric_ref(value: str, *, field_name: str = "metric") -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"'{field_name}' is required")
    if normalized.startswith("metric.") and len(normalized) > len("metric."):
        return normalized
    raise ValueError(
        f"'{field_name}' must be a canonical metric ref like 'metric.watch_time', got: {normalized}"
    )


def dataset_source_to_authority_locator(source: str) -> dict[str, Any]:
    """Convert a dot-separated dataset_source to an authority locator dict."""
    parts = [part for part in source.split(".") if part]
    if len(parts) >= 3:
        return {"catalog": parts[-3], "schema": parts[-2], "table": parts[-1]}
    if len(parts) == 2:
        return {"catalog": None, "schema": parts[0], "table": parts[1]}
    return {"catalog": None, "schema": None, "table": source}


def dataset_native_metric_input_field_map(
    metric_family: str,
    payload: dict[str, Any],
) -> dict[str, str]:
    """Resolve metric_input slot names from dataset-native payload fields."""
    fields = payload.get("dataset_fields")
    available = set(fields) if isinstance(fields, dict) else set()
    measure_type = _optional_str(payload.get("measure_type"))
    dimensions = [str(item) for item in list(payload.get("dimensions") or [])]

    def choose(*names: str, default: str) -> str:
        for name in names:
            if name in available:
                return name
        return default

    if metric_family == "count_metric":
        return {"count_target": choose("id", "session_id", default="*")}
    if metric_family == "sum_metric":
        return {"measure": choose("value", "play_duration_seconds", default="value")}
    if metric_family == "average_metric":
        if measure_type == "average":
            return {
                "numerator": choose("play_duration_seconds", "value", default="value"),
                "denominator": choose("session_id", "id", default="id"),
            }
        return {
            "numerator": choose("numerator", "value", default="numerator"),
            "denominator": choose("denominator", "id", default="denominator"),
        }
    if metric_family == "rate_metric":
        return {
            "numerator": choose("numerator", "value", default="numerator"),
            "denominator": choose("denominator", "id", default="denominator"),
        }
    if metric_family == "distribution_metric":
        return {"value_component": choose("value", "play_duration_seconds", default="value")}
    if metric_family == "score_metric":
        return {"score_source": choose("value", default="value")}
    _ = dimensions
    return {}


def table_name_matches_locator(table_name: str, locator: dict[str, Any] | str | None) -> bool:
    """Check whether a table name matches a carrier/authority locator."""
    normalized_table = table_name.strip()
    locator_dict = _carrier_locator_dict(locator)
    normalized_locator = ""
    if locator_dict is not None:
        normalized_locator = ".".join(
            value
            for value in [
                _optional_str(locator_dict.get("catalog")),
                _optional_str(locator_dict.get("schema")),
                _optional_str(locator_dict.get("table")),
            ]
            if value is not None
        )
    else:
        normalized_locator = str(locator or "").strip()
    if not normalized_table or not normalized_locator:
        return False
    if normalized_table == normalized_locator:
        return True
    return normalized_locator.endswith(f".{normalized_table}") or normalized_table.endswith(
        f".{normalized_locator}"
    )


def merge_filters(*filters: str | None) -> str | None:
    """AND-merge multiple filter expressions, ignoring None values."""
    parts = [f for f in filters if f]
    if not parts:
        return None
    return " AND ".join(f"({p})" for p in parts)


def observation_window_for_request(request: ResolvedWindowedQueryRequest) -> dict[str, Any]:
    """Extract the observation window from a resolved request."""
    return {
        "start": request.time_scope.current.start,
        "end": request.time_scope.current.end,
        "granularity": request.resolved_time_axis.observation_grain,
    }


_METRIC_QUERY_MODE_CONTRACTS: dict[str, Any] = {
    "compare": {
        "payload_fields": {
            "current_value": "current_value",
            "baseline_value": "baseline_value",
            "delta_pct": "delta_pct",
            "current_sessions": "current_sessions",
            "baseline_sessions": "baseline_sessions",
        },
        "required_payload_keys": (
            "current_value",
            "baseline_value",
            "delta_pct",
            "current_sessions",
            "baseline_sessions",
        ),
    },
    "single_window": {
        "payload_fields": {
            "current_value": "current_value",
            "current_sessions": "current_sessions",
        },
        "required_payload_keys": (
            "current_value",
            "current_sessions",
        ),
    },
}


def metric_query_mode_contract(mode: str) -> dict[str, Any]:
    """Return the metric query mode contract for a given mode string."""
    normalized = str(mode).strip().lower()
    contract = _METRIC_QUERY_MODE_CONTRACTS.get(normalized)
    if contract is None:
        raise ValueError(f"Unsupported metric_query mode: {mode}")
    payload_fields = dict(contract["payload_fields"])
    required_payload_keys = tuple(contract["required_payload_keys"])
    return {
        "mode": normalized,
        "payload_fields": payload_fields,
        "required_payload_keys": required_payload_keys,
        "required_row_fields": tuple(payload_fields[key] for key in required_payload_keys),
    }


def metric_query_quality_builder(mode: str) -> Any:
    """Return a quality builder lambda for a metric query mode."""
    normalized = metric_query_mode_contract(mode)["mode"]
    if normalized == "compare":
        return lambda row: {
            "freshness_ok": True,
            "sample_size_ok": min(row["current_sessions"] or 0, row["baseline_sessions"] or 0)
            >= 150,
        }
    return lambda row: {
        "freshness_ok": True,
        "sample_size_ok": (row.get("current_sessions") or 0) >= 150,
    }


def normalize_metric_rows(
    rows: list[dict[str, Any]],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    """Validate and normalize metric query rows against the mode contract."""
    contract = metric_query_mode_contract(mode)
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_dict = dict(row)
        missing = [field for field in contract["required_row_fields"] if field not in row_dict]
        if missing:
            missing_str = ", ".join(missing)
            raise ValueError(
                f"metric_query rows missing required columns at row {index}: {missing_str}"
            )
        normalized.append(row_dict)
    return normalized


def comparison_slice_label(row: dict[str, Any], dimensions: list[str]) -> str:
    """Build a slice label from dimension values in a row."""
    if not dimensions:
        return "overall"
    parts = [
        f"{dimension}={row[dimension]}"
        for dimension in dimensions
        if row.get(dimension) is not None
    ]
    return ", ".join(parts) if parts else "overall"


def metric_query_debug_payload(
    request: ResolvedWindowedQueryRequest,
    *,
    all_rows: list[dict[str, Any]],
    window_length_match: bool | None = None,
) -> dict[str, Any]:
    """Build debug payload for metric query results."""
    debug: dict[str, Any] = {
        "current_window": [request.time_scope.current.start, request.time_scope.current.end],
        "current_has_data": any(row.get("current_sessions") for row in all_rows),
    }
    if request.time_scope.mode == "single_window":
        return debug
    if request.time_scope.baseline is None:
        raise ValueError("metric_query debug payload requires baseline window")
    debug.update(
        {
            "baseline_window": [
                request.time_scope.baseline.start,
                request.time_scope.baseline.end,
            ],
            "baseline_has_data": any(row.get("baseline_sessions") for row in all_rows),
            "window_length_match": bool(window_length_match),
        }
    )
    return debug


def metric_query_summary(
    metric_name: str,
    rows: list[dict[str, Any]],
    *,
    mode: str,
    debug: dict[str, Any],
    dimensions: list[str],
    grain: str,
    current_len: int | None = None,
    baseline_len: int | None = None,
) -> str:
    """Build a human-readable summary for metric query results."""
    if mode == "single_window":
        if rows:
            top = rows[0]
            slice_label = comparison_slice_label(top, dimensions)
            return (
                f"Metric '{metric_name}' current window observation: highest value is "
                f"{top['current_value']} for {slice_label} "
                f"(current_sessions={top['current_sessions']})."
            )
        if debug["current_has_data"]:
            return (
                f"Metric '{metric_name}' current window observation returned no retained rows. "
                f"current_window={debug['current_window']}."
            )
        return (
            f"Metric '{metric_name}' current window has no data. "
            f"current_window={debug['current_window']}."
        )

    if rows:
        top = rows[0]
        direction = "decline" if (top.get("delta_pct") or 0) < 0 else "increase"
        slice_label = comparison_slice_label(top, dimensions)
        summary = (
            f"Metric '{metric_name}' comparison: top {direction} is "
            f"{top['delta_pct']}% for {slice_label} "
            f"(current_value={top['current_value']}, baseline_value={top['baseline_value']})."
        )
        if not debug["window_length_match"]:
            if current_len is None or baseline_len is None:
                raise ValueError("metric_query compare summary requires both window lengths")
            unit = "h" if grain == "hour" else "d"
            summary += (
                f" Window size mismatch: current={current_len}{unit}, "
                f"baseline={baseline_len}{unit}; count/sum metrics may not be comparable."
            )
        return summary

    if debug["current_has_data"] or debug["baseline_has_data"]:
        missing = []
        if not debug["current_has_data"]:
            missing.append("current")
        if not debug["baseline_has_data"]:
            missing.append("baseline")
        missing_str = " and ".join(missing) if missing else "one"
        return (
            f"Metric '{metric_name}' comparison: {missing_str} window has no data. "
            f"current_window={debug['current_window']}, baseline_window={debug['baseline_window']}."
        )

    return (
        f"Metric '{metric_name}' comparison returned no results. "
        f"current_window={debug['current_window']}, baseline_window={debug['baseline_window']}."
    )


def window_length(request: ResolvedWindowedQueryRequest, which: str) -> int:
    """Return the length of the current or baseline window in the request's grain."""
    if which == "current":
        window = request.time_scope.current
    else:
        if request.time_scope.baseline is None:
            raise ValueError("baseline window is not available")
        window = request.time_scope.baseline
    if request.time_scope.grain == "hour":
        start_dt = datetime.fromisoformat(window.start)
        end_dt = datetime.fromisoformat(window.end)
        return int((end_dt - start_dt).total_seconds() // 3600)
    start_day = date.fromisoformat(window.start)
    end_day = date.fromisoformat(window.end)
    return (end_day - start_day).days


def normalize_metric_query_order(order: str | None, *, mode: str) -> str | None:
    """Normalize the ORDER BY clause for a metric query mode."""
    normalized_mode = metric_query_mode_contract(mode)["mode"]
    if order is None:
        return "CURRENT_VALUE DESC" if normalized_mode == "single_window" else None
    normalized = order.strip().upper()
    if normalized_mode == "compare":
        if normalized in {"ASC", "DESC"}:
            return f"DELTA_PCT {normalized}"
        if normalized in {"DELTA_PCT ASC", "DELTA_PCT DESC"}:
            return normalized
        raise ValueError("metric_query compare mode supports only delta_pct ASC/DESC")
    if normalized in {
        "CURRENT_VALUE ASC",
        "CURRENT_VALUE DESC",
        "CURRENT_SESSIONS ASC",
        "CURRENT_SESSIONS DESC",
    }:
        return normalized
    raise ValueError(
        "metric_query single_window mode supports only current_value ASC/DESC or current_sessions ASC/DESC"
    )


_TEMPORAL_DIMENSIONS: frozenset[str] = frozenset(
    {
        "log_date",
        "event_date",
        "dt",
        "date",
        "day",
        "log_hour",
        "event_hour",
        "hour",
        "minute",
        "event_time",
        "timestamp",
        "ts",
    }
)

_MAX_DEFAULT_DIMENSIONS: int = 2


def infer_date_column(dimensions: list[str]) -> str:
    """Infer the date column from a metric's semantic dimensions.

    Checks for common date column names in priority order and falls back
    to ``event_date`` when no match is found.
    """
    candidates = ("log_date", "event_date", "dt", "date", "day")
    for candidate in candidates:
        if candidate in dimensions:
            return candidate
    return "event_date"


def comparison_dimensions(
    all_dimensions: list[str],
    date_column: str,
    *,
    requested: list[str] | None = None,
) -> list[str]:
    """Select dimensions suitable for a comparison GROUP BY.

    * Always excludes *date_column* (grouping by the period-splitting
      column produces NULL pivots).
    * When the caller supplied explicit *requested* dimensions, only
      *date_column* is removed -- the caller made a deliberate choice.
    * When no explicit dimensions are requested, all temporal
      dimensions (``_TEMPORAL_DIMENSIONS``) are stripped and the result
      is capped at ``_MAX_DEFAULT_DIMENSIONS``.
    """
    if requested:
        return [d for d in requested if d != date_column]

    excluded = _TEMPORAL_DIMENSIONS | {date_column}
    dims = [d for d in all_dimensions if d not in excluded]
    return dims[:_MAX_DEFAULT_DIMENSIONS]


def comparison_time_dimension_column(
    request: ResolvedWindowedQueryRequest,
    all_dimensions: list[str],
) -> str:
    """Determine the time dimension column used for period splitting."""
    analysis_expr = str(request.resolved_time_axis.analysis_time_expr or "").strip()
    if analysis_expr in all_dimensions:
        return analysis_expr
    override = request.resolved_time_axis.override_analysis_time_column
    if override:
        return str(override)
    return infer_date_column(all_dimensions)


def detect_date_format(raw_value: Any) -> str | None:
    """Detect whether a raw date value is YYYYMMDD or ISO format.

    Returns a strftime format string if the value is a compact date
    string, or ``None`` for native DATE / ISO strings.
    """
    if isinstance(raw_value, str) and len(raw_value) == 8 and raw_value.isdigit():
        return "%Y%m%d"
    return None


def shift_calendar_date(d: date, *, months: int = 0, years: int = 0) -> date:
    """Calendar shift with end-of-month clamp (e.g. 2026-03-31 -> 2026-02-28)."""
    from calendar import monthrange

    target_month = d.month + months
    target_year = d.year + years + (target_month - 1) // 12
    target_month = (target_month - 1) % 12 + 1
    target_day = min(d.day, monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day)


def compute_baseline_from_type(
    current_start: date, current_end: date, comparison_type: str
) -> tuple[date, date]:
    """Compute baseline window from a comparison_type enum.

    dod: shift -1 day  wow: shift -7 days
    mom: shift -1 calendar month  yoy: shift -1 calendar year
    The baseline window preserves the same span as the current window.
    """
    ct = comparison_type.lower()
    if ct == "dod":
        delta = timedelta(days=1)
        return current_start - delta, current_end - delta
    if ct == "wow":
        delta = timedelta(days=7)
        return current_start - delta, current_end - delta
    if ct == "mom":
        bs = shift_calendar_date(current_start, months=-1)
        return bs, bs + (current_end - current_start)
    if ct == "yoy":
        bs = shift_calendar_date(current_start, years=-1)
        return bs, bs + (current_end - current_start)
    raise ValueError(
        f"Unknown comparison_type '{comparison_type}'. Supported values: dod, wow, mom, yoy."
    )


# ── Model-loading helper ────────────────────────────────────────────────


def _load_model(runtime: MarivoRuntime, model_selector: Any) -> SemanticModel:
    model = runtime.ports.model_store.get(model_selector)
    if model is None:
        raise NotFoundError(
            code=ErrorCode.MODEL_NOT_FOUND,
            message=f"Model not found: {model_selector}",
        )
    return model


# ── Runtime-dependent helpers ───────────────────────────────────────────


def _resolve_runtime_metric_contract(
    runtime: MarivoRuntime, metric_ref: str
) -> ResolvedSemanticObject | None:
    metric_ref = _coerce_metric_ref(metric_ref)
    repo = runtime.ports.semantic_repository
    if repo is None:
        raise SemanticRuntimeNotReadyError(
            f"Semantic repository not available: {metric_ref}",
            semantic_ref=metric_ref,
            object_kind="metric",
            lifecycle_status="unknown",
            readiness_status="unavailable",
            blocking_requirements=[],
            capabilities={},
            dependency_refs=[],
        )
    try:
        result: ResolvedSemanticObject | None = repo.resolve_metric_ref(metric_ref)
        return result
    except (
        SemanticRuntimeInvalidRefError,
        SemanticRuntimeNotFoundError,
        SemanticRuntimeUnpublishedError,
    ):
        return None


def _metric_family_for_ref(runtime: MarivoRuntime, metric_ref: str) -> str | None:
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    header = resolved.semantic_object.get("header") or {}
    return _optional_str(header.get("metric_family"))


def _metric_input_field_map(runtime: MarivoRuntime, metric_ref: str) -> dict[str, str] | None:
    """Resolve metric_input slot names from the execution binding chosen for the metric."""
    metric_ref = _coerce_metric_ref(metric_ref)
    metric_family = _metric_family_for_ref(runtime, metric_ref)
    if metric_family is None:
        return None
    resolution = _select_metric_binding_resolution(
        runtime,
        metric_ref,
        required_slots=required_metric_input_slots(metric_family),
    )
    if resolution is None:
        return None
    return dict(resolution.input_field_map)


def _select_metric_binding_resolution(
    runtime: MarivoRuntime,
    metric_ref: str,
    *,
    required_slots: tuple[str, ...] = (),
    session_id: str | None = None,
) -> MetricBindingResolution | None:
    _ = (runtime, metric_ref, required_slots, session_id)
    return None


def _metric_binding_candidates(
    runtime: MarivoRuntime,
    metric_ref: str,
    *,
    session_id: str | None = None,
) -> list[dict[str, Any]]:
    _ = (runtime, metric_ref, session_id)
    return []


def _dataset_native_metric_resolution(
    runtime: MarivoRuntime, metric_ref: str
) -> MetricExecutionContext | None:
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    payload = resolved.semantic_object.get("payload") or {}
    dataset_source = _optional_str(payload.get("dataset_source"))
    datasource_id = _optional_str(payload.get("datasource_id"))
    if dataset_source is None or datasource_id is None:
        return None
    metric_family = _metric_family_for_ref(runtime, metric_ref)
    if metric_family is None:
        return None
    input_field_map = dataset_native_metric_input_field_map(metric_family, payload)
    authority_locator = dataset_source_to_authority_locator(dataset_source)
    return MetricExecutionContext(
        metric_ref=metric_ref,
        table_name=dataset_source,
        binding_ref=metric_ref,
        carrier_binding_key=None,
        source_object_ref=None,
        carrier_locator=authority_locator,
        authority_locator=authority_locator,
        mapping_id=None,
        execution_locator={**authority_locator, "datasource_id": datasource_id},
        routing_detail={"resolution_status": "dataset_native", "datasource_id": datasource_id},
        input_field_map=input_field_map,
        additivity_constraints=resolved.semantic_object.get("header", {}).get(
            "additivity_constraints"
        ),
    )


def _compile_typed_metric_sql(
    runtime: MarivoRuntime,
    metric_family: str,
    payload: dict[str, Any],
    metric_ref: str,
    *,
    input_field_map: dict[str, str] | None = None,
    engine_type: str | None = None,
) -> str | None:
    """Compile an aggregate SQL expression from a typed metric contract."""
    input_field_map = input_field_map or _metric_input_field_map(runtime, metric_ref)
    if input_field_map is None:
        return None

    if metric_family == "count_metric":
        count_target = payload.get("count_target") or {}
        aggregation = _optional_str(count_target.get("aggregation")) or "count"
        field_name = input_field_map.get("count_target")
        if aggregation == "count_distinct" and field_name:
            return f"COUNT(DISTINCT {field_name})"
        if aggregation == "count" and field_name:
            return f"COUNT({field_name})"
        return None

    if metric_family == "sum_metric":
        field_name = input_field_map.get("measure")
        if field_name:
            return f"SUM({field_name})"
        return None

    if metric_family == "average_metric":
        num_field = input_field_map.get("numerator")
        den_field = input_field_map.get("denominator")
        if num_field and den_field:
            return f"SUM({num_field}) / NULLIF(COUNT({den_field}), 0)"
        return None

    if metric_family == "rate_metric":
        num_field = input_field_map.get("numerator")
        den_field = input_field_map.get("denominator")
        if num_field and den_field:
            return f"SUM({num_field}) / NULLIF(SUM({den_field}), 0)"

    if metric_family == "distribution_metric":
        value_field = input_field_map.get("value_component")
        if value_field is None:
            return None
        distribution_spec = payload.get("distribution_spec") or {}
        kind = _optional_str(distribution_spec.get("kind"))
        if kind in {"percentile", "quantile"}:
            percentile = distribution_spec.get("percentile")
            if percentile is None:
                raise ValueError(
                    f"Metric '{metric_name_from_ref(metric_ref)}' is missing "
                    "distribution_spec.percentile for distribution_metric compilation"
                )
            try:
                percentile_value = float(percentile)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"Metric '{metric_name_from_ref(metric_ref)}' has a non-numeric "
                    "distribution_spec.percentile"
                ) from error
            if engine_type == "trino":
                return f"APPROX_PERCENTILE({value_field}, {percentile_value})"
            if engine_type == "duckdb":
                return f"QUANTILE_CONT({value_field}, {percentile_value})"
            raise ValueError(
                f"Metric '{metric_name_from_ref(metric_ref)}' requires an engine-specific "
                f"distribution kernel, unsupported engine_type='{engine_type}'"
            )
        if kind == "histogram_ready":
            raise ValueError(
                f"observe: UNSUPPORTED_OPERATION - metric '{metric_name_from_ref(metric_ref)}' "
                "uses distribution_spec.kind='histogram_ready', which standard observe does "
                "not compile in v1"
            )
        raise ValueError(
            f"observe: UNSUPPORTED_OPERATION - metric '{metric_name_from_ref(metric_ref)}' "
            f"uses unsupported distribution_spec.kind='{kind}'"
        )

    return None


def _compile_typed_metric_value_sql(
    runtime: MarivoRuntime,
    metric_family: str,
    payload: dict[str, Any],
    metric_ref: str,
    *,
    input_field_map: dict[str, str] | None = None,
) -> str | None:
    """Compile a per-row value expression from a typed metric contract."""
    input_field_map = input_field_map or _metric_input_field_map(runtime, metric_ref)
    if input_field_map is None:
        return None

    if metric_family == "sum_metric":
        return input_field_map.get("measure")

    if metric_family == "average_metric":
        numerator = payload.get("numerator") or {}
        denominator = payload.get("denominator") or {}
        if (_optional_str(numerator.get("aggregation")) or "sum") == "sum" and (
            _optional_str(denominator.get("aggregation")) or "count"
        ) == "count":
            return input_field_map.get("numerator")

    return None


# ── Public use-case functions ───────────────────────────────────────────


def resolve_metric_execution_context(
    runtime: MarivoRuntime,
    metric_ref: str,
    *,
    session_id: str | None = None,
) -> MetricExecutionContext:
    """Resolve the full execution context for a metric, including table, bindings, and routing."""
    metric_ref = _coerce_metric_ref(metric_ref)
    metric_name = metric_name_from_ref(metric_ref)
    repo = runtime.ports.semantic_repository
    if repo is None:
        raise SemanticRuntimeNotReadyError(
            f"Semantic repository not available: {metric_ref}",
            semantic_ref=metric_ref,
            object_kind="metric",
            lifecycle_status="unknown",
            readiness_status="unavailable",
            blocking_requirements=[],
            capabilities={},
            dependency_refs=[],
        )
    try:
        availability = repo.inspect_ref(metric_ref)
    except (SemanticRuntimeInvalidRefError, SemanticRuntimeNotFoundError):
        raise ValueError(f"Metric '{metric_name}' not found or not published") from None

    if availability.lifecycle_status != "active":
        raise ValueError(f"Metric '{metric_name}' not found or not published")
    if availability.readiness_status != "ready":
        raise SemanticRuntimeNotReadyError(
            f"Semantic ref is not ready: {metric_ref}",
            semantic_ref=metric_ref,
            object_kind=availability.resolved.object_kind,
            lifecycle_status=availability.lifecycle_status,
            readiness_status=availability.readiness_status,
            blocking_requirements=availability.blocking_requirements,
            capabilities=availability.capabilities,
            dependency_refs=availability.dependency_refs,
        )

    metric_family = _metric_family_for_ref(runtime, metric_ref)
    if metric_family is None:
        raise ValueError(f"Metric '{metric_name}' is missing metric_family metadata")
    required_slots = required_metric_input_slots(metric_family)
    resolution = _select_metric_binding_resolution(
        runtime,
        metric_ref,
        required_slots=required_slots,
        session_id=session_id,
    )
    metric_header = dict(availability.resolved.semantic_object.get("header") or {})
    metric_additivity_constraints = metric_header.get("additivity_constraints")
    dataset_resolution = _dataset_native_metric_resolution(runtime, metric_ref)
    if dataset_resolution is not None:
        return dataset_resolution
    if resolution is not None and resolution.table_name is not None:
        return MetricExecutionContext(
            metric_ref=metric_ref,
            table_name=resolution.table_name,
            binding_ref=resolution.binding_ref,
            carrier_binding_key=resolution.carrier_binding_key,
            source_object_ref=resolution.source_object_ref,
            carrier_locator=resolution.carrier_locator,
            authority_locator=resolution.authority_locator,
            mapping_id=resolution.mapping_id,
            execution_locator=resolution.execution_locator,
            routing_detail=resolution.routing_detail,
            input_field_map=dict(resolution.input_field_map),
            additivity_constraints=metric_additivity_constraints,
        )
    candidate_bindings = _metric_binding_candidates(runtime, metric_ref, session_id=session_id)
    metric_input_failures = [
        candidate
        for candidate in candidate_bindings
        if candidate.get("failure_stage") == "metric_input_coverage"
    ]
    if metric_input_failures:
        missing_slots = sorted(
            {
                str(slot)
                for candidate in metric_input_failures
                for slot in list(candidate.get("missing_metric_input_slots") or [])
                if str(slot).strip()
            }
        )
        raise ValueError(
            f"Metric execution binding for '{metric_name}' is missing required metric_input "
            f"coverage ({', '.join(missing_slots)})"
        )
    has_mapping_failures = any(
        candidate.get("failure_stage") == "mapping_route_preflight"
        for candidate in candidate_bindings
    )

    raise SemanticRuntimeNotReadyError(
        f"Metric execution preflight failed: {metric_ref}",
        semantic_ref=metric_ref,
        object_kind=availability.resolved.object_kind,
        lifecycle_status=availability.lifecycle_status,
        readiness_status=availability.readiness_status,
        blocking_requirements=[
            {
                "code": "METRIC_EXECUTION_BINDING_UNRESOLVED",
                "message": (
                    "Metric is ready in the semantic layer, but execution could not resolve "
                    "any published binding carrier to an execution route."
                    if has_mapping_failures
                    else "Metric is ready in the semantic layer, but execution could not "
                    "resolve any published binding carrier to a synced source object."
                ),
                "subject_ref": metric_ref,
                "details": {
                    "failure_stage": "metric_execution_preflight",
                    "candidate_bindings": candidate_bindings,
                },
            }
        ],
        capabilities=availability.capabilities,
        dependency_refs=availability.dependency_refs,
    )


def resolve_metric(
    runtime: MarivoRuntime,
    metric_name: str,
) -> Any:
    """Resolve a metric by name from the semantic repository."""
    repo = runtime.ports.semantic_repository
    if repo is None:
        raise SemanticRuntimeNotReadyError(
            f"Semantic repository not available: metric.{metric_name}",
            semantic_ref=f"metric.{metric_name}",
            object_kind="metric",
            lifecycle_status="unknown",
            readiness_status="unavailable",
            blocking_requirements=[],
            capabilities={},
            dependency_refs=[],
        )
    return repo.resolve_metric(metric_name)


def resolve_metric_table(
    runtime: MarivoRuntime,
    metric_ref: str,
    *,
    session_id: str | None = None,
) -> str | None:
    """Resolve an execution-ready table for a metric, if one can be derived."""
    try:
        return resolve_metric_execution_context(
            runtime,
            metric_ref,
            session_id=session_id,
        ).table_name
    except (SemanticRuntimeNotReadyError, ValueError):
        return None


def resolve_metric_dimensions(
    runtime: MarivoRuntime,
    metric_ref: str,
) -> list[str] | None:
    """Look up a published metric's dimensions from semantic runtime or entity binding."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    semantic_object = resolved.semantic_object
    header = semantic_object.get("header") or {}
    payload = semantic_object.get("payload") or {}

    legacy_dims = payload.get("dimensions")
    if legacy_dims is not None:
        return [str(dimension) for dimension in list(legacy_dims)]

    observed_entity_ref = _optional_str(header.get("observed_entity_ref"))
    if observed_entity_ref is not None:
        return _resolve_entity_dimensions(runtime, observed_entity_ref)

    return []


def _resolve_entity_dimensions(runtime: MarivoRuntime, entity_ref: str) -> list[str]:
    """Get canonical dimensions exposed by published entity bindings."""
    metadata = runtime.ports.metadata
    if metadata is None:
        return []
    result = resolve_entity_binding_dimensions(metadata, entity_ref)
    return list(result) if result is not None else []


def compile_step_with_feedback(
    runtime: MarivoRuntime,
    step: AnalysisStepIR,
    *,
    engine_type: str,
    semantic_context: dict[str, Any] | None = None,
) -> CompiledQuery:
    """Compile an analysis step IR into a query, with error feedback.

    Injects semantic_repository and compatibility_profile_reader from the
    runtime's ports, plus calendar_data_reader when available.
    """
    effective_semantic_context = dict(semantic_context or {})
    repo = runtime.ports.semantic_repository
    if repo is not None:
        effective_semantic_context.setdefault("semantic_repository", repo)
        effective_semantic_context.setdefault(
            "compatibility_profile_reader",
            repo._published_compatibility_profiles_for_subject_ref,
        )
    if runtime.ports.calendar_data_reader is not None:
        effective_semantic_context.setdefault(
            "calendar_data_reader", runtime.ports.calendar_data_reader
        )
    try:
        compiled = compile_step(
            step,
            engine_type=engine_type,
            semantic_context=effective_semantic_context,
        )
        execution_context = effective_semantic_context.get("metric_execution_context")
        if isinstance(execution_context, MetricExecutionContext):
            compiled.metadata["metric_execution_context"] = {
                "metric_ref": execution_context.metric_ref,
                "mapping_id": execution_context.mapping_id,
                "execution_locator": dict(execution_context.execution_locator or {}),
                "routing_detail": dict(execution_context.routing_detail or {}),
                "table_name": execution_context.table_name,
            }
        return compiled
    except (
        SemanticRuntimeNotReadyError,
        SemanticRequestCompatibilityError,
        ValueError,
    ) as error:
        raise compile_failure_from_error(
            step,
            error,
            semantic_context=effective_semantic_context,
        ) from error


def resolve_windowed_query_time_axis(
    runtime: MarivoRuntime,
    request: ResolvedWindowedQueryRequest,
    *,
    engine_type: str,
    metric_name: str | None = None,
    fallback_columns: list[str] | None = None,
) -> None:
    """Resolve the time axis for a windowed query request.

    Mutates request.resolved_time_axis in place via TimeAxisResolver.
    """
    has_explicit_override = any(
        (
            request.resolved_time_axis.override_analysis_time_column,
            request.resolved_time_axis.override_partition_date_column,
            request.resolved_time_axis.override_partition_hour_column,
        )
    )
    time_provider = runtime.ports.time_axis_metadata_provider
    if time_provider is None:
        raise ValueError("time_axis_metadata_provider not available in local mode")
    try:
        metadata_context = time_provider.load_for_windowed_query(
            table_name=request.table,
            metric_name=metric_name,
        )
    except ValueError:
        if not has_explicit_override:
            raise
        metadata_context = TimeAxisMetadataContext(
            available_columns=time_provider.load_available_columns(request.table)
        )

    available_columns = list(metadata_context.available_columns)
    if available_columns:
        for column in fallback_columns or []:
            name = str(column).strip()
            if name and name not in available_columns:
                available_columns.append(name)

    resolver = TimeAxisResolver(
        request=request,
        engine_type=engine_type,
        available_columns=available_columns,
        entity_time_capabilities=metadata_context.entity_time_capabilities,
        source_time_capabilities=metadata_context.source_time_capabilities,
    )
    request.resolved_time_axis = resolver.resolve()


def _resolve_scope_constraint_column(
    runtime: MarivoRuntime,
    constraint_key: str,
    *,
    metric_ref: str | None,
    table_name: str | None,
) -> str:
    """Resolve a scope constraint key to a physical column name."""
    if "." not in constraint_key:
        return constraint_key
    if not constraint_key.startswith("dimension."):
        raise ValueError(
            f"scope.constraints key '{constraint_key}' must be a physical column or "
            "a canonical dimension ref like 'dimension.cluster'"
        )
    if metric_ref is None or table_name is None:
        raise ValueError(
            f"scope.constraints key '{constraint_key}' requires a semantic metric scope"
        )

    dimension_sources = _metric_scope_dimension_sources(runtime, metric_ref, table_name)
    physical_names = sorted(dimension_sources.get(constraint_key) or [])
    if not physical_names:
        raise ValueError(
            f"scope.constraints key '{constraint_key}' is not available in metric semantic scope"
        )
    if len(physical_names) > 1:
        raise ValueError(
            f"scope.constraints key '{constraint_key}' does not resolve to a unique physical column"
        )
    return physical_names[0]


def _metric_scope_dimension_sources(
    runtime: MarivoRuntime,
    metric_ref: str,
    table_name: str,
) -> dict[str, set[str]]:
    """Resolve dimension-to-physical-column mapping for a metric scope."""
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return {}
    payload = resolved.semantic_object.get("payload") or {}
    dataset_source = _optional_str(payload.get("dataset_source"))
    if dataset_source is not None and not table_name_matches_locator(
        table_name,
        dataset_source,
    ):
        return {}
    fields = payload.get("dataset_fields")
    available = set(fields) if isinstance(fields, dict) else set()
    dimensions = [str(item) for item in list(payload.get("dimensions") or [])]
    result: dict[str, set[str]] = {}
    for dimension in dimensions:
        if dimension == "event_date":
            continue
        physical_name = dimension.removeprefix("dimension.")
        if not available or physical_name in available:
            result.setdefault(dimension, set()).add(physical_name)
    return result


def _constraints_dict_to_filter(
    runtime: MarivoRuntime,
    constraints: dict[str, Any],
    *,
    resolve_semantic_refs: bool = False,
    metric_ref: str | None = None,
    table_name: str | None = None,
) -> str | None:
    """Convert a constraints dict to a SQL WHERE filter string."""
    parts: list[str] = []
    for key, value in constraints.items():
        if isinstance(value, (dict, list)):
            continue
        column_name = key
        if resolve_semantic_refs:
            column_name = _resolve_scope_constraint_column(
                runtime,
                key,
                metric_ref=metric_ref,
                table_name=table_name,
            )
        parts.append(f"{column_name} = '{value}'")
    return " AND ".join(parts) if parts else None


def _resolve_predicate_ref_to_filter(
    runtime: MarivoRuntime,
    predicate_ref: str,
    *,
    metric_ref: str | None = None,
    table_name: str | None = None,
) -> str | None:
    """Resolve a predicate_ref to a SQL filter expression."""
    metadata = runtime.ports.metadata
    if metadata is None:
        raise ValueError("metadata port not available in local mode")
    row = metadata.query_one(
        "SELECT payload_json FROM semantic_predicate_contracts "
        "WHERE predicate_ref = ? AND status = 'published'",
        [predicate_ref],
    )
    if row is None:
        raise ValueError(
            f"predicate_ref '{predicate_ref}' does not reference a published predicate"
        )
    payload = json.loads(row["payload_json"] or "{}")
    expression = payload.get("expression")
    if expression is None:
        return None
    return _predicate_expression_to_sql(
        runtime, expression, metric_ref=metric_ref, table_name=table_name
    )


def _resolve_predicate_target_column(
    runtime: MarivoRuntime,
    target_ref: str,
    *,
    metric_ref: str | None = None,
    table_name: str | None = None,
) -> str:
    """Resolve a predicate target_ref to a physical column name."""
    if "." not in target_ref:
        return target_ref
    if target_ref.startswith("dimension."):
        return _resolve_scope_constraint_column(
            runtime,
            target_ref,
            metric_ref=metric_ref,
            table_name=table_name,
        )
    _ = (metric_ref, table_name)
    # Fall back: strip prefix and use as column hint (entity.user -> user).
    return target_ref.split(".", 1)[-1].replace(".", "_")


def _predicate_expression_to_sql(
    runtime: MarivoRuntime,
    expr: dict[str, Any],
    *,
    metric_ref: str | None = None,
    table_name: str | None = None,
) -> str:
    """Convert a predicate expression dict to a SQL WHERE clause."""
    op = expr.get("op")
    if op == "and":
        items = expr.get("items") or []
        parts = [
            _predicate_expression_to_sql(
                runtime, item, metric_ref=metric_ref, table_name=table_name
            )
            for item in items
        ]
        return " AND ".join(parts)
    target_ref = expr.get("target_ref", "")
    column = _resolve_predicate_target_column(
        runtime,
        target_ref,
        metric_ref=metric_ref,
        table_name=table_name,
    )
    value: Any = expr.get("value")
    if op in ("is_null", "is_not_null"):
        return f"{column} IS NULL" if op == "is_null" else f"{column} IS NOT NULL"
    if op == "between":
        lo, hi = value[0], value[1]
        return f"{column} BETWEEN '{lo}' AND '{hi}'"
    if op in ("in", "not_in"):
        vals = ", ".join(f"'{v}'" for v in value)
        sql_in = f"{column} IN ({vals})"
        return sql_in if op == "in" else f"NOT {sql_in}"
    if value is not None:
        return f"{column} {op} '{value}'"
    return f"{column} {op}"


def build_scoped_query(
    runtime: MarivoRuntime,
    session_id: str,
    request: ResolvedWindowedQueryRequest,
    *,
    engine_type: str,
) -> dict[str, Any]:
    """Build a scoped query context dict for a windowed query request."""
    analysis_time_expr = request.resolved_time_axis.analysis_time_expr
    if not analysis_time_expr:
        raise ValueError("windowed execution requires resolved_time_axis.analysis_time_expr")
    metric_ref = None
    if isinstance(request.value_spec, SemanticMetricValueSpec):
        metric_ref = request.value_spec.metric
    return {
        "mode": request.time_scope.mode,
        "engine_type": engine_type,
        "analysis_time_kind": request.resolved_time_axis.analysis_time_kind,
        "analysis_time_expr": analysis_time_expr,
        "analysis_time_format": request.resolved_time_axis.analysis_time_format,
        "partition_pruning_predicate": request.resolved_time_axis.partition_pruning_predicate,
        "current": {
            "start": request.time_scope.current.start,
            "end": request.time_scope.current.end,
        },
        "baseline": (
            {
                "start": request.time_scope.baseline.start,
                "end": request.time_scope.baseline.end,
            }
            if request.time_scope.baseline is not None
            else None
        ),
        "session_constraints_filter": None,
        "session_raw_filter": None,
        "scope_constraints_filter": _constraints_dict_to_filter(
            runtime,
            request.scope.constraints,
            resolve_semantic_refs=True,
            metric_ref=metric_ref,
            table_name=request.table,
        ),
        "scope_predicate_filter": (
            _resolve_predicate_ref_to_filter(
                runtime,
                request.scope.predicate_ref,
                metric_ref=metric_ref,
                table_name=request.table,
            )
            if request.scope.predicate_ref is not None
            else request.scope.predicate
        ),
    }


def build_metric_query(
    runtime: MarivoRuntime,
    metric_name: str,
    table_name: str,
    metric_sql: str,
    dimensions: list[str],
    date_column: str = "event_date",
    order: str = "ASC",
    limit: int = 3,
) -> str:
    """Build a current-vs-baseline comparison SQL query from metric definition.

    Uses the metric's SQL expression and dimensions to generate a
    sliced comparison query with delta_pct calculation.
    """
    _ = runtime  # currently pure, runtime accepted for future semantic resolution
    result = compile_metric_query(
        metric_name=metric_name,
        table_name=table_name,
        metric_sql=metric_sql,
        dimensions=dimensions,
        date_column=date_column,
        order=order,
        limit=limit,
    )
    return str(result)


def resolve_metric_sql(
    runtime: MarivoRuntime,
    metric_ref: str,
) -> str | None:
    """Resolve an aggregate SQL expression for a published metric."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    semantic_object = resolved.semantic_object
    header = semantic_object.get("header") or {}
    payload = semantic_object.get("payload") or {}

    # Legacy: definition_sql in payload
    definition_sql = payload.get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)

    metric_family = _optional_str(header.get("metric_family"))
    typed_metric_ref = _optional_str(header.get("metric_ref"))
    if metric_family is not None and typed_metric_ref is not None:
        return _compile_typed_metric_sql(
            runtime,
            metric_family,
            payload,
            typed_metric_ref,
        )

    return None


def resolve_metric_value_sql(
    runtime: MarivoRuntime,
    metric_ref: str,
) -> str | None:
    """Resolve a per-row value expression for sample-summary style execution."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    semantic_object = resolved.semantic_object
    header = semantic_object.get("header") or {}
    payload = semantic_object.get("payload") or {}

    definition_sql = payload.get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)

    metric_family = _optional_str(header.get("metric_family"))
    typed_metric_ref = _optional_str(header.get("metric_ref"))
    if metric_family is not None and typed_metric_ref is not None:
        return _compile_typed_metric_value_sql(
            runtime,
            metric_family,
            payload,
            typed_metric_ref,
        )

    return None


def resolve_metric_sql_for_execution(
    runtime: MarivoRuntime,
    metric_ref: str,
    execution_context: MetricExecutionContext | None = None,
    *,
    engine_type: str | None = None,
) -> str:
    """Resolve the aggregate SQL expression for a metric, raising on missing metadata."""
    metric_ref = _coerce_metric_ref(metric_ref)
    metric_name = metric_name_from_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        raise ValueError(f"Metric '{metric_name}' not found or not published")
    header = resolved.semantic_object.get("header") or {}
    payload = resolved.semantic_object.get("payload") or {}
    definition_sql = payload.get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)
    metric_family = _optional_str(header.get("metric_family"))
    typed_metric_ref = _optional_str(header.get("metric_ref"))
    if metric_family is None or typed_metric_ref is None:
        raise ValueError(f"Metric '{metric_name}' is missing typed metric metadata")
    input_field_map = (
        dict(execution_context.input_field_map)
        if execution_context is not None and execution_context.input_field_map is not None
        else _metric_input_field_map(runtime, metric_ref)
    )
    sql = _compile_typed_metric_sql(
        runtime,
        metric_family,
        payload,
        typed_metric_ref,
        input_field_map=input_field_map,
        engine_type=engine_type,
    )
    if sql is None:
        required_slots = ", ".join(required_metric_input_slots(metric_family))
        raise ValueError(
            f"Metric execution binding for '{metric_name}' is missing required metric_input "
            f"coverage ({required_slots})"
        )
    return sql


def resolve_metric_value_sql_for_execution(
    runtime: MarivoRuntime,
    metric_ref: str,
    execution_context: MetricExecutionContext | None = None,
) -> str | None:
    """Resolve the per-row value SQL expression for a metric, raising on missing metadata."""
    metric_ref = _coerce_metric_ref(metric_ref)
    metric_name = metric_name_from_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        raise ValueError(f"Metric '{metric_name}' not found or not published")
    header = resolved.semantic_object.get("header") or {}
    payload = resolved.semantic_object.get("payload") or {}
    definition_sql = payload.get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)
    metric_family = _optional_str(header.get("metric_family"))
    typed_metric_ref = _optional_str(header.get("metric_ref"))
    if metric_family is None or typed_metric_ref is None:
        raise ValueError(f"Metric '{metric_name}' is missing typed metric metadata")
    input_field_map = (
        dict(execution_context.input_field_map)
        if execution_context is not None and execution_context.input_field_map is not None
        else _metric_input_field_map(runtime, metric_ref)
    )
    return _compile_typed_metric_value_sql(
        runtime,
        metric_family,
        payload,
        typed_metric_ref,
        input_field_map=input_field_map,
    )


def build_step_semantic_metadata(
    runtime: MarivoRuntime,
    compiled_queries: CompiledQuery | list[CompiledQuery],
) -> dict[str, Any] | None:
    """Assemble a typed semantic snapshot from one or more CompiledQuery objects.

    Ghost method: delegates to the pure core function but keeps the runtime
    parameter for consistency with the other use-case functions in this module.
    """
    from app.core.semantic.step_metadata import build_step_semantic_metadata as _build
    from app.evidence_engine.ref_boundary import assert_no_canonical_refs_in_semantic_payload

    _ = runtime  # currently pure, runtime accepted for future semantic resolution
    result: dict[str, Any] | None = _build(compiled_queries)
    if result is not None:
        assert_no_canonical_refs_in_semantic_payload(result, surface="step_semantic_metadata")
    return result


def _resolve_metric_direction(runtime: MarivoRuntime, metric_ref: str) -> str | None:
    """Look up a published metric's desired_direction for recommendation policy."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolver = runtime.ports.semantic_resolver
    if resolver is None:
        return None
    resolved = resolver.resolve_metric(metric_name_from_ref(metric_ref))
    return resolved.desired_direction if resolved else None


def _resolved_scope_filter(
    runtime: MarivoRuntime,
    session_id: str,
    request: ResolvedWindowedQueryRequest,
) -> str | None:
    """Build the combined scope filter for a windowed query request."""
    metric_ref = None
    if isinstance(request.value_spec, SemanticMetricValueSpec):
        metric_ref = request.value_spec.metric
    scope_constraints = _constraints_dict_to_filter(
        runtime,
        request.scope.constraints,
        resolve_semantic_refs=True,
        metric_ref=metric_ref,
        table_name=request.table,
    )
    scope_predicate = (
        _resolve_predicate_ref_to_filter(
            runtime,
            request.scope.predicate_ref,
            metric_ref=metric_ref,
            table_name=request.table,
        )
        if request.scope.predicate_ref is not None
        else request.scope.predicate
    )
    return merge_filters(
        scope_constraints,
        scope_predicate,
    )


def _resolve_entity_for_metric(runtime: MarivoRuntime, metric_ref: str) -> dict[str, Any] | None:
    """Return the published entity linked to the given metric name, or None."""
    try:
        metric_ref = _coerce_metric_ref(metric_ref)
        repo = runtime.ports.semantic_repository
        if repo is None:
            return None
        resolved_metric = repo.resolve_metric_ref(metric_ref)
        observed_entity_ref = resolved_metric.semantic_object.get("header", {}).get(
            "observed_entity_ref"
        )
        if not observed_entity_ref:
            return None
        resolved_entity = repo.resolve_entity(str(observed_entity_ref).removeprefix("entity."))
        if resolved_entity is None:
            return None
        return {
            "entity_contract_id": resolved_entity.metadata.get("entity_contract_id"),
            "name": resolved_entity.name,
            "status": resolved_entity.metadata.get("status"),
            "properties": dict(resolved_entity.metadata.get("properties") or {}),
        }
    except Exception:
        return None


def resolve_engine(
    runtime: MarivoRuntime,
    table_names: list[str],
    *,
    session_id: str | None = None,
) -> tuple[Any, str, dict[str, str]]:
    """Resolve the analytics engine, its type, and qualified table names.

    Uses the DataSource port (resolve_tables) to perform routing resolution,
    then extracts the engine, datasource_type, and qualified names from the
    RoutingResolutionResult.
    """
    resolution = runtime.ports.data_source.resolve_tables(table_names, session_id=session_id)
    qualified = resolution.route.qualified_names if resolution.route is not None else {}
    return resolution.engine, resolution.datasource_type, qualified


def resolve_engine_for_session(
    runtime: MarivoRuntime,
    session_id: str,
    table_names: list[str],
) -> tuple[Any, str, dict[str, str]]:
    """Resolve the analytics engine for a given session.

    Delegates to resolve_engine with session_id. Falls back to calling
    without session_id if the underlying router does not support it.
    """
    try:
        return resolve_engine(runtime, table_names, session_id=session_id)
    except TypeError as error:
        if "unexpected keyword argument 'session_id'" not in str(error):
            raise
        return resolve_engine(runtime, table_names)
