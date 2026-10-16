"""Immutable occurrence-aware binding of an ordinary named Metric observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo._definition.expressions import (
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldCompare,
    FieldIn,
    FieldIsNull,
    FieldPredicate,
    MemberCount,
    MetricNode,
    PerReduce,
    PerWeightedMean,
    PredAnd,
    PredNot,
    PredOr,
    RowCompare,
    RowField,
    SourceAggregate,
    SourceWeightedMean,
    TimeBinding,
)
from marivo._definition.graph import DefinitionNode, _encode, children, fingerprint
from marivo._definition.types import AxisBinding, CalendarCoordinate, ExactRef, FieldRead
from marivo._temporal import InstantAnchor, TimeScope, _SessionTimeScope, anchor_from_datetime
from marivo.analysis.binding.coordinates import (
    CoordinateBinding,
    bind_read,
    field_read,
    native_read,
)
from marivo.analysis.core.model import reject
from marivo.analysis.core.time_grid import instant
from marivo.analysis.dsl.temporal import CalendarAxis, freeze_scope
from marivo.analysis.observation.relationship_binding import RelationshipResolver
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic.expressions import Dimension, Field, Measure, Predicate, TimeField, _Resolver


@dataclass(frozen=True, slots=True)
class ObservationPeriod:
    start: InstantAnchor
    end: InstantAnchor
    timezone: str


@dataclass(frozen=True, slots=True)
class ContributionBinding:
    """One source reduction usage, before any physical fusion or source reading."""

    occurrence: tuple[int, ...]
    definition: MetricNode
    root: ExactRef
    intrinsic_conditions: tuple[FieldPredicate, ...]
    common_condition: FieldPredicate | None
    reads: tuple[FieldRead, ...]
    coordinates: tuple[FieldRead, ...]
    time: TimeBinding | None
    period: ObservationPeriod | None
    physical_dtype: Literal["unresolved"] = "unresolved"


@dataclass(frozen=True, slots=True)
class ObservationBinding:
    """One observation's conditions, frozen period, output domain and occurrences."""

    period: ObservationPeriod | None
    condition: FieldPredicate | None
    coordinates: CoordinateBinding
    contributions: tuple[ContributionBinding, ...]

    @property
    def fingerprint(self) -> str:
        return fingerprint(_encode(self, {}))


def _row_fields(node: DefinitionNode) -> tuple[RowField, ...]:
    if isinstance(node, RowField):
        return (node,)
    return tuple(field for edge in children(node) for field in _row_fields(edge.child))


def _predicate_reads(
    node: FieldPredicate, state: CompiledSemanticState, resolver: _Resolver
) -> tuple[FieldRead, ...]:
    if isinstance(node, (PredAnd, PredOr)):
        return tuple(
            read for child in node.operands for read in _predicate_reads(child, state, resolver)
        )
    if isinstance(node, PredNot):
        return _predicate_reads(node.operand, state, resolver)
    if isinstance(node, RowCompare):
        return tuple(
            native_read(field.ref, state.registry, resolver) for field in _row_fields(node.operand)
        )
    return (node.read or native_read(node.field, state.registry, resolver),)


def bind_observation(
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    *,
    during: TimeScope | CalendarAxis | None,
    by: tuple[Field, ...],
    where: Predicate | None,
    timezone: str,
    state: CompiledSemanticState | None,
) -> ObservationBinding:
    if during is not None and not isinstance(during, (TimeScope, CalendarAxis)):
        reject(
            "TimeScope, CalendarAxis or None",
            type(during).__name__,
            "Use session.time_scope(...), optionally followed by .by(mv.grain(...)).",
            "session.observe",
        )
    if not isinstance(by, tuple) or any(
        not isinstance(field, (Dimension, TimeField, Measure)) for field in by
    ):
        reject(
            "a tuple of resolved scalar fields",
            type(by).__name__,
            "Pass by=(field,) using resolved semantic fields.",
            "session.observe",
        )
    if where is not None and not isinstance(where, Predicate):
        reject(
            "a typed Field predicate",
            type(where).__name__,
            "Compare resolved fields and combine predicates with &, |, ~.",
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
        AxisBinding(
            field.root, field.ref, tuple(step.ref for step in field.path), "field", field.anchor
        )
        for field in by
    )
    if len({repr(axis) for axis in axes}) != len(axes):
        reject(
            "distinct output axes", repr(by), "Remove the repeated field axis.", "session.observe"
        )
    condition = where.node if where is not None else None
    contributions: list[ContributionBinding] = []
    resolver = _Resolver(state) if state is not None else None
    paths = RelationshipResolver.build(state.registry) if state is not None else None

    def visit(
        node: DefinitionNode, occurrence: tuple[int, ...], filters: tuple[FieldPredicate, ...]
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*occurrence, 0), filters)
            return
        if isinstance(node, ContributionFilter):
            if not isinstance(
                node.predicate,
                (FieldCompare, FieldIn, FieldIsNull, RowCompare, PredAnd, PredOr, PredNot),
            ):
                reject(
                    "ordinary field conditions",
                    type(node.predicate).__name__,
                    "Population evaluation is delivered in R3.",
                    "session.observe",
                )
            visit(node.operand, (*occurrence, 0), (*filters, node.predicate))
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
            time = None
            time_read = None
            if node.time is not None:
                time_read = native_read(node.time.field, state.registry, resolver)
                dimension = state.registry.dimensions[node.time.field.ref.path]
                if (
                    period is None
                    and dimension.parse is not None
                    and getattr(dimension.parse, "sample_interval", None) is not None
                ):
                    reject(
                        "an explicit period for sampled time",
                        f"None for {dimension.semantic_id} at {occurrence}",
                        "Pass during=session.time_scope(...).",
                        "session.observe",
                    )
            reads = [native_read(field.ref, state.registry, resolver) for field in fields]
            if node.time is not None and time_read is not None:
                reads.append(
                    FieldRead(time_read.field, time_read.entity, node.time.path, node.time.at)
                )
            for predicate in (*filters, *((condition,) if condition is not None else ())):
                reads.extend(_predicate_reads(predicate, state, resolver))
            bound = tuple(bind_read(read, root, state.registry, resolver, paths) for read in reads)
            if node.time is not None:
                bound_time = bound[len(fields)]
                time = TimeBinding(bound_time.field, bound_time.path, bound_time.at)
            coordinates = tuple(
                bind_read(field_read(field), root, state.registry, resolver, paths) for field in by
            )
            if period is not None and node.time is None:
                reject(
                    "a declared business time role for this period",
                    root.ref.path,
                    "Declare a default time or construct the reduction with time=field.",
                    "session.observe",
                )
            contributions.append(
                ContributionBinding(
                    occurrence,
                    node,
                    root,
                    filters,
                    condition,
                    bound,
                    coordinates,
                    time,
                    period,
                )
            )
            return
        for edge in children(node):
            visit(edge.child, (*occurrence, edge.index), filters)

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
        "calendar" if calendar is not None else "grouped" if axes else "singleton"
    )
    return ObservationBinding(
        period, condition, CoordinateBinding(tuple(axes), mode), tuple(contributions)
    )
