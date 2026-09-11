"""Governed source coordinate paths, grains, and contribution admission."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from marivo._temporal import Grain, PeriodCalendarSnapshotV1, TemporalResolver, TimeScope
from marivo.analysis.datasets.actions import construct_operator
from marivo.analysis.datasets.base import Dataset
from marivo.analysis.datasets.descriptors import _CORE_TOKEN
from marivo.analysis.observation.contracts import (
    CoordinatePathBinding,
    DimensionInput,
    MetricCoordinateAggregationV1,
    MetricDefinition,
    MetricPayload,
    ObservationOwner,
    TimeDimensionInput,
    additional_captures,
    construction_error,
    metric_contracts,
    metric_definition,
    path_dependency_fingerprint,
    producer_contract,
    source_owner_of,
)
from marivo.analysis.observation.errors import ObservationConstructionError
from marivo.refs import Ref, SemanticKind
from marivo.semantic.catalog import DimensionEntry, TimeDimensionEntry
from marivo.semantic.ir import DateParse, TargetDimensionContract
from marivo.semantic.metric_graph import (
    AggregateNodeV1,
    CumulativeAnchorV1,
    WeightedMeanAggregateNodeV1,
    component_node,
)
from marivo.semantic.validator import Registry, normalize_target_dimension, normalize_target_entity


def functional_path(
    registry: Registry,
    source: str,
    target: str,
    *,
    allow_versioned_target: bool = False,
    allow_versioned_source: bool = False,
    allow_versioned_intermediates: bool = False,
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
            repair="Use one directly bound or uniquely to-one governed mapping for this operation; a to-many contribution mapping requires its own coordinate admission.",
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
                (allow_versioned_target and entity.ref.path == target)
                or (allow_versioned_source and entity.ref.path == source)
                or (allow_versioned_intermediates and entity.ref.path not in (source, target))
            ):
                raise construction_error(
                    "resolved atemporal relationship representations",
                    "versioned source or intermediate path",
                    repair="Use a non-versioned path or an already selected Population endpoint; historical enrichment requires its own temporal contract.",
                )
        current = destination
    return found[0]


def governed_path(registry: Registry, source: str, target: str) -> tuple[str, ...]:
    """Resolve one typed relationship path, retaining governed to-many edges."""
    if source == target:
        return ()
    edges: dict[str, list[tuple[str, str]]] = {}
    for relation in registry.relationships.values():
        left = normalize_target_entity(registry, relation.from_entity)
        right = normalize_target_entity(registry, relation.to_entity)
        left_types, right_types = dict(left.columns), dict(right.columns)
        if any(
            left_types.get(key.from_key) != right_types.get(key.to_key)
            or key.from_key not in left_types
            for key in relation.keys
        ):
            continue
        if not (
            tuple(key.from_key for key in relation.keys) == left.primary_key
            or tuple(key.to_key for key in relation.keys) == right.primary_key
        ):
            continue
        edges.setdefault(relation.from_entity, []).append(
            (relation.to_entity, relation.semantic_id)
        )
        edges.setdefault(relation.to_entity, []).append(
            (relation.from_entity, relation.semantic_id)
        )
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
            "one typed governed coordinate path", f"{source} -> {target}: missing or ambiguous path"
        )
    return found[0]


def path_is_functional(registry: Registry, source: str, path: tuple[str, ...]) -> bool:
    current = source
    for name in path:
        relation = registry.relationships[name]
        forward = current == relation.from_entity
        current = relation.to_entity if forward else relation.from_entity
        keys = tuple(key.to_key if forward else key.from_key for key in relation.keys)
        if keys != registry.entities[current].primary_key:
            return False
    return True


def _bind_coordinate_path(
    owner: ObservationOwner, definition: MetricDefinition, axis: TargetDimensionContract
) -> CoordinatePathBinding:
    registry = owner.semantic_registry
    spine = governed_path(registry, definition.entity.ref.path, axis.entity_ref.path)
    branches: dict[str, tuple[str, ...]] = {}
    overlap = False
    for metric in definition.metrics:
        for component in metric.components:
            root = component.computation_root.path
            branch = governed_path(registry, root, axis.entity_ref.path)
            branches[root] = branch
            if not path_is_functional(registry, root, branch):
                # Fanout authority belongs to the selected Metric's contribution graph.
                if component.fanout_policy != "aggregate_then_join":
                    raise construction_error(
                        "a contribution-safe coordinate path for every component",
                        f"{metric.key}:{component.role}: fanout to {axis.ref.path}",
                        repair="Use a coordinate on the component's contribution root, or author the exact aggregate_then_join Metric contract.",
                    )
                overlap = True
    functional = path_is_functional(registry, definition.entity.ref.path, spine)
    temporal_roots = {
        component.computation_root.path
        for metric in definition.metrics
        for component in metric.components
        if component.status_time_dimension is not None
    }
    if (
        axis.entity_ref.path in temporal_roots
        and registry.entities[axis.entity_ref.path].versioning is not None
    ):
        functional = False
    for entity_id in path_entities(
        registry, definition.entity.ref.path, (spine, *branches.values())
    ):
        if (
            registry.entities[entity_id].versioning is not None
            and entity_id != definition.entity.ref.path
            and entity_id not in temporal_roots
        ):
            raise construction_error(
                "a resolved temporal contract for every versioned coordinate path",
                f"versioned intermediate or coordinate Entity {entity_id}",
            )
    partition: Literal["functional", "disjoint", "overlapping"] = (
        "overlapping" if overlap else "functional" if functional else "disjoint"
    )
    return CoordinatePathBinding(axis.ref.path, spine, tuple(branches.items()), partition)


def _validate_spine(
    owner: ObservationOwner, definition: MetricDefinition, paths: tuple[CoordinatePathBinding, ...]
) -> None:
    fanouts = tuple(
        item.spine_path
        for item in paths
        if not path_is_functional(
            owner.semantic_registry, definition.entity.ref.path, item.spine_path
        )
    )
    for left in fanouts:
        for right in fanouts:
            if left[: len(right)] != right and right[: len(left)] != left:
                raise construction_error(
                    "one governed coordinate tuple spine",
                    "independent fanout coordinate branches",
                    repair="Use coordinates on one governed contribution path; independent one-to-many branches do not define a Cartesian coordinate domain.",
                )


_FIXED_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800}
_GRAIN_ORDER = {
    "second": 0,
    "minute": 1,
    "hour": 2,
    "day": 3,
    "week": 4,
    "month": 5,
    "quarter": 6,
    "year": 7,
}


def _snapshot_for_grain(owner: ObservationOwner, grain: Grain) -> PeriodCalendarSnapshotV1 | None:
    if grain.kind == "builtin":
        return None
    calendar = grain.calendar
    if calendar is None or grain.level is None:
        raise construction_error("a complete semantic Grain", "missing calendar or level")
    authored = owner.semantic_registry.period_calendars.get(calendar.path)
    matches = tuple(
        item for item in owner.period_calendar_snapshots if item.calendar_ref == calendar
    )
    if authored is None or len(matches) != 1:
        raise construction_error(
            "one already loaded certified calendar snapshot",
            "missing calendar authority",
            repair="Load the current certified calendar snapshot into the source Session before declaring its grain.",
        )
    snapshot = matches[0]
    if (
        grain.level not in snapshot.levels
        or snapshot.boundary_timezone != authored.boundary_timezone
        or tuple(value.isoformat() for value in snapshot.coverage) != authored.coverage
    ):
        raise construction_error(
            "current exact calendar coverage, timezone and level",
            "stale or incompatible calendar snapshot",
        )
    return snapshot


def _validate_grain(
    owner: ObservationOwner, axis: TargetDimensionContract, grain: Grain
) -> PeriodCalendarSnapshotV1 | None:
    if not isinstance(grain, Grain):
        raise construction_error("a helper-produced TemporalGrain", type(grain).__name__)
    snapshot = _snapshot_for_grain(owner, grain)
    if axis.granularity not in _GRAIN_ORDER:
        raise construction_error(
            "declared physical time granularity", f"{axis.ref.path}: missing granularity"
        )
    if grain.kind == "builtin":
        if (
            grain.unit not in _GRAIN_ORDER
            or _GRAIN_ORDER[grain.unit] < _GRAIN_ORDER[axis.granularity]
        ):
            raise construction_error(
                "grain no finer than the source time precision", grain.to_token()
            )
        requested_seconds = _FIXED_SECONDS.get(grain.unit)
        parse = owner.semantic_registry.dimensions[axis.ref.path].parse
        sample = None if parse is None or isinstance(parse, DateParse) else parse.sample_interval
        if (
            sample is not None
            and requested_seconds is not None
            and grain.count is not None
            and requested_seconds * grain.count < _FIXED_SECONDS[sample.unit] * sample.count
        ):
            raise construction_error(
                "grain no finer than the declared sample interval", grain.to_token()
            )
    if (
        snapshot is not None
        and axis.timezone is not None
        and axis.timezone != snapshot.boundary_timezone
    ):
        raise construction_error(
            "one governed temporal boundary timezone", "calendar and time-axis timezone differ"
        )
    return snapshot


def _anchor_key(anchor: CumulativeAnchorV1) -> tuple[str, ...]:
    if anchor == "all_history":
        return ("all_history",)
    if anchor[0] == "trailing":
        return ("trailing", str(anchor[1] * _FIXED_SECONDS[anchor[2]]))
    reset = anchor[1]
    return ("grain_to_date", reset.to_token() if isinstance(reset, Grain) else reset)


def _calendar_bound(value: date | datetime, timezone: str) -> datetime:
    if isinstance(value, datetime):
        return (
            value.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
            if value.tzinfo is not None
            else value
        )
    return datetime.combine(value, time.min)


def bind_aggregation(owner: ObservationOwner, definition: MetricDefinition) -> MetricDefinition:
    """Bind intrinsic graph facts to this exact source coordinate transition."""
    contracts = []
    selected_snapshot = definition.temporal_snapshot
    for metric in definition.metrics:
        for component in metric.components:
            node = component_node(metric.graph, component.node_id)
            if isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1)):
                for condition in node.filter:
                    dimension = normalize_target_dimension(
                        owner.semantic_registry, condition.dimension_ref.path
                    )
                    path = governed_path(
                        owner.semantic_registry,
                        component.computation_root.path,
                        dimension.entity_ref.path,
                    )
                    if (
                        not path_is_functional(
                            owner.semantic_registry, component.computation_root.path, path
                        )
                        and component.fanout_policy != "aggregate_then_join"
                    ):
                        raise construction_error(
                            "a governed semijoin contribution filter",
                            f"{metric.key}:{component.role}: unsafe fanout filter",
                            repair="Author this component's exact aggregate_then_join contract before using its one-to-many filter.",
                        )
        axes = tuple(
            dict.fromkeys(
                (
                    *[
                        item.status_time_dimension.path
                        for item in metric.components
                        if item.status_time_dimension is not None
                    ],
                    *[item.over_ref.path for item in metric.cumulative],
                )
            )
        )
        if len(axes) > 1:
            raise construction_error(
                "one common governed status/cumulative time axis",
                f"{metric.key}: incompatible component time axes",
            )
        if metric.cumulative:
            keys = {_anchor_key(item.anchor) for item in metric.cumulative}
            covered = tuple(f"{item.role}.base" for item in metric.cumulative)
            if len(keys) != 1 or any(
                not component.role.startswith(covered) for component in metric.components
            ):
                raise construction_error(
                    "compatible cumulative anchors on every component",
                    f"{metric.key}: mixed cumulative component graph",
                )
            if definition.time_axis is None and definition.time_scope is None:
                raise construction_error(
                    "a finite cumulative evaluation endpoint without a time coordinate",
                    f"{metric.key}: missing observation time_scope",
                    repair="Supply an explicit finite observation time_scope; membership scope does not provide an evaluation endpoint.",
                )
            for cumulative in metric.cumulative:
                anchor = cumulative.anchor
                if (
                    isinstance(anchor, tuple)
                    and anchor[0] == "grain_to_date"
                    and isinstance(anchor[1], Grain)
                    and anchor[1].kind == "semantic"
                ):
                    snapshot = _snapshot_for_grain(owner, anchor[1])
                    if selected_snapshot is not None and selected_snapshot != snapshot:
                        raise construction_error(
                            "one compatible certified calendar authority",
                            f"{metric.key}: incompatible reset calendar",
                        )
                    selected_snapshot = snapshot
        if definition.time_axis is not None:
            if axes and definition.time_axis.ref.path != axes[0]:
                raise construction_error(
                    "the exact governed status/cumulative time axis",
                    f"{metric.key}: {definition.time_axis.ref.path}",
                )
            grain = definition.grain
            if grain is None:
                raise construction_error("a time coordinate with its exact grain", "missing grain")
            for item in metric.cumulative:
                anchor = item.anchor
                if anchor == "all_history":
                    continue
                if anchor[0] == "trailing":
                    span = anchor[1] * _FIXED_SECONDS[anchor[2]]
                    width = (
                        _FIXED_SECONDS.get(grain.unit or "") if grain.kind == "builtin" else None
                    )
                    if width is None or grain.count is None or span % (width * grain.count):
                        raise construction_error(
                            "a trailing span divisible by the fixed query grain",
                            f"{metric.key}:{item.role}: incompatible trailing grain",
                        )
                else:
                    reset = anchor[1]
                    if isinstance(reset, Grain) and reset.kind == "semantic":
                        snapshot = selected_snapshot
                        builtin_width = (
                            (_FIXED_SECONDS.get(grain.unit or "", 0) * (grain.count or 0))
                            if grain.kind == "builtin"
                            else 0
                        )
                        builtin_contained = bool(builtin_width and 86400 % builtin_width == 0)
                        if (
                            snapshot is None
                            or reset.calendar != snapshot.calendar_ref
                            or reset.level is None
                            or (grain.kind == "builtin" and not builtin_contained)
                            or (
                                grain.kind == "semantic"
                                and (
                                    grain.calendar != snapshot.calendar_ref
                                    or grain.level is None
                                    or (
                                        not TemporalResolver(snapshot).rolls_up_to(
                                            grain.level, reset.level
                                        )
                                        and grain.level != reset.level
                                    )
                                )
                            )
                        ):
                            raise construction_error(
                                "display buckets contained in one certified reset period",
                                f"{metric.key}:{item.role}",
                            )
                    else:
                        token = reset.to_token() if isinstance(reset, Grain) else reset
                        if (
                            grain.kind != "builtin"
                            or grain.unit is None
                            or grain.count != 1
                            or _GRAIN_ORDER.get(grain.unit, 99) > _GRAIN_ORDER[token]
                            or (grain.unit == "week" and token != "week")
                        ):
                            raise construction_error(
                                "display buckets contained in one reset period",
                                f"{metric.key}:{item.role}: {grain.to_token()} under {token}",
                            )
        partitions = (
            ("entity_identity", "disjoint"),
            *((path.ref, path.partition) for path in definition.coordinate_paths),
        )
        contracts.append(
            MetricCoordinateAggregationV1(
                metric.key,
                tuple(component.role for component in metric.components),
                partitions,
                axes,
                metric.source_requirements,
                "source_required"
                if metric.requires_source_recompute
                or any(path.partition == "overlapping" for path in definition.coordinate_paths)
                else "exact_components",
            )
        )
    if selected_snapshot is not None and definition.time_scope is not None:
        scope = definition.time_scope
        timezone = selected_snapshot.boundary_timezone
        coverage_start, coverage_end = (
            _calendar_bound(bound, timezone) for bound in selected_snapshot.coverage
        )
        end = _calendar_bound(scope.end, timezone)
        if definition.time_axis is not None:
            start = _calendar_bound(scope.start, timezone)
            if start < coverage_start or end > coverage_end:
                raise construction_error(
                    "the complete displayed scope within certified calendar coverage",
                    "display scope outside calendar coverage",
                    repair="Choose a displayed scope within the bound snapshot coverage, or load a certified snapshot covering that scope.",
                )
        elif not coverage_start < end <= coverage_end:
            raise construction_error(
                "an exclusive evaluation endpoint inside a certified reset period",
                "evaluation endpoint outside calendar coverage",
                repair="Choose an observation end within the bound reset calendar coverage, or load a certified snapshot covering that endpoint.",
            )
    return replace(
        definition, aggregation_contracts=tuple(contracts), temporal_snapshot=selected_snapshot
    )


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
            functional_path(
                owner.semantic_registry, root, axis.entity_ref.path, allow_versioned_source=True
            )
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
                functional_path(
                    owner.semantic_registry, root, dimension.entity, allow_versioned_source=True
                )
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
    owner = source_owner_of(dataset)
    selected = tuple(normalize_dimension_input(owner, item, time=False) for item in dimensions)
    all_dimensions = definition.dimensions + selected
    if len({item.ref.path for item in all_dimensions}) != len(all_dimensions):
        raise construction_error("distinct ordered Dimensions", "duplicate Dimension")
    bindings = tuple(_bind_coordinate_path(owner, definition, dimension) for dimension in selected)
    all_paths = (*definition.coordinate_paths, *bindings)
    _validate_spine(owner, definition, all_paths)
    paths = tuple(binding.spine_path for binding in bindings)
    added_dependencies = tuple(
        (
            dimension.ref.path,
            path_dependency_fingerprint(
                owner,
                definition.entity.ref.path,
                (binding.spine_path, *(path for _, path in binding.component_paths)),
            ),
        )
        for dimension, binding in zip(selected, bindings, strict=True)
    )
    updated = replace(
        definition,
        dimensions=all_dimensions,
        coordinate_dependencies=(*definition.coordinate_dependencies, *added_dependencies),
        coordinate_paths=all_paths,
    )
    updated = bind_aggregation(owner, updated)
    row, row_set = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    captures = additional_captures(
        dataset,
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(
                owner.semantic_registry,
                definition.entity.ref.path,
                (*paths, *(path for binding in bindings for _, path in binding.component_paths)),
            )
        ),
    )
    payload = MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=captures)
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.with_dimensions",
        contract_versions=producer_contract("metric.with_dimensions").versions,
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
    owner = source_owner_of(dataset)
    axis = normalize_dimension_input(owner, time_dimension, time=True)
    snapshot = _validate_grain(owner, axis, grain)
    binding = _bind_coordinate_path(owner, definition, axis)
    paths = (*definition.coordinate_paths, binding)
    _validate_spine(owner, definition, paths)
    if definition.reference_axis is not None and definition.reference_axis.ref != axis.ref:
        raise construction_error(
            "the exact observation reference axis", "unproven temporal alignment"
        )
    updated = replace(
        definition,
        time_axis=axis,
        grain=grain,
        temporal_snapshot=snapshot,
        coordinate_paths=paths,
        coordinate_dependencies=(
            *definition.coordinate_dependencies,
            (
                axis.ref.path,
                path_dependency_fingerprint(
                    owner,
                    definition.entity.ref.path,
                    (binding.spine_path, *(path for _, path in binding.component_paths)),
                ),
            ),
        ),
    )
    updated = bind_aggregation(owner, updated)
    row, row_set = metric_contracts(updated, dataset._registration.ids, owner.semantic_registry)
    captures = additional_captures(
        dataset,
        tuple(
            normalize_target_entity(owner.semantic_registry, name)
            for name in path_entities(
                owner.semantic_registry,
                definition.entity.ref.path,
                (binding.spine_path, *(path for _, path in binding.component_paths)),
            )
        ),
    )
    payload = MetricPayload(_token=_CORE_TOKEN, definition=updated, captures=captures)
    return construct_operator(
        owner=owner,
        registry=dataset._registry,
        operator_id="metric.with_time_axis",
        contract_versions=producer_contract("metric.with_time_axis").versions,
        inputs=(dataset,),
        row_contract=row,
        row_set_contract=row_set,
        payload=payload,
    )
