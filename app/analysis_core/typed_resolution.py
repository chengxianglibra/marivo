from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from app.semantic_runtime.errors import (
    SemanticRuntimeError,
    SemanticRuntimeNotReadyError,
)
from app.semantic_runtime.resolution import ResolvedSemanticObject
from app.semantic_runtime.semantic_metadata import runtime_ref_kind
from app.time_scope import (
    SemanticMetricValueSpec,
    normalize_aggregate_query_request,
    normalize_metric_query_request,
)

if TYPE_CHECKING:
    from app.analysis_core.ir import AnalysisStepIR
    from app.semantic_runtime import SemanticRuntimeRepository


RequestClass = Literal["root_metric_process", "typed_ref", "derived_macro"]


@dataclass(slots=True)
class NormalizedCompilerRequest:
    intent_kind: str
    request_class: RequestClass
    table_name: str | None
    metric_ref: str | None = None
    process_ref: str | None = None
    left_process_ref: str | None = None
    right_process_ref: str | None = None
    upstream_refs: list[str] = field(default_factory=list)
    request_scope: dict[str, Any] | None = None
    request_time_scope: dict[str, Any] | None = None
    request_dimensions: list[str] = field(default_factory=list)
    request_result_mode: str | None = None
    request_options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedCompilerInputs:
    normalized_request: NormalizedCompilerRequest
    resolved_metric: ResolvedSemanticObject | None = None
    resolved_process: ResolvedSemanticObject | None = None
    resolved_left_process: ResolvedSemanticObject | None = None
    resolved_right_process: ResolvedSemanticObject | None = None
    resolved_filter_time: ResolvedSemanticObject | None = None
    resolved_dimensions: list[ResolvedSemanticObject] = field(default_factory=list)
    resolved_bindings: list[ResolvedSemanticObject] = field(default_factory=list)
    metric_entity_anchor_ref: str | None = None
    resolved_imported_dimensions: list[ResolvedImportedDimensionBridge] = field(
        default_factory=list
    )
    imported_dimension_conflicts: dict[str, list[ResolvedImportedDimensionBridge]] = field(
        default_factory=dict
    )
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved_dimension_refs(self) -> list[str]:
        return [dimension.ref for dimension in self.resolved_dimensions]

    @property
    def resolved_imported_dimension_refs(self) -> list[str]:
        return [dimension.dimension_ref for dimension in self.resolved_imported_dimensions]


@dataclass(slots=True)
class ResolvedImportedDimensionBridge:
    dimension_ref: str
    source_binding_ref: str
    source_entity_ref: str
    import_key: str


