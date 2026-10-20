"""Immutable occurrence-aware binding of an ordinary named Metric observation."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal, TypeAlias

from marivo._definition.correspondence import coordinate_matches
from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    MeasureAssertion,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import DefinitionNode, children, walk
from marivo._definition.observations import ContributionBinding as ContributionBinding
from marivo._definition.observations import ObservationBinding as ObservationBinding
from marivo._definition.observations import ObservationPeriod as ObservationPeriod
from marivo._definition.relations import (
    MembershipBinding,
    PopulationDependency,
    SampleDomain,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
    IntervalCoordinate,
)
from marivo._temporal import (
    PeriodCalendarSnapshotV1,
)
from marivo.analysis.binding.coordinates import (
    CoordinateBinding,
    bind_identity,
    bind_read,
    native_read,
)
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.binding.sampling import sampling_coverage
from marivo.analysis.core.model import reject
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.expressions import _Resolver

_SampleLayer: TypeAlias = tuple[
    tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...],
    tuple[MembershipBinding, ...],
]


def _row_fields(node: DefinitionNode) -> tuple[RowField, ...]:
    if isinstance(node, RowField):
        return (node,)
    return tuple(field for edge in children(node) for field in _row_fields(edge.child))


def _predicate_reads(
    node: PredicateExpression, state: CompiledSemanticState, resolver: _Resolver
) -> tuple[FieldRead, ...]:
    return tuple(
        field.read or native_read(field.ref, state.registry, resolver)
        for leaf in node.inputs
        if isinstance(leaf, RowExpression)
        for field in _row_fields(leaf)
    )


def bind_observation(
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    *,
    by: tuple[FieldRead | IdentityRead | CalendarCoordinate | IntervalCoordinate, ...],
    where: PredicateExpression | None,
    timezone: str,
    state: CompiledSemanticState | None,
    calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
    memberships: tuple[MembershipBinding, ...] = (),
    population: PopulationDependency | None = None,
) -> ObservationBinding:
    if not isinstance(by, tuple) or any(
        not isinstance(field, (FieldRead, IdentityRead, CalendarCoordinate, IntervalCoordinate))
        for field in by
    ):
        reject(
            "a tuple of resolved fields and time domains",
            repr(by),
            "Use by=(field, scope).",
            "Session.observe",
        )
    time_axes = tuple(
        axis for axis in by if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
    )
    if len(time_axes) > 1:
        reject(
            "at most one output time axis",
            repr(time_axes),
            "Choose one explicit time domain.",
            "Session.observe",
        )
    time_axis = time_axes[0] if time_axes else None
    calendar = time_axis if isinstance(time_axis, CalendarCoordinate) else None
    period = (
        ObservationPeriod(time_axis.start, time_axis.end, time_axis.timezone)
        if isinstance(time_axis, IntervalCoordinate)
        else ObservationPeriod(
            min(cell[2] for cell in time_axis.cells),
            max(cell[3] for cell in time_axis.cells),
            time_axis.timezone,
        )
        if isinstance(time_axis, CalendarCoordinate) and time_axis.cells
        else None
    )
    axes = tuple(
        field
        if isinstance(field, (CalendarCoordinate, IntervalCoordinate))
        else AxisBinding(
            field.entity,
            field.field if isinstance(field, FieldRead) else None,
            field.path,
            "field" if isinstance(field, FieldRead) else "identity",
            field.at,
        )
        for field in by
    )
    if len(set(axes)) != len(axes):
        reject("distinct output axes", repr(by), "Remove repeated axes.", "Session.observe")
    condition = where
    contributions: list[ContributionBinding] = []
    count_populations: dict[PopulationDependency, PopulationDependency] = {}
    resolver = _Resolver(state) if state is not None else None
    paths = RelationshipResolver.build(state.registry) if state is not None else None

    def visit(
        node: DefinitionNode,
        occurrence: tuple[int, ...],
        filters: tuple[PredicateExpression, ...],
        sample_axes: tuple[AxisBinding | CalendarCoordinate | IntervalCoordinate, ...] = (),
        sample_layers: tuple[_SampleLayer, ...] = (),
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*occurrence, 0), filters, sample_axes, sample_layers)
            return
        if isinstance(node, ContributionFilter):
            visit(
                node.operand,
                (*occurrence, 0),
                (*filters, node.predicate),
                sample_axes,
                sample_layers,
            )
            return
        if isinstance(node, (PerReduce, PerWeightedMean)) and (
            period is None
            and isinstance(node.samples, tuple)
            and any(
                axis.field is not None and axis.field.ref.kind.value == "time_dimension"
                for axis in node.samples
            )
        ):
            reject(
                "an explicit observation period for time samples",
                f"None at {occurrence}",
                "Pass during=session.time_scope(...).",
                "session.observe",
            )
        if isinstance(node, MemberCount):
            domain = node.population
            requested = tuple(dict.fromkeys((*axes, *sample_axes)))
            for axis in requested:
                if any(coordinate_matches(axis, candidate) for candidate in domain.axes):
                    continue
                if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)) and not any(
                    isinstance(existing, (CalendarCoordinate, IntervalCoordinate))
                    for existing in domain.axes
                ):
                    continue
                reject(
                    "count coordinates already present in the Population",
                    repr(axis),
                    "Classify the identity domain explicitly before counting by new attributes.",
                    "Population.count",
                )
            count_populations[domain] = domain
            return
        if isinstance(node, (PerReduce, PerWeightedMean)):
            sample_domain = node.samples
            inner_domain_axes = (
                sample_domain if isinstance(sample_domain, tuple) else sample_domain.axes
            )
            inner_axes = tuple(
                axis
                for axis in inner_domain_axes
                if isinstance(axis, (AxisBinding, CalendarCoordinate, IntervalCoordinate))
            )
            inner_memberships = (
                sample_domain.memberships if isinstance(sample_domain, SampleDomain) else ()
            )
            contribution_count = len(contributions)
            for edge in children(node):
                if edge.role in ("operand", "value", "weight"):
                    visit(
                        edge.child,
                        (*occurrence, edge.index),
                        filters,
                        (*sample_axes, *inner_axes),
                        (*sample_layers, (inner_axes, inner_memberships)),
                    )
            if inner_memberships and len(contributions) == contribution_count:
                reject(
                    "fact contributions for membership allocation",
                    "count-only internal samples",
                    "Remove ineffective memberships or include a fact Metric in the sample formula.",
                    "Metric.per.memberships",
                )
            return
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            if state is None or resolver is None or paths is None:
                reject(
                    "captured semantic declarations for source binding",
                    "no semantic state",
                    "Load the source definitions in this Session's Catalog.",
                    "session.observe",
                )
            fields = _row_fields(node)
            roots = {field.root.ref for field in fields}
            if isinstance(node, EntityCount):
                roots.add(node.entity.ref)
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
            root = resolver.entity_ref(root_ref.path)
            contribution_period = period
            coordinate_calendar_axes = tuple(
                axis
                for axis in (*axes, *sample_axes)
                if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
            )
            if contribution_period is None and coordinate_calendar_axes:
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
            time = None
            time_read = None
            if node.time is not None:
                time_read = native_read(node.time.field, state.registry, resolver)
                dimension = state.registry.dimensions[node.time.field.ref.path]
                if (
                    contribution_period is None
                    and dimension.parse is not None
                    and getattr(dimension.parse, "sample_interval", None) is not None
                ):
                    reject(
                        "an explicit period for sampled time",
                        f"None for {dimension.semantic_id} at {occurrence}",
                        "Pass during=session.time_scope(...).",
                        "session.observe",
                    )
            reads = [
                field.read or native_read(field.ref, state.registry, resolver) for field in fields
            ]
            if node.time is not None and time_read is not None:
                reads.append(
                    FieldRead(time_read.field, time_read.entity, node.time.path, node.time.at)
                )
            predicates = (*filters, *((condition,) if condition is not None else ()))
            for predicate in predicates:
                reads.extend(_predicate_reads(predicate, state, resolver))
            from marivo._definition.relations import condition_populations

            population_reads: dict[AxisBinding, FieldRead | IdentityRead] = {}
            for predicate in predicates:
                for domain in condition_populations(predicate):
                    for axis in domain.axes:
                        if not isinstance(axis, AxisBinding):
                            reject(
                                "entity or field coordinates for a source predicate",
                                "CalendarAxis",
                                "Select source identities before applying in_population.",
                                "Metric.where",
                            )
                        read = (
                            FieldRead(axis.field, axis.entity, axis.path, axis.at)
                            if axis.field is not None
                            else IdentityRead(axis.entity, axis.path, axis.at)
                        )
                        population_reads[axis] = (
                            bind_read(read, root, state.registry, resolver, paths)
                            if isinstance(read, FieldRead)
                            else bind_identity(read, root, state.registry, resolver, paths)
                        )
            bound = tuple(bind_read(read, root, state.registry, resolver, paths) for read in reads)
            if node.time is not None:
                bound_time = bound[len(fields)]
                time = TimeBinding(bound_time.field, bound_time.path, bound_time.at)
            all_memberships = tuple(
                dict.fromkeys(
                    (
                        *memberships,
                        *(binding for _, rules in sample_layers for binding in rules),
                    )
                )
            )
            coordinate_list: list[FieldRead | IdentityRead] = []
            coordinate_layers = (
                (by, memberships),
                *(
                    (
                        tuple(
                            FieldRead(axis.field, axis.entity, axis.path, axis.at)
                            if axis.field is not None
                            else IdentityRead(axis.entity, axis.path, axis.at)
                            for axis in layer_axes
                            if isinstance(axis, AxisBinding)
                        ),
                        rules,
                    )
                    for layer_axes, rules in sample_layers
                ),
            )
            for layer_reads, layer_memberships in coordinate_layers:
                allocated = tuple(
                    read for item in layer_memberships for read in membership_reads(item, root)
                )
                for coordinate_read in layer_reads:
                    if isinstance(coordinate_read, (CalendarCoordinate, IntervalCoordinate)):
                        continue
                    if isinstance(coordinate_read, IdentityRead):
                        coordinate_list.append(
                            bind_identity(coordinate_read, root, state.registry, resolver, paths)
                        )
                    else:
                        member = next(
                            (
                                item
                                for item in allocated
                                if item.field.ref == coordinate_read.field.ref
                                and item.at == coordinate_read.at
                            ),
                            None,
                        )
                        coordinate_list.append(
                            member
                            if member is not None
                            else bind_read(coordinate_read, root, state.registry, resolver, paths)
                        )
            coordinates = tuple(coordinate_list)
            coordinate_calendars: list[tuple[str, CalendarCoordinate | IntervalCoordinate]] = []
            physical_reads = iter(coordinates)
            physical_slot = 0
            from marivo._definition.types import coordinate_read_keys

            for axis in (*axes, *sample_axes):
                if isinstance(axis, (CalendarCoordinate, IntervalCoordinate)):
                    coordinate_calendars.append((f"key.{physical_slot}", axis))
                    physical_slot += 1
                else:
                    physical_slot += len(coordinate_read_keys(next(physical_reads)))
            if contribution_period is not None and node.time is None:
                reject(
                    "a declared business time role for this period",
                    root.ref.path,
                    "Declare a default time or construct the reduction with time=field.",
                    "session.observe",
                )

            def bind_row(value: RowNode) -> RowNode:
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
                    return replace(value, operand=bind_row(value.operand))
                return replace(value, inputs=tuple(bind_leaf(item) for item in value.inputs))

            def bind_leaf(value: RowField | MeasureAssertion) -> RowField | MeasureAssertion:
                result = bind_row(value)
                assert isinstance(result, (RowField, MeasureAssertion))
                return result

            def bind_predicate(predicate: PredicateExpression) -> PredicateExpression:
                return replace(
                    predicate,
                    inputs=tuple(
                        replace(item, inputs=tuple(bind_leaf(leaf) for leaf in item.inputs))
                        if isinstance(item, RowExpression)
                        else item
                        for item in predicate.inputs
                    ),
                )

            contributions.append(
                ContributionBinding(
                    occurrence,
                    node,
                    root,
                    tuple(bind_predicate(p) for p in filters),
                    bind_predicate(condition) if condition is not None else None,
                    bound,
                    coordinates,
                    time,
                    contribution_period,
                    sample_axes=sample_axes,
                    memberships=all_memberships,
                    output_axis_count=sum(
                        not isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                        for axis in by
                    ),
                    calendars=tuple(coordinate_calendars),
                    population_reads=tuple(population_reads.items()),
                )
            )
            return
        for edge in children(node):
            visit(edge.child, (*occurrence, edge.index), filters, sample_axes, sample_layers)

    for index, (_, node) in enumerate(metrics):
        visit(node, (index,), ())
    if memberships and not contributions:
        reject(
            "fact contributions for membership allocation",
            "count-only outputs",
            "Remove ineffective memberships or include a fact Metric.",
            "Session.observe.memberships",
        )
    if (
        not contributions
        and not count_populations
        and (any(isinstance(axis, AxisBinding) for axis in axes) or where is not None)
    ):
        reject(
            "a contribution source for field classification or conditions",
            "source-free Metric outputs",
            "Observe a source Metric before requesting field grouping or conditions.",
            "session.observe",
        )
    mode: Literal["calendar", "grouped", "singleton"] = (
        "calendar"
        if any(isinstance(axis, CalendarCoordinate) for axis in axes)
        else "grouped"
        if axes
        else "singleton"
    )
    from marivo.analysis.binding.windows import bind_windows

    bound_contributions = bind_windows(
        tuple(contributions), metrics, declared, period, calendar, calendar_snapshots
    )
    if state is not None:
        bound_contributions = tuple(
            replace(item, coverage=sampling_coverage(item, state, calendar=calendar))
            for item in bound_contributions
        )
    if (
        not contributions
        and count_populations
        and (
            where is not None
            or any(
                isinstance(node, ContributionFilter)
                for node in walk((node for _, node in metrics), resolve=declared.get).order
            )
        )
    ):
        reject(
            "fact contributions for where conditions",
            "count-only outputs",
            "Remove the ineffective fact condition or include a fact Metric.",
            "Session.observe",
        )
    return ObservationBinding(
        period,
        condition,
        CoordinateBinding(tuple(axes), mode, population, memberships),
        bound_contributions,
        tuple(count_populations.items()),
    )
