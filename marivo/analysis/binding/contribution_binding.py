"""Resolve the exact fields, relationship reads and calendars of a contribution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypeAlias

from marivo._definition.correspondence import ComparisonAxis, coordinate_matches
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    EntityCount,
    FieldCalendarSample,
    MeasureAssertion,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import DefinitionNode, children
from marivo._definition.observation_time import ContributionClockBinding, ObservationPeriod
from marivo._definition.observations import (
    CalendarGrouping,
    SampleAxis,
    SampleIntervalRanges,
    SampleLayer,
)
from marivo._definition.observations import ContributionBinding as ContributionBinding
from marivo._definition.observations import ObservationBinding as ObservationBinding
from marivo._definition.relations import (
    MembershipBinding,
    condition_populations,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    EntityIdentityRead,
    ExactRef,
    FieldRead,
    IntervalCoordinate,
    coordinate_read_keys,
    field_axis,
    field_read_key,
    fixed_attribute_time,
)
from marivo._definition.windows import ObservationWindow
from marivo.analysis.binding.calendar_samples import validate_sample_parent
from marivo.analysis.binding.coordinates import (
    bind_identity,
    bind_read,
    native_read,
)
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.binding.relationship_binding import RelationshipResolver
from marivo.analysis.model.errors import reject
from marivo.semantic.spi import CompiledSemanticState
from marivo.semantic.spi import DefinitionResolver as _Resolver

ObservationCoordinate: TypeAlias = (
    FieldRead | EntityIdentityRead | CalendarCoordinate | IntervalCoordinate
)
_Contribution: TypeAlias = SourceAggregate | SourceWeightedMean | EntityCount


@dataclass(frozen=True, slots=True)
class ContributionGeometry:
    by: tuple[ObservationCoordinate, ...]
    axes: tuple[ComparisonAxis, ...]
    memberships: tuple[MembershipBinding, ...]
    period: ObservationPeriod | None
    defer_windows: bool


def row_fields(node: DefinitionNode) -> tuple[RowField, ...]:
    if isinstance(node, RowField):
        return (node,)
    return tuple(field for edge in children(node) for field in row_fields(edge.child))


def _predicate_reads(
    node: PredicateExpression, state: CompiledSemanticState, resolver: _Resolver
) -> tuple[FieldRead, ...]:
    return tuple(
        field.read or native_read(field.ref, state.registry, resolver)
        for leaf in node.inputs
        if isinstance(leaf, RowExpression)
        for field in row_fields(leaf)
    )


def _resolve_root(
    node: _Contribution,
    fields: tuple[RowField, ...],
    state: CompiledSemanticState,
    resolver: _Resolver,
) -> ExactRef:
    roots = (
        {node.entity.ref}
        if isinstance(node, EntityCount)
        else {node.contribution_root.ref}
        if node.contribution_root is not None
        else {field.root.ref for field in fields}
    )
    if len(roots) != 1:
        reject(
            "one contribution root per source reduction",
            repr(roots),
            "Reduce each fact root separately before composing Metrics.",
            "session.observe",
        )
    root_ref = next(iter(roots))
    if root_ref.path not in state.registry.entities:
        reject(
            "a declared contribution Entity",
            root_ref.path,
            "Resolve the source Metric in this Session's Catalog.",
            "session.observe",
        )
    return resolver.entity_ref(root_ref.path)


def _contribution_period(geometry: ContributionGeometry) -> ObservationPeriod | None:
    contribution_period = geometry.period
    coordinate_calendar_axes = tuple(
        axis for axis in geometry.axes if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
    )
    if contribution_period is None and coordinate_calendar_axes and not geometry.defer_windows:
        cells = tuple(
            cell
            for axis in coordinate_calendar_axes
            for cell in (
                axis.cells
                if isinstance(axis, CalendarCoordinate)
                else ((axis.start, axis.end, axis.start, axis.end),)
            )
        )
        if cells:
            contribution_period = ObservationPeriod(
                min(cell[2] for cell in cells),
                max(cell[3] for cell in cells),
                coordinate_calendar_axes[0].timezone,
            )
    return contribution_period


@dataclass(frozen=True, slots=True)
class _ContributionScope:
    state: CompiledSemanticState
    resolver: _Resolver
    paths: RelationshipResolver
    root: ExactRef

    def value_reads(
        self,
        node: _Contribution,
        fields: tuple[RowField, ...],
        predicates: tuple[PredicateExpression, ...],
    ) -> list[FieldRead]:
        state, resolver = self.state, self.resolver
        time_read = (
            native_read(node.time.field, state.registry, resolver)
            if node.time is not None
            else None
        )
        reads = [field.read or native_read(field.ref, state.registry, resolver) for field in fields]
        if node.time is not None and time_read is not None:
            reads.append(
                replace(
                    time_read,
                    path=node.time.path,
                    at=node.time.at,
                    source_at=node.time.source_at,
                    step_ats=node.time.step_ats,
                )
            )
        for predicate in predicates:
            reads.extend(_predicate_reads(predicate, state, resolver))
        return reads

    def bind_values(
        self, node: _Contribution, reads: list[FieldRead], field_count: int
    ) -> tuple[tuple[FieldRead, ...], ContributionClockBinding | None]:
        state, resolver, paths, root = self.state, self.resolver, self.paths, self.root
        bound = tuple(bind_read(read, root, state.registry, resolver, paths) for read in reads)
        time = None
        if node.time is not None:
            bound_time = bound[field_count]
            time = replace(
                node.time,
                field=bound_time.field,
                path=bound_time.path,
                at=fixed_attribute_time(bound_time.at),
                source_at=bound_time.source_at,
                step_ats=bound_time.step_ats,
            )
        return bound, time

    def bind_populations(
        self,
        node: _Contribution,
        predicates: tuple[PredicateExpression, ...],
        memberships: tuple[MembershipBinding, ...],
    ) -> tuple[tuple[AxisBinding, FieldRead | EntityIdentityRead], ...]:
        state, resolver, paths, root = self.state, self.resolver, self.paths, self.root
        population_reads: dict[AxisBinding, FieldRead | EntityIdentityRead] = {}
        for predicate in predicates:
            for domain in condition_populations(predicate):
                for axis in domain.axes:
                    if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                        if node.time is None:
                            reject(
                                "a contribution clock for time coordinates in in_population",
                                f"{root.ref.key} has no contribution clock for {type(axis).__name__}",
                                "Use a Metric with the intended TimeField or project the time axis out of the predicate Population.",
                                "Metric.where",
                                canonical_id="in_population",
                            )
                        continue
                    read = axis.as_read()
                    parent_read = next(
                        (
                            allocated
                            for membership in memberships
                            for original, allocated in zip(
                                membership.fields,
                                membership_reads(membership, root),
                                strict=True,
                            )
                            if coordinate_matches(field_axis(original), axis)
                        ),
                        None,
                    )
                    if read.path_facts and any(
                        step.cardinality in ("one_to_many", "many_to_many")
                        for step in read.path_facts
                    ):
                        if read.path_root is None or read.path_root.ref != root.ref:
                            reject(
                                "an explicit relationship role starting at the contribution Entity",
                                repr(read.path_root),
                                "Use mv.identity(mv.path(...)) starting at this Metric's contribution root.",
                                "Metric.where",
                            )
                        population_reads[axis] = read
                        continue
                    population_reads[axis] = (
                        parent_read
                        if parent_read is not None
                        else (
                            bind_read(read, root, state.registry, resolver, paths)
                            if isinstance(read, FieldRead)
                            else bind_identity(read, root, state.registry, resolver, paths)
                        )
                    )
        return tuple(population_reads.items())

    def bind_coordinates(
        self, geometry: ContributionGeometry, sample_layers: tuple[SampleLayer, ...]
    ) -> tuple[tuple[FieldRead | EntityIdentityRead, ...], tuple[MembershipBinding, ...]]:
        state, resolver, paths, root = self.state, self.resolver, self.paths, self.root
        by, memberships = geometry.by, geometry.memberships
        all_memberships = tuple(
            dict.fromkeys(
                (
                    *memberships,
                    *(binding for layer in sample_layers for binding in layer.memberships),
                )
            )
        )
        coordinate_list: list[FieldRead | EntityIdentityRead] = []
        coordinate_layers = (
            (by, memberships),
            *(
                (
                    tuple(axis.as_read() for axis in layer.axes if isinstance(axis, AxisBinding)),
                    layer.memberships,
                )
                for layer in sample_layers
            ),
        )
        for layer_reads, layer_memberships in coordinate_layers:
            allocated = tuple(
                (original, read)
                for item in layer_memberships
                for original, read in zip(item.fields, membership_reads(item, root), strict=True)
            )
            for coordinate_read in layer_reads:
                if isinstance(coordinate_read, (CalendarCoordinate, IntervalCoordinate)):
                    continue
                if isinstance(coordinate_read, EntityIdentityRead):
                    coordinate_list.append(
                        bind_identity(coordinate_read, root, state.registry, resolver, paths)
                    )
                else:
                    member = next(
                        (
                            item
                            for original, item in allocated
                            if coordinate_matches(field_axis(original), field_axis(coordinate_read))
                        ),
                        None,
                    )
                    coordinate_list.append(
                        member
                        if member is not None
                        else bind_read(coordinate_read, root, state.registry, resolver, paths)
                    )
        coordinates = tuple(coordinate_list)

        return coordinates, all_memberships

    def bind_row(self, value: RowNode) -> RowNode:
        state, resolver, paths, root = self.state, self.resolver, self.paths, self.root
        if isinstance(value, RowField):
            return replace(
                value,
                read=bind_read(
                    value.read or native_read(value.ref, state.registry, resolver),
                    root,
                    state.registry,
                    resolver,
                    paths,
                ),
            )
        if isinstance(value, MeasureAssertion):
            return replace(value, operand=self.bind_row(value.operand))
        return replace(value, inputs=tuple(self.bind_leaf(item) for item in value.inputs))

    def bind_leaf(self, value: RowField | MeasureAssertion) -> RowField | MeasureAssertion:
        result = self.bind_row(value)
        assert isinstance(result, (RowField, MeasureAssertion))
        return result

    def bind_predicate(self, predicate: PredicateExpression) -> PredicateExpression:
        return replace(
            predicate,
            inputs=tuple(
                replace(item, inputs=tuple(self.bind_leaf(leaf) for leaf in item.inputs))
                if isinstance(item, RowExpression)
                else item
                for item in predicate.inputs
            ),
        )


@dataclass(frozen=True, slots=True)
class _CoordinateCalendars:
    groupings: tuple[tuple[str, CalendarGrouping], ...]
    coordinate_slots: tuple[str, ...]
    sample_reads: tuple[tuple[str, FieldRead], ...]
    sample_time_slots: tuple[str, ...]
    sample_interval_ranges: tuple[tuple[str, SampleIntervalRanges], ...]


def _bind_calendars(
    scope: _ContributionScope,
    axes: tuple[ComparisonAxis, ...],
    sample_layers: tuple[SampleLayer, ...],
    coordinates: tuple[FieldRead | EntityIdentityRead, ...],
    contribution_time_read: FieldRead | None,
) -> _CoordinateCalendars:
    state, resolver, root = scope.state, scope.resolver, scope.root
    coordinate_calendars: list[tuple[str, CalendarGrouping]] = []
    sample_calendar_reads: list[tuple[str, FieldRead]] = []
    sample_time_slots: list[str] = []
    sample_interval_ranges: list[tuple[str, SampleIntervalRanges]] = []
    parent_calendars: list[tuple[FieldRead, CalendarCoordinate | BuiltinSampleCoordinate]] = []
    physical_reads = iter(coordinates)
    physical_slot = 0
    coordinate_slots: list[str] = []
    bound_axes = (
        *((axis, None) for axis in axes),
        *((axis, layer.interval_ordinals) for layer in sample_layers for axis in layer.axes),
    )
    for axis_index, (axis, ordinals) in enumerate(bound_axes):
        if isinstance(axis, BuiltinSampleCoordinate):
            slot = f"key.{physical_slot}"
            if isinstance(axis.sample, FieldCalendarSample):
                sample_field = axis.sample.field
                if sample_field.entity.ref != root.ref:
                    reject(
                        "one native calendar sample field on every contribution root",
                        field_read_key(sample_field),
                        "Fold each root's Metric by its own native field before composing them.",
                        "Metric.per",
                    )
                sample_read = native_read(sample_field.field, state.registry, resolver)
            else:
                if contribution_time_read is None:
                    physical_slot += 1
                    continue
                sample_read = contribution_time_read
            for parent_read, parent in parent_calendars:
                if sample_read == parent_read:
                    validate_sample_parent(axis, parent)
            parent_calendars.append((sample_read, axis))
            coordinate_calendars.append((slot, axis))
            sample_calendar_reads.append((slot, sample_read))
            physical_slot += 1
        elif isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
            slot = f"key.{physical_slot}"
            if contribution_time_read is not None:
                coordinate_calendars.append((slot, axis))
                if axis_index >= len(axes):
                    sample_time_slots.append(slot)
                    if isinstance(axis, IntervalCoordinate):
                        selected = range(len(axis.bounds)) if ordinals is None else ordinals
                        sample_interval_ranges.append(
                            (
                                slot,
                                tuple((index, *axis.bounds[index]) for index in selected),
                            )
                        )
            if isinstance(axis, CalendarCoordinate) and contribution_time_read is not None:
                parent_calendars.append((contribution_time_read, axis))
            physical_slot += 1
        else:
            width = len(coordinate_read_keys(next(physical_reads)))
            coordinate_slots.extend(f"key.{i}" for i in range(physical_slot, physical_slot + width))
            physical_slot += width
    return _CoordinateCalendars(
        tuple(coordinate_calendars),
        tuple(coordinate_slots),
        tuple(sample_calendar_reads),
        tuple(sample_time_slots),
        tuple(sample_interval_ranges),
    )


def _validate_status_time(
    node: _Contribution,
    fields: tuple[RowField, ...],
    coordinates: tuple[FieldRead | EntityIdentityRead, ...],
    state: CompiledSemanticState,
) -> None:
    if isinstance(node, SourceAggregate) and node.reducer == "sum":
        for field in fields:
            measure = state.registry.measures.get(field.ref.ref.path)
            status = measure.status_time_dimension if measure is not None else None
            if status is not None and not any(
                isinstance(read, FieldRead)
                and read.field.ref.path == status
                and not read.path
                and read.at is None
                for read in coordinates
            ):
                reject(
                    "the exact native status-time coordinate for this sum",
                    f"{field.ref.ref.key} without {status}",
                    "Retain the raw status TimeField with per(status_time).last() before folding calendar buckets.",
                    "Metric.per",
                )


def bind_contribution(
    node: _Contribution,
    occurrence: tuple[int, ...],
    metric: str,
    geometry: ContributionGeometry,
    state: CompiledSemanticState,
    resolver: _Resolver,
    paths: RelationshipResolver,
    filters: tuple[PredicateExpression, ...],
    condition: PredicateExpression | None,
    sample_axes: tuple[SampleAxis, ...],
    sample_layers: tuple[SampleLayer, ...],
    *,
    window_intent: ObservationWindow | None = None,
) -> ContributionBinding:
    fields = row_fields(node)
    root = _resolve_root(node, fields, state, resolver)
    contribution_period = _contribution_period(geometry)
    scope = _ContributionScope(state, resolver, paths, root)
    predicates = (*filters, *((condition,) if condition is not None else ()))
    reads = scope.value_reads(node, fields, predicates)
    population_reads = scope.bind_populations(node, predicates, geometry.memberships)
    bound, time = scope.bind_values(node, reads, len(fields))
    coordinates, all_memberships = scope.bind_coordinates(geometry, sample_layers)
    output_axis_count = sum(
        not isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) for axis in geometry.by
    )
    calendars = _bind_calendars(
        scope,
        geometry.axes,
        sample_layers,
        coordinates,
        bound[len(fields)] if time is not None else None,
    )
    _validate_status_time(node, fields, coordinates, state)
    return ContributionBinding(
        occurrence,
        node,
        root,
        tuple(scope.bind_predicate(p) for p in filters),
        scope.bind_predicate(condition) if condition is not None else None,
        (
            *bound,
            *(read for _, read in calendars.sample_reads),
        ),
        coordinates,
        time,
        contribution_period if time is not None else None,
        sample_axes=sample_axes,
        metric=metric,
        memberships=all_memberships,
        output_axis_count=output_axis_count,
        calendars=calendars.groupings,
        coordinate_slots=calendars.coordinate_slots,
        sample_calendar_reads=calendars.sample_reads,
        sample_time_slots=calendars.sample_time_slots,
        sample_interval_ranges=calendars.sample_interval_ranges,
        population_reads=population_reads,
        window_intent=window_intent,
    )
