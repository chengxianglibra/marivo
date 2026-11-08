"""Bind source-free window rules to exact observation endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from marivo._definition.expressions import (
    BuiltinSampleCoordinate,
    DeclaredMetricRef,
    FieldCalendarSample,
    MetricNode,
    MetricWindow,
    PerReduce,
    PerWeightedMean,
)
from marivo._definition.graph import DefinitionNode, children, walk
from marivo._definition.observations import SampleWindow
from marivo._definition.types import CalendarCoordinate, ExactRef
from marivo._definition.windows import (
    AllHistory,
    ObservationWindow,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
)
from marivo._temporal import Grain, anchor_from_datetime, session_time_scope
from marivo._temporal_grid import _floor, _next, bind_grid, instant
from marivo._timezones import normalize_timezone
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ContributionBinding, ObservationPeriod
    from marivo.analysis.core.graph import BoundGraph


def actual_window_axis(graph: BoundGraph) -> CalendarCoordinate | None:
    """Find a report calendar whose window endpoints depend on actual source keys."""
    observation = graph.signature.observation
    if observation is None or not any(
        item.window_rule is not None and item.sample_window is None
        for item in observation.contributions
    ):
        return None
    return next(
        (
            axis
            for axis in graph.signature.axes
            if isinstance(axis, CalendarCoordinate) and axis.actual_only
        ),
        None,
    )


def require_finite_attribution_scope(graph: BoundGraph) -> None:
    """Require captured finite report endpoints for source-side attribution."""
    if any(
        isinstance(axis, CalendarCoordinate) and axis.actual_only for axis in graph.signature.axes
    ):
        reject(
            "finite report endpoints for source-side attribution",
            "actual Grain-only report keys",
            "Supply a CalendarAxis with explicit report endpoints before constructing the attribution.",
            "analysis_frame.attribute",
            canonical_id="analysis_frame.attribute",
        )


def _previous(value: datetime, grain: Grain) -> datetime:
    """The predecessor in civil calendar space, without epsilon arithmetic."""
    unit, count = grain.unit, grain.count
    assert unit is not None and count is not None
    if unit in ("day", "week"):
        return value - timedelta(days=(1 if unit == "day" else 7) * count)
    months = {"month": 1, "quarter": 3, "year": 12}[unit] * count
    ordinal = value.year * 12 + value.month - 1 - months
    return value.replace(year=ordinal // 12, month=ordinal % 12 + 1)


def _reset(end: datetime, grain: Grain, zone: str) -> tuple[datetime, datetime]:
    if grain.unit in ("second", "minute", "hour"):
        width = timedelta(
            seconds={"second": 1, "minute": 60, "hour": 3600}[grain.unit] * (grain.count or 1)
        )
        grid = bind_grid(
            session_time_scope(
                anchor_from_datetime(end - width), anchor_from_datetime(end + width), zone
            ),
            grain,
        )
        return next(
            (cell.original_start, cell.original_end)
            for cell in grid.cells
            if cell.original_start < end <= cell.original_end
        )
    local = end.astimezone(normalize_timezone(zone).tz)
    left = _floor(local, grain)
    if left == local:
        # Boundary ownership is exact: the period just completed owns this endpoint.
        left = _previous(left, grain)
    return instant(left.replace(tzinfo=None), zone), instant(
        _next(left, grain).replace(tzinfo=None), zone
    )


def validate_date_contribution(contribution: ContributionBinding, path: str) -> None:
    """Reject fabricated subday DATE contributions before a business read."""
    from marivo._definition.types import FieldRead

    is_role = contribution.time is not None and contribution.time.field.ref.path == path
    is_sample = any(
        isinstance(read, FieldRead) and read.field.ref.path == path
        for read in contribution.coordinates[contribution.output_axis_count :]
    )
    sample_slots = {
        slot for slot, read in contribution.sample_calendar_reads if read.field.ref.path == path
    }
    if not is_role and not is_sample and not sample_slots:
        return
    calendars = tuple(
        axis
        for slot, axis in contribution.calendars
        if slot in sample_slots
        or ((is_role or is_sample) and not isinstance(axis, BuiltinSampleCoordinate))
    )
    if any(
        isinstance(axis, CalendarCoordinate) and not axis.cells and not axis.actual_only
        for axis in calendars
    ):
        return

    def fail(received: str) -> None:
        reject(
            "civil DATE contributions at complete business-midnight boundaries",
            received,
            "Use day-or-larger output and reset Grains with complete civil-date ranges, "
            "or use a real timestamp TimeField for subday contributions.",
            "session.observe",
        )

    rule = contribution.window_rule
    if isinstance(rule, TrailingDuration) and rule.duration % timedelta(days=1):
        fail(f"{path}: elapsed duration {rule.duration}")
    if isinstance(rule, ToDate) and rule.grain.unit in ("second", "minute", "hour"):
        fail(f"{path}: subday reset {rule.grain!r}")
    for axis in calendars:
        if isinstance(axis, (CalendarCoordinate, BuiltinSampleCoordinate)):
            if axis.grain_spec.unit in ("second", "minute", "hour"):
                fail(f"{path}: subday calendar {axis.grain_spec._to_token()}")
            if isinstance(axis, BuiltinSampleCoordinate):
                continue
            bounds = tuple(bound for cell in axis.cells for bound in cell[2:])
        else:
            bounds = (axis.start, axis.end)
        zone = normalize_timezone(axis.timezone).tz
        offending = next(
            (bound for bound in bounds if bound.astimezone(zone).time() != datetime.min.time()),
            None,
        )
        if offending is not None:
            local = offending.astimezone(zone).isoformat()
            fail(f"{path}: calendar boundary {local} is not local midnight in {axis.timezone}")
    period = contribution.period
    if period is not None and (is_role or is_sample):
        zone = normalize_timezone(period.timezone).tz
        offending = next(
            (
                bound
                for bound in (period.start, period.end)
                if bound is not None and bound.astimezone(zone).time() != datetime.min.time()
            ),
            None,
        )
        if offending is not None:
            local = offending.astimezone(zone).isoformat()
            fail(f"{path}: calendar boundary {local} is not local midnight in {period.timezone}")


def bind_windows(
    contributions: tuple[ContributionBinding, ...],
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    period: ObservationPeriod | None,
    calendar: CalendarCoordinate | None,
    *,
    native_starts: Mapping[tuple[ObservationWindow, datetime], datetime] | None = None,
) -> tuple[ContributionBinding, ...]:
    """Expand each window usage into independent source reads per output endpoint."""
    from marivo.analysis.binding.observe import ObservationPeriod

    rules: dict[tuple[int, ...], ObservationWindow] = {}
    sample_windows: dict[tuple[int, ...], SampleWindow] = {}
    sample_grains: dict[SampleWindow, Grain] = {}

    def visit(
        node: DefinitionNode,
        path: tuple[int, ...],
        rule: ObservationWindow | None,
        sample: SampleWindow | None = None,
        depth: int = 0,
        window_sample: SampleWindow | None = None,
    ) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*path, 0), rule, sample, depth, window_sample)
            return
        if isinstance(node, MetricWindow):
            if any(False for child in walk((node.operand,), resolve=declared.get).order):
                reject(
                    "builtin calendar samples inside a Metric window",
                    "a Metric window wrapping a custom calendar fold",
                    "Use a builtin sample Grain, or observe the custom calendar separately.",
                    "Metric.window",
                )
            if rule is not None:
                reject(
                    "one window per dependency path",
                    "nested windows",
                    "Apply one rule to the complete base formula.",
                    "Metric.window",
                )
            rule = node.rule
            window_sample = sample
            if (
                isinstance(rule, TrailingPeriods)
                and sample is not None
                and sample_grains[sample].unit not in ("day", "week", "month", "quarter", "year")
            ):
                reject(
                    "day/week/month/quarter/year sample periods",
                    repr(sample_grains[sample]),
                    "Use trailing_duration for elapsed subday windows.",
                    "Metric.window",
                )
        if rule is not None:
            rules[path] = rule
            if window_sample is not None:
                sample_windows[path] = window_sample
        if isinstance(node, (PerReduce, PerWeightedMean)):
            domain = node.samples
            depth += (
                1
                if isinstance(domain, (Grain, FieldCalendarSample))
                else len(domain if isinstance(domain, tuple) else domain.axes)
            )
            if isinstance(domain, (Grain, FieldCalendarSample)):
                sample = SampleWindow(path, depth)
                sample_grains[sample] = (
                    domain.grain if isinstance(domain, FieldCalendarSample) else domain
                )
        for edge in children(node):
            visit(
                edge.child,
                (*path, edge.index),
                rule,
                sample,
                depth,
                window_sample,
            )

    for index, (_, node) in enumerate(metrics):
        visit(node, (index,), None)
    if not rules:
        return contributions
    deferred = tuple(
        replace(
            item, window_rule=rules[item.occurrence], sample_window=sample_windows[item.occurrence]
        )
        for item in contributions
        if item.occurrence in sample_windows
    )
    contributions = tuple(item for item in contributions if item.occurrence not in sample_windows)
    rules = {path: rule for path, rule in rules.items() if path not in sample_windows}
    if not rules:
        return (*contributions, *deferred)
    if calendar is not None and calendar.actual_only and not calendar.cells:
        grain = calendar.grain_spec
        for deferred_rule in rules.values():
            if isinstance(deferred_rule, TrailingPeriods) and grain.unit not in (
                "day",
                "week",
                "month",
                "quarter",
                "year",
            ):
                reject(
                    "day/week/month/quarter/year periods",
                    calendar.grain,
                    "Use trailing_duration for elapsed subday windows.",
                    "Metric.window",
                )
        # Keep W as a rule until execution has obtained the complete O key image.
        return (
            *deferred,
            *(replace(item, window_rule=rules.get(item.occurrence)) for item in contributions),
        )
    if calendar is not None and not calendar.cells:
        # Retain schema-bearing reads without creating window endpoints from O.
        return (
            *deferred,
            *(
                replace(
                    contribution,
                    calendars=tuple(
                        (slot, replace(axis, ranges=())) if axis == calendar else (slot, axis)
                        for slot, axis in contribution.calendars
                    ),
                )
                if contribution.occurrence in rules
                else contribution
                for contribution in contributions
            ),
        )
    if period is None:
        reject(
            "an explicit window endpoint",
            "no report endpoint",
            "Pass a report TimeScope in where or a CalendarAxis in by to session.observe(...).",
            "Metric.window",
        )
    # Validate endpoint operations even when no source clock consumes the rule.
    validation_cells = calendar.cells if calendar is not None else ()
    validation_ends = tuple(cell[3] for cell in validation_cells) or (period.end,)
    for validation_rule in dict.fromkeys(rules.values()):
        if any(
            item.time is not None and rules.get(item.occurrence) == validation_rule
            for item in contributions
        ):
            continue
        if isinstance(validation_rule, TrailingPeriods) and (
            calendar is None or any(cell[:2] != cell[2:] for cell in validation_cells)
        ):
            reject(
                "complete calendar buckets for trailing_periods",
                "missing or partial report buckets",
                "Observe a CalendarAxis with complete periods.",
                "Metric.window",
            )
        if isinstance(validation_rule, TrailingPeriods) and calendar is not None:
            grain = calendar.grain_spec
            if grain.unit not in ("day", "week", "month", "quarter", "year"):
                reject(
                    "day/week/month/quarter/year periods",
                    calendar.grain,
                    "Use trailing_duration for elapsed subday windows.",
                    "Metric.window",
                )
        for index, end in enumerate(validation_ends):
            if isinstance(validation_rule, Since) and validation_rule.start >= end:
                reject(
                    "window start before each endpoint",
                    repr((validation_rule.start, end)),
                    "Choose an earlier since start.",
                    "Metric.window",
                )
            if isinstance(validation_rule, ToDate):
                native_start = (
                    native_starts.get((validation_rule, validation_cells[index][0]))
                    if native_starts is not None and validation_cells
                    else None
                )
                if native_start is not None:
                    continue
                validation_start, validation_right = _reset(
                    end, validation_rule.grain, period.timezone
                )
                if (
                    validation_cells
                    and not validation_start
                    <= validation_cells[index][0]
                    < validation_cells[index][1]
                    <= validation_right
                ):
                    reject(
                        "each nominal output bucket inside one reset period",
                        repr(validation_cells[index]),
                        "Choose an output Grain contained by the reset Grain.",
                        "Metric.window",
                    )
    result: list[ContributionBinding] = list(deferred)
    for contribution in contributions:
        rule = rules.get(contribution.occurrence)
        if rule is None or contribution.time is None:
            result.append(replace(contribution, window_rule=rule))
            continue
        # Native keys need not bound the selected instants. In particular a
        # repeated civil hour may localize to a key after the explicit end.
        # Consume the captured endpoint without imposing TimeCell containment.
        cells = calendar.cells if calendar is not None else ()
        endpoints = tuple(cell[3] for cell in cells) if cells else (period.end,)
        for index, end in enumerate(endpoints):
            native_start = (
                native_starts.get((rule, calendar.cells[index][0]))
                if native_starts is not None and calendar is not None
                else None
            )
            start: datetime | None
            if isinstance(rule, AllHistory):
                start = None
            elif isinstance(rule, Since):
                start = rule.start
            elif isinstance(rule, TrailingDuration):
                start = end.astimezone(timezone.utc) - rule.duration
            elif isinstance(rule, ToDate):
                if native_start is not None and calendar is not None and not calendar.actual_only:
                    start = native_start
                else:
                    start, right = _reset(end, rule.grain, period.timezone)
                    if cells and not start <= cells[index][0] < cells[index][1] <= right:
                        reject(
                            "each nominal output bucket inside one reset period",
                            repr(cells[index]),
                            "Choose an output Grain contained by the reset Grain.",
                            "Metric.window",
                        )
                    if native_start is not None:
                        start = native_start
            else:
                assert isinstance(rule, TrailingPeriods)
                if calendar is None or any(cell[:2] != cell[2:] for cell in cells):
                    reject(
                        "complete calendar buckets for trailing_periods",
                        "missing or partial report buckets",
                        "Observe a CalendarAxis with complete periods.",
                        "Metric.window",
                    )
                if native_start is not None:
                    start = native_start
                else:
                    grid = calendar
                    grain = grid.grain_spec
                    if grain.unit not in ("day", "week", "month", "quarter", "year"):
                        reject(
                            "day/week/month/quarter/year periods",
                            grid.grain,
                            "Use trailing_duration for elapsed subday windows.",
                            "Metric.window",
                        )
                    local: datetime = cells[index][0].astimezone(
                        normalize_timezone(period.timezone).tz
                    )
                    for _ in range(rule.count - 1):
                        local = _previous(local, grain)
                    start = instant(local.replace(tzinfo=None), period.timezone)
            if start is not None and start >= end:
                reject(
                    "window start before each endpoint",
                    repr((start, end)),
                    "Choose an earlier since start.",
                    "Metric.window",
                )
            result.append(
                replace(
                    contribution,
                    occurrence=(*contribution.occurrence, index),
                    logical_occurrence=contribution.occurrence,
                    window_rule=rule,
                    window_key=(calendar.cells[index][0] if calendar is not None else index)
                    if contribution.calendars
                    else None,
                    period=ObservationPeriod(
                        None if start is None else anchor_from_datetime(start),
                        anchor_from_datetime(end),
                        period.timezone,
                    ),
                )
            )
    return tuple(result)
