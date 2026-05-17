"""Semantic resolution + step compilation use case functions.

Absorbs the model-loading + metric-resolution + step-compilation helpers
that previously lived on SemanticLayerService. Functions take
runtime: MarivoRuntime as the first argument; the runtime carries
ports.model_store from which the SemanticModel is loaded, and the result
is then handed to core.semantic.* pure functions for the actual work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from marivo.contracts.errors import ErrorCode, NotFoundError
from marivo.contracts.semantic import SemanticModel
from marivo.core.semantic.additivity import is_all_additive_dimensions
from marivo.core.semantic.compiler import (
    CompiledQuery,
    SemanticRequestCompatibilityError,
)
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.core.semantic.resolution import ResolvedSemanticObject
from marivo.runtime.errors import (
    SemanticRuntimeInvalidRefError,
    SemanticRuntimeNotFoundError,
    SemanticRuntimeNotReadyError,
    SemanticRuntimeUnpublishedError,
)
from marivo.runtime.semantic.compile_step import compile_step
from marivo.runtime.semantic.feedback import compile_failure_from_error
from marivo.time_axis_metadata import TimeAxisMetadataContext
from marivo.time_scope import (
    ResolvedWindowedQueryRequest,
    SemanticMetricValueSpec,
    TimeAxisResolver,
)

if TYPE_CHECKING:
    from marivo.runtime.runtime import MarivoRuntime


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
    additive_dimensions: list[str] = field(default_factory=list)


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
    """Return the short metric name for display and semantic repository lookups."""
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
    repo = runtime.semantic_repository
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
        additive_dimensions=resolved.semantic_object.get("header", {}).get(
            "additive_dimensions", []
        ),
    )


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
    repo = runtime.semantic_repository
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

    resolution = _select_metric_binding_resolution(
        runtime,
        metric_ref,
        session_id=session_id,
    )
    metric_header = dict(availability.resolved.semantic_object.get("header") or {})
    metric_additive_dimensions = metric_header.get("additive_dimensions", [])
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
            additive_dimensions=metric_additive_dimensions,
        )
    raise SemanticRuntimeNotReadyError(
        f"Metric execution preflight failed: {metric_ref}",
        semantic_ref=metric_ref,
        object_kind=availability.resolved.object_kind,
        lifecycle_status=availability.lifecycle_status,
        readiness_status=availability.readiness_status,
        blocking_requirements=availability.blocking_requirements,
        capabilities=availability.capabilities,
        dependency_refs=availability.dependency_refs,
    )


def resolve_metric(
    runtime: MarivoRuntime,
    metric_name: str,
) -> Any:
    """Resolve a metric by name from the semantic repository."""
    repo = runtime.semantic_repository
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
    additive_dimensions = header.get("additive_dimensions")
    if isinstance(additive_dimensions, list):
        if is_all_additive_dimensions([str(dimension) for dimension in additive_dimensions]):
            payload = semantic_object.get("payload") or {}
            dimensions = payload.get("dimensions")
            if isinstance(dimensions, list):
                return [str(dimension) for dimension in dimensions]
        return [str(dimension) for dimension in additive_dimensions]

    observed_entity_ref = _optional_str(header.get("observed_entity_ref"))
    if observed_entity_ref is not None:
        return []

    return []


def compile_step_with_feedback(
    runtime: MarivoRuntime,
    step: AnalysisStepIR,
    *,
    engine_type: str,
    semantic_context: dict[str, Any] | None = None,
) -> CompiledQuery:
    """Compile an analysis step IR into a query, with error feedback.

    Injects semantic_repository and compatibility_profile_reader from the
    runtime's ports.
    """
    effective_semantic_context = dict(semantic_context or {})
    repo = runtime.semantic_repository
    if repo is not None:
        effective_semantic_context.setdefault("semantic_repository", repo)
        effective_semantic_context.setdefault(
            "compatibility_profile_reader",
            lambda subject_ref: None,
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
    time_provider = runtime.time_axis_metadata_provider
    if time_provider is None:
        raise ValueError("time_axis_metadata_provider not wired into runtime")
    try:
        metadata_context = time_provider.load_for_windowed_query(
            table_name=request.table,
            metric_name=metric_name,
            engine_type=engine_type,
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
        time_field_expressions=metadata_context.time_field_expressions,
        time_field_data_types=metadata_context.time_field_data_types,
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
        "scope_predicate_filter": request.scope.predicate,
    }


def resolve_metric_sql(
    runtime: MarivoRuntime,
    metric_ref: str,
) -> str | None:
    """Resolve an aggregate SQL expression for a published metric."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    definition_sql = resolved.semantic_object.get("payload", {}).get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)
    return None


