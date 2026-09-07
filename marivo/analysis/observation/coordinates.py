"""Local functional-path and initial day-coordinate admission."""

from __future__ import annotations

from dataclasses import replace

from marivo._temporal import Grain, TimeScope
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.observation.contracts import (
    DimensionInput,
    MetricPayload,
    ObservationOwner,
    TimeDimensionInput,
    additional_captures,
    construction_error,
    metric_contracts,
    metric_definition,
    owner_of,
    path_dependency_fingerprint,
)
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.refs import Ref, SemanticKind
from marivo.semantic.catalog import DimensionEntry, TimeDimensionEntry
from marivo.semantic.ir import TargetDimensionContract
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity


def functional_path(
    registry: Registry, source: str, target: str, *, allow_versioned_target: bool = False
) -> tuple[str, ...]:
    """Prove a unique path whose every destination join key is its full identity."""
    if source == target:
        return ()
    edges: dict[str, list[tuple[str, str]]] = {}
    for relation in registry.relationships.values():
        left = registry.entities[relation.from_entity]
        right = registry.entities[relation.to_entity]
        if right.primary_key and tuple(key.to_key for key in relation.keys) == right.primary_key:
            edges.setdefault(left.semantic_id, []).append((right.semantic_id, relation.semantic_id))
        if left.primary_key and tuple(key.from_key for key in relation.keys) == left.primary_key:
            edges.setdefault(right.semantic_id, []).append((left.semantic_id, relation.semantic_id))
    found: list[tuple[str, ...]] = []

    def walk(current: str, visited: frozenset[str], path: tuple[str, ...]) -> None:
        if len(found) > 1:
            return
        for destination, relation in sorted(edges.get(current, ())):
            if destination in visited:
                continue
            candidate = (*path, relation)
            if destination == target:
                found.append(candidate)
            else:
                walk(destination, visited | {destination}, candidate)

    walk(source, frozenset({source}), ())
    if len(found) != 1:
        raise construction_error(
            "one unique functional governed relationship path",
            "missing, ambiguous, or fanout path",
            repair="Use a directly bound or uniquely to-one coordinate; general contribution allocations belong to a later slice.",
        )
    current = source
    for name in found[0]:
        relation = registry.relationships[name]
        left_contract = normalize_target_entity(registry, relation.from_entity)
        right_contract = normalize_target_entity(registry, relation.to_entity)
        left_types, right_types = dict(left_contract.columns), dict(right_contract.columns)
        if any(
            key.from_key not in left_types
            or key.to_key not in right_types
            or left_types[key.from_key] != right_types[key.to_key]
            for key in relation.keys
        ):
            raise construction_error(
                "exact compatible declared relationship key types",
                "missing or incompatible join keys",
            )
        destination = (
            relation.to_entity if current == relation.from_entity else relation.from_entity
        )
        for entity in (left_contract, right_contract):
            if entity.version is not None and not (
                allow_versioned_target and entity.ref.path == target
            ):
                raise construction_error(
                    "resolved atemporal relationship representations",
                    "versioned source or intermediate path",
                    repair="Use a non-versioned path or an already selected Population endpoint; historical enrichment requires its own temporal contract.",
                )
        current = destination
    return found[0]


def path_entities(
    registry: Registry, source: str, paths: tuple[tuple[str, ...], ...]
) -> tuple[str, ...]:
    ids = {source}
    for path in paths:
        for name in path:
            relation = registry.relationships[name]
            ids.update((relation.from_entity, relation.to_entity))
    return tuple(sorted(ids))


def normalize_dimension_input(
    owner: ObservationOwner, value: DimensionInput | TimeDimensionInput, *, time: bool
) -> TargetDimensionContract:
    expected = SemanticKind.TIME_DIMENSION if time else SemanticKind.DIMENSION
    if isinstance(value, (DimensionEntry, TimeDimensionEntry)):
        if (
            type(value) not in (DimensionEntry, TimeDimensionEntry)
            or value._catalog is not owner.catalog_identity
        ):
            raise construction_error("current exact catalog entry", "foreign or stale entry")
        reference = value.ref
    elif type(value) is Ref:
        reference = value
    else:
        raise construction_error("exact Dimension ref or current entry", type(value).__name__)
    if reference.kind is not expected:
        raise construction_error(expected.value, reference.kind.value)
    return normalize_target_dimension(owner.semantic_registry, reference.path)


