"""Closed physical input to a source reduction."""

from dataclasses import dataclass
from datetime import datetime

from marivo._definition.expressions import EntityCount, SourceAggregate, SourceWeightedMean
from marivo._definition.observations import CalendarGrouping
from marivo._definition.types import CalendarCoordinate, FieldRead, IntervalCoordinate
from marivo.analysis.model.data_types import DurationDataType
from marivo.datasource.spi import DEFAULT_BACKEND_TYPE


@dataclass(frozen=True, slots=True)
class ReductionInput:
    """Closed physical input to a source reduction; no Session or Signature."""

    node: SourceAggregate | SourceWeightedMean | EntityCount
    axes: tuple[tuple[str, str], ...]
    value_slot: str
    calendar: CalendarCoordinate | IntervalCoordinate | None = None
    time_column: str | None = None
    duration: DurationDataType | None = None
    window_key: int | datetime | None = None
    window_coordinates: tuple[tuple[str, int | datetime], ...] = ()
    preserve_sample_presence: bool = False
    calendars: tuple[tuple[str, CalendarGrouping], ...] = ()
    sampled: bool = False
    provider: str = DEFAULT_BACKEND_TYPE
    sample_calendar_reads: tuple[tuple[str, FieldRead], ...] = ()
