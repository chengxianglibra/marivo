"""Physical keys for frozen time coordinates, independent of selection order."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo.analysis.core.data_types import ScalarDataType, TimestampDataType

if TYPE_CHECKING:
    from marivo._definition.observations import ContributionBinding


def window_endpoint_keys(
    contributions: tuple[ContributionBinding, ...],
) -> tuple[tuple[tuple[int, ...], tuple[datetime, ...]], ...]:
    """Keep each logical window's selected native keys, independently of facts."""
    endpoints: dict[tuple[int, ...], list[datetime]] = {}
    for contribution in contributions:
        if contribution.window_rule is not None and isinstance(contribution.window_key, datetime):
            occurrence = contribution.logical_occurrence or contribution.occurrence
            endpoints.setdefault(occurrence, []).append(contribution.window_key)
    return tuple((occurrence, tuple(dict.fromkeys(keys))) for occurrence, keys in endpoints.items())


def require_window_endpoints(
    keys: pd.Series,
    endpoints: tuple[tuple[tuple[int, ...], tuple[datetime, ...]], ...],
) -> None:
    """Reject undefined window positions before ordinary empty-result filling."""
    from marivo.analysis.core.model import reject

    for occurrence, selected in endpoints:
        missing = keys.loc[~keys.isin(selected)].drop_duplicates()
        if not missing.empty:
            reject(
                "a defined endpoint for every output time key of each window branch",
                f"window {occurrence}: missing endpoints {missing.head(6).tolist()}",
                "Observe the different time domains separately and explicitly align their fixed "
                "Frames, or choose a report axis supported by every contributing executor.",
                "observe.window_endpoints",
                canonical_id="session.observe",
            )


def time_key_type(
    axis: CalendarCoordinate | IntervalCoordinate,
) -> TimestampDataType | ScalarDataType:
    """Calendar identities are actual executor keys; an interval retains its unit key."""
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