def normalize_step_request(
    step: AnalysisStepIR,
    *,
    semantic_context: Mapping[str, Any] | None = None,
) -> NormalizedCompilerRequest:
    semantic_context = semantic_context or {}
    if step.step_type == "metric_query":
        table_name: str | None
        request_scope: dict[str, Any] | None
        request_time_scope: dict[str, Any] | None
        request_dimensions: list[str]
        request_options: dict[str, Any]
        metric_name: str | None
        if "time_scope" in step.params:
            normalized = normalize_metric_query_request(step.params)
            if not isinstance(normalized.value_spec, SemanticMetricValueSpec):
                raise ValueError("metric_query must normalize to a semantic metric value spec")
            table_name = normalized.table
            request_scope = asdict(normalized.scope)
            request_time_scope = asdict(normalized.time_scope)
            request_dimensions = _normalize_dimension_refs(
                normalized.grouping or _string_list(semantic_context.get("dimensions"))
            )
            request_options = _request_options_from_windowed_request(normalized)
            metric_name = normalized.value_spec.metric
        else:
            table_name = step.table_name()
            request_scope = None
            request_time_scope = _mapping_dict(step.params.get("scoped_query"))
            request_dimensions = _normalize_dimension_refs(
                _string_list(step.params.get("dimensions"))
                or _string_list(semantic_context.get("dimensions"))
            )
            request_options = _filter_none_dict(
                order=str(step.params.get("order") or "").strip() or None,
                limit=_optional_int(step.params.get("limit")),
                scoped_query=request_time_scope,
            )
            metric_name = (
                step.primary_metric_name()
                or str(step.params.get("metric") or "").strip()
                or str(step.params.get("metric_name") or "").strip()
            )
        if not metric_name:
            raise ValueError("metric_query requires 'metric' or 'metric_name' param")
        return NormalizedCompilerRequest(
            intent_kind="metric_query",
            request_class="root_metric_process",
            table_name=table_name,
            metric_ref=_normalize_metric_ref(metric_name),
            request_scope=request_scope,
            request_time_scope=request_time_scope,
            request_dimensions=request_dimensions,
            request_options=request_options,
        )

    if step.step_type == "aggregate_query":
        if "time_scope" in step.params and "measures" in step.params:
            normalized = normalize_aggregate_query_request(step.params)
            return NormalizedCompilerRequest(
                intent_kind="aggregate_query",
                request_class="root_metric_process",
                table_name=normalized.table,
                request_scope=asdict(normalized.scope),
                request_time_scope=asdict(normalized.time_scope),
                request_dimensions=_normalize_dimension_refs(normalized.grouping),
                request_options=_request_options_from_windowed_request(normalized),
            )

        return NormalizedCompilerRequest(
            intent_kind="aggregate_query",
            request_class="root_metric_process",
            table_name=step.table_name(),
            request_time_scope=_mapping_dict(step.params.get("scoped_query")),
            request_dimensions=_normalize_dimension_refs(_string_list(step.params.get("group_by"))),
            request_options=_filter_none_dict(
                order=str(step.params.get("order") or step.params.get("order_by") or "").strip()
                or None,
                limit=_optional_int(step.params.get("limit")),
                compare_period=_optional_bool(step.params.get("compare_period")),
                select=list(step.params.get("select") or []) or None,
                measures=list(step.params.get("measures") or []) or None,
            ),
        )

    if step.step_type in {"sample_rows", "profile_table_row_count", "profile_table_columns"}:
        return NormalizedCompilerRequest(
            intent_kind=step.step_type,
            request_class="root_metric_process",
            table_name=step.table_name(),
            request_options={"step_type": step.step_type},
        )

    if step.step_type == "profile_table_column_profile":
        return NormalizedCompilerRequest(
            intent_kind=step.step_type,
            request_class="root_metric_process",
            table_name=step.table_name(),
            request_options={
                "step_type": step.step_type,
                "column_name": str(step.params.get("column_name") or "").strip() or None,
            },
        )

    metric_name = (
        step.primary_metric_name()
        or str(step.params.get("metric") or "").strip()
        or str(step.params.get("metric_name") or "").strip()
        or None
    )
    request_dimensions = _normalize_dimension_refs(
        _string_list(step.params.get("dimensions"))
        or _string_list(semantic_context.get("dimensions"))
    )
    return NormalizedCompilerRequest(
        intent_kind=step.step_type,
        request_class="root_metric_process",
        table_name=step.table_name(),
        metric_ref=_normalize_metric_ref(metric_name) if metric_name else None,
        request_dimensions=request_dimensions,
        request_options={"step_type": step.step_type},
    )


def resolve_compiler_inputs(
    normalized_request: NormalizedCompilerRequest,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
    binding_reader: Any = None,
) -> ResolvedCompilerInputs:
    resolved = ResolvedCompilerInputs(normalized_request=normalized_request)
    if semantic_repository is None:
        if normalized_request.metric_ref is not None:
            resolved.warnings.append(
                {
                    "code": "semantic_repository_missing",
                    "message": "Cannot resolve metric_ref without semantic_repository",
                    "metric_ref": normalized_request.metric_ref,
                }
            )
        for dim_ref in normalized_request.request_dimensions:
            resolved.warnings.append(
                {
                    "code": "semantic_repository_missing",
                    "message": "Cannot resolve dimension_ref without semantic_repository",
                    "dimension_ref": dim_ref,
                }
            )
        return resolved

    if normalized_request.metric_ref is not None:
        resolved.resolved_metric = _resolve_runtime_ref(
            semantic_repository.resolve_metric_ref,
            normalized_request.metric_ref,
            label="metric",
        )

    if normalized_request.process_ref is not None:
        resolved.resolved_process = _resolve_runtime_ref(
            semantic_repository.resolve_process_ref,
            normalized_request.process_ref,
            label="process",
        )

    if normalized_request.left_process_ref is not None:
        resolved.resolved_left_process = _resolve_runtime_ref(
            semantic_repository.resolve_process_ref,
            normalized_request.left_process_ref,
            label="left_process",
        )

    if normalized_request.right_process_ref is not None:
        resolved.resolved_right_process = _resolve_runtime_ref(
            semantic_repository.resolve_process_ref,
            normalized_request.right_process_ref,
            label="right_process",
        )

    for dimension_ref in normalized_request.request_dimensions:
        try:
            resolved.resolved_dimensions.append(
                _resolve_runtime_ref(
                    semantic_repository.resolve_dimension_ref,
                    dimension_ref,
                    label="dimension",
                )
            )
        except ValueError as error:
            resolved.warnings.append(
                {
                    "code": "dimension_ref_unresolved",
                    "message": str(error),
                    "dimension_ref": dimension_ref,
                }
            )

    filter_time_ref = _resolved_filter_time_ref(normalized_request, resolved)
    if filter_time_ref is not None:
        try:
            resolved.resolved_filter_time = _resolve_runtime_ref(
                semantic_repository.resolve_time_ref,
                filter_time_ref,
                label="time",
            )
        except ValueError as error:
            resolved.warnings.append(
                {
                    "code": "time_ref_unresolved",
                    "message": str(error),
                    "time_ref": filter_time_ref,
                }
            )

    resolved.resolved_bindings = _resolve_bindings_for_inputs(
        resolved,
        binding_reader=binding_reader,
    )
    (
        resolved.metric_entity_anchor_ref,
        resolved.resolved_imported_dimensions,
        resolved.imported_dimension_conflicts,
    ) = _resolve_imported_dimension_bridges(
        resolved,
        semantic_repository=semantic_repository,
    )

    return resolved


