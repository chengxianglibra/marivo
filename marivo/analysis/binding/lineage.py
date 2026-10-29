"""Capture logical semantic origins before physical compilation or business reads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields, is_dataclass
from typing import TYPE_CHECKING

from marivo._definition.expressions import DeclaredMetricRef
from marivo._definition.graph import DefinitionNode, children
from marivo._definition.relations import (
    ObserveQuery,
    PopulationClassify,
    PopulationProgram,
    ReadQuery,
)
from marivo._definition.types import ExactRef
from marivo._temporal import Grain
from marivo.analysis.core.lineage import (
    ColumnLineage,
    FrameLineage,
    Origins,
    SemanticObjectIdentity,
    all_origins,
    directory,
    merge,
    union,
)
from marivo.analysis.core.model import BoundQuantity
from marivo.refs import Ref
from marivo.semantic._compiled_state import PublishedDeclaration
from marivo.semantic.expressions import _Resolver

if TYPE_CHECKING:
    from marivo.analysis.core.data_schema import DataSchema
    from marivo.analysis.core.frame_data import LiteralFrameBinding, SourceFrameBinding
    from marivo.analysis.core.frame_interpretation import FrameValueContext
    from marivo.analysis.core.frame_structure import PendingDataSchema, PendingFrameAxis
    from marivo.analysis.core.graph import BoundGraph
    from marivo.semantic._compiled_state import CompiledSemanticState


def _capture(
    root: SourceFrameBinding | LiteralFrameBinding,
) -> tuple[Callable[[object], Origins], Callable[[DefinitionNode], ColumnLineage]]:
    from marivo.analysis.core.frame_data import SourceFrameBinding

    state = root.state if isinstance(root, SourceFrameBinding) else None
    resolver = _Resolver(state) if state is not None else None
    memo: dict[tuple[str, str, str], Origins] = {}

    def identity(exact: ExactRef) -> Origins:
        key = (exact.ref.kind.value, exact.ref.path, exact.version)
        if key not in memo:
            own: Origins = (SemanticObjectIdentity(*key),)
            memo[key] = own
            if (
                state is not None
                and resolver is not None
                and not isinstance(state.definitions.get(exact.ref), PublishedDeclaration)
            ):
                own = union(
                    own,
                    *(
                        identity(resolver.exact(ref))
                        for ref in state.dependencies.get(exact.ref, ())
                    ),
                )
            memo[key] = own
        return memo[key]

    captured: dict[int, tuple[object, Origins]] = {}

    def collect(value: object) -> Origins:
        prior = captured.get(id(value))
        if prior is not None:
            return prior[1]
        result = collect_value(value)
        if isinstance(value, tuple) or is_dataclass(value):
            captured[id(value)] = (value, result)
        return result

    def collect_value(value: object) -> Origins:
        if isinstance(value, ExactRef):
            return identity(value)
        if isinstance(value, Ref):
            return identity(resolver.exact(value)) if resolver is not None else ()
        if isinstance(value, Grain):
            return collect(value._calendar) if value.kind == "semantic" else ()
        if isinstance(value, tuple):
            return union(*(collect(item) for item in value))
        if is_dataclass(value) and not isinstance(value, type):
            return union(
                *(
                    collect(getattr(value, field.name))
                    for field in fields(value)
                    if field.name not in ("recipe", "keys", "rows")
                )
            )
        return ()

    definitions = dict(root.graph.signature.declared_metrics)
    nodes: dict[int, ColumnLineage] = {}

    def expression(node: DefinitionNode) -> ColumnLineage:
        if id(node) in nodes:
            return nodes[id(node)]
        edges = children(node)
        child_ids = {id(edge.child) for edge in edges}
        context_fields = {
            "time",
            "predicate",
            "samples",
            "population",
            "members",
            "by",
            "axes",
            "rule",
        }
        own = union(
            *(
                collect(getattr(node, f.name))
                for f in fields(node)
                if f.name not in ("recipe", "authorization")
                and f.name not in context_fields
                and id(getattr(node, f.name)) not in child_ids
                and f.name not in ("inputs", "operands", "factors", "metrics", "keys", "rows")
            )
        )
        context = union(
            *(
                collect(getattr(node, f.name))
                for f in fields(node)
                if f.name in context_fields and id(getattr(node, f.name)) not in child_ids
            )
        )
        result = ColumnLineage(own, context)
        if isinstance(node, DeclaredMetricRef):
            result = merge(result, expression(definitions[node.ref]))
        for edge in edges:
            child = expression(edge.child)
            if edge.role in ("predicate", "samples", "population", "members"):
                result = merge(result, context=all_origins(child))
            else:
                result = merge(result, child)
        nodes[id(node)] = result
        return result

    return collect, expression


def definition_lineage(root: SourceFrameBinding, node: DefinitionNode) -> ColumnLineage:
    """Capture one actual diagnostic expression, without inferring from output names."""
    _, expression = _capture(root)
    return expression(node)


def source_lineage(
    schema: DataSchema | PendingDataSchema, root: SourceFrameBinding | LiteralFrameBinding
) -> FrameLineage:
    from marivo.analysis.core.frame_data import SourceFrameBinding

    collect, expression = _capture(root)
    graph = root.graph
    axes = tuple(ColumnLineage(collect(axis)) for axis in graph.signature.axes)
    domain = union(*(all_origins(axis) for axis in axes))
    observation = graph.signature.observation
    population = observation.coordinates.population if observation is not None else None
    if isinstance(graph.root, ReadQuery):
        classified = graph.root.population
        assert isinstance(classified, PopulationProgram) and isinstance(
            classified.root, PopulationClassify
        )
        population = classified.root.operand
    if observation is not None:
        domain = union(
            domain,
            collect(observation.condition),
            collect(population),
            collect(observation.coordinates.memberships),
            collect(observation.coordinates.parent_scope),
        )
    if (
        observation is not None
        and observation.coordinates.population is None
        and observation.coordinates.mode == "grouped"
    ):
        domain = union(
            domain,
            *(
                collect((c.root, c.intrinsic_conditions, c.common_condition, c.time, c.coordinates))
                for c in observation.contributions
            ),
        )
    if isinstance(root, SourceFrameBinding):
        domain = union(
            domain,
            *(
                union(
                    item.frame.lineage._domain,
                    *(all_origins(c) for c in item.frame.lineage.axes.values()),
                )
                for item in root.frame_key_inputs
            ),
        )
    values: list[ColumnLineage] = []
    if isinstance(graph.root, ObserveQuery):
        for quantity in graph.signature.quantities:
            assert isinstance(quantity, BoundQuantity)
            value = expression(quantity.definition)
            if observation is not None:
                for contribution in observation.contributions:
                    if contribution.metric == quantity.name:
                        value = merge(
                            value,
                            context=collect(
                                (
                                    contribution.intrinsic_conditions,
                                    contribution.common_condition,
                                    contribution.coordinates,
                                    contribution.time,
                                    contribution.sample_axes,
                                    contribution.memberships,
                                    contribution.calendars,
                                    contribution.population_reads,
                                    contribution.window_rule,
                                )
                            ),
                        )
            values.append(value)
    elif isinstance(graph.root, ReadQuery):
        values = [
            ColumnLineage(collect(graph.root.population.axes[index]))
            for index in graph.root.positions
        ]
    else:
        values = [ColumnLineage() for _ in schema.values]
    return directory(schema, tuple(values), axes, domain=domain)


def observation_contexts(
    graph: BoundGraph,
    axes: tuple[PendingFrameAxis, ...],
    state: CompiledSemanticState | None,
) -> tuple[tuple[str, FrameValueContext], ...]:
    """Capture direct subjects and explicit outer windows before declaration expansion."""
    from datetime import datetime, timezone

    from marivo._definition.expressions import MetricWindow
    from marivo._definition.windows import (
        AllHistory,
        Since,
        ToDate,
        TrailingDuration,
        TrailingPeriods,
    )
    from marivo.analysis.core.data_schema import CalendarDataAxis, IntervalDataAxis
    from marivo.analysis.core.frame_interpretation import (
        FrameAllHistoryWindow,
        FrameAxisTime,
        FrameBuiltinGrain,
        FrameFixedTime,
        FrameNamedGrain,
        FrameObservationContext,
        FrameObservationWindow,
        FrameSinceWindow,
        FrameToDateWindow,
        FrameTrailingDurationWindow,
        FrameTrailingPeriodsWindow,
    )

    if not isinstance(graph.root, ObserveQuery):
        return ()
    time_axes = tuple(a for a in axes if isinstance(a, (CalendarDataAxis, IntervalDataAxis)))
    observation = graph.signature.observation
    time: FrameAxisTime | FrameFixedTime | None = None
    if len(time_axes) == 1:
        time = FrameAxisTime(time_axes[0].token)
    elif observation is not None and observation.period is not None:
        period = observation.period
        if period.start is not None:
            time = FrameFixedTime(
                datetime.fromisoformat(period.start.astimezone(timezone.utc).isoformat()),
                datetime.fromisoformat(period.end.astimezone(timezone.utc).isoformat()),
                period.timezone,
            )
    result: list[tuple[str, FrameValueContext]] = []
    for quantity in graph.signature.quantities:
        if not isinstance(quantity, BoundQuantity):
            continue
        node = quantity.definition
        rule = node.rule if isinstance(node, MetricWindow) else None
        direct = node.operand if isinstance(node, MetricWindow) else node
        subject = (
            SemanticObjectIdentity(
                direct.ref.ref.kind.value, direct.ref.ref.path, direct.ref.version
            )
            if isinstance(direct, DeclaredMetricRef)
            else None
        )
        window: FrameObservationWindow | None = None
        if time is not None:
            if isinstance(rule, AllHistory):
                window = FrameAllHistoryWindow()
            elif isinstance(rule, Since):
                window = FrameSinceWindow(
                    datetime.fromisoformat(rule.start.astimezone(timezone.utc).isoformat())
                )
            elif isinstance(rule, TrailingDuration):
                window = FrameTrailingDurationWindow(rule.duration)
            elif isinstance(rule, TrailingPeriods):
                window = FrameTrailingPeriodsWindow(rule.count)
            elif isinstance(rule, ToDate):
                grain = rule.grain
                if grain.kind == "semantic":
                    assert grain._calendar is not None and grain.level is not None
                    if state is not None:
                        exact = _Resolver(state).exact(grain._calendar)
                        window = FrameToDateWindow(
                            FrameNamedGrain(
                                SemanticObjectIdentity(
                                    exact.ref.kind.value, exact.ref.path, exact.version
                                ),
                                grain.level,
                            )
                        )
                else:
                    from marivo.analysis.core.frame_calendar import _UNITS

                    assert grain.unit is not None and grain.count is not None
                    window = FrameToDateWindow(FrameBuiltinGrain(_UNITS[grain.unit], grain.count))
        result.append((quantity.name, FrameObservationContext(subject, time, window)))
    return tuple(result)
