"""Immutable occurrence-aware binding of an ordinary named Metric observation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from marivo._definition.correspondence import coordinate_matches, interval_key_pairs
from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    ContributionFilter,
    DeclaredMetricRef,
    EntityCount,
    FieldCalendarSample,
    MemberCount,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
    PredicateExpression,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import DefinitionNode, children, walk
from marivo._definition.observation_time import ObservationPeriod as ObservationPeriod
from marivo._definition.observations import ContributionBinding as ContributionBinding
from marivo._definition.observations import ObservationBinding as ObservationBinding
from marivo._definition.observations import (
    SampleAxis,
    SampleAxisMatch,
    SampleAxisMatches,
    SampleLayer,
)
from marivo._definition.population_builders import membership_anchor
from marivo._definition.relations import (
    MembershipBinding,
    PopulationDependency,
    PopulationProduct,
    PopulationProgram,
    PopulationProject,
    SampleDomain,
    population_keys,
    sample_population,
)
from marivo._definition.types import (
    AxisBinding,
    CalendarCoordinate,
    EntityIdentityRead,
    ExactRef,
    FieldRead,
    IntervalCoordinate,
    RowAttributeTime,
    field_axis,
)
from marivo._definition.windows import ObservationWindow
from marivo._kernel.temporal import Grain, InstantAnchor
from marivo.analysis.binding.contribution_binding import (
    ContributionGeometry,
    ObservationCoordinate,
    bind_contribution,
)
from marivo.analysis.binding.coordinates import (
    CoordinateBinding,
)
from marivo.analysis.binding.relationship_binding import RelationshipResolver
from marivo.analysis.binding.temporal_predicates import normalize_temporal_predicate
from marivo.analysis.binding.windows import bind_windows
from marivo.analysis.model.errors import reject
from marivo.semantic.spi import CompiledSemanticState
from marivo.semantic.spi import DefinitionResolver as _Resolver


def _membership_roles(
    layer: SampleLayer, axis: AxisBinding
) -> frozenset[
    tuple[
        tuple[str, ...],
        InstantAnchor | None,
        tuple[InstantAnchor | None, ...],
        InstantAnchor | None,
    ]
]:
    return frozenset(
        (
            tuple(step.ref.ref.key for step in path.relationships),
            path.source_at,
            path.step_ats,
            membership_anchor(binding.fields, path),
        )
        for binding in layer.memberships
        if any(coordinate_matches(field_axis(field), axis) for field in binding.fields)
        for path in binding.paths
    )


def _shared_sample_axes(
    parents: tuple[SampleLayer, ...], sample_layer: SampleLayer
) -> SampleAxisMatches:
    parents_with_layers = tuple((axis, layer) for layer in parents for axis in layer.axes)
    return tuple(
        SampleAxisMatch(
            outer,
            inner,
            interval_key_pairs(parent, sample)
            if isinstance(parent, IntervalCoordinate) and isinstance(sample, IntervalCoordinate)
            else None,
        )
        for outer, (parent, parent_layer) in enumerate(parents_with_layers)
        for inner, sample in enumerate(sample_layer.axes, start=len(parents_with_layers))
        if _sample_axis_matches(parent, sample, parent_layer, sample_layer)
    )


def _sample_axis_matches(
    parent: SampleAxis, sample: SampleAxis, parent_layer: SampleLayer, sample_layer: SampleLayer
) -> bool:
    if isinstance(parent, BuiltinSampleCoordinate) or isinstance(sample, BuiltinSampleCoordinate):
        return False
    if isinstance(parent, AxisBinding) and isinstance(sample, AxisBinding):
        return (
            coordinate_matches(parent, sample)
            and parent.versioned == sample.versioned
            and _membership_roles(parent_layer, parent) == _membership_roles(sample_layer, sample)
        )
    return coordinate_matches(parent, sample)


def _selected_interval_ordinals(population: PopulationDependency | None) -> tuple[int, ...] | None:
    if population is None:
        return None
    for index, axis in enumerate(population.axes):
        if isinstance(axis, IntervalCoordinate):
            return _interval_ordinal_support(population, index)
    return None


def _interval_ordinal_support(
    population: PopulationDependency, index: int
) -> tuple[int, ...] | None:
    """Recognize captured possible keys without enumerating independent source factors."""
    if isinstance(population, PopulationProgram):
        node = population.root
        if isinstance(node, PopulationProduct):
            support = None
            offset = 0
            for factor in node.factors:
                end = offset + len(factor.axes)
                if offset <= index < end:
                    support = _interval_ordinal_support(factor, index - offset)
                elif population_keys(factor) == ():
                    return ()
                offset = end
            return support
        if isinstance(node, PopulationProject):
            support = _interval_ordinal_support(node.operand, node.positions[index])
            rebased = dict(node.interval_keys).get(index)
            if support is not None and rebased is not None:
                return tuple(sorted({rebased[key] for key in support}))
            return support
    rows = population_keys(population)
    if rows is None:
        return None
    ordinals = set()
    for row in rows:
        ordinal = row[index]
        assert type(ordinal) is int
        ordinals.add(ordinal)
    return tuple(sorted(ordinals))


def bind_observation(
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    *,
    by: tuple[ObservationCoordinate, ...],
    where: PredicateExpression | None,
    timezone: str,
    state: CompiledSemanticState | None,
    memberships: tuple[MembershipBinding, ...] = (),
    population: PopulationDependency | None = None,
    report_period: ObservationPeriod | None = None,
    pending_interval: int | None = None,
    empty_interval: bool = False,
) -> ObservationBinding:
    _validate_output_coordinates(by)
    geometry, time_axis = _capture_output_geometry(
        by, memberships, report_period, pending_interval, empty_interval
    )
    condition = (
        normalize_temporal_predicate(where, timezone, location="Session.observe.where")
        if where is not None
        else None
    )
    binder = _ObservationBinder(
        geometry,
        metrics,
        declared,
        condition,
        timezone,
        state,
        _Resolver(state) if state is not None else None,
        RelationshipResolver.build(state.registry) if state is not None else None,
        SampleLayer(geometry.axes, memberships),
    )
    for index, (_, node) in enumerate(metrics):
        binder.visit(node, (index,), ())
    return _finalize_observation(
        binder, time_axis, where, population, pending_interval, empty_interval
    )


def _validate_output_coordinates(by: object) -> None:
    if not isinstance(by, tuple) or any(
        not isinstance(
            field, (FieldRead, EntityIdentityRead, CalendarCoordinate, IntervalCoordinate)
        )
        for field in by
    ):
        reject(
            "a tuple of resolved fields and time domains",
            repr(by),
            "Use by=(field, scope.by(mv.grain('day'))) or where=scope for range-only totals.",
            "Session.observe",
        )


def _capture_output_geometry(
    by: tuple[ObservationCoordinate, ...],
    memberships: tuple[MembershipBinding, ...],
    report_period: ObservationPeriod | None,
    pending_interval: int | None,
    empty_interval: bool,
) -> tuple[ContributionGeometry, CalendarCoordinate | IntervalCoordinate | None]:
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
    period = report_period or (
        ObservationPeriod(time_axis.start, time_axis.end, time_axis.timezone)
        if isinstance(time_axis, IntervalCoordinate)
        and pending_interval is None
        and not empty_interval
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
        else field_axis(field)
        if isinstance(field, FieldRead)
        else AxisBinding(
            field.entity,
            None,
            field.path,
            "identity",
            field.at,
            source_at=field.source_at,
            step_ats=field.step_ats,
            path_root=field.path_root,
            path_facts=field.path_facts,
        )
        for field in by
    )
    if len(set(axes)) != len(axes):
        reject("distinct output axes", repr(by), "Remove repeated axes.", "Session.observe")
    return (
        ContributionGeometry(
            by, axes, memberships, period, pending_interval is not None or empty_interval
        ),
        time_axis,
    )


@dataclass(slots=True)
class _ObservationBinder:
    geometry: ContributionGeometry
    metrics: tuple[tuple[str, MetricNode], ...]
    declared: dict[ExactRef, MetricNode]
    condition: PredicateExpression | None
    timezone: str
    state: CompiledSemanticState | None
    resolver: _Resolver | None
    paths: RelationshipResolver | None
    output_layer: SampleLayer
    contributions: list[ContributionBinding] = field(default_factory=list)
    calendar_samples: list[tuple[tuple[int, ...], BuiltinSampleCoordinate]] = field(
        default_factory=list
    )
    sample_matches: list[tuple[tuple[int, ...], SampleAxisMatches]] = field(default_factory=list)
    count_populations: dict[PopulationDependency, PopulationDependency] = field(
        default_factory=dict
    )

    def visit(
        self,
        node: DefinitionNode,
        occurrence: tuple[int, ...],
        filters: tuple[PredicateExpression, ...],
        sample_axes: tuple[SampleAxis, ...] = (),
        sample_layers: tuple[SampleLayer, ...] = (),
        window_intent: ObservationWindow | None = None,
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            self.visit(
                self.declared[node.ref],
                (*occurrence, 0),
                filters,
                sample_axes,
                sample_layers,
                window_intent,
            )
            return
        if isinstance(node, ContributionFilter):
            self.visit(
                node.operand,
                (*occurrence, 0),
                (
                    *filters,
                    normalize_temporal_predicate(
                        node.predicate, self.timezone, location="Metric.where"
                    ),
                ),
                sample_axes,
                sample_layers,
                window_intent,
            )
            return
        if isinstance(node, MemberCount):
            self._bind_count(node, sample_axes)
            return
        if isinstance(node, (PerReduce, PerWeightedMean)):
            self._bind_sample(node, occurrence, filters, sample_axes, sample_layers, window_intent)
            return
        if isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)):
            state, resolver, paths = self.state, self.resolver, self.paths
            if state is None or resolver is None or paths is None:
                reject(
                    "captured semantic declarations for source binding",
                    "no semantic state",
                    "Load the source definitions in this Session's Catalog.",
                    "session.observe",
                )
            self.contributions.append(
                bind_contribution(
                    node,
                    occurrence,
                    self.metrics[occurrence[0]][0],
                    self.geometry,
                    state,
                    resolver,
                    paths,
                    filters,
                    self.condition,
                    sample_axes,
                    sample_layers,
                    window_intent=window_intent,
                )
            )
            return
        if isinstance(node, MetricWindow):
            window_intent = node.rule
        for edge in children(node):
            self.visit(
                edge.child,
                (*occurrence, edge.index),
                filters,
                sample_axes,
                sample_layers,
                window_intent,
            )

    def _bind_count(self, node: MemberCount, sample_axes: tuple[SampleAxis, ...]) -> None:
        domain = node.population
        requested = tuple(dict.fromkeys((*self.geometry.axes, *sample_axes)))
        for axis in requested:
            if isinstance(axis, BuiltinSampleCoordinate):
                continue
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
        self.count_populations[domain] = domain

    def _bind_sample(
        self,
        node: PerReduce | PerWeightedMean,
        occurrence: tuple[int, ...],
        filters: tuple[PredicateExpression, ...],
        sample_axes: tuple[SampleAxis, ...],
        sample_layers: tuple[SampleLayer, ...],
        window_intent: ObservationWindow | None,
    ) -> None:
        sample_domain = node.samples
        inner_domain_axes: tuple[object, ...]
        if isinstance(sample_domain, (Grain, FieldCalendarSample)):
            inner = BuiltinSampleCoordinate(sample_domain, self.timezone)
            self.calendar_samples.append((occurrence, inner))
            inner_domain_axes = (inner,)
        else:
            inner_domain_axes = (
                sample_domain if isinstance(sample_domain, tuple) else sample_domain.axes
            )
        inner_axes = _sample_axes_from_capture(inner_domain_axes)
        inner_memberships = (
            sample_domain.memberships if isinstance(sample_domain, SampleDomain) else ()
        )
        explicit_population = sample_population(sample_domain)
        sample_layer = SampleLayer(
            inner_axes, inner_memberships, _selected_interval_ordinals(explicit_population)
        )
        if explicit_population is not None:
            matches = _shared_sample_axes((self.output_layer, *sample_layers), sample_layer)
            if matches:
                self.sample_matches.append((occurrence, matches))
        contribution_count = len(self.contributions)
        for edge in children(node):
            if edge.role in ("operand", "value", "weight"):
                self.visit(
                    edge.child,
                    (*occurrence, edge.index),
                    filters,
                    (*sample_axes, *inner_axes),
                    (*sample_layers, sample_layer),
                    window_intent,
                )
        if (
            isinstance(sample_domain, (Grain, FieldCalendarSample))
            and len(self.contributions) == contribution_count
        ):
            reject(
                "fact contributions representing actual calendar samples",
                "a source-free or count-only sample formula",
                "Use a fact Metric inside the calendar sample.",
                "Metric.per",
            )
        if inner_memberships and len(self.contributions) == contribution_count:
            reject(
                "fact contributions for membership allocation",
                "count-only internal samples",
                "Remove ineffective memberships or include a fact Metric in the sample formula.",
                "Metric.per.memberships",
            )


def _finalize_observation(
    binder: _ObservationBinder,
    time_axis: CalendarCoordinate | IntervalCoordinate | None,
    where: PredicateExpression | None,
    population: PopulationDependency | None,
    pending_interval: int | None,
    empty_interval: bool,
) -> ObservationBinding:
    axes, memberships, period = (
        binder.geometry.axes,
        binder.geometry.memberships,
        binder.geometry.period,
    )
    metrics, declared, condition = binder.metrics, binder.declared, binder.condition
    contributions = binder.contributions
    count_populations = binder.count_populations
    calendar_samples, sample_matches = binder.calendar_samples, binder.sample_matches
    calendar = time_axis if isinstance(time_axis, CalendarCoordinate) else None
    if (
        time_axis is not None
        and population is None
        and not any(item.time is not None for item in contributions)
        and not any(
            coordinate_matches(time_axis, axis)
            for domain in count_populations
            for axis in domain.axes
        )
    ):
        reject(
            "a time-coordinate provider or an explicit output Population",
            "report-independent Metric branches without existing report time coordinates",
            "Use by=mv.product(axis, mv.coordinates(by=(), keys=((),))) for a complete time domain, or where=scope for a range total.",
            "session.observe",
            canonical_id="session.observe",
        )
    _validate_row_anchor_contributions(contributions, metrics, declared)
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
    bound_contributions = (
        tuple(contributions)
        if pending_interval is not None or empty_interval
        else bind_windows(
            tuple(contributions),
            metrics,
            declared,
            period,
            calendar,
            interval=time_axis if isinstance(time_axis, IntervalCoordinate) else None,
        )
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
        CoordinateBinding(
            tuple(axes),
            mode,
            population,
            memberships,
        ),
        bound_contributions,
        tuple(count_populations.items()),
        tuple(calendar_samples),
        pending_interval,
        empty_interval,
        tuple(sample_matches),
    )


def _sample_axes_from_capture(values: tuple[object, ...]) -> tuple[SampleAxis, ...]:
    return tuple(
        value
        for value in values
        if isinstance(
            value, (AxisBinding, CalendarCoordinate, IntervalCoordinate, BuiltinSampleCoordinate)
        )
    )


def _validate_row_anchor_contributions(
    contributions: list[ContributionBinding],
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
) -> None:
    row_reads = tuple(
        read
        for contribution in contributions
        for read in (*contribution.reads, *contribution.coordinates)
        if isinstance(read.at, RowAttributeTime)
    )
    if row_reads and (
        len({item.root.ref for item in contributions}) != 1
        or any(
            isinstance(item, (MetricWindow, PerReduce, PerWeightedMean))
            for item in walk((node for _, node in metrics), resolve=declared.get).order
        )
    ):
        reject(
            "single-root observe classification without windows or internal samples",
            ", ".join(sorted({item.root.ref.path for item in contributions})),
            "Use ordinary single-root observe with a fact-root TimeDimension, or use a fixed InstantAnchor for this composition.",
            "session.observe.at",
        )