def _request_options_from_windowed_request(request: Any) -> dict[str, Any]:
    return _filter_none_dict(
        compare_kind=str(request.compare_kind),
        order=request.order,
        limit=request.limit,
        resolved_time_axis=asdict(request.resolved_time_axis),
    )


def _normalize_metric_ref(metric_name: str) -> str:
    normalized = metric_name.strip()
    if runtime_ref_kind(normalized) == "metric":
        return normalized
    return f"metric.{normalized}"


def _normalize_dimension_refs(dimensions: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for dimension in dimensions:
        candidate = dimension.strip()
        if not candidate:
            continue
        ref_kind = runtime_ref_kind(candidate)
        if ref_kind is not None and ref_kind != "dimension":
            raise ValueError(f"Invalid dimension ref: {dimension}")
        if candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return normalized


def _mapping_dict(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return dict(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        item_text = str(item or "").strip()
        if not item_text or item_text in seen:
            continue
        normalized.append(item_text)
        seen.add(item_text)
    return normalized


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _filter_none_dict(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _resolve_runtime_ref(
    resolver: Any,
    semantic_ref: str,
    *,
    label: str,
) -> ResolvedSemanticObject:
    try:
        resolved = resolver(semantic_ref)
    except SemanticRuntimeNotReadyError:
        raise
    except SemanticRuntimeError as error:
        raise ValueError(f"Could not resolve {label} ref '{semantic_ref}': {error}") from error
    return cast("ResolvedSemanticObject", resolved)


def _resolved_filter_time_ref(
    normalized_request: NormalizedCompilerRequest,
    resolved: ResolvedCompilerInputs,
) -> str | None:
    # NOTE: time_scope compatibility validation is deferred to S5-02 (Gate 3).
    # For now we extract the time ref without validating time_scope compatibility.
    _ = normalized_request
    if resolved.resolved_metric is not None:
        header = resolved.resolved_metric.semantic_object.get("header") or {}
        primary_time_ref = header.get("primary_time_ref")
        if primary_time_ref is not None:
            return str(primary_time_ref)

    if resolved.resolved_process is not None:
        anchor_time_ref = resolved.resolved_process.semantic_object.get("anchor_time_ref")
        if anchor_time_ref is not None:
            return str(anchor_time_ref)

    return None


def _resolve_bindings_for_inputs(
    resolved: ResolvedCompilerInputs,
    *,
    binding_reader: Any,
) -> list[ResolvedSemanticObject]:
    if binding_reader is None:
        return []
    object_refs: list[str] = []
    if resolved.resolved_metric is not None:
        object_refs.append(resolved.resolved_metric.ref)
    if resolved.resolved_process is not None:
        object_refs.append(resolved.resolved_process.ref)
    if not object_refs:
        return []
    bindings: list[ResolvedSemanticObject] = []
    seen: set[str] = set()
    for object_ref in object_refs:
        for binding in list(binding_reader(object_ref) or []):
            if binding.ref in seen:
                continue
            bindings.append(binding)
            seen.add(binding.ref)
    return bindings


def _resolve_imported_dimension_bridges(
    resolved: ResolvedCompilerInputs,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
) -> tuple[
    str | None,
    list[ResolvedImportedDimensionBridge],
    dict[str, list[ResolvedImportedDimensionBridge]],
]:
    metric = resolved.resolved_metric
    if metric is None:
        return None, [], {}

    entity_anchor_ref = _metric_entity_anchor_ref(metric)
    if entity_anchor_ref is None:
        return None, [], {}

    metric_bindings = [
        binding
        for binding in resolved.resolved_bindings
        if binding.object_kind == "binding"
        and _binding_scope(binding) == "metric"
        and _bound_object_ref(binding) == metric.ref
    ]
    if not metric_bindings:
        return entity_anchor_ref, [], {}

    grouped_bridges: dict[str, list[ResolvedImportedDimensionBridge]] = {}
    for metric_binding in metric_bindings:
        interface_contract = dict(metric_binding.semantic_object.get("interface_contract") or {})
        for binding_import in interface_contract.get("imports") or []:
            imported_binding_ref = str(binding_import.get("binding_ref") or "").strip()
            if not imported_binding_ref:
                continue
            import_key = str(binding_import.get("import_key") or "").strip()
            imported_binding = _resolve_imported_binding(
                imported_binding_ref,
                metric_ref=metric.ref,
                import_key=import_key,
                semantic_repository=semantic_repository,
                warnings=resolved.warnings,
            )
            if imported_binding is None:
                continue
            if _binding_scope(imported_binding) != "entity":
                continue
            source_entity_ref = _bound_object_ref(imported_binding)
            if source_entity_ref != entity_anchor_ref:
                continue
            for bridge in _bridges_from_imported_binding(
                imported_binding,
                source_entity_ref=source_entity_ref,
                import_key=import_key,
            ):
                grouped_bridges.setdefault(bridge.dimension_ref, []).append(bridge)

    resolved_bridges: list[ResolvedImportedDimensionBridge] = []
    conflicts: dict[str, list[ResolvedImportedDimensionBridge]] = {}
    for dimension_ref, dimension_bridges in grouped_bridges.items():
        if len(dimension_bridges) == 1:
            resolved_bridges.append(dimension_bridges[0])
            continue
        conflicts[dimension_ref] = dimension_bridges

    resolved_bridges.sort(key=lambda bridge: bridge.dimension_ref)
    sorted_conflicts = {
        dimension_ref: sorted(
            dimension_bridges,
            key=lambda bridge: (
                bridge.source_binding_ref,
                bridge.import_key,
            ),
        )
        for dimension_ref, dimension_bridges in sorted(conflicts.items())
    }
    return entity_anchor_ref, resolved_bridges, sorted_conflicts


def _metric_entity_anchor_ref(metric: ResolvedSemanticObject) -> str | None:
    header = dict(metric.semantic_object.get("header") or {})
    observed_entity_ref = _optional_str(header.get("observed_entity_ref"))
    if observed_entity_ref is not None:
        return observed_entity_ref
    return _optional_str(header.get("population_subject_ref"))


def _resolve_imported_binding(
    imported_binding_ref: str,
    *,
    metric_ref: str,
    import_key: str,
    semantic_repository: SemanticRuntimeRepository | None,
    warnings: list[dict[str, Any]],
) -> ResolvedSemanticObject | None:
    if semantic_repository is None:
        warnings.append(
            {
                "code": "binding_import_unresolved",
                "message": "Cannot resolve imported binding_ref without semantic_repository",
                "metric_ref": metric_ref,
                "binding_ref": imported_binding_ref,
                "import_key": import_key,
            }
        )
        return None
    try:
        return _resolve_runtime_ref(
            semantic_repository.resolve_binding_ref,
            imported_binding_ref,
            label="binding",
        )
    except ValueError as error:
        warnings.append(
            {
                "code": "binding_import_unresolved",
                "message": str(error),
                "metric_ref": metric_ref,
                "binding_ref": imported_binding_ref,
                "import_key": import_key,
            }
        )
        return None


def _bridges_from_imported_binding(
    imported_binding: ResolvedSemanticObject,
    *,
    source_entity_ref: str,
    import_key: str,
) -> list[ResolvedImportedDimensionBridge]:
    interface_contract = dict(imported_binding.semantic_object.get("interface_contract") or {})
    bridges: dict[str, ResolvedImportedDimensionBridge] = {}
    for field_binding in interface_contract.get("field_bindings") or []:
        target = dict(field_binding.get("target") or {})
        if str(target.get("target_kind") or "").strip() != "stable_descriptor":
            continue
        dimension_ref = str(field_binding.get("semantic_ref") or "").strip()
        if not dimension_ref.startswith("dimension."):
            continue
        bridges.setdefault(
            dimension_ref,
            ResolvedImportedDimensionBridge(
                dimension_ref=dimension_ref,
                source_binding_ref=imported_binding.ref,
                source_entity_ref=source_entity_ref,
                import_key=import_key,
            ),
        )
    return list(bridges.values())


def _binding_scope(binding: ResolvedSemanticObject) -> str | None:
    header = dict(binding.semantic_object.get("header") or {})
    return _optional_str(header.get("binding_scope"))


def _bound_object_ref(binding: ResolvedSemanticObject) -> str | None:
    header = dict(binding.semantic_object.get("header") or {})
    return _optional_str(header.get("bound_object_ref"))


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
