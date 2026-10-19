"""Bind source-free window rules to exact observation endpoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from marivo._definition.expressions import DeclaredMetricRef, MetricNode, MetricWindow
from marivo._definition.graph import DefinitionNode, children
from marivo._definition.types import ExactRef
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
    PeriodCalendarSnapshotV1,
    anchor_from_datetime,
    semantic_grain,
)
from marivo._temporal_grid import _floor, _next, instant
from marivo._timezones import parse_timezone
from marivo.analysis.core.model import reject

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ContributionBinding, ObservationPeriod
    from marivo.analysis.dsl.temporal import CalendarAxis


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
    local = end.astimezone(parse_timezone(zone)[1])
    left = _floor(local, grain)
    if left == local:
        # Boundary ownership is exact: the period just completed owns this endpoint.
        if grain.unit in ("second", "minute", "hour"):
            width = {"second": 1, "minute": 60, "hour": 3600}[grain.unit] * (grain.count or 1)
            left -= timedelta(seconds=width)
        else:
            left = _previous(left, grain)
    return instant(left.replace(tzinfo=None), zone), instant(
        _next(left, grain).replace(tzinfo=None), zone
    )


def bind_windows(
    contributions: tuple[ContributionBinding, ...],
    metrics: tuple[tuple[str, MetricNode], ...],
    declared: dict[ExactRef, MetricNode],
    period: ObservationPeriod | None,
    calendar: CalendarAxis | None,
    calendar_snapshots: tuple[PeriodCalendarSnapshotV1, ...] = (),
) -> tuple[ContributionBinding, ...]:
    """Expand each window usage into independent source reads per output endpoint."""
    from marivo.analysis.binding.observe import ObservationPeriod

    rules: dict[tuple[int, ...], ObservationWindow] = {}

    def visit(node: DefinitionNode, path: tuple[int, ...], rule: ObservationWindow | None) -> None:
        if isinstance(node, DeclaredMetricRef):
            visit(declared[node.ref], (*path, 0), rule)
            return
        if isinstance(node, MetricWindow):
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
                    and calendar.grid.calendar_snapshot is not None
                    and calendar.grid.calendar_snapshot.calendar_ref == snapshot.calendar_ref
                    and calendar.grid.snapshot_digest != snapshot.snapshot_digest
                ):
                    reject(
                        "one certified snapshot for the output and reset calendar",
                        repr((calendar.grid.snapshot_digest, snapshot.snapshot_digest)),
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
    if calendar is None and any(
        item.occurrence in rules and item.calendars for item in contributions
    ):
        reject(
            "an explicit during CalendarAxis for window endpoint binding",
            "a CalendarAxis captured only in a Population",
            "Bind the window output with during=scope.by(grain); Population calendar samples do not supply window endpoints.",
            "Metric.window",
        )
    if period is None:
        reject(
            "an explicit window endpoint",
            "during=None",
            "Pass a TimeScope or CalendarAxis to observe.",
            "Metric.window",
        )
    result: list[ContributionBinding] = []
    for contribution in contributions:
        rule = rules.get(contribution.occurrence)
        if rule is None:
            result.append(contribution)
            continue
        cells = calendar.grid.cells if calendar is not None else ()
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
                grid = calendar.grid
                if grid.calendar_snapshot is not None:
                    captured = grid.grain_spec
                    assert captured.calendar is not None and captured.level is not None
                    grain = semantic_grain(
                        calendar=captured.calendar,
                        level=captured.level,
                        snapshot=grid.calendar_snapshot,
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
                            grid.grain_token,
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
                    window_key=index if calendar is not None else None,
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
