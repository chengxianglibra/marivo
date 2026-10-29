"""Explicit scope normalization for semantic runtime previews."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, TypeAlias

from marivo.datasource.ir import JsonSourceIR, QueryParamScalar, QueryParamScalarList
from marivo.datasource.json_source import normalize_json_source_params
from marivo.datasource.source import AuthoringScope, PartitionScope, UnprunedScope
from marivo.preview import PREVIEW_DEFAULT_LIMIT, PreviewLimitError, validate_preview_limit
from marivo.refs import EntityKind, Ref, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError, _raise, repair

if TYPE_CHECKING:
    from marivo.datasource.runtime import DatasourceConnectionService
    from marivo.semantic.catalog import Entity, SemanticCatalog
    from marivo.semantic.validator import Registry

PreviewScope: TypeAlias = AuthoringScope | Sequence[tuple["Entity", AuthoringScope]]
PreviewSourceBindings: TypeAlias = Sequence[
    tuple["Entity", Mapping[str, QueryParamScalar | QueryParamScalarList]]
]


def normalize_preview_limit(limit: int, *, operation: Literal["preview", "preview_many"]) -> int:
    """Validate the retained-row limit at its semantic public boundary."""
    try:
        return validate_preview_limit(limit)
    except PreviewLimitError as exc:
        _raise(
            ErrorKind.MATERIALIZE_FAILED,
            f"catalog.{operation} received an invalid preview limit.",
            cls=SemanticRuntimeError,
            expected=f"an integer preview limit from {exc.min_limit} to {exc.max_limit} inclusive",
            received=f"{limit!r} ({type(limit).__name__})",
            details={"query_executed": False},
            hint=(
                f"Choose an integer from {exc.min_limit} to {exc.max_limit} inclusive for retained "
                "preview rows; scope.max_rows separately guards source acquisition."
            ),
            repair_value=repair(
                kind="reauthor",
                canonical_id=operation,
                action=f"Retry catalog.{operation} with limit={PREVIEW_DEFAULT_LIMIT}.",
            ),
        )


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


def preflight_preview_scope(
    bindings: NormalizedPreviewScope,
    *,
    registry: Registry,
    connections: DatasourceConnectionService,
    project_root: Path,
) -> None:
    """Reuse datasource's metadata-only physical pruning checks for every input."""
    from marivo.datasource import credentials as cr
    from marivo.datasource.inspection import _inspect_resolved_source, _preflight_scope

    with connections.resolution_context(), cr.operation_context(project_root=project_root):
        for entity_id, scope in bindings.scopes:
            entity = registry.entities[entity_id]
            inspection = _inspect_resolved_source(
                registry.datasources[entity.datasource],
                entity.source,
                project_root=project_root,
                connections=connections,
            )
            _preflight_scope(inspection, scope=scope)


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
    if ref in registry.declared_metrics:
        return SemanticKind.METRIC
    if ref in registry.relationships:
        return SemanticKind.RELATIONSHIP
    if ref in registry.period_calendars:
        return SemanticKind.PERIOD_CALENDAR
    if ref in registry.temporal_sets:
        return SemanticKind.TEMPORAL_SET
    raise KeyError(ref)


