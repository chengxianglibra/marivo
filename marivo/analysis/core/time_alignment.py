"""Closed frozen time-key captures and pure complete-grid evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Annotated, Literal, TypeAlias
from zoneinfo import ZoneInfo

from pydantic import Field

from marivo._temporal import TimeScope, canonical_key
from marivo.analysis.core.time_grid import BoundTimeGrid
from marivo.analysis.datasets.errors import DatasetConstructionError
from marivo.datasource.timezone import parse_timezone
from marivo.refs import Ref, SemanticKind, TemporalSetKind


def _invalid(received: str) -> DatasetConstructionError:
    return DatasetConstructionError(
        expected="complete original grids and a closed frozen time-key mapping",
        received=received,
        repair="Use mv.ordinal(), mv.weekday(), or a holiday constructor with certified whole-day occurrences in each grid's boundary timezone.",
        location="analysis.time_alignment",
    )


@dataclass(frozen=True, slots=True)
class OccurrenceTimeCapture:
    temporal_set: Ref[TemporalSetKind]
    key: str | int | float | bool
    start: date | datetime
    end: date | datetime
    boundary_timezone: str
    snapshot_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.temporal_set) is not Ref
            or self.temporal_set.kind is not SemanticKind.TEMPORAL_SET
            or type(self.snapshot_digest) is not str
            or not self.snapshot_digest
            or type(self.start) is not type(self.end)
            or self.start >= self.end
        ):
            raise _invalid("occurrence lacks exact certified identity or half-open bounds")
        try:
            canonical_key(self.key)
            zone = parse_timezone(self.boundary_timezone)[1]
        except (TypeError, ValueError, KeyError) as error:
            raise _invalid("invalid occurrence key or boundary timezone") from error
        if isinstance(self.start, datetime):
            assert isinstance(self.end, datetime)
            if any(
                bound.utcoffset() is None or bound.astimezone(zone).time() != time()
                for bound in (self.start, self.end)
            ):
                raise _invalid("holiday occurrence must have aware whole-day midnight bounds")
            # JSON restores offset tzinfo objects; use the certification's UTC
            # representation on both paths so repr-based definition hashes agree.
            object.__setattr__(self, "start", self.start.astimezone(ZoneInfo("UTC")))
            object.__setattr__(self, "end", self.end.astimezone(ZoneInfo("UTC")))
        elif type(self.start) is not date:
            raise _invalid("occurrence bounds must be civil dates or aware whole-day timestamps")

    @property
    def dates(self) -> tuple[date, date]:
        zone = parse_timezone(self.boundary_timezone)[1]
        start = (
            self.start.astimezone(zone).date() if isinstance(self.start, datetime) else self.start
        )
        end = self.end.astimezone(zone).date() if isinstance(self.end, datetime) else self.end
        return start, end


def capture_occurrence(scope: TimeScope) -> OccurrenceTimeCapture:
    if not isinstance(scope, TimeScope) or scope.kind != "temporal_occurrence":
        received = (
            f"TimeScope(kind={scope.kind!r})"
            if isinstance(scope, TimeScope)
            else type(scope).__name__
        )
        raise _invalid(
            f"holiday input must be a certified TemporalSet occurrence TimeScope; received {received}"
        )
    return OccurrenceTimeCapture(
        scope.temporal_set,
        scope.key,
        scope.start,
        scope.end,
        scope.boundary_timezone,
        scope.snapshot_digest,
    )


@dataclass(frozen=True, slots=True)
class OrdinalTimeMapping:
    kind: Literal["ordinal"] = "ordinal"


@dataclass(frozen=True, slots=True)
class WeekdayTimeMapping:
    kind: Literal["weekday"] = "weekday"


@dataclass(frozen=True, slots=True)
class HolidayTimeMapping:
    current: OccurrenceTimeCapture
    baseline: OccurrenceTimeCapture
    kind: Literal["holiday"] = "holiday"


@dataclass(frozen=True, slots=True)
class HolidayThenWeekdayTimeMapping:
    current: OccurrenceTimeCapture
    baseline: OccurrenceTimeCapture
    kind: Literal["holiday_then_weekday"] = "holiday_then_weekday"


TimeKeyMapping: TypeAlias = Annotated[
    OrdinalTimeMapping | WeekdayTimeMapping | HolidayTimeMapping | HolidayThenWeekdayTimeMapping,
    Field(discriminator="kind"),
]


def evaluate_time_keys(
    grid: BoundTimeGrid, mapping: TimeKeyMapping, side: Literal["current", "baseline"]
) -> tuple[tuple[str, str], ...]:
    """Evaluate only captured authority; never read sources or a Catalog."""
    if type(mapping) is OrdinalTimeMapping and mapping.kind == "ordinal":
        return tuple((cell.identity, f"ordinal:{i}") for i, cell in enumerate(grid.cells, 1))
    if grid.grain_token != "day" and not (
        grid.calendar_snapshot is not None and grid.grain_token.endswith("::day")
    ):
        raise _invalid(f"{mapping.kind} requires single-day grain; received {grid.grain_token!r}")
    zone = parse_timezone(grid.boundary_timezone)[1]
    dates = tuple(cell.original_start.astimezone(zone).date() for cell in grid.cells)
    keys: list[str] = []
    if type(mapping) is WeekdayTimeMapping and mapping.kind == "weekday":
        counts: dict[int, int] = {}
        for day in dates:
            weekday = day.isoweekday()
            counts[weekday] = counts.get(weekday, 0) + 1
            keys.append(f"weekday:{weekday}:{counts[weekday]}")
    elif isinstance(mapping, (HolidayTimeMapping, HolidayThenWeekdayTimeMapping)) and (
        (type(mapping) is HolidayTimeMapping and mapping.kind == "holiday")
        or (
            type(mapping) is HolidayThenWeekdayTimeMapping
            and mapping.kind == "holiday_then_weekday"
        )
    ):
        occurrence = mapping.current if side == "current" else mapping.baseline
        if occurrence.boundary_timezone != grid.boundary_timezone:
            raise _invalid(
                f"{side} occurrence timezone {occurrence.boundary_timezone!r} differs from grid {grid.boundary_timezone!r}"
            )
        start, end = occurrence.dates
        for day in dates:
            offset = (day - start).days
            if isinstance(mapping, HolidayTimeMapping):
                keys.append(f"holiday:{offset}")
            elif start <= day < end:
                keys.append(f"holiday_then_weekday:holiday:{offset}")
            else:
                direction = "before" if day < start else "after"
                distance = (start - day).days - 1 if day < start else (day - end).days
                keys.append(
                    f"holiday_then_weekday:{direction}:{day.isoweekday()}:{distance // 7 + 1}"
                )
    else:
        raise _invalid("unknown or inconsistent frozen time-key kind")
    if len(set(keys)) != len(keys):
        raise _invalid(f"{mapping.kind} maps multiple original buckets to one aligned key")
    return tuple(zip((cell.identity for cell in grid.cells), keys, strict=True))
