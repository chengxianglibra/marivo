"""Physical keys for frozen time coordinates, independent of selection order."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa

from marivo._definition.correspondence import interval_key_pairs
from marivo._definition.types import CalendarCoordinate, IntervalCoordinate
from marivo.analysis.model.data_types import ScalarDataType, TimestampDataType
from marivo.analysis.model.errors import reject

if TYPE_CHECKING:
    from marivo._definition.observations import ContributionBinding

_CALENDAR_DTYPE = pd.ArrowDtype(pa.timestamp("us", tz="UTC"))
_INTERVAL_DTYPE = pd.ArrowDtype(pa.int64())


def window_endpoint_keys(
    contributions: tuple[ContributionBinding, ...],
) -> tuple[tuple[tuple[int, ...], tuple[datetime, ...]], ...]:
    """Keep each logical window's selected native keys, independently of facts."""
    endpoints: dict[tuple[int, ...], list[datetime]] = {}
    for contribution in contributions:
        occurrence = contribution.window_group_id
        if (
            contribution.window_rule is not None
            and occurrence is not None
            and isinstance(contribution.window_key, datetime)
        ):
            endpoints.setdefault(occurrence, []).append(contribution.window_key)
    return tuple((occurrence, tuple(dict.fromkeys(keys))) for occurrence, keys in endpoints.items())


def require_window_endpoints(
    keys: pd.Series,
    endpoints: tuple[tuple[tuple[int, ...], tuple[datetime, ...]], ...],
) -> None:
    """Reject undefined window positions before ordinary empty-result filling."""
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
    return (
        tuple(cell[0] for cell in axis.cells)
        if isinstance(axis, CalendarCoordinate)
        else tuple(range(len(axis.bounds)))
    )


def time_key_series(axis: CalendarCoordinate | IntervalCoordinate) -> pd.Series:
    """Construct exact Arrow-backed directory keys, including an empty selection."""
    return pd.Series(
        time_keys(axis),
        dtype=_CALENDAR_DTYPE if isinstance(axis, CalendarCoordinate) else _INTERVAL_DTYPE,
    )


def match_time_keys(
    values: pd.Series,
    source: CalendarCoordinate | IntervalCoordinate,
    target: CalendarCoordinate | IntervalCoordinate,
) -> pd.Series:
    """Translate represented keys between compatible axes, marking unmatched rows as NA."""
    if isinstance(target, IntervalCoordinate):
        assert isinstance(source, IntervalCoordinate)
        pairs = interval_key_pairs(target, source)
        if pairs is not None:
            ordinals = pd.Series({old: new for new, old in pairs}, dtype=_INTERVAL_DTYPE)
            values = values.map(ordinals)
        return values.astype(_INTERVAL_DTYPE)
    assert isinstance(source, CalendarCoordinate)
    keys = values.astype(_CALENDAR_DTYPE)
    return keys if source.cells == target.cells else keys.where(keys.isin(time_keys(target)))