def _exact_semantic_ref(ref: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
        SemanticKind.PERIOD_CALENDAR: ref_factory.period_calendar,
        SemanticKind.TEMPORAL_SET: ref_factory.temporal_set,
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
    if kind == SemanticKind.PERIOD_CALENDAR:
        field = registry.dimensions.get(registry.period_calendars[ref].date)
        return (field.entity,) if field is not None else ()
    if kind == SemanticKind.TEMPORAL_SET:
        field = registry.dimensions.get(registry.temporal_sets[ref].occurrence_id)
        return (field.entity,) if field is not None else ()
    if kind != SemanticKind.METRIC:
        return ()

    if ref in registry.declared_metrics:
        from marivo.semantic._deferred_semantics import consume_metric
        from marivo.semantic._numeric_preview import validate_preview_metric

        metric = registry.declared_metrics[ref]
        validate_preview_metric(metric)
        return tuple(root.ref.path for root in consume_metric(metric).roots)

    return ()


def dependency_entities_for_ref(ref: str, *, registry: Registry) -> tuple[str, ...]:
    return dependency_entities(ref, _semantic_kind(ref, registry), registry)


def _normalize_entity_key(
    key: Entity,
    *,
    preview_ref: str,
    parameter: str,
    catalog: SemanticCatalog,
) -> str:
    from marivo.semantic.catalog import _normalize_semantic_input

    return _normalize_semantic_input(
        catalog,
        key,
        allowed_kinds=frozenset({SemanticKind.ENTITY}),
        location=parameter,
    ).path


def _validate_scope(
    scope: object,
    *,
    preview_ref: str,
    operation: str = "catalog.preview",
) -> AuthoringScope:
    scope_label = "Preview scope" if operation == "catalog.preview" else "Online validation scope"
    if not isinstance(scope, PartitionScope | UnprunedScope):
        _blocked(
            preview_ref,
            f"{operation}(..., scope=...) requires a scope from "
            "md.partition(), md.time_range(), or md.unpruned().",
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
            else "Online validation partition scope"
        )
        _blocked(preview_ref, f"{partition_label} is invalid.", details={})
    return scope


def _normalize_scopes(
    entity_ids: tuple[str, ...],
    scope: PreviewScope,
    *,
    preview_ref: str,
    operation: str = "catalog.preview",
    catalog: SemanticCatalog,
) -> tuple[tuple[str, AuthoringScope], ...]:
    operation_label = "preview" if operation == "catalog.preview" else "online validation"
    if len(entity_ids) == 1:
        if isinstance(scope, Sequence | Mapping):
            _blocked(
                preview_ref,
                f"A single-entity {operation_label} requires one AuthoringScope, not an Entity pair sequence.",
                details={"received_type": type(scope).__name__},
            )
        return (
            (
                entity_ids[0],
                _validate_scope(scope, preview_ref=preview_ref, operation=operation),
            ),
        )
    if not isinstance(scope, Sequence) or isinstance(scope, str | bytes):
        _blocked(
            preview_ref,
            f"A multi-entity {operation_label} requires an Entity object/scope pair sequence.",
            details={"received_type": type(scope).__name__},
        )
    by_entity: dict[str, AuthoringScope] = {}
    for pair in scope:
        if not isinstance(pair, tuple) or len(pair) != 2:
            _blocked(
                preview_ref,
                "Scope entries must be (Entity object, AuthoringScope) pairs.",
                details={},
            )
        key, value = pair
        entity_id = _normalize_entity_key(
            key,
            preview_ref=preview_ref,
            parameter=f"{operation}(..., scope=...) pairs",
            catalog=catalog,
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
            "Preview scope pairs"
            if operation == "catalog.preview"
            else "catalog.validate scope pairs"
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
    catalog: SemanticCatalog,
) -> Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]]:
    if source_bindings is not None and (
        not isinstance(source_bindings, Sequence) or isinstance(source_bindings, str | bytes)
    ):
        _blocked(
            preview_ref,
            "catalog.preview(..., source_bindings=...) requires an Entity object/parameter mapping pair sequence.",
            details={"received_type": type(source_bindings).__name__},
        )
    supplied: dict[str, Mapping[str, QueryParamScalar | QueryParamScalarList]] = {}
    for pair in source_bindings or ():
        if not isinstance(pair, tuple) or len(pair) != 2:
            _blocked(
                preview_ref,
                "Source binding entries must be (Entity object, parameter mapping) pairs.",
                details={},
            )
        key, value = pair
        entity_id = _normalize_entity_key(
            key,
            preview_ref=preview_ref,
            parameter="catalog.preview(..., source_bindings=...) pairs",
            catalog=catalog,
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
    catalog: SemanticCatalog,
) -> NormalizedPreviewScope:
    """Validate one preview's explicit execution inputs without connecting."""
    entity_ids = dependency_entities(ref, kind, registry)
    if not entity_ids:
        _blocked(
            ref,
            f"catalog.preview() does not support {kind} refs.",
            details={"kind": str(kind)},
        )
    scopes = _normalize_scopes(entity_ids, scope, preview_ref=ref, catalog=catalog)
    normalized_source_bindings = _normalize_source_bindings(
        entity_ids,
        source_bindings,
        preview_ref=ref,
        registry=registry,
        catalog=catalog,
    )
    return _normalized_preview_scope(
        ref=ref,
        kind=kind,
        entity_ids=entity_ids,
        scopes=scopes,
        source_bindings=normalized_source_bindings,
        registry=registry,
    )


def _normalized_preview_scope(
    *,
    ref: str,
    kind: SemanticKind,
    entity_ids: tuple[str, ...],
    scopes: tuple[tuple[str, AuthoringScope], ...],
    source_bindings: Mapping[str, Mapping[str, QueryParamScalar | QueryParamScalarList]],
    registry: Registry,
) -> NormalizedPreviewScope:
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
        source_bindings=source_bindings,
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
    catalog: SemanticCatalog,
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
    normalized_scopes = dict(
        _normalize_scopes(entity_ids, scope, preview_ref=batch_refs[0], catalog=catalog)
    )
    normalized_bindings = _normalize_source_bindings(
        entity_ids,
        source_bindings,
        preview_ref=batch_refs[0],
        registry=registry,
        catalog=catalog,
    )
    normalized: list[NormalizedPreviewScope] = []
    for ref, kind in refs:
        dependencies = dependency_entities(ref, kind, registry)
        normalized.append(
            _normalized_preview_scope(
                ref=ref,
                kind=kind,
                entity_ids=dependencies,
                scopes=tuple(
                    (entity_id, normalized_scopes[entity_id]) for entity_id in dependencies
                ),
                source_bindings={
                    entity_id: normalized_bindings[entity_id]
                    for entity_id in dependencies
                    if entity_id in normalized_bindings
                },
                registry=registry,
            )
        )
    return tuple(normalized)
