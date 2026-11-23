"""Project a captured time grid onto a definition coordinate."""

from marivo._definition.types import CalendarCoordinate, CalendarOrigin, _merge_time_ranges
from marivo._kernel.temporal_grid import BoundTimeGrid
from marivo._kernel.time_types import anchor_from_datetime


def calendar_coordinate(grid: BoundTimeGrid) -> CalendarCoordinate:
    """Capture the exact selected cells and their direct offset correspondence."""
    return CalendarCoordinate(
        grid.identity,
        grid.grain_spec,
        grid.boundary_timezone,
        tuple(
            (
                anchor_from_datetime(c.original_start),
                anchor_from_datetime(c.original_end),
                anchor_from_datetime(c.start),
                anchor_from_datetime(c.end),
            )
            for c in grid.cells
        ),
        CalendarOrigin(
            calendar_coordinate(grid.origin.grid),
            grid.origin.kind,
            grid.origin.by,
            grid.origin.entries,
        )
        if grid.origin is not None
        else None,
        civil_bounds=grid.civil_bounds,
        ranges=_merge_time_ranges(
            tuple(
                (anchor_from_datetime(cell.start), anchor_from_datetime(cell.end))
                for cell in grid.cells
            )
        ),
    )
