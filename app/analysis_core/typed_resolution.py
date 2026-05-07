# DEPRECATED: Pure data classes and helpers extracted to
# app.core.semantic.typed_resolution.  This file retains the I/O-bound
# normalize_step_request and resolve_compiler_inputs orchestrators.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from app.analysis_core.calendar_policy import (
    CalendarPolicyResolutionError,
    validate_calendar_policy_ref,
)
from app.semantic_runtime.errors import SemanticRuntimeError, SemanticRuntimeNotReadyError
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
    request_scope_predicate_ref: str | None = None
    request_time_scope: dict[str, Any] | None = None
    request_dimensions: list[str] = field(default_factory=list)
    request_result_mode: str | None = None
    request_calendar_policy_ref: str | None = None
    request_options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedImportedDimensionBridge:
    dimension_ref: str
    source_binding_ref: str
    source_entity_ref: str
    import_key: str


@dataclass(slots=True)
class ResolvedEntityField:
    field_ref: str
    entity_ref: str
    local_field_ref: str
    entity_revision: int
    value_type: str | None = None
    nullable: bool | None = None
    unit: str | None = None
    enum_hint: str | None = None
    profile_summary: dict[str, Any] | None = None
    sensitivity_tags: list[str] = field(default_factory=list)
    physical_column: str | None = None
    physical_expression_locator: dict[str, Any] | None = None
    source_object_ref: str | None = None
    source_object_fqn: str | None = None
    carrier_kind: str | None = None
    usage_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FieldResolutionIssue:
    code: str
    field_ref: str
    message: str
    usage_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntityComposition:
    anchor_entity_ref: str | None
    component_entity_refs: list[str] = field(default_factory=list)
    all_entity_refs: list[str] = field(default_factory=list)
    is_cross_entity: bool = False


@dataclass(slots=True)
class ResolvedRelationship:
    relationship_ref: str
    left_entity_ref: str
    right_entity_ref: str
    key_alignment: dict[str, Any] = field(default_factory=dict)
    time_alignment: dict[str, Any] | None = None
    cardinality: str | None = None
    grain_compatibility: dict[str, Any] | None = None
    snapshot_effective_window_alignment: dict[str, Any] | None = None
    revision: int | None = None


@dataclass(slots=True)
class ResolvedCompilerInputs:
    normalized_request: NormalizedCompilerRequest
    resolved_metric: ResolvedSemanticObject | None = None
    resolved_process: ResolvedSemanticObject | None = None
    resolved_left_process: ResolvedSemanticObject | None = None
    resolved_right_process: ResolvedSemanticObject | None = None
    resolved_filter_time: ResolvedSemanticObject | None = None
    resolved_dimensions: list[ResolvedSemanticObject] = field(default_factory=list)
    metric_entity_anchor_ref: str | None = None
    resolved_imported_dimensions: list[ResolvedImportedDimensionBridge] = field(
        default_factory=list
    )
    imported_dimension_conflicts: dict[str, list[ResolvedImportedDimensionBridge]] = field(
        default_factory=dict
    )
    resolved_entity_fields: dict[str, ResolvedEntityField] = field(default_factory=dict)
    entity_field_usage_details: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    field_resolution_issues: list[FieldResolutionIssue] = field(default_factory=list)
    entity_composition: EntityComposition = field(default_factory=lambda: EntityComposition(None))
    resolved_relationships: dict[str, ResolvedRelationship] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resolved_dimension_refs(self) -> list[str]:
        return [dimension.ref for dimension in self.resolved_dimensions]

    @property
    def resolved_imported_dimension_refs(self) -> list[str]:
        return [dimension.dimension_ref for dimension in self.resolved_imported_dimensions]

    @property
    def resolved_entity_field_refs(self) -> list[str]:
        return sorted(self.resolved_entity_fields)