def resolve_metric_value_sql(
    runtime: MarivoRuntime,
    metric_ref: str,
) -> str | None:
    """Resolve a per-row value expression for a published metric."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolved = _resolve_runtime_metric_contract(runtime, metric_ref)
    if resolved is None:
        return None
    definition_sql = resolved.semantic_object.get("payload", {}).get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)
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
    payload = resolved.semantic_object.get("payload") or {}
    definition_sql = payload.get("definition_sql")
    if definition_sql is not None:
        return str(definition_sql)
    raise ValueError(
        "Metric must have an ANSI SQL expression in its definition_sql or expression field"
    )


def build_step_semantic_metadata(
    runtime: MarivoRuntime,
    compiled_queries: CompiledQuery | list[CompiledQuery],
) -> dict[str, Any] | None:
    """Assemble a typed semantic snapshot from one or more CompiledQuery objects.

    Ghost method: delegates to the pure core function but keeps the runtime
    parameter for consistency with the other use-case functions in this module.
    """
    from marivo.core.semantic.step_metadata import build_step_semantic_metadata as _build
    from marivo.runtime.evidence.ref_boundary import assert_no_canonical_refs_in_semantic_payload

    _ = runtime  # currently pure, runtime accepted for future semantic resolution
    result: dict[str, Any] | None = _build(compiled_queries)
    if result is not None:
        assert_no_canonical_refs_in_semantic_payload(result, surface="step_semantic_metadata")
    return result


def _resolve_metric_direction(runtime: MarivoRuntime, metric_ref: str) -> str | None:
    """Look up a published metric's desired_direction for recommendation policy."""
    metric_ref = _coerce_metric_ref(metric_ref)
    resolver = runtime.semantic_resolver
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
    scope_predicate = request.scope.predicate
    return merge_filters(
        scope_constraints,
        scope_predicate,
    )


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


# ── Step execution functions (extracted from SemanticLayerService) ───────


def _resolve_engine_for_session_with_routing(
    runtime: MarivoRuntime,
    session_id: str,
    table_names: list[str],
) -> tuple[Any, str, dict[str, str], dict[str, Any] | None]:
    """Like resolve_engine_for_session but also returns routing feedback dict."""
    try:
        resolution = runtime.ports.data_source.resolve_tables(table_names, session_id=session_id)
    except TypeError as error:
        if "unexpected keyword argument 'session_id'" not in str(error):
            raise
        resolution = runtime.ports.data_source.resolve_tables(table_names)
    qualified = resolution.route.qualified_names if resolution.route is not None else {}
    routing_feedback = resolution.feedback.to_dict() if resolution.feedback is not None else None
    return resolution.engine, resolution.datasource_type, qualified, routing_feedback


