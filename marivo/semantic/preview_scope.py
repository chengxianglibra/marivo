"""Explicit scope normalization for semantic runtime previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn, TypeAlias

from marivo.datasource.ir import JsonSourceIR, QueryParamScalar, QueryParamScalarList
from marivo.datasource.json_source import normalize_json_source_params
from marivo.datasource.source import AuthoringScope, PartitionScope, UnprunedScope
from marivo.refs import EntityKind, Ref, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise
from marivo.semantic.ir import composition_components

if TYPE_CHECKING:
    from marivo.semantic.validator import Registry

PreviewScope: TypeAlias = AuthoringScope | Mapping[Ref[EntityKind], AuthoringScope]
PreviewSourceBindings: TypeAlias = Mapping[
    Ref[EntityKind],
    Mapping[str, QueryParamScalar | QueryParamScalarList],
]


@dataclass(frozen=True, slots=True)
class NormalizedPreviewScope:
    """Connection-free normalized execution inputs for one semantic ref."""

    checked_ref: Ref[SemanticKindTag]
    entity_refs: tuple[Ref[EntityKind], ...]
    scopes: tuple[tuple[str, AuthoringScope], ...]
    source_bindings: Mapping[
        str,
        Mapping[str, QueryParamScalar | QueryParamScalarList],
    ]
    backend: str
    datasource_id: str
    timeout_seconds: int

    @property
    def entity_ids(self) -> tuple[str, ...]:
        return tuple(ref.path for ref in self.entity_refs)

    @property
    def entity_scopes(self) -> Mapping[str, AuthoringScope]:
        return dict(self.scopes)


def _blocked(ref: str, message: str, *, details: Mapping[str, object]) -> NoReturn:
    _raise(
        ErrorKind.MATERIALIZE_FAILED,
        message,
        cls=SemanticRuntimeError,
        refs=(ref,),
        details={"query_executed": False, **details},
    )


def _semantic_kind(ref: str, registry: Registry) -> SemanticKind:
    if ref in registry.entities:
        return SemanticKind.ENTITY
    if ref in registry.dimensions:
        return (
            SemanticKind.TIME_DIMENSION
            if registry.dimensions[ref].is_time_dimension
            else SemanticKind.DIMENSION
        )
    if ref in registry.measures:
        return SemanticKind.MEASURE
    if ref in registry.metrics:
        return SemanticKind.METRIC
    if ref in registry.relationships:
        return SemanticKind.RELATIONSHIP
    if ref in registry.events:
        return SemanticKind.EVENT
    if ref in registry.state_models:
        return SemanticKind.STATE_MODEL
    if ref in registry.period_calendars:
        return SemanticKind.PERIOD_CALENDAR
    if ref in registry.temporal_sets:
        return SemanticKind.TEMPORAL_SET
    if ref in registry.work_schedules:
        return SemanticKind.WORK_SCHEDULE
    raise KeyError(ref)


def _exact_semantic_ref(ref: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
        SemanticKind.EVENT: ref_factory.event,
        SemanticKind.STATE_MODEL: ref_factory.state_model,
        SemanticKind.PERIOD_CALENDAR: ref_factory.period_calendar,
        SemanticKind.TEMPORAL_SET: ref_factory.temporal_set,
        SemanticKind.WORK_SCHEDULE: ref_factory.work_schedule,
    }.get(kind)
    if factory is None:
        raise AssertionError(f"unsupported preview ref kind: {kind}")
    return factory(ref)


def dependency_entities(ref: str, kind: SemanticKind, registry: Registry) -> tuple[str, ...]:
    """Return every entity required to execute one semantic ref."""
    if kind == SemanticKind.ENTITY:
        return (ref,)
    if kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        return (registry.dimensions[ref].entity,)
    if kind == SemanticKind.MEASURE:
        return (registry.measures[ref].entity,)
    if kind == SemanticKind.RELATIONSHIP:
        relationship = registry.relationships[ref]
        return tuple(dict.fromkeys((relationship.from_entity, relationship.to_entity)))
    if kind == SemanticKind.EVENT:
        event = registry.events[ref]
        event_entities = [event.source_entity]
        for participant in event.participants:
            endpoint = event.source_entity
            for relationship_id in participant.path or ():
                endpoint = registry.relationships[relationship_id].to_entity
                if endpoint not in event_entities:
                    event_entities.append(endpoint)
        return tuple(event_entities)
    if kind == SemanticKind.STATE_MODEL:
        model = registry.state_models[ref]
        entities: list[str] = []
        event_refs = tuple(
            dict.fromkeys(
                (
                    *(item.trigger.event_ref for item in model.inceptions),
                    *(item.trigger.event_ref for item in model.transitions),
                )
            )
        )
        for event_ref in event_refs:
            for entity_id in dependency_entities(event_ref, SemanticKind.EVENT, registry):
                if entity_id not in entities:
                    entities.append(entity_id)
        if model.subject not in entities:
            entities.append(model.subject)
        return tuple(entities)
    if kind == SemanticKind.PERIOD_CALENDAR:
        field = registry.dimensions.get(registry.period_calendars[ref].date)
        return (field.entity,) if field is not None else ()
    if kind == SemanticKind.TEMPORAL_SET:
        field = registry.dimensions.get(registry.temporal_sets[ref].occurrence_id)
        return (field.entity,) if field is not None else ()
    if kind == SemanticKind.WORK_SCHEDULE:
        field = registry.dimensions.get(registry.work_schedules[ref].date)
        return (field.entity,) if field is not None else ()
    if kind != SemanticKind.METRIC:
        return ()

    ordered: list[str] = []
    visited_metrics: set[str] = set()

    def visit(metric_id: str) -> None:
        if metric_id in visited_metrics:
            return
        visited_metrics.add(metric_id)
        metric = registry.metrics[metric_id]
        for entity_id in metric.entities:
            if entity_id in registry.entities and entity_id not in ordered:
                ordered.append(entity_id)
        if metric.composition is not None:
            for component in composition_components(metric.composition).values():
                if component in registry.metrics:
                    visit(component)

    visit(ref)
    return tuple(ordered)


def dependency_entities_for_ref(ref: str, *, registry: Registry) -> tuple[str, ...]:
    return dependency_entities(ref, _semantic_kind(ref, registry), registry)


def _normalize_entity_key(key: object, *, preview_ref: str, parameter: str) -> str:
    if type(key) is Ref and key.kind is SemanticKind.ENTITY:
        return key.path
    _blocked(
        preview_ref,
        f"{parameter} requires exact Ref[entity] keys.",
        details={"received_type": type(key).__name__},
    )


def _validate_scope(
    scope: object,
    *,
    preview_ref: str,
    operation: str = "catalog.preview",
) -> AuthoringScope:
    scope_label = "Preview scope" if operation == "catalog.preview" else "Source-health scope"
    if not isinstance(scope, PartitionScope | UnprunedScope):
        _blocked(
            preview_ref,
            f"{operation}(..., scope=...) requires md.PartitionScope or md.UnprunedScope.",
            details={"received_type": type(scope).__name__},
        )
    if type(scope.max_rows) is not int or scope.max_rows < 1:
        _blocked(
            preview_ref,
            f"{scope_label} max_rows must be a positive integer.",
            details={},
        )
    if type(scope.timeout_seconds) is not int or scope.timeout_seconds < 1:
        _blocked(
            preview_ref,
            f"{scope_label} timeout_seconds must be a positive integer.",
            details={},
        )
    if isinstance(scope, PartitionScope) and scope._time_range is not None:
        predicate = scope._time_range
        invalid = (
            bool(scope.values)
            or not predicate.column
            or type(predicate.start) is not type(predicate.end)
            or predicate.start >= predicate.end
        )
    elif isinstance(scope, PartitionScope):
        invalid = not scope.values or any(
            type(entry) is not tuple
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not entry[0]
            or not isinstance(entry[1], str)
            or not entry[1]
            for entry in scope.values
        )
    else:
        invalid = False
    if invalid:
        partition_label = (
            "Preview partition scope"
            if operation == "catalog.preview"
            else "Source-health partition scope"
        )
        _blocked(preview_ref, f"{partition_label} is invalid.", details={})
    return scope


def _normalize_scopes(
    entity_ids: tuple[str, ...],
    scope: PreviewScope,
    *,
    preview_ref: str,
    operation: str = "catalog.preview",
) -> tuple[tuple[str, AuthoringScope], ...]:
    operation_label = "preview" if operation == "catalog.preview" else "source-health"
    if len(entity_ids) == 1:
        if isinstance(scope, Mapping):
            _blocked(
                preview_ref,
                f"A single-entity {operation_label} requires one AuthoringScope, not a Mapping.",
                details={"received_type": type(scope).__name__},
            )
        return (
            (
                entity_ids[0],
                _validate_scope(scope, preview_ref=preview_ref, operation=operation),
            ),
        )
    if not isinstance(scope, Mapping):
        _blocked(
            preview_ref,
            f"A multi-entity {operation_label} requires a Mapping keyed by exact Ref[entity].",
            details={"received_type": type(scope).__name__},
        )
    by_entity: dict[str, AuthoringScope] = {}
    for key, value in scope.items():
        entity_id = _normalize_entity_key(
            key,
            preview_ref=preview_ref,
            parameter=f"{operation}(..., scope=...) Mapping",
        )
        if entity_id in by_entity:
            _blocked(
                preview_ref,
                f"{operation_label.capitalize()} scope repeats entity {entity_id!r}.",
                details={},
            )
        by_entity[entity_id] = _validate_scope(
            value,
            preview_ref=preview_ref,
            operation=operation,
        )
    if set(by_entity) != set(entity_ids):
        mapping_label = (
            "Preview scope Mapping"
            if operation == "catalog.preview"
            else "catalog.source_health scope Mapping"
        )
        _blocked(
            preview_ref,
            f"{mapping_label} must cover exactly the dependency entities.",
            details={
                "missing": tuple(
                    entity_id for entity_id in entity_ids if entity_id not in by_entity
                ),
                "unrelated": tuple(
                    entity_id for entity_id in by_entity if entity_id not in entity_ids
                ),
            },
        )
    return tuple((entity_id, by_entity[entity_id]) for entity_id in entity_ids)


def _normalize_source_bindings(
    entity_ids: tuple[str, ...],
    source_bindings: PreviewSourceBindings | None,
    *,
    preview_ref: str,
    registry: Registry,
) -> Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]]:
    if source_bindings is not None and not isinstance(source_bindings, Mapping):
        _blocked(
            preview_ref,
            "catalog.preview(..., source_bindings=...) requires a Mapping.",
            details={"received_type": type(source_bindings).__name__},
        )
    supplied: dict[str, Mapping[str, QueryParamScalar | QueryParamScalarList]] = {}
    for key, value in (source_bindings or {}).items():
        entity_id = _normalize_entity_key(
            key,
            preview_ref=preview_ref,
            parameter="catalog.preview(..., source_bindings=...) Mapping",
        )
        if entity_id in supplied:
            _blocked(preview_ref, f"Source bindings repeat entity {entity_id!r}.", details={})
        if not isinstance(value, Mapping):
            _blocked(
                preview_ref,
                "Source binding values must be parameter mappings.",
                details={"entity": entity_id, "received_type": type(value).__name__},
            )
        supplied[entity_id] = value
    unrelated = tuple(entity_id for entity_id in supplied if entity_id not in entity_ids)
    if unrelated:
        _blocked(
            preview_ref,
            "Source bindings contain entities outside the preview dependency closure.",
            details={"unrelated": unrelated},
        )

    normalized: dict[str, Mapping[str, QueryParamScalar | QueryParamScalarList]] = {}
    for entity_id in entity_ids:
        source = registry.entities[entity_id].source
        binding = supplied.get(entity_id)
        if isinstance(source, JsonSourceIR):
            try:
                params = normalize_json_source_params(source, binding)
            except (TypeError, ValueError) as exc:
                _blocked(
                    preview_ref,
                    f"Source bindings for entity {entity_id!r} are invalid: {exc}",
                    details={"entity": entity_id},
                )
            if params:
                normalized[entity_id] = params
        elif binding is not None:
            _blocked(
                preview_ref,
                "Source bindings are supported only for parameterized JSON sources.",
                details={"entity": entity_id, "source_kind": source.kind},
            )
    return normalized


def normalize_preview_scope(
    *,
    ref: str,
    kind: SemanticKind,
    scope: PreviewScope,
    source_bindings: PreviewSourceBindings | None,
    registry: Registry,
) -> NormalizedPreviewScope:
    """Validate one preview's explicit execution inputs without connecting."""
    entity_ids = dependency_entities(ref, kind, registry)
    if not entity_ids:
        _blocked(
            ref,
            f"catalog.preview() does not support {kind} refs.",
            details={"kind": str(kind)},
        )
    scopes = _normalize_scopes(entity_ids, scope, preview_ref=ref)
    normalized_source_bindings = _normalize_source_bindings(
        entity_ids,
        source_bindings,
        preview_ref=ref,
        registry=registry,
    )
    datasource_ids = tuple(registry.entities[entity_id].datasource for entity_id in entity_ids)
    if len(set(datasource_ids)) != 1:
        _blocked(
            ref,
            "Scoped preview requires all dependency entities to share one datasource backend.",
            details={"datasources": datasource_ids},
        )
    datasource = registry.datasources[datasource_ids[0]]
    return NormalizedPreviewScope(
        checked_ref=_exact_semantic_ref(ref, kind),
        entity_refs=tuple(ref_factory.entity(entity_id) for entity_id in entity_ids),
        scopes=scopes,
        source_bindings=normalized_source_bindings,
        backend=datasource.backend_type,
        datasource_id=datasource_ids[0],
        timeout_seconds=min(value.timeout_seconds for _entity_id, value in scopes),
    )


