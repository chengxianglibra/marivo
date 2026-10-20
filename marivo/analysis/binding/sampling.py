"""Finite declared sampling expectations for each contribution occurrence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from marivo._definition.types import (
    CalendarCoordinate,
    FieldRead,
    IntervalCoordinate,
    coordinate_read_keys,
)
from marivo._temporal import builtin_grain, time_scope
from marivo._temporal_grid import bind_grid
from marivo.analysis.core.model import reject
from marivo.semantic.ir import DatetimeParse, StrptimeParse, TimestampParse

if TYPE_CHECKING:
    from marivo.analysis.binding.observe import ContributionBinding
    from marivo.semantic._compiled_state import CompiledSemanticState


from marivo._definition.observations import SampleCoverage as SampleCoverage


def sampling_coverage(
    contribution: ContributionBinding,
    state: CompiledSemanticState,
    *,
    calendar: CalendarCoordinate | None,
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
        parse = dimension.parse if dimension is not None else None
        sampling = (
            parse.sample_interval
            if isinstance(parse, (DatetimeParse, TimestampParse, StrptimeParse))
            else None
        )
        if sampling is not None:
            period = contribution.period
            if period is None or period.start is None:
                reject(
                    "a finite period for declared sample coverage",
                    read.field.ref.path,
                    "Use a finite during scope and a finite window rule.",
                    "session.observe",
                )
            grain = builtin_grain(sampling.unit, count=sampling.count)
            grid = bind_grid(
                time_scope(start=period.start, end=period.end),
                grain,
                report_timezone=period.timezone,
                explicit_timezone=period.timezone,
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