def normalize_step_request(
    step: AnalysisStepIR,
    *,
    semantic_context: Mapping[str, Any] | None = None,
) -> NormalizedCompilerRequest:
    semantic_context = semantic_context or {}
    raw_calendar_policy_ref = step.params.get("calendar_policy_ref")
    if raw_calendar_policy_ref is not None and not isinstance(raw_calendar_policy_ref, str):
        raise ValueError("calendar_policy_ref must be a string when provided")
    if raw_calendar_policy_ref is not None and step.step_type not in {
        "observe",
        "metric_query",
        "aggregate_query",
    }:
        raise ValueError("calendar_policy_ref is only supported for observe steps")
    try:
        request_calendar_policy_ref = validate_calendar_policy_ref(raw_calendar_policy_ref)
    except CalendarPolicyResolutionError as error:
        raise ValueError(str(error)) from error
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
            request_scope_predicate_ref = normalized.scope.predicate_ref
        else:
            table_name = step.table_name()
            request_scope = None
            request_scope_predicate_ref = None
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
            request_scope_predicate_ref=request_scope_predicate_ref,
            request_time_scope=request_time_scope,
            request_dimensions=request_dimensions,
            request_calendar_policy_ref=request_calendar_policy_ref,
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
                request_scope_predicate_ref=normalized.scope.predicate_ref,
                request_time_scope=asdict(normalized.time_scope),
                request_dimensions=_normalize_dimension_refs(normalized.grouping),
                request_calendar_policy_ref=request_calendar_policy_ref,
                request_options=_request_options_from_windowed_request(normalized),
            )

        request_time_scope = None
        if "time_scope" in step.params:
            from app.time_scope import _normalize_time_scope

            request_time_scope = asdict(
                _normalize_time_scope(step.params.get("time_scope"), "aggregate_query")
            )

        return NormalizedCompilerRequest(
            intent_kind="aggregate_query",
            request_class="root_metric_process",
            table_name=step.table_name(),
            request_time_scope=request_time_scope or _mapping_dict(step.params.get("scoped_query")),
            request_dimensions=_normalize_dimension_refs(_string_list(step.params.get("group_by"))),
            request_calendar_policy_ref=request_calendar_policy_ref,
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
            request_calendar_policy_ref=request_calendar_policy_ref,
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
            request_calendar_policy_ref=request_calendar_policy_ref,
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
        request_calendar_policy_ref=request_calendar_policy_ref,
        request_options={"step_type": step.step_type},
    )


