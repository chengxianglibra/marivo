"""Bind source-free window rules to exact observation endpoints."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from marivo._definition.expressions import DeclaredMetricRef, MetricNode, MetricWindow
from marivo._definition.graph import DefinitionNode, children
from marivo._definition.relations import QueryKeys
from marivo._definition.types import ExactRef
from marivo._definition.windows import (
    AllHistory,
    ObservationWindow,
    Since,
    ToDate,
    TrailingDuration,
    TrailingPeriods,
)
from marivo._temporal import Grain, anchor_from_datetime, builtin_grain
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
) -> tuple[ContributionBinding, ...]:
    """Expand each window usage into independent source reads per output endpoint."""
    from marivo.analysis.binding.observe import ObservationPeriod

    rules: dict[tuple[int, ...], ObservationWindow] = {}

    def visit(node: DefinitionNode, path: tuple[int, ...], rule: ObservationWindow | None) -> None:
        if isinstance(node, QueryKeys):
            # Captured Query inputs own their declarations and observation
            # endpoints; this invocation binds only its current Metric scope.
            return
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
                    from marivo._temporal import semantic_grain
                    from marivo.refs import ref

                    calendar_path, level = grid.grain_token.split("::", 1)
                    grain = semantic_grain(
                        calendar=ref.period_calendar(calendar_path),
                        level=level,
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
                    grain = builtin_grain(grid.grain_token)
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
                    period=ObservationPeriod(
                        None if start is None else anchor_from_datetime(start),
                        anchor_from_datetime(end),
                        period.timezone,
                    ),
                )
            )
    return tuple(result)
