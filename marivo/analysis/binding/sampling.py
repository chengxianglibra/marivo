"""Finite declared sampling expectations for each contribution occurrence."""

from __future__ import annotations

from collections.abc import Mapping
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
from marivo.analysis.core.time_authority import SourceTimeAuthority

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
    for i, read in enumerate(contribution.coordinates):
        while f"key.{offset}" in calendar_slots:
            offset += 1
        width = len(coordinate_read_keys(read))
        if i < contribution.output_axis_count or not isinstance(read, FieldRead):
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
            if outer_calendar is not None and contribution.window_key is None:
                output_slot, output_calendar = outer_calendar
                result.extend(
                    SampleCoverage(
                        f"key.{offset}",
                        tuple(point for point in points if cell[2] <= point < cell[3]),
                        index,
                        output_slot,
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
                    )
                )
        offset += width
    return tuple(result)
