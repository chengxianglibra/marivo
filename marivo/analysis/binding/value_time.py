"""Capture inert per-value contribution-time facts before definitions are discarded."""

from __future__ import annotations

from datetime import datetime, timezone

from marivo._definition.expressions import (
    DeclaredMetricRef,
    FieldCalendarSample,
    MemberCount,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import children
from marivo._definition.relations import ObserveQuery
from marivo._definition.types import CalendarCoordinate, ExactRef, IntervalCoordinate
from marivo._definition.windows import (
    AllHistory,
    ObservationWindow,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
)
from marivo._temporal import Grain
from marivo.analysis.core.frame_interpretation import (
    FrameAllHistoryWindow,
    FrameBuiltinGrain,
    FrameCalendarEndpoint,
    FrameClockedContribution,
    FrameContributionSampleWindowEndpoint,
    FrameFieldSampleWindowEndpoint,
    FrameFixedTime,
    FrameIndependentContribution,
    FrameIntervalEndpoint,
    FrameObservationWindow,
    FrameObservedTimeSemantics,
    FrameReportWindowEndpoint,
    FrameSinceWindow,
    FrameTimeContribution,
    FrameTimeEndpoint,
    FrameToDateWindow,
    FrameTrailingDurationWindow,
    FrameTrailingPeriodsWindow,
    FrameValueTimeSemantics,
    FrameWindowEndpoint,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.core.lineage import SemanticObjectIdentity
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._metric_time import metric_time_occurrences


def _identity(value: ExactRef | None) -> SemanticObjectIdentity | None:
    return (
        None
        if value is None
        else SemanticObjectIdentity(value.ref.kind.value, value.ref.path, value.version)
    )


def _instant(value: datetime) -> datetime:
    return datetime.fromisoformat(value.astimezone(timezone.utc).isoformat())


def _window(
    rule: ObservationWindow | None, state: CompiledSemanticState | None
) -> FrameObservationWindow | None:
    if isinstance(rule, AllHistory):
        return FrameAllHistoryWindow()
    if isinstance(rule, Since):
        return FrameSinceWindow(_instant(rule.start))
    if isinstance(rule, TrailingDuration):
        return FrameTrailingDurationWindow(rule.duration)
    if isinstance(rule, TrailingPeriods):
        return FrameTrailingPeriodsWindow(rule.count)
    if isinstance(rule, ToDate):
        grain = rule.grain
        from marivo.analysis.core.frame_calendar import _UNITS

        assert grain.unit is not None and grain.count is not None
        return FrameToDateWindow(FrameBuiltinGrain(_UNITS[grain.unit], grain.count))
    return None


def observation_time_semantics(
    graph: BoundGraph,
    state: CompiledSemanticState | None = None,
) -> tuple[tuple[str, FrameValueTimeSemantics], ...]:
    """Capture every original branch and its own window/sample/report application."""
    if not isinstance(graph.root, ObserveQuery):
        return ()
    observation = graph.signature.observation
    period = observation.period if observation is not None else None
    report = (
        FrameFixedTime(_instant(period.start), _instant(period.end), period.timezone)
        if period is not None and period.start is not None
        else None
    )
    axis = next(
        (
            axis
            for axis in graph.signature.axes
            if isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
        ),
        None,
    )
    has_axis = axis is not None
    report_axis: FrameTimeEndpoint | None = None
    report_cells: tuple[tuple[datetime, datetime, datetime, datetime], ...] = ()
    if isinstance(axis, CalendarCoordinate):
        grain = _window(ToDate(axis.grain_spec), state)
        assert isinstance(grain, FrameToDateWindow)
        report_axis = FrameCalendarEndpoint(
            "calendar:" + axis.grain, None, axis.timezone, grain.grain
        )
        report_cells = tuple(
            (_instant(a), _instant(b), _instant(c), _instant(d)) for a, b, c, d in axis.cells
        )
    elif isinstance(axis, IntervalCoordinate):
        report_axis = FrameIntervalEndpoint("interval", None, axis.timezone)
        report_cells = (
            (_instant(axis.start), _instant(axis.end), _instant(axis.start), _instant(axis.end)),
        )

    declared = dict(graph.signature.declared_metrics)
    result: list[tuple[str, FrameValueTimeSemantics]] = []
    for name, root in graph.root.metrics:
        facts: list[FrameTimeContribution] = []

        def visit(
            node: MetricNode,
            path: tuple[str, ...],
            metric: ExactRef | None = None,
            rule: ObservationWindow | None = None,
            samples: tuple[str, ...] = (),
            sample_endpoint: FrameWindowEndpoint | None = None,
            window_endpoint: FrameWindowEndpoint | None = None,
            *,
            facts: list[FrameTimeContribution] = facts,
            name: str = name,
        ) -> None:
            if isinstance(node, DeclaredMetricRef):
                visit(
                    declared[node.ref],
                    (*path, node.ref.ref.key),
                    node.ref,
                    rule,
                    samples,
                    sample_endpoint,
                    window_endpoint,
                )
                return
            if isinstance(node, MetricWindow):
                rule = node.rule
                window_endpoint = sample_endpoint or FrameReportWindowEndpoint()
            if isinstance(node, (PerReduce, PerWeightedMean)):
                sample = node.samples
                role = (
                    "actual_contribution_clock"
                    if isinstance(sample, Grain)
                    else "explicit_field"
                    if isinstance(sample, (FieldCalendarSample, tuple))
                    else "explicit_population"
                )
                samples = (*samples, role)
                if isinstance(sample, (Grain, FieldCalendarSample)):
                    grain = sample.grain if isinstance(sample, FieldCalendarSample) else sample
                    from marivo.analysis.core.frame_calendar import _UNITS

                    assert grain.unit is not None and grain.count is not None
                    captured = FrameBuiltinGrain(_UNITS[grain.unit], grain.count)
                    # Builtin sample bindings own the Session's frozen timezone.
                    binding = (
                        next(
                            (
                                coordinate
                                for _, coordinate in observation.calendar_samples
                                if getattr(coordinate, "sample", None) == sample
                            ),
                            None,
                        )
                        if observation is not None
                        else None
                    )
                    assert binding is not None
                    if isinstance(sample, FieldCalendarSample):
                        field = _identity(sample.field.field)
                        assert field is not None
                        sample_endpoint = FrameFieldSampleWindowEndpoint(
                            len(samples), field, captured, binding.timezone
                        )
                    else:
                        sample_endpoint = FrameContributionSampleWindowEndpoint(
                            len(samples), captured, binding.timezone
                        )
            occurrences = metric_time_occurrences(node, declared)
            if len(occurrences) == 1 and occurrences[0].node is node:
                occurrence = occurrences[0]
                clock = occurrence.binding
                if clock is not None:
                    field = _identity(clock.field)
                    assert field is not None
                    facts.append(
                        FrameClockedContribution(
                            name,
                            path,
                            _identity(metric),
                            _identity(occurrence.root),
                            field,
                            tuple(p.ref.key for p in clock.path),
                            _instant(clock.at) if clock.at is not None else None,
                            clock.source,
                            report,
                            "buckets" if has_axis else "range" if report else "none",
                            _window(rule, state),
                            samples,
                            report_axis=report_axis,
                            report_cells=report_cells,
                            window_endpoint=window_endpoint,
                        )
                    )
                else:
                    assert occurrence.reason is not None
                    matching = isinstance(node, MemberCount) and any(
                        isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
                        for axis in node.population.axes
                    )
                    facts.append(
                        FrameIndependentContribution(
                            name,
                            path,
                            _identity(metric),
                            _identity(occurrence.root),
                            occurrence.reason,
                            report,
                            "coordinate_match"
                            if matching and has_axis
                            else "broadcast"
                            if has_axis
                            else "range_ignored"
                            if report
                            else "none",
                            _window(rule, state),
                            samples,
                            report_axis=report_axis,
                            report_cells=report_cells,
                            window_endpoint=window_endpoint,
                        )
                    )
                return
            for edge in children(node):
                if isinstance(edge.child, MetricNode):
                    visit(
                        edge.child,
                        (*path, f"{edge.role}[{edge.index}]"),
                        metric,
                        rule,
                        samples,
                        sample_endpoint,
                        window_endpoint,
                    )

        visit(root, ())
        result.append((name, FrameObservedTimeSemantics(tuple(facts))))
    return tuple(result)
