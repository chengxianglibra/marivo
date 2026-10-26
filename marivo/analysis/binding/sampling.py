"""Finite declared sampling expectations for each contribution occurrence."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone
from typing import TYPE_CHECKING

from marivo._definition.types import (
    CalendarCoordinate,
    FieldRead,
    IntervalCoordinate,
    coordinate_read_keys,
)
from marivo._temporal import builtin_grain, session_time_scope
from marivo._temporal_grid import bind_grid
from marivo.analysis.core.model import reject
from marivo.analysis.core.time_authority import SourceTimeAuthority, time_zone

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ContributionBinding
    from marivo.semantic._compiled_state import CompiledSemanticState


from marivo._definition.observations import SampleCoverage as SampleCoverage


def sampling_coverage(
    contribution: ContributionBinding,
    state: CompiledSemanticState,
    *,
    calendar: CalendarCoordinate | None,
    authorities: Mapping[str, SourceTimeAuthority],
    wall_paths: set[str] | frozenset[str] = frozenset(),
) -> tuple[SampleCoverage, ...]:
    result: list[SampleCoverage] = []
    offset = 0
    calendar_slots = dict(contribution.calendars)
    if not calendar_slots and calendar is not None:
        offset = 1
    outer_calendar_count = len(calendar_slots) - sum(
        isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
        for axis in contribution.sample_axes
    )
    outer_calendar = contribution.calendars[0] if outer_calendar_count else None
    sample_rules = tuple(
        layer
        for layer in contribution.sample_layers
        for axis in layer.axes
        if not isinstance(axis, (CalendarCoordinate, IntervalCoordinate))
    )
    names: list[tuple[str, str]] = []
    name_slot = 0
    for read in contribution.coordinates:
        while f"key.{name_slot}" in calendar_slots:
            names.append((f"key.{name_slot}", "period"))
            name_slot += 1
        for field in coordinate_read_keys(read):
            names.append((f"key.{name_slot}", field))
            name_slot += 1
    for i, read in enumerate(contribution.coordinates):
        while f"key.{offset}" in calendar_slots:
            offset += 1
        width = len(coordinate_read_keys(read))
        if i < contribution.output_axis_count or not isinstance(read, FieldRead):
            offset += width
            continue
        layer = sample_rules[i - contribution.output_axis_count] if sample_rules else None
        if layer is not None and not layer.requires_coverage:
            offset += width
            continue
        dimension = state.registry.dimensions.get(read.field.ref.path)
        sampling = dimension.sample_interval if dimension is not None else None
        if sampling is not None:
            period = contribution.period
            if period is None or period.start is None:
                reject(
                    "a finite period for declared sample coverage",
                    read.field.ref.path,
                    "Pass by=(scope,) with a finite TimeScope and use a bounded window rule.",
                    "session.observe",
                )
            grain = builtin_grain(sampling.unit, count=sampling.count)
            authority = authorities[read.field.ref.path]
            source_zone = "UTC" if authority.kind == "instant" else authority.read_timezone
            if source_zone is None:
                reject(
                    "a timestamp source clock for subday sampling",
                    read.field.ref.path,
                    "Use a real timestamp TimeField with a representable sample_interval.",
                    "session.observe",
                )
            grid = bind_grid(
                session_time_scope(period.start, period.end, source_zone),
                grain,
                report_timezone=source_zone,
                explicit_timezone=source_zone,
            )
            # A clipped first interval owns its declared original sample instant.
            # Only instants inside the exact consumed half-open period are required.
            points = tuple(
                cell.original_start
                for cell in grid.cells
                if period.start <= cell.original_start < period.end
            )
            wall_timezone = source_zone if read.field.ref.path in wall_paths else None
            if wall_timezone is not None:
                # Both repeated instants name one raw clock. A skipped clock
                # has no scheduled sample; real grid instants exclude it.
                local_zone = time_zone(wall_timezone)
                points = tuple(
                    dict.fromkeys(
                        later
                        for point in points
                        for later in (
                            point.astimezone(local_zone).replace(fold=1).astimezone(timezone.utc),
                        )
                        if period.start <= later < period.end
                    )
                )
            if outer_calendar is not None and contribution.window_key is None:
                output_slot, output_calendar = outer_calendar
                result.extend(
                    SampleCoverage(
                        f"key.{offset}",
                        tuple(point for point in points if cell[2] <= point < cell[3]),
                        index,
                        output_slot,
                        wall_timezone,
                        contribution.metric,
                        read.field.ref.path,
                        layer.reducer if layer is not None else "",
                        layer.occurrence if layer is not None else (),
                        type(period)(cell[2], cell[3], period.timezone),
                        tuple(names),
                    )
                    for index, cell in enumerate(
                        output_calendar.cells
                        if isinstance(output_calendar, CalendarCoordinate)
                        else (
                            (
                                output_calendar.start,
                                output_calendar.end,
                                output_calendar.start,
                                output_calendar.end,
                            ),
                        )
                    )
                )
            else:
                result.append(
                    SampleCoverage(
                        f"key.{offset}",
                        points,
                        contribution.window_key,
                        outer_calendar[0] if outer_calendar is not None else "key.0",
                        wall_timezone,
                        contribution.metric,
                        read.field.ref.path,
                        layer.reducer if layer is not None else "",
                        layer.occurrence if layer is not None else (),
                        period,
                        tuple(names),
                    )
                )
        offset += width
    return tuple(result)