def _make_provenance(
    sql: str = "",
    params: list[Any] | None = None,
    engine_type: str = "duckdb",
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a provenance token for a step execution."""
    from marivo.core.intent.primitives import make_provenance

    return make_provenance(sql, params, engine_type=engine_type, routing=routing)


def _insert_step(
    runtime: MarivoRuntime,
    step_id: str,
    session_id: str,
    step_type: str,
    summary: str,
    result: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    semantic_metadata: dict[str, Any] | None = None,
) -> None:
    """Insert a step record via runtime ports."""
    from marivo.contracts.ids import SessionId, StepId

    runtime.ports.step_store.insert_step(
        StepId(step_id),
        SessionId(session_id),
        step_type,
        summary,
        result,
        provenance=provenance,
        semantic_metadata=semantic_metadata,
    )


def _insert_artifact(
    runtime: MarivoRuntime,
    session_id: str,
    step_id: str,
    artifact_type: str,
    name: str,
    content: Any,
    *,
    lifecycle: str = "committed",
    artifact_schema_version: str | None = None,
) -> str:
    """Insert an artifact record via runtime ports. Returns artifact_id."""
    from uuid import uuid4

    from marivo.contracts.ids import SessionId, StepId

    artifact_id = f"art_{uuid4().hex[:12]}"
    runtime.ports.artifact_store.insert_artifact(
        SessionId(session_id),
        StepId(step_id),
        artifact_type,
        name,
        content,
        lifecycle=lifecycle,
        artifact_schema_version=artifact_schema_version,
    )
    return artifact_id


def _fetch_column_metadata(
    short_name: str,
    columns: list[str],
) -> dict[str, dict[str, str]]:
    """Column metadata resolution -- currently a no-op placeholder."""
    return {}


def run_metric_query(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Generic metric comparison step driven by semantic metric definitions."""
    from marivo.core.intent.primitives import new_step_id
    from marivo.runtime.semantic.executor import execute_compiled
    from marivo.time_scope import normalize_metric_query_request

    resolved = normalize_metric_query_request(params)
    if not isinstance(resolved.value_spec, SemanticMetricValueSpec):
        raise ValueError("metric_query requires a semantic metric request")
    mode = resolved.time_scope.mode
    metric_name = resolved.value_spec.metric

    step_type = "metric_query"
    step_id = new_step_id()

    metric_sql = resolve_metric_sql(runtime, metric_name)
    all_dimensions = resolve_metric_dimensions(runtime, metric_name)
    if metric_sql is None or all_dimensions is None:
        raise ValueError(
            f"Metric '{metric_name}' not found, not published, or missing typed execution metadata"
        )

    engine, engine_type, qualified, routing_feedback = _resolve_engine_for_session_with_routing(
        runtime, session_id, [resolved.table]
    )
    resolve_windowed_query_time_axis(
        runtime,
        resolved,
        engine_type=engine_type,
        metric_name=metric_name,
        fallback_columns=all_dimensions,
    )
    scoped_query = build_scoped_query(runtime, session_id, resolved, engine_type=engine_type)
    comparison_time_column = comparison_time_dimension_column(resolved, all_dimensions)

    # Allow caller to select a subset of dimensions for grouping
    requested_dims = list(resolved.grouping)
    if requested_dims:
        invalid = set(requested_dims) - set(all_dimensions)
        if invalid:
            raise ValueError(f"Invalid dimensions {invalid}; valid: {all_dimensions}")

    dimensions = comparison_dimensions(
        all_dimensions,
        comparison_time_column,
        requested=requested_dims,
    )
    if requested_dims and not dimensions:
        filtered_out = [d for d in requested_dims if d == comparison_time_column]
        raise ValueError(
            f"Cannot use '{filtered_out[0]}' as comparison dimension because "
            f"it is the period-splitting column (date_column='{comparison_time_column}'). "
            f"Use a different dimension or omit dimensions for overall aggregate comparison."
        )
    limit = resolved.limit or 10

    qualified_table = qualified.get(resolved.table, resolved.table)
    current_len = window_length(resolved, "current")
    baseline_len: int | None = None
    window_size_mismatch = False
    if mode == "compare":
        baseline_len = window_length(resolved, "baseline")
        window_size_mismatch = current_len != baseline_len
    compiled_query = compile_step_with_feedback(
        runtime,
        AnalysisStepIR(
            index=0,
            step_type=step_type,
            params={
                key: value
                for key, value in {
                    "table": qualified_table,
                    "metric": metric_name,
                    "limit": limit,
                    "order": normalize_metric_query_order(resolved.order, mode=mode),
                    "scoped_query": scoped_query,
                }.items()
                if value is not None
            },
        ),
        engine_type=engine_type,
        semantic_context={
            "metric_sql": metric_sql,
            "dimensions": dimensions,
        },
    )
    all_rows = normalize_metric_rows(
        execute_compiled(engine, compiled_query, session_id=session_id).rows,
        mode=mode,
    )
    if mode == "compare":
        rows = [row for row in all_rows if row.get("delta_pct") is not None]
    else:
        rows = list(all_rows)
    artifact_id = _insert_artifact(
        runtime, session_id, step_id, "table", f"{metric_name}_metric_query", rows
    )

    _debug = metric_query_debug_payload(
        resolved,
        all_rows=all_rows,
        window_length_match=(not window_size_mismatch) if mode == "compare" else None,
    )
    summary = metric_query_summary(
        metric_name,
        rows,
        mode=mode,
        debug=_debug,
        dimensions=dimensions,
        grain=resolved.time_scope.grain,
        current_len=current_len,
        baseline_len=baseline_len,
    )

    provenance = _make_provenance(
        compiled_query.sql, compiled_query.params, engine_type=engine_type, routing=routing_feedback
    )

    result: dict[str, Any] = {
        "step_type": step_type,
        "metric_name": metric_name,
        "summary": summary,
        "artifact_id": artifact_id,
    }
    if not rows:
        result["debug"] = _debug
    elif mode == "compare" and window_size_mismatch:
        result["debug"] = {
            k: _debug[k] for k in ("current_window", "baseline_window", "window_length_match")
        }
    _insert_step(
        runtime,
        step_id,
        session_id,
        step_type,
        summary,
        result,
        provenance=provenance,
    )
    return result


def run_profile_table(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Profile a table: row count, column stats (null rate, distinct count)."""
    from marivo.core.intent.primitives import new_step_id
    from marivo.runtime.semantic.executor import annotate_sql, execute_compiled

    table_name = params.get("table_name")
    if not table_name:
        raise ValueError("profile_table requires 'table_name' param")

    step_type = "profile_table"
    step_id = new_step_id()

    short_name = table_name.split(".")[-1]
    engine, engine_type, qualified, routing_feedback = _resolve_engine_for_session_with_routing(
        runtime, session_id, [table_name]
    )
    qualified_table = qualified.get(table_name, table_name)

    row_count_query = compile_step_with_feedback(
        runtime,
        AnalysisStepIR(
            index=0, step_type="profile_table_row_count", params={"table_name": qualified_table}
        ),
        engine_type=engine_type,
    )
    row_count: int | None = None
    row_count_error: str | None = None
    try:
        row_count_row = execute_compiled(engine, row_count_query, session_id=session_id).rows[0]
        row_count = row_count_row["row_count"]
    except Exception as exc:
        row_count_error = str(exc)

    columns_available = True
    columns_error: str | None = None
    try:
        columns_query = compile_step_with_feedback(
            runtime,
            AnalysisStepIR(
                index=0,
                step_type="profile_table_columns",
                params={"table_name": qualified_table, "short_name": short_name},
            ),
            engine_type=engine_type,
        )
        col_rows = execute_compiled(engine, columns_query, session_id=session_id).rows
        columns = [r["column_name"] for r in col_rows]
    except Exception:
        # Fallback: derive column names from SELECT * LIMIT 0 result schema
        try:
            schema_rows = engine.query_rows(
                annotate_sql(f"SELECT * FROM {qualified_table} LIMIT 0", session_id)
            )
            columns = list(schema_rows[0].keys()) if schema_rows else []
        except Exception as exc:
            columns = []
            columns_available = False
            columns_error = str(exc)

    # Infer date column + recent value for partition-filtered profiling (Trino)
    profile_date_column: str | None = None
    profile_date_value: str | None = None
    _date_candidates = ("log_date", "event_date", "dt", "date", "day")
    for dc in _date_candidates:
        if dc in columns:
            try:
                max_row = engine.query_rows(
                    annotate_sql(f"SELECT MAX({dc}) AS max_date FROM {qualified_table}", session_id)
                )
                if max_row and max_row[0].get("max_date") is not None:
                    profile_date_column = dc
                    profile_date_value = str(max_row[0]["max_date"])
                    break
            except Exception:
                continue

    col_metadata = _fetch_column_metadata(short_name, columns)
    col_profiles = []
    for col in columns[:20]:  # cap at 20 columns for safety
        try:
            profile_params: dict[str, Any] = {"table_name": qualified_table, "column_name": col}
            if profile_date_column and profile_date_value:
                profile_params["date_column"] = profile_date_column
                profile_params["date_value"] = profile_date_value
            stats_query = compile_step_with_feedback(
                runtime,
                AnalysisStepIR(
                    index=0,
                    step_type="profile_table_column_profile",
                    params=profile_params,
                ),
                engine_type=engine_type,
            )
            stats = execute_compiled(engine, stats_query, session_id=session_id).rows[0]
            entry: dict[str, Any] = {
                "column": col,
                "total": stats["total"],
                "non_null": stats["non_null"],
                "null_rate": round(1 - stats["non_null"] / max(stats["total"], 1), 4),
                "distinct_count": stats["distinct_count"],
            }
            if col in col_metadata:
                entry.update(col_metadata[col])
            col_profiles.append(entry)
        except Exception:
            err_entry: dict[str, Any] = {"column": col, "error": "failed to profile"}
            if col in col_metadata:
                err_entry.update(col_metadata[col])
            col_profiles.append(err_entry)

    profile_scope = None
    if profile_date_column:
        profile_scope = {
            "date_column": profile_date_column,
            "date_value": profile_date_value,
            "scoped_row_count": col_profiles[0]["total"]
            if col_profiles and "total" in col_profiles[0]
            else None,
        }
    # If the row-count query failed and no columns were found, the table
    # does not exist (or is otherwise completely inaccessible).
    if row_count_error is not None and not columns:
        raise ValueError(f"Table '{table_name}' is inaccessible: {row_count_error}")

    profile_errors: dict[str, str] = {}
    if row_count_error is not None:
        profile_errors["row_count"] = row_count_error
    if not columns_available and columns_error is not None:
        profile_errors["columns"] = columns_error
    artifact: dict[str, Any] = {
        "table_name": table_name,
        "row_count": row_count,
        "profile_scope": profile_scope,
        "columns": col_profiles,
    }
    if profile_errors:
        artifact["errors"] = profile_errors
    artifact_id = _insert_artifact(
        runtime, session_id, step_id, "profile", f"{short_name}_profile", artifact
    )

    scope_note = (
        f" (column stats scoped to {profile_date_column}={profile_date_value})"
        if profile_date_column
        else ""
    )
    failure_notes: list[str] = []
    if row_count_error is not None:
        failure_notes.append(f"row_count unavailable: {row_count_error}")
    if not columns_available:
        col_detail = f": {columns_error}" if columns_error else ""
        failure_notes.append(
            f"columns unavailable (schema query failed{col_detail}; use sample_rows limit=1 to inspect columns)"
        )
    if failure_notes:
        failure_str = "; ".join(failure_notes)
        summary = f"Table '{table_name}' profile incomplete — {failure_str}."
    else:
        summary = (
            f"Table '{table_name}' has {row_count} rows and {len(columns)} columns{scope_note}."
        )
    provenance = _make_provenance(
        f"profile:{table_name}", engine_type=engine_type, routing=routing_feedback
    )
    result = {
        "step_type": step_type,
        "summary": summary,
        "artifact_id": artifact_id,
        "profile": artifact,
    }
    _insert_step(
        runtime,
        step_id,
        session_id,
        step_type,
        summary,
        result,
        provenance=provenance,
    )
    return result


def run_sample_rows(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Return a sample of rows from a table."""
    from marivo.core.intent.primitives import new_step_id
    from marivo.runtime.semantic.executor import annotate_sql, execute_compiled

    table_name = params.get("table_name")
    if not table_name:
        raise ValueError("sample_rows requires 'table_name' param")

    user_filter = params.get("filter")
    if user_filter:
        params = {**params, "filter": user_filter}

    step_type = "sample_rows"
    step_id = new_step_id()

    limit = int(params.get("limit", 10))
    short_name = table_name.split(".")[-1]
    engine, engine_type, qualified, routing_feedback = _resolve_engine_for_session_with_routing(
        runtime, session_id, [table_name]
    )
    qualified_table = qualified.get(table_name, table_name)

    # Build compiler params with filter/columns passthrough
    compiler_params: dict[str, Any] = {"table_name": qualified_table, "limit": limit}

    if params.get("filter"):
        compiler_params["filter"] = params["filter"]
    if params.get("columns"):
        compiler_params["columns"] = params["columns"]

    # Auto-detect partition column for Trino-like engines (same logic as profile_table)
    if not params.get("filter") and not params.get("date_column"):
        _date_candidates = ("log_date", "event_date", "dt", "date", "day")
        try:
            col_query = compile_step_with_feedback(
                runtime,
                AnalysisStepIR(
                    index=0,
                    step_type="profile_table_columns",
                    params={"table_name": qualified_table, "short_name": short_name},
                ),
                engine_type=engine_type,
            )
            col_rows = execute_compiled(engine, col_query, session_id=session_id).rows
            columns_list = [r["column_name"] for r in col_rows]
            for dc in _date_candidates:
                if dc in columns_list:
                    try:
                        max_row = engine.query_rows(
                            annotate_sql(
                                f"SELECT MAX({dc}) AS max_date FROM {qualified_table}", session_id
                            )
                        )
                        if max_row and max_row[0].get("max_date") is not None:
                            compiler_params["date_column"] = dc
                            compiler_params["date_value"] = str(max_row[0]["max_date"])
                            break
                    except Exception:
                        continue
        except Exception:
            pass
    elif params.get("date_column"):
        compiler_params["date_column"] = params["date_column"]
        if params.get("date_value"):
            compiler_params["date_value"] = params["date_value"]
        elif params.get("period_end"):
            compiler_params["date_value"] = params["period_end"]

    compiled_query = compile_step_with_feedback(
        runtime,
        AnalysisStepIR(index=0, step_type=step_type, params=compiler_params),
        engine_type=engine_type,
    )
    rows = execute_compiled(engine, compiled_query, session_id=session_id).rows

    actual_columns = list(rows[0].keys()) if rows else list(params.get("columns") or [])
    col_metadata = _fetch_column_metadata(short_name, actual_columns)

    artifact_id = _insert_artifact(
        runtime, session_id, step_id, "sample", f"{short_name}_sample", rows
    )
    summary = f"Sampled {len(rows)} rows from '{table_name}'."
    provenance = _make_provenance(
        compiled_query.sql, compiled_query.params, engine_type=engine_type, routing=routing_feedback
    )
    result = {
        "step_type": step_type,
        "summary": summary,
        "artifact_id": artifact_id,
        "rows": rows,
        "columns_metadata": col_metadata,
    }
    _insert_step(
        runtime,
        step_id,
        session_id,
        step_type,
        summary,
        result,
        provenance=provenance,
        semantic_metadata=build_step_semantic_metadata(runtime, compiled_query),
    )
    return result


def run_aggregate_query(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Run an ad-hoc GROUP BY + aggregation query."""
    from marivo.core.intent.primitives import new_step_id
    from marivo.runtime.semantic.executor import execute_compiled
    from marivo.time_scope import (
        AdHocAggregateValueSpec,
        normalize_aggregate_query_request,
    )

    resolved = normalize_aggregate_query_request(params)
    table_name = resolved.table

    step_type = "aggregate_query"
    step_id = new_step_id()
    short_name = table_name.split(".")[-1]
    engine, engine_type, qualified, routing_feedback = _resolve_engine_for_session_with_routing(
        runtime, session_id, [table_name]
    )
    resolve_windowed_query_time_axis(
        runtime,
        resolved,
        engine_type=engine_type,
        fallback_columns=list(resolved.grouping),
    )
    scoped_query = build_scoped_query(runtime, session_id, resolved, engine_type=engine_type)
    qualified_table = qualified.get(table_name, table_name)

    measures = (
        resolved.value_spec.measures
        if isinstance(resolved.value_spec, AdHocAggregateValueSpec)
        else []
    )
    compiler_params: dict[str, Any] = {
        "table": qualified_table,
        "measures": [{"expr": measure.expr, "as": measure.alias} for measure in measures],
        "group_by": list(resolved.grouping),
        "limit": resolved.limit or 100,
        "scoped_query": scoped_query,
    }
    if resolved.order:
        compiler_params["order"] = resolved.order

    compiled_query = compile_step_with_feedback(
        runtime,
        AnalysisStepIR(index=0, step_type=step_type, params=compiler_params),
        engine_type=engine_type,
    )
    rows = execute_compiled(engine, compiled_query, session_id=session_id).rows
    compare_period = resolved.time_scope.mode == "compare"

    artifact_id = _insert_artifact(
        runtime, session_id, step_id, "aggregate", f"{short_name}_aggregate", rows
    )
    if not rows:
        _partition_cols = {"log_date", "event_date", "dt", "date", "day"}
        where_lower = str(scoped_query.get("partition_pruning_predicate") or "").lower()
        has_partition_hint = any(col in where_lower for col in _partition_cols)
        if has_partition_hint:
            summary = (
                f"Aggregate query on '{table_name}' returned 0 rows. "
                "Possible cause: partition filter syntax or date range contains no data. "
                "Verify the date format matches the engine (e.g. YYYYMMDD for Trino/Iceberg)."
            )
        else:
            summary = f"Aggregate query on '{table_name}' returned 0 rows."
    elif compare_period:
        _baseline = resolved.time_scope.baseline
        summary = (
            f"Period-over-period aggregate on '{table_name}': "
            f"{len(rows)} dimension slice(s) compared "
            f"(current {resolved.time_scope.current.start}–{resolved.time_scope.current.end} vs "
            f"baseline {_baseline.start if _baseline else '?'}–{_baseline.end if _baseline else '?'})."
        )
    else:
        summary = f"Aggregate query on '{table_name}' returned {len(rows)} rows."
    provenance = _make_provenance(
        compiled_query.sql, compiled_query.params, engine_type=engine_type, routing=routing_feedback
    )
    result = {
        "step_type": step_type,
        "summary": summary,
        "artifact_id": artifact_id,
        "rows": rows,
    }
    _insert_step(runtime, step_id, session_id, step_type, summary, result, provenance=provenance)
    return result


def run_attribute_change(
    runtime: MarivoRuntime, session_id: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Attribute a metric change across candidate dimensions."""
    from marivo.core.intent.primitives import new_step_id
    from marivo.runtime.semantic.executor import annotate_sql, execute_compiled

    metric_name = params.get("metric_name")
    table_name = params.get("table_name")
    if not metric_name or not table_name:
        raise ValueError("attribute_change requires 'metric_name' and 'table_name' params")

    candidate_dimensions_raw = params.get("candidate_dimensions")
    if not isinstance(candidate_dimensions_raw, list):
        raise ValueError("candidate_dimensions must not be empty")
    candidate_dimensions = [
        str(dim).strip() for dim in candidate_dimensions_raw if str(dim).strip()
    ]
    candidate_dimensions = list(dict.fromkeys(candidate_dimensions))
    if not candidate_dimensions:
        raise ValueError("candidate_dimensions must not be empty")

    metric_sql = resolve_metric_sql(runtime, str(metric_name))
    if metric_sql is None:
        raise ValueError(
            f"Metric '{metric_name}' not found, not published, or missing typed execution metadata"
        )

    period_end_p = params.get("period_end")
    baseline_start_p = params.get("baseline_start")
    baseline_end_p = params.get("baseline_end")
    if not period_end_p or not baseline_start_p or not baseline_end_p:
        raise ValueError(
            "attribute_change requires 'period_end', 'baseline_start', and 'baseline_end' params"
        )

    period_start_p = params.get("period_start") or period_end_p
    period_start = date.fromisoformat(str(period_start_p))
    period_end = date.fromisoformat(str(period_end_p))
    baseline_start = date.fromisoformat(str(baseline_start_p))
    baseline_end = date.fromisoformat(str(baseline_end_p))
    step_id = new_step_id()

    metric_dimensions = resolve_metric_dimensions(runtime, str(metric_name)) or []
    date_column = str(params.get("date_column") or infer_date_column(metric_dimensions))
    top_k = max(1, int(params.get("top_k", 5)))
    min_contribution_pct = max(0.0, float(params.get("min_contribution_pct", 5.0)))
    min_contribution_fraction = min_contribution_pct / 100.0
    query_limit = max(top_k, int(params.get("limit", 1000)))

    user_where = params.get("where") or params.get("filter")
    merged_where = user_where

    table_name_str = str(table_name)
    short_name = table_name_str.split(".")[-1]
    engine, engine_type, qualified, routing_feedback = _resolve_engine_for_session_with_routing(
        runtime, session_id, [table_name_str]
    )
    qualified_table = qualified.get(table_name_str, table_name_str)

    try:
        row = engine.query_rows(
            annotate_sql(
                f"SELECT MAX({date_column}) AS max_date FROM {qualified_table}", session_id
            )
        )[0]
        date_fmt = detect_date_format(row["max_date"])
    except Exception:
        date_fmt = detect_date_format(str(period_end_p))

    contributions: list[dict[str, Any]] = []
    query_sql_parts: list[str] = []
    query_params: list[Any] = []
    compiled_queries: list[CompiledQuery] = []
    current_has_data = False
    baseline_has_data = False

    for dimension in candidate_dimensions:
        scoped_query: dict[str, Any] = {
            "mode": "compare",
            "analysis_time_kind": "date_field",
            "analysis_time_expr": date_column,
            "current": {
                "start": period_start.isoformat(),
                "end": (period_end + timedelta(days=1)).isoformat(),
            },
            "baseline": {
                "start": baseline_start.isoformat(),
                "end": (baseline_end + timedelta(days=1)).isoformat(),
            },
        }
        if date_fmt:
            scoped_query["analysis_time_format"] = date_fmt
        if merged_where:
            scoped_query["scope_predicate_filter"] = str(merged_where)
        step_ir = AnalysisStepIR(
            index=0,
            step_type="aggregate_query",
            params={
                "table": qualified_table,
                "measures": [{"expr": metric_sql, "as": "metric_value"}],
                "group_by": [dimension],
                "order": "metric_value_delta_pct DESC",
                "scoped_query": scoped_query,
                "limit": query_limit,
            },
        )
        compiled_query = compile_step_with_feedback(
            runtime,
            step_ir,
            engine_type=engine_type,
            semantic_context={},
        )
        rows = execute_compiled(engine, compiled_query, session_id=session_id).rows
        query_sql_parts.append(compiled_query.sql)
        query_params.extend(compiled_query.params)
        compiled_queries.append(compiled_query)

        has_current_rows = any(r.get("metric_value_current") is not None for r in rows)
        has_baseline_rows = any(r.get("metric_value_baseline") is not None for r in rows)
        baseline_has_data = baseline_has_data or has_baseline_rows
        if not has_current_rows:
            continue

        dim_contributors: list[dict[str, Any]] = []
        for r in rows:
            current_value_raw = r.get("metric_value_current")
            baseline_value_raw = r.get("metric_value_baseline")
            if current_value_raw is None and baseline_value_raw is None:
                continue

            current_value = float(current_value_raw or 0.0)
            baseline_value = float(baseline_value_raw or 0.0)
            delta_value = current_value - baseline_value
            delta_pct = None if baseline_value == 0.0 else (delta_value / baseline_value) * 100.0
            dim_value = r.get(dimension)
            if current_value_raw is not None:
                current_has_data = True
            if baseline_value_raw is not None:
                baseline_has_data = True
            dim_contributors.append(
                {
                    "value": dim_value,
                    "current_value": current_value,
                    "baseline_value": baseline_value,
                    "delta_value": delta_value,
                    "delta_pct": delta_pct,
                    "current_row_count": None,
                    "baseline_row_count": None,
                }
            )

        total_abs_delta = sum(abs(entry["delta_value"]) for entry in dim_contributors)
        for entry in dim_contributors:
            entry["contribution_pct"] = (
                (abs(entry["delta_value"]) / total_abs_delta) * 100.0
                if total_abs_delta > 0
                else 0.0
            )

        sorted_contributors = sorted(
            dim_contributors,
            key=lambda entry: (
                abs(entry["delta_pct"])
                if entry["delta_pct"] is not None
                else abs(entry["delta_value"]),
                abs(entry["delta_value"]),
            ),
            reverse=True,
        )
        top_contributors = [
            {
                "value": entry["value"],
                "current_value": entry["current_value"],
                "baseline_value": entry["baseline_value"],
                "delta_pct": entry["delta_pct"],
                "contribution_pct": entry["contribution_pct"],
                "current_row_count": entry["current_row_count"],
                "baseline_row_count": entry["baseline_row_count"],
            }
            for entry in sorted_contributors
            if entry["contribution_pct"] >= min_contribution_fraction
        ][:top_k]

        contributions.append(
            {
                "dimension": dimension,
                "top_contributors": top_contributors,
            }
        )

    artifact_id = _insert_artifact(
        runtime,
        session_id,
        step_id,
        "table",
        f"{short_name}_attribution",
        {
            "metric_name": metric_name,
            "table_name": qualified_table,
            "candidate_dimensions": candidate_dimensions,
            "contributions": contributions,
        },
    )

    query_blob = "\n".join(query_sql_parts)
    provenance = _make_provenance(
        query_blob, query_params, engine_type=engine_type, routing=routing_feedback
    )
    summary = (
        f"Attributed metric '{metric_name}' across {len(candidate_dimensions)} dimension(s)."
        if contributions
        else f"Attribute change on '{metric_name}' returned no results."
    )

    debug = {
        "current_window": [str(period_start), str(period_end)],
        "baseline_window": [str(baseline_start), str(baseline_end)],
        "current_has_data": current_has_data,
        "baseline_has_data": baseline_has_data,
        "dimensions": candidate_dimensions,
    }

    result = {
        "step_type": "attribute_change",
        "metric_name": metric_name,
        "table_name": qualified_table,
        "summary": summary,
        "artifact_id": artifact_id,
        "contributions": contributions,
        "debug": debug,
    }

    _insert_step(
        runtime,
        step_id,
        session_id,
        "attribute_change",
        summary,
        result,
        provenance=provenance,
        semantic_metadata=build_step_semantic_metadata(runtime, compiled_queries),
    )
    return result
