"""Bind source-free window rules to exact observation endpoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from marivo._definition.expressions import (
    DeclaredMetricRef,
    EntityCount,
    MetricNode,
    MetricWindow,
    SourceAggregate,
    SourceWeightedMean,
)
from marivo._definition.graph import DefinitionNode, children
from marivo._definition.types import CalendarCoordinate, ExactRef
from marivo._definition.windows import (
    AllHistory,
    ObservationWindow,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
)
from marivo._temporal import (
    Grain,
    PeriodCalendarSnapshotV2,
    anchor_from_datetime,
    semantic_grain,
    session_time_scope,
)
from marivo._temporal_grid import _floor, _next, bind_grid, instant
from marivo._timezones import parse_timezone
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ContributionBinding, ObservationPeriod


def _previous(value: datetime, grain: Grain) -> datetime:
    """The predecessor in civil calendar space, without epsilon arithmetic."""
    unit, count = grain.unit, grain.count
    assert unit is not None and count is not None
    if unit in ("day", "week"):
        return value - timedelta(days=(1 if unit == "day" else 7) * count)
    months = {"month": 1, "quarter": 3, "year": 12}[unit] * count
    ordinal = value.year * 12 + value.month - 1 - months
    return value.replace(year=ordinal // 12, month=ordinal % 12 + 1)


def _periods(grain: Grain, zone: str) -> tuple[tuple[datetime, datetime], ...]:
    snapshot = grain._snapshot
    if snapshot is None or snapshot.boundary_timezone != zone:
        reject(
            "a captured calendar in the observation timezone",
            repr(grain),
            "Use a certified Grain matching the observation timezone.",
            "Metric.window",
        )
    if grain.level == "day":
        cursor = snapshot.coverage[0]
        result: list[tuple[datetime, datetime]] = []
        while cursor < snapshot.coverage[1]:
            right = cursor + timedelta(days=1)
            result.append((instant(cursor, zone), instant(right, zone)))
            cursor = right
        return tuple(result)
    return tuple(
        sorted(
            (instant(p.start_date, zone), instant(p.end_date, zone))
            for p in snapshot.periods
            if p.level_name == grain.level
        )
    )


def _reset(end: datetime, grain: Grain, zone: str) -> tuple[datetime, datetime]:
    if grain.kind == "semantic":
        matches = tuple(
            (left, right) for left, right in _periods(grain, zone) if left < end <= right
        )
        if len(matches) != 1:
            reject(
                "one certified reset period at the endpoint",
                end.isoformat(),
                "Extend calendar coverage or choose a uniquely covering Grain.",
                "Metric.window",
            )
        return matches[0]
    if grain.unit in ("second", "minute", "hour"):
        width = timedelta(
            seconds={"second": 1, "minute": 60, "hour": 3600}[grain.unit] * (grain.count or 1)
        )
        grid = bind_grid(
            session_time_scope(
                anchor_from_datetime(end - width), anchor_from_datetime(end + width), zone
            ),
            grain,
            report_timezone=zone,
            explicit_timezone=zone,
        )
        return next(
            (cell.original_start, cell.original_end)
            for cell in grid.cells
            if cell.original_start < end <= cell.original_end
        )
    local = end.astimezone(parse_timezone(zone)[1])
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
    if not is_role and not is_sample:
        return
    calendars = tuple(axis for _, axis in contribution.calendars)
    if any(isinstance(axis, CalendarCoordinate) and not axis.cells for axis in calendars):
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
    if (
        isinstance(rule, ToDate)
        and rule.grain.kind == "builtin"
        and rule.grain.unit in ("second", "minute", "hour")
    ):
        fail(f"{path}: subday reset {rule.grain!r}")
    for axis in calendars:
        if isinstance(axis, CalendarCoordinate):
            if axis.grain_spec.kind == "builtin" and axis.grain_spec.unit in (
                "second",
                "minute",
                "hour",
            ):
                fail(f"{path}: subday output {axis.grain}")
            bounds = tuple(bound for cell in axis.cells for bound in cell[2:])
        else:
            bounds = (axis.start, axis.end)
        zone = parse_timezone(axis.timezone)[1]
        if any(bound.astimezone(zone).time() != datetime.min.time() for bound in bounds):
            fail(f"{path}: non-midnight calendar boundary")
    period = contribution.period
    if period is not None:
        zone = parse_timezone(period.timezone)[1]
        if any(
            bound is not None and bound.astimezone(zone).time() != datetime.min.time()
            for bound in (period.start, period.end)
        ):
            fail(f"{path}: non-midnight contribution boundary")


def bind_windows(
    contributions: tuple[ContributionBinding, ...],
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    period: ObservationPeriod | None,
    calendar: CalendarCoordinate | None,
    calendar_snapshots: tuple[PeriodCalendarSnapshotV2, ...] = (),
) -> tuple[ContributionBinding, ...]:
    """Expand each window usage into independent source reads per output endpoint."""
    from marivo.analysis.binding.observe import ObservationPeriod

    rules: dict[tuple[int, ...], ObservationWindow] = {}

    def has_fact(node: DefinitionNode) -> bool:
        if isinstance(node, DeclaredMetricRef):
            return has_fact(declared[node.ref])
        return isinstance(node, (SourceAggregate, SourceWeightedMean, EntityCount)) or any(
            has_fact(edge.child) for edge in children(node)
        )

    def visit(node: DefinitionNode, path: tuple[int, ...], rule: ObservationWindow | None) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*path, 0), rule)
            return
        if isinstance(node, MetricWindow):
            if not has_fact(node.operand):
                reject(
                    "fact contributions for a window",
                    "a count-only or source-free formula",
                    "Apply windows to fact Metrics; identity counts retain their captured domain.",
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
            if isinstance(rule, ToDate) and rule.grain.kind == "semantic":
                grain = rule.grain
                snapshot = next(
                    (item for item in calendar_snapshots if item.calendar_ref == grain.calendar),
                    None,
                )
                if snapshot is None:
                    reject(
                        "a current certified calendar in the completed Session scope",
                        repr(grain),
                        "Inspect calendar.details() and catalog.readiness(refs=[calendar.ref]); certify the calendar before creating a new Session handle.",
                        "Metric.window",
                    )
                captured = grain._snapshot
                if captured is not None and captured.snapshot_digest != snapshot.snapshot_digest:
                    reject(
                        "the same certified snapshot for the explicit Grain and Session",
                        f"{grain!r}: captured={captured.snapshot_digest}; Session={snapshot.snapshot_digest}",
                        "Use ms.calendar_grain(...) for a scope lookup, or rebuild the Grain and observation in a new Session handle after certification.",
                        "Metric.window",
                    )
                if grain.level not in ("day", *snapshot.levels):
                    reject(
                        f"a calendar level in {('day', *snapshot.levels)!r}",
                        repr(grain.level),
                        "Use a level declared by the consumed calendar.",
                        "Metric.window",
                    )
                if (
                    calendar is not None
                    and calendar.snapshot is not None
                    and calendar.snapshot.calendar_ref == snapshot.calendar_ref
                    and calendar.snapshot_digest != snapshot.snapshot_digest
                ):
                    reject(
                        "one certified snapshot for the output and reset calendar",
                        repr((calendar.snapshot_digest, snapshot.snapshot_digest)),
                        "Build the output axis and Metric in the same completed Session scope.",
                        "Metric.window",
                    )
                assert grain.calendar is not None and grain.level is not None
                rule = ToDate(
                    semantic_grain(calendar=grain.calendar, level=grain.level, snapshot=snapshot)
                )
        if rule is not None:
            rules[path] = rule
        for edge in children(node):
            visit(edge.child, (*path, edge.index), rule)

    for index, (_, node) in enumerate(metrics):
        visit(node, (index,), None)
    if not rules:
        return contributions
    if calendar is not None and not calendar.cells:
        # Retain source type bindings, without inventing an endpoint. Exact
        # calendar membership makes these schema-bearing reads empty.
        return contributions
    if period is None:
        reject(
            "an explicit window endpoint",
            "no output time axis",
            "Pass by=(scope,) or by=(calendar_axis,) to session.observe(...).",
            "Metric.window",
        )
    result: list[ContributionBinding] = []
    for contribution in contributions:
        rule = rules.get(contribution.occurrence)
        if rule is None:
            result.append(contribution)
            continue
        from marivo._temporal_grid import TimeCell

        cells = (
            tuple(
                TimeCell(str(index), *cell, cell[0] != cell[2] or cell[1] != cell[3])
                for index, cell in enumerate(calendar.cells)
            )
            if calendar is not None
            else ()
        )
        endpoints = tuple(c.end for c in cells) if cells else (period.end,)
        for index, end in enumerate(endpoints):
            start: datetime | None
            if isinstance(rule, AllHistory):
                start = None
            elif isinstance(rule, Since):
                start = rule.start
            elif isinstance(rule, TrailingDuration):
                start = end.astimezone(timezone.utc) - rule.duration
            elif isinstance(rule, ToDate):
                start, right = _reset(end, rule.grain, period.timezone)
                if (
                    cells
                    and not start
                    <= cells[index].original_start
                    < cells[index].original_end
                    <= right
                ):
                    reject(
                        "each nominal output bucket inside one reset period",
                        repr(cells[index]),
                        "Choose an output Grain contained by the reset Grain.",
                        "Metric.window",
                    )
            else:
                assert isinstance(rule, TrailingPeriods)
                if calendar is None or any(c.partial for c in cells):
                    reject(
                        "complete calendar buckets for trailing_periods",
                        "missing or partial CalendarAxis",
                        "Observe a complete CalendarAxis.",
                        "Metric.window",
                    )
                grid = calendar
                if grid.snapshot is not None:
                    captured = grid.grain_spec
                    assert captured.calendar is not None and captured.level is not None
                    grain = semantic_grain(
                        calendar=captured.calendar,
                        level=captured.level,
                        snapshot=grid.snapshot,
                    )
                    periods = _periods(grain, period.timezone)
                    position = next(
                        (
                            i
                            for i, p in enumerate(periods)
                            if p == (cells[index].original_start, cells[index].original_end)
                        ),
                        -1,
                    )
                    if position < rule.count - 1:
                        reject(
                            "certified predecessor periods",
                            str(rule.count),
                            "Extend the certified calendar coverage.",
                            "Metric.window",
                        )
                    start = periods[position - rule.count + 1][0]
                else:
                    grain = grid.grain_spec
                    if grain.unit not in ("day", "week", "month", "quarter", "year"):
                        reject(
                            "day/week/month/quarter/year periods",
                            grid.grain,
                            "Use trailing_duration for elapsed subday windows.",
                            "Metric.window",
                        )
                    local = cells[index].original_start.astimezone(
                        parse_timezone(period.timezone)[1]
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
                    window_key=index if contribution.calendars else None,
                    window_calendar_digest=(
                        rule.grain._snapshot.snapshot_digest
                        if isinstance(rule, ToDate) and rule.grain._snapshot is not None
                        else None
                    ),
                    period=ObservationPeriod(
                        None if start is None else anchor_from_datetime(start),
                        anchor_from_datetime(end),
                        period.timezone,
                    ),
                )
            )
    return tuple(result)
