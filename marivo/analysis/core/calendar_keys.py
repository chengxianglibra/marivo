"""Physical keys for frozen time coordinates, independent of selection order."""

from datetime import datetime

import pandas as pd

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo.analysis.core.data_types import ScalarDataType, TimestampDataType


def time_key_type(
    axis: CalendarCoordinate | IntervalCoordinate,
) -> TimestampDataType | ScalarDataType:
    """Calendar identities are nominal UTC starts; an interval retains its unit key."""
    return (
        TimestampDataType("us", "UTC")
        if isinstance(axis, CalendarCoordinate)
        else ScalarDataType("int64")
    )


def time_keys(axis: CalendarCoordinate | IntervalCoordinate) -> tuple[datetime | int, ...]:
    """Keep the complete selected directory's order without renumbering its keys."""
    return tuple(cell[0] for cell in axis.cells) if isinstance(axis, CalendarCoordinate) else (0,)


def time_key_series(axis: CalendarCoordinate | IntervalCoordinate) -> pd.Series:
    """Construct exact Arrow-backed directory keys, including an empty selection."""
    return pd.Series(
        time_keys(axis),
        dtype="timestamp[us, tz=UTC][pyarrow]"
        if isinstance(axis, CalendarCoordinate)
        else "int64[pyarrow]",
    )