def resolve_compiler_inputs(
    normalized_request: NormalizedCompilerRequest,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
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

    (
        resolved.metric_entity_anchor_ref,
        resolved.resolved_imported_dimensions,
        resolved.imported_dimension_conflicts,
    ) = _resolve_imported_dimension_bridges(
        resolved,
        semantic_repository=semantic_repository,
    )
    _resolve_entity_field_groundings(
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

    _ = (semantic_repository, metric)
    return entity_anchor_ref, [], {}


def _metric_entity_anchor_ref(metric: ResolvedSemanticObject) -> str | None:
    header = dict(metric.semantic_object.get("header") or {})
    observed_entity_ref = _optional_str(header.get("observed_entity_ref"))
    if observed_entity_ref is not None:
        return observed_entity_ref
    return _optional_str(header.get("population_subject_ref"))


def _resolve_entity_field_groundings(
    resolved: ResolvedCompilerInputs,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
) -> None:
    predicate_objects = _resolve_predicates_for_field_usage(
        resolved,
        semantic_repository=semantic_repository,
    )
    field_usages = _collect_entity_field_usages(resolved, predicate_objects=predicate_objects)
    resolved.entity_composition = _build_entity_composition(resolved, field_usages)
    if semantic_repository is None:
        return
    for field_ref in sorted(field_usages):
        entity_ref, local_field_ref = _split_entity_field_ref(field_ref)
        if entity_ref is None:
            resolved.field_resolution_issues.append(
                FieldResolutionIssue(
                    code="ambiguous_field_ref",
                    field_ref=field_ref,
                    message="Entity field ref must use entity.<entity>.field.<field> form",
                    usage_path=field_usages[field_ref][0] if field_usages[field_ref] else None,
                )
            )
            continue
        try:
            entity = semantic_repository.resolve_entity_ref(entity_ref)
        except SemanticRuntimeError as error:
            resolved.field_resolution_issues.append(
                FieldResolutionIssue(
                    code="missing_entity_binding",
                    field_ref=field_ref,
                    message=str(error),
                    usage_path=field_usages[field_ref][0] if field_usages[field_ref] else None,
                    details={"entity_ref": entity_ref},
                )
            )
            continue
        entity_field = _entity_field_snapshot(
            field_ref,
            local_field_ref=local_field_ref,
            entity=entity,
            usage_paths=field_usages[field_ref],
        )
        if entity_field is None:
            resolved.field_resolution_issues.append(
                FieldResolutionIssue(
                    code="missing_entity_field",
                    field_ref=field_ref,
                    message="Entity field ref could not be resolved on the entity contract",
                    usage_path=field_usages[field_ref][0] if field_usages[field_ref] else None,
                    details={"entity_ref": entity_ref, "local_field_ref": local_field_ref},
                )
            )
            continue
        if not entity_field.source_object_ref and not entity_field.source_object_fqn:
            resolved.field_resolution_issues.append(
                FieldResolutionIssue(
                    code="missing_entity_binding",
                    field_ref=field_ref,
                    message="Entity field ref has no entity binding source locator",
                    usage_path=field_usages[field_ref][0] if field_usages[field_ref] else None,
                    details={"entity_ref": entity_ref},
                )
            )
        resolved.resolved_entity_fields[field_ref] = entity_field


def _collect_entity_field_usages(
    resolved: ResolvedCompilerInputs,
    *,
    predicate_objects: list[ResolvedSemanticObject],
) -> dict[str, list[str]]:
    usages: dict[str, list[str]] = {}

    def add(ref: str | None, usage_path: str, details: dict[str, Any] | None = None) -> None:
        field_ref = _normalize_entity_field_ref(ref)
        if field_ref is None:
            return
        paths = usages.setdefault(field_ref, [])
        if usage_path not in paths:
            paths.append(usage_path)
        usage_details = resolved.entity_field_usage_details.setdefault(field_ref, [])
        usage_details.append({"usage_path": usage_path, **(details or {})})

    if resolved.resolved_metric is not None:
        header = dict(resolved.resolved_metric.semantic_object.get("header") or {})
        payload = dict(resolved.resolved_metric.semantic_object.get("payload") or {})
        _ = header
        for component_name, component in _metric_component_items(payload):
            add(
                _optional_str(component.get("input_field_ref")),
                f"metric.{component_name}.input_field_ref",
            )
    for dimension in resolved.resolved_dimensions:
        interface_contract = dict(dimension.semantic_object.get("interface_contract") or {})
        add(
            _optional_str(interface_contract.get("source_field_ref")),
            f"{dimension.ref}.source_field_ref",
        )
    if resolved.resolved_filter_time is not None:
        header = dict(resolved.resolved_filter_time.semantic_object.get("header") or {})
        add(_optional_str(header.get("source_field_ref")), "time.source_field_ref")
    for predicate in predicate_objects:
        interface_contract = dict(predicate.semantic_object.get("interface_contract") or {})
        for atom in _predicate_atoms(dict(interface_contract.get("expression") or {})):
            add(
                _optional_str(atom.get("target_ref")),
                f"{predicate.ref}.expression.target_ref",
                {"operator": _optional_str(atom.get("op"))},
            )
    for process in (
        resolved.resolved_process,
        resolved.resolved_left_process,
        resolved.resolved_right_process,
    ):
        if process is None:
            continue
        semantic_object = dict(process.semantic_object)
        for field_ref in _collect_entity_field_refs_from_value(
            [
                semantic_object.get("interface_contract") or {},
                semantic_object.get("payload") or {},
            ]
        ):
            add(field_ref, f"{process.ref}.field_ref")
    return usages


def _metric_component_items(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for component_name in (
        "count_target",
        "measure",
        "numerator",
        "denominator",
        "value_component",
        "score_source",
    ):
        component = payload.get(component_name)
        if isinstance(component, dict):
            items.append((component_name, component))
    return items


def _resolve_predicates_for_field_usage(
    resolved: ResolvedCompilerInputs,
    *,
    semantic_repository: SemanticRuntimeRepository | None,
) -> list[ResolvedSemanticObject]:
    if semantic_repository is None:
        return []
    predicate_refs = _metric_predicate_refs(resolved)
    predicates: list[ResolvedSemanticObject] = []
    for predicate_ref in predicate_refs:
        try:
            predicates.append(
                _resolve_runtime_ref(
                    semantic_repository.resolve_predicate_ref,
                    predicate_ref,
                    label="predicate",
                )
            )
        except ValueError as error:
            resolved.warnings.append(
                {
                    "code": "predicate_ref_unresolved",
                    "message": str(error),
                    "predicate_ref": predicate_ref,
                }
            )
    return predicates


def _metric_predicate_refs(resolved: ResolvedCompilerInputs) -> list[str]:
    metric = resolved.resolved_metric
    if metric is None:
        return []
    refs: list[str] = []
    seen: set[str] = set()

    def add(raw_ref: Any) -> None:
        ref = _optional_str(raw_ref)
        if ref is not None and ref.startswith("predicate.") and ref not in seen:
            refs.append(ref)
            seen.add(ref)

    header = dict(metric.semantic_object.get("header") or {})
    payload = dict(metric.semantic_object.get("payload") or {})
    for ref in header.get("default_predicate_refs") or payload.get("default_predicate_refs") or []:
        add(ref)
    for _component_name, component in _metric_component_items(payload):
        for ref in component.get("qualifier_refs") or []:
            add(ref)
    add(resolved.normalized_request.request_scope_predicate_ref)
    return refs


def _predicate_atoms(expression: dict[str, Any]) -> list[dict[str, Any]]:
    if expression.get("target_ref") is not None:
        return [expression]
    atoms: list[dict[str, Any]] = []
    for item in expression.get("items") or []:
        if isinstance(item, dict):
            atoms.extend(_predicate_atoms(item))
    return atoms


def _collect_entity_field_refs_from_value(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        for nested in value.values():
            refs.extend(_collect_entity_field_refs_from_value(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.extend(_collect_entity_field_refs_from_value(nested))
    elif isinstance(value, str) and _normalize_entity_field_ref(value) is not None:
        refs.append(value)
    return refs


def _build_entity_composition(
    resolved: ResolvedCompilerInputs,
    field_usages: dict[str, list[str]],
) -> EntityComposition:
    anchor_entity_ref = None
    if resolved.resolved_metric is not None:
        anchor_entity_ref = _metric_entity_anchor_ref(resolved.resolved_metric)
    component_entity_refs: list[str] = []
    all_entity_refs: list[str] = []
    seen_components: set[str] = set()
    seen_all: set[str] = set()
    for field_ref, usage_paths in field_usages.items():
        entity_ref, _local = _split_entity_field_ref(field_ref)
        if entity_ref is None:
            continue
        if entity_ref not in seen_all:
            all_entity_refs.append(entity_ref)
            seen_all.add(entity_ref)
        if entity_ref not in seen_components and any(
            path.startswith("metric.") and path.endswith(".input_field_ref") for path in usage_paths
        ):
            component_entity_refs.append(entity_ref)
            seen_components.add(entity_ref)
    if anchor_entity_ref is not None and anchor_entity_ref not in seen_all:
        all_entity_refs.append(anchor_entity_ref)
        seen_all.add(anchor_entity_ref)
    return EntityComposition(
        anchor_entity_ref=anchor_entity_ref,
        component_entity_refs=sorted(component_entity_refs),
        all_entity_refs=sorted(all_entity_refs),
        is_cross_entity=len(seen_all) > 1,
    )


def _entity_field_snapshot(
    field_ref: str,
    *,
    local_field_ref: str,
    entity: ResolvedSemanticObject,
    usage_paths: list[str],
) -> ResolvedEntityField | None:
    interface_contract = dict(entity.semantic_object.get("interface_contract") or {})
    field = None
    for candidate in interface_contract.get("fields") or []:
        if (
            isinstance(candidate, dict)
            and _optional_str(candidate.get("field_ref")) == local_field_ref
        ):
            field = dict(candidate)
            break
    if field is None:
        return None
    binding = dict(interface_contract.get("binding") or {})
    entity_ref = entity.ref
    return ResolvedEntityField(
        field_ref=field_ref,
        entity_ref=entity_ref,
        local_field_ref=local_field_ref,
        entity_revision=entity.revision,
        value_type=_optional_str(field.get("value_type")),
        nullable=_optional_boolish(field.get("nullable")),
        unit=_optional_str(field.get("unit")),
        enum_hint=_optional_str(field.get("enum_hint")),
        profile_summary=dict(field.get("profile_summary") or {})
        if isinstance(field.get("profile_summary"), dict)
        else None,
        sensitivity_tags=[str(tag) for tag in field.get("sensitivity_tags") or []],
        physical_column=_optional_str(field.get("physical_column")),
        physical_expression_locator=dict(field.get("physical_expression_locator") or {})
        if isinstance(field.get("physical_expression_locator"), dict)
        else None,
        source_object_ref=_optional_str(binding.get("source_object_ref")),
        source_object_fqn=_optional_str(binding.get("source_object_fqn")),
        carrier_kind=_optional_str(binding.get("carrier_kind")),
        usage_paths=list(usage_paths),
    )


def _normalize_entity_field_ref(value: str | None) -> str | None:
    text = _optional_str(value)
    if text is None:
        return None
    if text.startswith("entity.") and ".field." in text:
        return text
    if text.startswith("field."):
        return text
    return None


def _split_entity_field_ref(value: str) -> tuple[str | None, str]:
    if value.startswith("field."):
        return None, value
    if value.startswith("entity.") and ".field." in value:
        entity_ref, field_name = value.split(".field.", 1)
        return entity_ref, f"field.{field_name}"
    return None, value


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_boolish(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)