def resolve_time_axis(
    owner: ObservationOwner,
    roots: tuple[str, ...],
    scope: TimeScope | None,
    explicit: TimeDimensionInput | None,
    *,
    entity_membership: bool = False,
) -> TargetDimensionContract | None:
    if scope is None:
        if explicit is not None:
            raise construction_error(
                "time_scope with time_dimension", "time_dimension without scope"
            )
        return None
    if not isinstance(scope, TimeScope):
        raise construction_error("immutable TimeScope", type(scope).__name__)
    if explicit is not None:
        axis = normalize_dimension_input(owner, explicit, time=True)
        for root in roots:
            functional_path(owner.semantic_registry, root, axis.entity_ref.path)
        return axis
    if entity_membership:
        own_defaults = tuple(
            dimension
            for dimension in owner.semantic_registry.dimensions.values()
            if dimension.entity == roots[0] and dimension.is_time_dimension and dimension.is_default
        )
        if len(own_defaults) == 1:
            return normalize_target_dimension(owner.semantic_registry, own_defaults[0].semantic_id)
        if len(own_defaults) > 1:
            raise construction_error(
                "one declared Entity default time axis", "ambiguous Entity defaults"
            )
    candidates: list[TargetDimensionContract] = []
    for dimension in owner.semantic_registry.dimensions.values():
        if not dimension.is_time_dimension:
            continue
        try:
            for root in roots:
                functional_path(owner.semantic_registry, root, dimension.entity)
        except ObservationConstructionError:
            continue
        candidates.append(
            normalize_target_dimension(owner.semantic_registry, dimension.semantic_id)
        )
    defaults = tuple(item for item in candidates if item.is_default)
    selected = (
        tuple(candidates) if entity_membership else (defaults if defaults else tuple(candidates))
    )
    if len(selected) != 1:
        raise construction_error(
            "one compatible default or sole Metric reference time axis",
            "missing or ambiguous reference axis",
            repair="Pass time_dimension using one exact compatible current time-Dimension ref.",
        )
    return selected[0]


def with_dimensions(dataset: Dataset, dimensions: tuple[DimensionInput, ...]) -> Dataset:
    definition = metric_definition(dataset)
    if not definition.entity_present or not dimensions:
        raise construction_error(
            "Entity-present input and at least one Dimension", "invalid coordinate declaration"
        )
    owner = owner_of(dataset)
    selected = tuple(normalize_dimension_input(owner, item, time=False) for item in dimensions)
    all_dimensions = definition.dimensions + selected
    if len({item.ref.path for item in all_dimensions}) != len(all_dimensions):
        raise construction_error("distinct ordered Dimensions", "duplicate Dimension")
    paths = []
    for dimension in selected:
        path = functional_path(
            owner.semantic_registry, definition.entity.ref.path, dimension.entity_ref.path
        )
        for metric in definition.metrics:
            for root in metric.computation_roots:
                functional_path(owner.semantic_registry, root.path, dimension.entity_ref.path)
        for entity_id in path_entities(
            owner.semantic_registry, definition.entity.ref.path, (path,)
        ):
            if normalize_target_entity(owner.semantic_registry, entity_id).version is not None:
                raise construction_error(
                    "atemporal functional coordinate in this slice", "versioned coordinate path"
                )
        paths.append(path)
    added_dependencies = tuple(
        (
            dimension.ref.path,
            path_dependency_fingerprint(owner, definition.entity.ref.path, (path,)),
        )
        for dimension, path in zip(selected, paths, strict=True)
    )
    updated = replace(
        definition,
        dimensions=all_dimensions,
        coordinate_dependencies=(*definition.coordinate_dependencies, *added_dependencies),
    )
    row, row_set = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    captures = additional_captures(
        dataset,
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(
                owner.semantic_registry, definition.entity.ref.path, tuple(paths)
            )
        ),
    )
    payload = MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=captures)
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.with_dimensions",
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=row_set,
        payload=payload,
    )


def with_time_axis(dataset: Dataset, time_dimension: TimeDimensionInput, grain: Grain) -> Dataset:
    definition = metric_definition(dataset)
    if not definition.entity_present or definition.time_axis is not None:
        raise construction_error(
            "Entity-present input without a time coordinate", "second time axis or reduced Entity"
        )
    if (
        not isinstance(grain, Grain)
        or grain.kind != "builtin"
        or grain.unit != "day"
        or grain.count != 1
    ):
        raise construction_error("the initial builtin day grain", "unsupported grain")
    owner = owner_of(dataset)
    axis = normalize_dimension_input(owner, time_dimension, time=True)
    path = functional_path(
        owner.semantic_registry, definition.entity.ref.path, axis.entity_ref.path
    )
    for metric in definition.metrics:
        for root in metric.computation_roots:
            functional_path(owner.semantic_registry, root.path, axis.entity_ref.path)
    if axis.granularity not in ("second", "minute", "hour", "day"):
        raise construction_error(
            "declared source granularity no coarser than day", "incompatible or missing granularity"
        )
    if definition.reference_axis is not None and definition.reference_axis.ref != axis.ref:
        raise construction_error(
            "the exact observation reference axis", "unproven temporal alignment"
        )
    updated = replace(
        definition,
        time_axis=axis,
        coordinate_dependencies=(
            *definition.coordinate_dependencies,
            (
                axis.ref.path,
                path_dependency_fingerprint(owner, definition.entity.ref.path, (path,)),
            ),
        ),
    )
    row, row_set = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    captures = additional_captures(
        dataset,
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(owner.semantic_registry, definition.entity.ref.path, (path,))
        ),
    )
    payload = MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=captures)
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.with_time_axis",
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=row_set,
        payload=payload,
    )
