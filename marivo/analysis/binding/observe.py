"""Immutable occurrence-aware binding of an ordinary named Metric observation."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldPredicate,
    MeasureAssertion,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    RowExpression,
    RowField,
    RowNode,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import DefinitionNode, children
from marivo._definition.observations import ContributionBinding as ContributionBinding
from marivo._definition.observations import ObservationBinding as ObservationBinding
from marivo._definition.observations import ObservationPeriod as ObservationPeriod
from marivo._definition.relations import (
    MembershipBinding,
    PopulationCondition,
    PopulationDependency,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    ExactRef,
    FieldRead,
    IdentityRead,
)
from marivo._temporal import TimeScope, _SessionTimeScope, anchor_from_datetime
from marivo.analysis.binding.coordinates import (
    CoordinateBinding,
    bind_identity,
    bind_read,
    native_read,
)
from marivo.analysis.binding.membership import membership_reads
from marivo.analysis.binding.sampling import sampling_coverage
from marivo.analysis.core.model import reject
from marivo.analysis.core.time_grid import instant
from marivo.analysis.dsl.temporal import CalendarAxis, freeze_scope
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.expressions import _Resolver


def _row_fields(node: DefinitionNode) -> tuple[RowField, ...]:
    if isinstance(node, RowField):
        return (node,)
    return tuple(field for edge in children(node) for field in _row_fields(edge.child))


def _predicate_reads(
    node: FieldPredicate, state: CompiledSemanticState, resolver: _Resolver
) -> tuple[FieldRead, ...]:
    return tuple(
        field.read or native_read(field.ref, state.registry, resolver)
        for field in _row_fields(node)
    )


def bind_observation(
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    *,
    during: TimeScope | CalendarAxis | None,
    by: tuple[FieldRead | IdentityRead | CalendarCoordinate, ...],
    where: FieldPredicate | None,
    timezone: str,
    state: CompiledSemanticState | None,
    memberships: tuple[MembershipBinding, ...] = (),
    population: PopulationDependency | None = None,
) -> ObservationBinding:
    if during is not None and not isinstance(during, (TimeScope, CalendarAxis)):
        reject(
            "TimeScope, CalendarAxis or None",
            type(during).__name__,
            "Use session.time_scope(...), optionally followed by .by(mv.grain(...)).",
            "session.observe",
        )
    if not isinstance(by, tuple) or any(
        not isinstance(field, (FieldRead, IdentityRead, CalendarCoordinate)) for field in by
    ):
        reject(
            "a tuple of resolved scalar fields",
            type(by).__name__,
            "Pass by=(field,) using resolved semantic fields.",
            "session.observe",
        )
    calendar = during if isinstance(during, CalendarAxis) else None
    scope = during.scope if isinstance(during, CalendarAxis) else during
    period = None
    if scope is not None:
        scope = freeze_scope(scope, timezone)
        zone = (
            scope.boundary_timezone
            if isinstance(scope, _SessionTimeScope) or scope.kind != "absolute"
            else timezone
        )
        period = ObservationPeriod(
            anchor_from_datetime(instant(scope.start, zone)),
            anchor_from_datetime(instant(scope.end, zone)),
            zone,
        )
    axes: list[AxisBinding | CalendarCoordinate] = []
    if calendar is not None:
        grid = calendar.grid
        axes.append(
            CalendarCoordinate(
                grid.identity,
                grid.grain_token,
                grid.boundary_timezone,
                grid.snapshot_digest,
                tuple(
                    (
                        anchor_from_datetime(c.original_start),
                        anchor_from_datetime(c.original_end),
                        anchor_from_datetime(c.start),
                        anchor_from_datetime(c.end),
                    )
                    for c in grid.cells
                ),
            )
        )
    axes.extend(
        field
        if isinstance(field, CalendarCoordinate)
        else AxisBinding(
            field.entity,
            field.field if isinstance(field, FieldRead) else None,
            field.path,
            "field" if isinstance(field, FieldRead) else "identity",
            field.at,
        )
        for field in by
    )
    if len({repr(axis) for axis in axes}) != len(axes):
        reject(
            "distinct output axes", repr(by), "Remove the repeated field axis.", "session.observe"
        )
    condition = where
    contributions: list[ContributionBinding] = []
    resolver = _Resolver(state) if state is not None else None
    paths = RelationshipResolver.build(state.registry) if state is not None else None

    def visit(
        node: DefinitionNode,
        occurrence: tuple[int, ...],
        filters: tuple[FieldPredicate | PopulationCondition, ...],
        sample_axes: tuple[AxisBinding | CalendarCoordinate, ...] = (),
        sample_memberships: tuple[MembershipBinding, ...] = (),
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*occurrence, 0), filters, sample_axes, sample_memberships)
            return
        if isinstance(node, ContributionFilter):
            visit(
                node.operand,
                (*occurrence, 0),
                (*filters, node.predicate),
                sample_axes,
                sample_memberships,
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
            reject(
                "ordinary source reductions",
                "member_count",
                "Population observation is delivered in R3.",
                "session.observe",
            )
        if isinstance(node, (PerReduce, PerWeightedMean)):
            domain = node.samples
            inner_axes = domain if isinstance(domain, tuple) else domain.axes
            inner_memberships = () if isinstance(domain, tuple) else domain.memberships
            for edge in children(node):
                if edge.role in ("operand", "value", "weight"):
                    visit(
                        edge.child,
                        (*occurrence, edge.index),
                        filters,
                        (*sample_axes, *inner_axes),
                        (*sample_memberships, *inner_memberships),
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
                axis for axis in (*axes, *sample_axes) if isinstance(axis, CalendarCoordinate)
            )
            if contribution_period is None and coordinate_calendar_axes:
                cells = tuple(cell for axis in coordinate_calendar_axes for cell in axis.cells)
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
            row_filters = tuple(
                predicate for predicate in filters if isinstance(predicate, RowExpression)
            )
            population_filters = tuple(
                predicate for predicate in filters if not isinstance(predicate, RowExpression)
            )
            for predicate in (*row_filters, *((condition,) if condition is not None else ())):
                reads.extend(_predicate_reads(predicate, state, resolver))
            from marivo._definition.relations import condition_populations

            population_reads: dict[AxisBinding, FieldRead | IdentityRead] = {}
            for predicate in population_filters:
                for domain in condition_populations(predicate):
                    for axis in domain.axes:
                        if isinstance(axis, CalendarCoordinate):
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
            sample_reads = tuple(
                FieldRead(axis.field, axis.entity, axis.path, axis.at)
                if axis.field is not None
                else IdentityRead(axis.entity, axis.path, axis.at)
                for axis in sample_axes
                if isinstance(axis, AxisBinding)
            )
            all_memberships = tuple(dict.fromkeys((*memberships, *sample_memberships)))
            allocated = tuple(
                read for item in all_memberships for read in membership_reads(item, root)
            )
            coordinate_list: list[FieldRead | IdentityRead] = []
            for coordinate_read in (*by, *sample_reads):
                if isinstance(coordinate_read, CalendarCoordinate):
                    continue
                if isinstance(coordinate_read, IdentityRead):
                    coordinate_list.append(
                        bind_identity(coordinate_read, root, state.registry, resolver, paths)
                    )
                else:
                    member = next(
                        (item for item in allocated if item.field.ref == coordinate_read.field.ref),
                        None,
                    )
                    coordinate_list.append(
                        member
                        if member is not None
                        else bind_read(coordinate_read, root, state.registry, resolver, paths)
                    )
            coordinates = tuple(coordinate_list)
            coordinate_calendars: list[tuple[str, CalendarCoordinate]] = []
            physical_reads = iter(coordinates)
            physical_slot = 0
            from marivo._definition.types import coordinate_read_keys

            for axis in (*axes, *sample_axes):
                if isinstance(axis, CalendarCoordinate):
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

            def bind_predicate(predicate: FieldPredicate) -> FieldPredicate:
                return replace(
                    predicate, inputs=tuple(bind_leaf(item) for item in predicate.inputs)
                )

            contributions.append(
                ContributionBinding(
                    occurrence,
                    node,
                    root,
                    tuple(bind_predicate(p) for p in row_filters),
                    bind_predicate(condition) if condition is not None else None,
                    bound,
                    coordinates,
                    time,
                    contribution_period,
                    sample_axes=sample_axes,
                    memberships=all_memberships,
                    output_axis_count=sum(not isinstance(axis, CalendarCoordinate) for axis in by),
                    calendars=tuple(coordinate_calendars),
                    population_conditions=population_filters,
                    population_reads=tuple(population_reads.items()),
                )
            )
            return
        for edge in children(node):
            visit(edge.child, (*occurrence, edge.index), filters, sample_axes, sample_memberships)

    for index, (_, node) in enumerate(metrics):
        visit(node, (index,), ())
    if not contributions and (by or where is not None):
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

    bound_contributions = bind_windows(tuple(contributions), metrics, declared, period, calendar)
    if state is not None:
        bound_contributions = tuple(
            replace(item, coverage=sampling_coverage(item, state, calendar=calendar))
            for item in bound_contributions
        )
    return ObservationBinding(
        period,
        condition,
        CoordinateBinding(tuple(axes), mode, population, memberships),
        bound_contributions,
    )