def normalize_preview_batch_scopes(
    *,
    refs: Sequence[tuple[str, SemanticKind]],
    scope: PreviewScope,
    source_bindings: PreviewSourceBindings | None,
    registry: Registry,
) -> tuple[NormalizedPreviewScope, ...]:
    """Validate one complete explicit scope set for a semantic ref batch."""
    batch_refs = tuple(ref for ref, _kind in refs)
    entity_ids = tuple(
        dict.fromkeys(
            entity_id
            for ref, kind in refs
            for entity_id in dependency_entities(ref, kind, registry)
        )
    )
    if not entity_ids:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            "catalog.preview_many(refs, scope=...) requires at least one executable semantic ref.",
            cls=SemanticRuntimeError,
            refs=batch_refs,
            details={"query_executed": False},
        )
    normalized_scopes = dict(_normalize_scopes(entity_ids, scope, preview_ref=batch_refs[0]))
    normalized_bindings = _normalize_source_bindings(
        entity_ids,
        source_bindings,
        preview_ref=batch_refs[0],
        registry=registry,
    )
    normalized: list[NormalizedPreviewScope] = []
    for ref, kind in refs:
        dependencies = dependency_entities(ref, kind, registry)
        ref_scope: PreviewScope
        if len(dependencies) == 1:
            ref_scope = normalized_scopes[dependencies[0]]
        else:
            ref_scope = {
                ref_factory.entity(entity_id): normalized_scopes[entity_id]
                for entity_id in dependencies
            }
        ref_bindings = {
            ref_factory.entity(entity_id): normalized_bindings[entity_id]
            for entity_id in dependencies
            if entity_id in normalized_bindings
        }
        normalized.append(
            normalize_preview_scope(
                ref=ref,
                kind=kind,
                scope=ref_scope,
                source_bindings=ref_bindings,
                registry=registry,
            )
        )
    return tuple(normalized)
