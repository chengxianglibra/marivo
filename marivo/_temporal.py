"""Dependency-neutral temporal values and certified period resolution.

This module deliberately has no datasource, semantic-registry, Ibis, pandas,
or analysis-session dependency.  Semantic authoring certifies a finite period
snapshot here; later analysis slices consume the exact same resolver.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast, overload, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marivo.refs import PeriodCalendarKind, Ref

_GRAIN_UNITS = frozenset({"second", "minute", "hour", "day", "week", "month", "quarter", "year"})
_JSON_SCALAR = str | int | float | bool


def _require_timezone(value: object) -> str:
    if type(value) is not str or not value:
        raise ValueError("boundary_timezone must be a non-empty IANA timezone name")
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"boundary_timezone {value!r} is not a valid IANA timezone") from exc
    return value


def canonical_key(value: object) -> _JSON_SCALAR:
    """Validate one finite JSON scalar used as a certified period key."""
    if type(value) is float and (value != value or value in {float("inf"), float("-inf")}):
        raise ValueError("period keys must be finite JSON scalars")
    if type(value) not in {str, int, float, bool}:
        raise TypeError(
            "period keys must be exact JSON scalars (str, int, float, or bool); "
            f"received {type(value).__name__}"
        )
    return cast("_JSON_SCALAR", value)


def _key_token(value: _JSON_SCALAR) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class Grain:
    """One closed aggregation grain, created through public helpers only."""

    # ``kind`` defaults to builtin so the dependency-neutral value can also be
    # consumed by the existing analysis execution helpers, which historically
    # constructed ``Grain(unit=..., count=...)`` directly.  Public callers use
    # ``builtin_grain`` or ``semantic_grain`` and never need to spell it.
    kind: Literal["builtin", "semantic"] = "builtin"
    unit: str | None = None
    count: int | None = 1
    calendar: Ref[PeriodCalendarKind] | None = None
    level: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "builtin":
            if self.unit not in _GRAIN_UNITS or type(self.count) is not int or self.count < 1:
                raise ValueError("builtin Grain requires a supported unit and count >= 1")
            if self.unit not in {"second", "minute", "hour"} and self.count != 1:
                raise ValueError(f"calendar grain {self.unit!r} only supports count == 1")
            if self.calendar is not None or self.level is not None:
                raise ValueError("builtin Grain cannot include calendar or level")
            return
        if self.kind == "semantic":
            if type(self.calendar) is not Ref or self.calendar.kind.value != "period_calendar":
                raise TypeError("semantic Grain requires Ref[period_calendar]")
            if type(self.level) is not str or not self.level:
                raise ValueError("semantic Grain requires a non-empty level")
            if self.unit is not None or self.count is not None:
                raise ValueError("semantic Grain cannot include builtin unit or count")
            return
        raise ValueError(f"unknown Grain kind {self.kind!r}")

    @property
    def is_subday(self) -> bool:
        return self.kind == "builtin" and self.unit in {"second", "minute", "hour"}

    @property
    def is_day(self) -> bool:
        return self.kind == "builtin" and self.unit == "day" and self.count == 1

    def width_seconds(self) -> int:
        """Return fixed width for builtin sub-day/day/week grains."""
        if self.kind != "builtin" or self.unit not in {
            "second",
            "minute",
            "hour",
            "day",
            "week",
        }:
            raise ValueError(
                f"Grain.width_seconds() is undefined for calendar-variable grain {self!r}"
            )
        assert self.count is not None
        return (
            self.count
            * {
                "second": 1,
                "minute": 60,
                "hour": 3600,
                "day": 86400,
                "week": 7 * 86400,
            }[self.unit]
        )

    def to_token(self) -> str:
        """Return a stable display token for metadata and diagnostics."""
        if self.kind == "builtin":
            assert self.unit is not None and self.count is not None
            return self.unit if self.count == 1 else f"{self.count}{self.unit}"
        assert self.calendar is not None and self.level is not None
        return f"{self.calendar.path}::{self.level}"

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Grain):
            return NotImplemented
        if self.kind != "builtin" or other.kind != "builtin":
            raise TypeError("semantic Grain has no fixed rank")
        assert self.unit is not None and other.unit is not None
        ranks = {
            "second": 0,
            "minute": 1,
            "hour": 2,
            "day": 3,
            "week": 4,
            "month": 5,
            "quarter": 6,
            "year": 7,
        }
        if self.is_subday and other.is_subday:
            return self.width_seconds() < other.width_seconds()
        return ranks[self.unit] < ranks[other.unit]

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Grain):
            return NotImplemented
        if self.kind != "builtin" or other.kind != "builtin":
            raise TypeError("semantic Grain has no fixed rank")
        return other < self

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Grain):
            return NotImplemented
        return not self > other

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Grain):
            return NotImplemented
        return not self < other

    def __repr__(self) -> str:
        if self.kind == "builtin":
            assert self.unit is not None and self.count is not None
            token = self.unit if self.count == 1 else f"{self.count}{self.unit}"
            return f"Grain({token!r})"
        assert self.calendar is not None and self.level is not None
        return f"Grain(calendar={self.calendar.key!r}, level={self.level!r})"


def builtin_grain(unit: str, *, count: int = 1) -> Grain:
    """Create the builtin Grain variant behind ``mv.grain``."""
    return Grain(kind="builtin", unit=unit, count=count)


def semantic_grain(*, calendar: Ref[PeriodCalendarKind], level: str) -> Grain:
    """Create the semantic Grain variant behind ``ms.calendar_grain``."""
    return Grain(kind="semantic", unit=None, count=None, calendar=calendar, level=level)


def period_calendar_definition_digest(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    boundary_timezone: str,
    coverage: tuple[str | date, str | date],
    levels: tuple[tuple[str, str], ...],
    correspondences: tuple[tuple[str, str, str], ...] = (),
    dependency_digest: str | None = None,
) -> str:
    """Return the declaration identity used to reject stale certified evidence."""
    payload = {
        "schema": "period-calendar-definition/v1",
        "calendar_ref": calendar_ref.key,
        "boundary_timezone": boundary_timezone,
        "coverage": [value.isoformat() if isinstance(value, date) else value for value in coverage],
        "levels": [list(item) for item in levels],
        "correspondences": [list(item) for item in correspondences],
        "dependency_digest": dependency_digest,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class TimeScopeContractV1(BaseModel):
    """Versioned serialization returned by :meth:`TimeScope.contract`."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_: Literal["time-scope/v1"] = Field(
        default="time-scope/v1",
        alias="schema",
    )
    kind: Literal["absolute", "calendar_period"]
    start: date | datetime
    end: date | datetime
    calendar_ref: str | None = None
    snapshot_digest: str | None = None
    boundary_timezone: str | None = None
    level: str | None = None
    key: _JSON_SCALAR | None = None

    @model_validator(mode="after")
    def _validate_contract(self) -> TimeScopeContractV1:
        if type(self.start) is not type(self.end) or self.start >= self.end:
            raise ValueError("time-scope contract bounds must have one type and be non-empty")
        provenance = (
            self.calendar_ref,
            self.snapshot_digest,
            self.boundary_timezone,
            self.level,
            self.key,
        )
        if self.kind == "absolute":
            if any(value is not None for value in provenance):
                raise ValueError("absolute time-scope contract cannot contain period provenance")
            return self
        if type(self.start) is not date:
            raise ValueError("calendar-period time-scope contract bounds must be civil dates")
        if any(type(value) is not str or not value for value in provenance[:4]) or self.key is None:
            raise ValueError("calendar-period time-scope contract requires complete provenance")
        canonical_key(self.key)
        return self

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        kwargs.setdefault("exclude_none", True)
        return cast("dict[str, object]", super().model_dump(*args, **kwargs))


class TimeScope(BaseModel):
    """One immutable public selection window shared by semantic and analysis.

    Absolute analysis callers continue to provide strict ISO strings.  Certified
    calendar lookups provide normalized civil ``date`` bounds and provenance.
    Keeping both forms on this one value type prevents a catalog result from
    becoming an unusable private temporal object at the analysis boundary.

    The interval is half-open: ``start`` is inclusive and ``end`` is exclusive.
    For date-only strings, ``end="2026-08-01"`` includes all of July and
    excludes August 1.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    start: str | datetime | date
    end: str | datetime | date
    calendar: Ref[PeriodCalendarKind] | None = None
    snapshot_digest: str | None = None
    boundary_timezone: str | None = None
    level: str | None = None
    key: _JSON_SCALAR | None = None
    ordinal: int | None = None

    @property
    def kind(self) -> Literal["absolute", "calendar_period"]:
        """Return the closed variant tag used by the current runtime."""
        return "calendar_period" if self.calendar is not None else "absolute"

    @model_validator(mode="after")
    def _validate_scope(self) -> TimeScope:
        provenance = (
            self.calendar,
            self.snapshot_digest,
            self.boundary_timezone,
            self.level,
            self.key,
            self.ordinal,
        )
        if self.calendar is None:
            if any(value is not None for value in provenance[1:]):
                raise ValueError("absolute TimeScope cannot contain semantic period provenance")
        else:
            if type(self.calendar) is not Ref or self.calendar.kind.value != "period_calendar":
                raise TypeError("period TimeScope requires Ref[period_calendar]")
            if type(self.snapshot_digest) is not str or not self.snapshot_digest:
                raise ValueError("period TimeScope requires snapshot_digest")
            if type(self.boundary_timezone) is not str or not self.boundary_timezone:
                raise ValueError("period TimeScope requires boundary_timezone")
            if type(self.level) is not str or not self.level:
                raise ValueError("period TimeScope requires level")
            if self.key is None or type(self.ordinal) is not int or self.ordinal < 0:
                raise ValueError("period TimeScope requires key and non-negative ordinal")
            canonical_key(self.key)
        return self

    def __repr__(self) -> str:
        if self.kind == "absolute":
            return f"TimeScope([{_scope_bound_text(self.start)}, {_scope_bound_text(self.end)}))"
        assert self.calendar is not None and self.level is not None
        return (
            f"TimeScope(period={self.calendar.key!r}/{self.level}:{self.key!r}; "
            "call .show() for detail)"
        )

    def show(self, *, max_output_bytes: int | None = 8192) -> None:
        """Render a bounded exact scope summary for agents."""
        del max_output_bytes
        print(self._render_summary())

    def _render_summary(self) -> str:
        values = [
            f"kind={self.kind}",
            f"start={_scope_bound_text(self.start)}",
            f"end={_scope_bound_text(self.end)}",
        ]
        if self.calendar is not None:
            values.extend(
                (
                    f"calendar={self.calendar.key}",
                    f"snapshot={self.snapshot_digest}",
                    f"level={self.level}",
                    f"key={self.key!r}",
                    f"ordinal={self.ordinal}",
                )
            )
        return "TimeScope(" + ", ".join(values) + ")"

    def contract(self) -> TimeScopeContractV1:
        """Return the bounded, versioned scope identity used by artifacts."""
        if isinstance(self.start, str) and isinstance(self.end, str):
            start, end = _parse_scope_strings(self.start, self.end)
        elif isinstance(self.start, str) or isinstance(self.end, str):
            raise ValueError("TimeScope contract cannot mix string and normalized bounds")
        else:
            start, end = self.start, self.end
        if self.kind == "absolute":
            return TimeScopeContractV1(kind="absolute", start=start, end=end)
        assert self.calendar is not None
        return TimeScopeContractV1(
            kind="calendar_period",
            start=start,
            end=end,
            calendar_ref=self.calendar.path,
            snapshot_digest=self.snapshot_digest,
            boundary_timezone=self.boundary_timezone,
            level=self.level,
            key=self.key,
        )

    @overload
    def model_dump(self, *, mode: Literal["json"], **kwargs: Any) -> dict[str, object]: ...

    @overload
    def model_dump(self, *, mode: str = "python", **kwargs: Any) -> dict[str, object]: ...

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        # Preserve the existing absolute-window payload shape while retaining
        # semantic provenance whenever it is present.
        kwargs.setdefault("exclude_none", True)
        return cast("dict[str, object]", super().model_dump(*args, **kwargs))


class BuiltinPeriodBindingV1(BaseModel):
    """Closed artifact identity for the built-in Gregorian/ISO authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["builtin_period"] = "builtin_period"
    authority_id: Literal["builtin:gregorian-iso/v1"] = "builtin:gregorian-iso/v1"
    level_name: str
    boundary_timezone: str


class SemanticPeriodBindingV1(BaseModel):
    """Closed artifact identity for one certified semantic calendar level."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["semantic_period"] = "semantic_period"
    calendar_ref: str
    snapshot_digest: str
    level_name: str


PeriodBindingV1 = BuiltinPeriodBindingV1 | SemanticPeriodBindingV1
TemporalAuthorityBindingV1 = PeriodBindingV1


class FrameTemporalContractV1(BaseModel):
    """Versioned temporal authority carried by an observed frame."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_: Literal["frame-temporal/v1"] = Field(
        default="frame-temporal/v1",
        alias="schema",
        serialization_alias="schema",
    )
    time_scope: TimeScopeContractV1 | None = None
    observation_period: PeriodBindingV1 | None = None
    cumulative_reset_period: PeriodBindingV1 | None = None
    actual_start: date | datetime | None = None
    actual_end: date | datetime | None = None
    output_period_keys: tuple[_JSON_SCALAR, ...] = ()
    display_timezone: str

    @model_validator(mode="after")
    def _validate_contract(self) -> FrameTemporalContractV1:
        if (self.actual_start is None) != (self.actual_end is None):
            raise ValueError("frame temporal bounds must be provided together")
        if (
            self.actual_start is not None
            and self.actual_end is not None
            and (
                type(self.actual_start) is not type(self.actual_end)
                or self.actual_start >= self.actual_end
            )
        ):
            raise ValueError("frame temporal bounds must be one non-empty half-open interval")
        if not self.display_timezone:
            raise ValueError("frame temporal contract requires display_timezone")
        return self


def period_binding_for_grain(
    grain: Grain | Any,
    *,
    snapshot: PeriodCalendarSnapshotV1 | None,
    boundary_timezone: str,
) -> PeriodBindingV1:
    """Resolve a unified Grain to its closed persisted authority binding."""
    # The public analysis window still carries its dependency-local Pydantic
    # ``analysis.windows.grain.Grain`` for builtin inputs.  Keep this
    # dependency-neutral helper as the single authority by accepting that
    # shape at the boundary and lowering it to the same builtin binding.
    if not isinstance(grain, Grain):
        unit = getattr(grain, "unit", None)
        count = getattr(grain, "count", None)
        if not isinstance(unit, str) or not isinstance(count, int):
            raise TypeError("period binding requires a Grain value")
        return BuiltinPeriodBindingV1(
            level_name=unit if count == 1 else f"{count}{unit}",
            boundary_timezone=boundary_timezone,
        )
    if grain.kind == "builtin":
        return BuiltinPeriodBindingV1(
            level_name=grain.to_token(),
            boundary_timezone=boundary_timezone,
        )
    if snapshot is None or grain.calendar is None or grain.level is None:
        raise ValueError("semantic Grain requires its certified snapshot for a period binding")
    if snapshot.calendar_ref != grain.calendar:
        raise ValueError("semantic Grain and snapshot calendar refs do not match")
    return SemanticPeriodBindingV1(
        calendar_ref=grain.calendar.path,
        snapshot_digest=snapshot.snapshot_digest,
        level_name=grain.level,
    )


def _scope_bound_text(value: str | datetime | date) -> str:
    return value.isoformat() if not isinstance(value, str) else value


def _parse_scope_strings(start: str, end: str) -> tuple[datetime | date, datetime | date]:
    def parse(value: str) -> datetime | date:
        raw = value.strip()
        if not raw:
            raise ValueError("TimeScope bounds must be non-empty ISO strings")
        if len(raw) == 10 and "T" not in raw and " " not in raw:
            try:
                return date.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError(f"invalid ISO date bound {value!r}") from exc
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO datetime bound {value!r}") from exc

    parsed_start = parse(start)
    parsed_end = parse(end)
    if type(parsed_start) is not type(parsed_end):
        raise ValueError("TimeScope cannot mix date and datetime bounds")
    return parsed_start, parsed_end


def absolute_time_scope(*, start: date, end: date) -> TimeScope:
    return TimeScope(start=start, end=end)


@dataclass(frozen=True, slots=True)
class PeriodRecord:
    level_name: str
    key: _JSON_SCALAR
    start_date: date
    end_date: date
    global_ordinal: int

    def __post_init__(self) -> None:
        if not self.level_name or self.start_date >= self.end_date or self.global_ordinal < 0:
            raise ValueError("invalid normalized period record")
        canonical_key(self.key)


@dataclass(frozen=True, slots=True)
class ContainmentRecord:
    source_level: str
    target_level: str
    source_key: _JSON_SCALAR
    target_key: _JSON_SCALAR
    ordinal_in_target: int


@dataclass(frozen=True, slots=True)
class CorrespondenceRecord:
    """One certified optional same-level period-to-baseline mapping."""

    name: str
    level_name: str
    current_key: _JSON_SCALAR
    baseline_key: _JSON_SCALAR | None

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or type(self.level_name) is not str
            or not self.level_name
        ):
            raise ValueError("invalid normalized correspondence record")
        canonical_key(self.current_key)
        if self.baseline_key is not None:
            canonical_key(self.baseline_key)


@dataclass(frozen=True, slots=True)
class PeriodCalendarSnapshotV1:
    """Certified compact period authority, normalized before identity hashing."""

    calendar_ref: Ref[PeriodCalendarKind]
    boundary_timezone: str
    coverage: tuple[date, date]
    levels: tuple[str, ...]
    periods: tuple[PeriodRecord, ...]
    containments: tuple[ContainmentRecord, ...]
    snapshot_digest: str
    correspondences: tuple[CorrespondenceRecord, ...] = ()
    schema: Literal["period-calendar-snapshot/v1"] = "period-calendar-snapshot/v1"

    def __post_init__(self) -> None:
        if type(self.calendar_ref) is not Ref or self.calendar_ref.kind.value != "period_calendar":
            raise TypeError("calendar_ref must be Ref[period_calendar]")
        _require_timezone(self.boundary_timezone)
        start, end = self.coverage
        if type(start) is not date or type(end) is not date or start >= end:
            raise ValueError("coverage must be a non-empty [start, end) civil-date interval")
        if self.schema != "period-calendar-snapshot/v1":
            raise ValueError("unsupported period calendar snapshot schema")
        expected = _snapshot_digest(
            calendar_ref=self.calendar_ref,
            boundary_timezone=self.boundary_timezone,
            coverage=self.coverage,
            levels=self.levels,
            periods=self.periods,
            containments=self.containments,
            correspondences=self.correspondences,
        )
        if self.snapshot_digest != expected:
            raise ValueError("snapshot_digest does not match normalized period-calendar content")

    def period_scope(self, level: str, key: _JSON_SCALAR) -> TimeScope:
        key = canonical_key(key)
        if level == "day":
            if not isinstance(key, str):
                raise KeyError(f"period day:{key!r} is not in certified calendar coverage")
            try:
                day = date.fromisoformat(key)
            except ValueError as exc:
                raise KeyError(f"period day:{key!r} is not in certified calendar coverage") from exc
            if day < self.coverage[0] or day >= self.coverage[1]:
                raise KeyError(f"period day:{key!r} is not in certified calendar coverage")
            return TimeScope(
                start=day,
                end=day + timedelta(days=1),
                calendar=self.calendar_ref,
                snapshot_digest=self.snapshot_digest,
                boundary_timezone=self.boundary_timezone,
                level="day",
                key=key,
                ordinal=(day - self.coverage[0]).days,
            )
        for period in self.periods:
            if period.level_name == level and _key_token(period.key) == _key_token(key):
                return TimeScope(
                    start=period.start_date,
                    end=period.end_date,
                    calendar=self.calendar_ref,
                    snapshot_digest=self.snapshot_digest,
                    boundary_timezone=self.boundary_timezone,
                    level=level,
                    key=key,
                    ordinal=period.global_ordinal,
                )
        raise KeyError(f"period {level}:{key!r} is not in certified calendar coverage")


def _snapshot_payload(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    levels: tuple[str, ...],
    periods: tuple[PeriodRecord, ...],
    containments: tuple[ContainmentRecord, ...],
    correspondences: tuple[CorrespondenceRecord, ...],
) -> dict[str, object]:
    return {
        "schema": "period-calendar-snapshot/v1",
        "calendar_ref": calendar_ref.key,
        "boundary_timezone": boundary_timezone,
        "coverage": [coverage[0].isoformat(), coverage[1].isoformat()],
        "levels": list(levels),
        "periods": [
            [
                p.level_name,
                _key_token(p.key),
                p.start_date.isoformat(),
                p.end_date.isoformat(),
                p.global_ordinal,
            ]
            for p in periods
        ],
        "containments": [
            [
                c.source_level,
                c.target_level,
                _key_token(c.source_key),
                _key_token(c.target_key),
                c.ordinal_in_target,
            ]
            for c in containments
        ],
        "correspondences": [
            [
                c.name,
                c.level_name,
                _key_token(c.current_key),
                _key_token(c.baseline_key) if c.baseline_key is not None else None,
            ]
            for c in correspondences
        ],
    }


def _snapshot_digest(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    levels: tuple[str, ...],
    periods: tuple[PeriodRecord, ...],
    containments: tuple[ContainmentRecord, ...],
    correspondences: tuple[CorrespondenceRecord, ...],
) -> str:
    payload = _snapshot_payload(
        calendar_ref=calendar_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        levels=levels,
        periods=periods,
        containments=containments,
        correspondences=correspondences,
    )
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def certify_period_calendar(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    rows: Iterable[Mapping[str, object]],
    levels: Mapping[str, str],
    correspondences: Mapping[str, tuple[str, str]] | None = None,
) -> PeriodCalendarSnapshotV1:
    """Certify exhaustive daily rows into the compact V1 period snapshot.

    ``rows`` must be the values from exactly one previously acquired snapshot.
    The function performs no reads and is deterministic across input row order.
    """
    _require_timezone(boundary_timezone)
    start, end = coverage
    if type(start) is not date or type(end) is not date or start >= end:
        raise ValueError("coverage must be a non-empty [start, end) civil-date interval")
    if not levels or "day" in levels or len(set(levels)) != len(levels):
        raise ValueError("levels must be a non-empty mapping with unique non-reserved names")
    if any(
        type(name) is not str or not name or type(column) is not str or not column
        for name, column in levels.items()
    ):
        raise ValueError("calendar levels must map non-empty names to source columns")
    correspondence_columns = {} if correspondences is None else dict(correspondences)
    if any(
        type(name) is not str
        or not name
        or type(level) is not str
        or level not in levels
        or type(column) is not str
        or not column
        for name, (level, column) in correspondence_columns.items()
    ):
        raise ValueError(
            "calendar correspondences must map names to declared levels and source columns"
        )

    daily: dict[date, Mapping[str, object]] = {}
    for row in rows:
        raw_date = row.get("date")
        if type(raw_date) is not date:
            raise TypeError("calendar snapshot rows require an exact civil date in column 'date'")
        if raw_date < start or raw_date >= end:
            continue
        if raw_date in daily:
            raise ValueError(f"calendar snapshot contains duplicate date {raw_date.isoformat()}")
        for column in levels.values():
            if column not in row or row[column] is None:
                raise ValueError(
                    f"calendar snapshot date {raw_date.isoformat()} has null/missing level {column!r}"
                )
            canonical_key(row[column])
        for name, (_level, column) in correspondence_columns.items():
            if column not in row:
                raise ValueError(
                    f"calendar snapshot date {raw_date.isoformat()} has missing correspondence {name!r}"
                )
            if row[column] is not None:
                canonical_key(row[column])
        daily[raw_date] = row
    required = (end - start).days
    if len(daily) != required:
        missing = next(
            (
                start + timedelta(days=index)
                for index in range(required)
                if start + timedelta(days=index) not in daily
            ),
            None,
        )
        raise ValueError(
            f"calendar snapshot does not completely cover declared range; first missing date is {missing}"
        )

    period_rows: list[PeriodRecord] = []
    by_level: dict[str, list[PeriodRecord]] = {}
    for level, column in (("day", "date"), *levels.items()):
        periods: list[PeriodRecord] = []
        current_key: _JSON_SCALAR | None = None
        current_token: str | None = None
        current_start: date | None = None
        seen_keys: set[str] = set()
        ordinal = 0
        for offset in range(required):
            value_date = start + timedelta(days=offset)
            value = (
                canonical_key(value_date.isoformat())
                if level == "day"
                else canonical_key(daily[value_date][column])
            )
            if current_key is None:
                current_key, current_start = value, value_date
                current_token = _key_token(value)
                continue
            value_token = _key_token(value)
            if value_token == current_token:
                continue
            assert current_start is not None
            token = _key_token(current_key)
            if token in seen_keys:
                raise ValueError(
                    f"calendar level {level!r} repeats discontiguous key {current_key!r}"
                )
            seen_keys.add(token)
            periods.append(PeriodRecord(level, current_key, current_start, value_date, ordinal))
            ordinal += 1
            current_key, current_start = value, value_date
            current_token = value_token
        assert current_start is not None and current_key is not None
        token = _key_token(current_key)
        if token in seen_keys:
            raise ValueError(f"calendar level {level!r} repeats discontiguous key {current_key!r}")
        periods.append(PeriodRecord(level, current_key, current_start, end, ordinal))
        by_level[level] = periods
        if level != "day":
            period_rows.extend(periods)

    containments: list[ContainmentRecord] = []
    names = tuple(by_level)
    for source in names:
        for target in names:
            if source == target:
                continue
            targets = by_level[target]
            mapped: list[tuple[PeriodRecord, PeriodRecord]] = []
            for source_period in by_level[source]:
                matches = [
                    target_period
                    for target_period in targets
                    if target_period.start_date <= source_period.start_date
                    and source_period.end_date <= target_period.end_date
                ]
                if len(matches) != 1:
                    mapped = []
                    break
                mapped.append((source_period, matches[0]))
            if not mapped or not any(
                sum(1 for _source, mapped_target in mapped if mapped_target == target_period) > 1
                for target_period in targets
            ):
                continue
            per_target_ordinal: dict[str, int] = {}
            for source_period, target_period in mapped:
                target_token = _key_token(target_period.key)
                ordinal = per_target_ordinal.get(target_token, 0)
                containments.append(
                    ContainmentRecord(source, target, source_period.key, target_period.key, ordinal)
                )
                per_target_ordinal[target_token] = ordinal + 1

    ordered_periods = tuple(sorted(period_rows, key=lambda p: (p.level_name, p.global_ordinal)))
    ordered_containments = tuple(
        sorted(
            containments,
            key=lambda c: (
                c.source_level,
                c.target_level,
                c.ordinal_in_target,
                _key_token(c.source_key),
            ),
        )
    )
    normalized_correspondences: list[CorrespondenceRecord] = []
    for name, (level, column) in sorted(correspondence_columns.items()):
        target_keys = {_key_token(period.key): period.key for period in by_level[level]}
        consumed: set[str] = set()
        for current in by_level[level]:
            # ``None`` is a meaningful certified value: it means this current
            # period has no baseline.  Keep it in the constancy check so a
            # period containing both null and a key cannot be silently
            # normalized to the non-null key.
            baseline_values: set[str | None] = {
                (
                    _key_token(canonical_key(daily[value_date][column]))
                    if daily[value_date].get(column) is not None
                    else None
                )
                for value_date in (
                    current.start_date + timedelta(days=offset)
                    for offset in range((current.end_date - current.start_date).days)
                )
            }
            if len(baseline_values) > 1:
                raise ValueError(
                    f"calendar correspondence {name!r} has conflicting baseline keys for {level}:{current.key!r}"
                )
            baseline_key: _JSON_SCALAR | None = None
            baseline_token: str | None = next(iter(baseline_values))
            if baseline_token is not None:
                baseline_key = target_keys.get(baseline_token)
                if baseline_key is None:
                    raise ValueError(
                        f"calendar correspondence {name!r} baseline key {json.loads(baseline_token)!r} "
                        f"does not identify a {level!r} period"
                    )
                if _key_token(baseline_key) == _key_token(current.key):
                    raise ValueError(
                        f"calendar correspondence {name!r} maps {level}:{current.key!r} to itself"
                    )
                if baseline_token in consumed:
                    raise ValueError(
                        f"calendar correspondence {name!r} maps multiple periods to baseline {baseline_key!r}"
                    )
                consumed.add(baseline_token)
            normalized_correspondences.append(
                CorrespondenceRecord(name, level, current.key, baseline_key)
            )
    ordered_correspondences = tuple(
        sorted(
            normalized_correspondences,
            key=lambda value: (value.name, value.level_name, _key_token(value.current_key)),
        )
    )
    ordered_levels = tuple(sorted(by_level))
    digest = _snapshot_digest(
        calendar_ref=calendar_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        levels=ordered_levels,
        periods=ordered_periods,
        containments=ordered_containments,
        correspondences=ordered_correspondences,
    )
    return PeriodCalendarSnapshotV1(
        calendar_ref=calendar_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        levels=ordered_levels,
        periods=ordered_periods,
        containments=ordered_containments,
        snapshot_digest=digest,
        correspondences=ordered_correspondences,
    )


def certify_period_calendar_rows(
    *,
    calendar_ref: Ref[PeriodCalendarKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    columns: tuple[str, ...],
    retained_values: tuple[tuple[_JSON_SCALAR | None, ...], ...],
    date_column: str,
    levels: Mapping[str, str],
    correspondences: Mapping[str, tuple[str, str]] | None = None,
) -> PeriodCalendarSnapshotV1:
    """Certify rows retained by one persisted datasource acquisition.

    The caller supplies physical column bindings from the semantic declaration;
    this bridge performs no datasource, pandas, or registry operation.
    """
    positions = {name: index for index, name in enumerate(columns)}
    correspondence_columns = (
        ()
        if correspondences is None
        else tuple(column for _level, column in correspondences.values())
    )
    required = (date_column, *levels.values(), *correspondence_columns)
    missing = tuple(name for name in required if name not in positions)
    if missing:
        raise ValueError(f"persisted snapshot is missing calendar columns {missing!r}")
    rows: list[dict[str, object]] = []
    for values in retained_values:
        if len(values) != len(columns):
            raise ValueError("persisted snapshot row width does not match selected columns")
        raw_date = values[positions[date_column]]
        if type(raw_date) is not str:
            raise TypeError(
                "calendar date values must be ISO civil-date strings in persisted evidence"
            )
        try:
            civil_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError(f"calendar date value {raw_date!r} is not an ISO civil date") from exc
        row: dict[str, object] = {"date": civil_date}
        for _level, column in levels.items():
            row[column] = values[positions[column]]
        for _name, (_level, column) in ({} if correspondences is None else correspondences).items():
            row[column] = values[positions[column]]
        rows.append(row)
    return certify_period_calendar(
        calendar_ref=calendar_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        rows=rows,
        levels=levels,
        correspondences=correspondences,
    )


@runtime_checkable
class TemporalResolverAdapter(Protocol):
    """Common lookup contract for certified and built-in period authorities."""

    def period(self, level: str, key: _JSON_SCALAR) -> PeriodRecord: ...

    def period_on(self, level: str, value: date) -> PeriodRecord: ...

    def period_before(self, level: str, exclusive_end: date) -> PeriodRecord: ...

    def scope(self, level: str, key: _JSON_SCALAR) -> TimeScope: ...

    def correspondence(self, name: str, level: str, key: _JSON_SCALAR) -> _JSON_SCALAR | None: ...

    def rolls_up_to(self, source_level: str, target_level: str) -> bool: ...


class TemporalResolver:
    """Pure lookup and correspondence adapter over one certified snapshot."""

    __slots__ = ("_correspondences", "_periods", "_snapshot")

    def __init__(self, snapshot: PeriodCalendarSnapshotV1) -> None:
        self._snapshot = snapshot
        self._periods = MappingProxyType(
            {
                level: tuple(period for period in snapshot.periods if period.level_name == level)
                for level in snapshot.levels
            }
        )
        self._correspondences = MappingProxyType(
            {
                (item.name, item.level_name, _key_token(item.current_key)): item.baseline_key
                for item in snapshot.correspondences
            }
        )

    @property
    def snapshot(self) -> PeriodCalendarSnapshotV1:
        return self._snapshot

    def period(self, level: str, key: _JSON_SCALAR) -> PeriodRecord:
        key = canonical_key(key)
        if level == "day":
            if not isinstance(key, str):
                raise KeyError(f"period day:{key!r} is not in certified calendar coverage")
            try:
                day = date.fromisoformat(key)
            except ValueError as exc:
                raise KeyError(f"period day:{key!r} is not in certified calendar coverage") from exc
            return self.period_on(level, day)
        for period in self._periods.get(level, ()):
            if _key_token(period.key) == _key_token(key):
                return period
        raise KeyError(f"period {level}:{key!r} is not in certified calendar coverage")

    def period_on(self, level: str, value: date) -> PeriodRecord:
        if value < self._snapshot.coverage[0] or value >= self._snapshot.coverage[1]:
            raise ValueError(f"date {value.isoformat()} is outside certified calendar coverage")
        if level == "day":
            ordinal = (value - self._snapshot.coverage[0]).days
            return PeriodRecord("day", value.isoformat(), value, value + timedelta(days=1), ordinal)
        for period in self._periods.get(level, ()):
            if period.start_date <= value < period.end_date:
                return period
        raise KeyError(f"unknown calendar level {level!r}")

    def period_before(self, level: str, exclusive_end: date) -> PeriodRecord:
        """Return the latest certified period ending no later than ``exclusive_end``."""
        if type(exclusive_end) is not date:
            raise TypeError("exclusive_end must be a civil date")
        if level == "day":
            start, end = self._snapshot.coverage
            if exclusive_end <= start:
                raise KeyError(
                    f"no certified {level!r} period ends on or before {exclusive_end.isoformat()}"
                )
            candidate = min(exclusive_end - timedelta(days=1), end - timedelta(days=1))
            return self.period_on(level, candidate)
        values = tuple(
            period for period in self._periods.get(level, ()) if period.end_date <= exclusive_end
        )
        if not values:
            raise KeyError(
                f"no certified {level!r} period ends on or before {exclusive_end.isoformat()}"
            )
        return values[-1]

    def scope(self, level: str, key: _JSON_SCALAR) -> TimeScope:
        return self._snapshot.period_scope(level, key)

    def correspondence(self, name: str, level: str, key: _JSON_SCALAR) -> _JSON_SCALAR | None:
        """Return the certified optional baseline key for one current period."""
        token = (name, level, _key_token(canonical_key(key)))
        if token not in self._correspondences:
            raise KeyError(f"unknown certified correspondence {name!r} for {level}:{key!r}")
        return self._correspondences[token]

    def rolls_up_to(self, source_level: str, target_level: str) -> bool:
        return any(
            item.source_level == source_level and item.target_level == target_level
            for item in self._snapshot.containments
        )


class GregorianIsoResolver:
    """Built-in Gregorian/ISO adapter implementing the resolver contract."""

    __slots__ = ()

    def period(self, level: str, key: _JSON_SCALAR) -> PeriodRecord:
        key = canonical_key(key)
        if level == "day":
            try:
                value = date.fromisoformat(cast("str", key))
            except (TypeError, ValueError) as exc:
                raise KeyError(f"invalid built-in day key {key!r}") from exc
            return self.period_on(level, value)
        if level == "week" and isinstance(key, str):
            try:
                year_text, week_text = key.split("-W", 1)
                value = date.fromisocalendar(int(year_text), int(week_text), 1)
            except (TypeError, ValueError) as exc:
                raise KeyError(f"invalid built-in week key {key!r}") from exc
            return self.period_on(level, value)
        if level == "month" and isinstance(key, str):
            try:
                year_text, month_text = key.split("-", 1)
                value = date(int(year_text), int(month_text), 1)
            except (TypeError, ValueError) as exc:
                raise KeyError(f"invalid built-in month key {key!r}") from exc
            return self.period_on(level, value)
        if level == "quarter" and isinstance(key, str) and "-Q" in key:
            try:
                year_text, quarter_text = key.split("-Q", 1)
                month = (int(quarter_text) - 1) * 3 + 1
                value = date(int(year_text), month, 1)
            except (TypeError, ValueError) as exc:
                raise KeyError(f"invalid built-in quarter key {key!r}") from exc
            return self.period_on(level, value)
        if level == "year" and isinstance(key, str):
            try:
                value = date(int(key), 1, 1)
            except (TypeError, ValueError) as exc:
                raise KeyError(f"invalid built-in year key {key!r}") from exc
            return self.period_on(level, value)
        raise KeyError(f"unknown built-in {level}:{key!r}")

    def period_on(self, level: str, value: date) -> PeriodRecord:
        if level == "day":
            return PeriodRecord("day", value.isoformat(), value, value + timedelta(days=1), 0)
        if level == "week":
            start = value - timedelta(days=value.weekday())
            iso = start.isocalendar()
            return PeriodRecord(
                "week", f"{iso.year}-W{iso.week:02d}", start, start + timedelta(days=7), 0
            )
        if level == "month":
            start = value.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            return PeriodRecord("month", f"{start.year}-{start.month:02d}", start, end, 0)
        if level == "quarter":
            month = ((value.month - 1) // 3) * 3 + 1
            start = value.replace(month=month, day=1)
            end_year = start.year + (1 if month == 10 else 0)
            end = date(end_year, 1 if month == 10 else month + 3, 1)
            return PeriodRecord("quarter", f"{start.year}-Q{((month - 1) // 3) + 1}", start, end, 0)
        if level == "year":
            start = date(value.year, 1, 1)
            return PeriodRecord("year", str(value.year), start, date(value.year + 1, 1, 1), 0)
        raise KeyError(f"unsupported built-in calendar level {level!r}")

    def period_before(self, level: str, exclusive_end: date) -> PeriodRecord:
        if type(exclusive_end) is not date:
            raise TypeError("exclusive_end must be a civil date")
        candidate = self.period_on(level, exclusive_end - timedelta(days=1))
        while candidate.end_date > exclusive_end:
            candidate = self.period_on(level, candidate.start_date - timedelta(days=1))
        return candidate

    def scope(self, level: str, key: _JSON_SCALAR) -> TimeScope:
        period = self.period(level, key)
        return absolute_time_scope(start=period.start_date, end=period.end_date)

    def correspondence(self, name: str, level: str, key: _JSON_SCALAR) -> _JSON_SCALAR | None:
        raise KeyError(f"built-in Gregorian/ISO authority has no named correspondence {name!r}")

    def rolls_up_to(self, source_level: str, target_level: str) -> bool:
        return (source_level, target_level) in {
            ("day", "week"),
            ("day", "month"),
            ("day", "quarter"),
            ("day", "year"),
            ("month", "quarter"),
            ("month", "year"),
            ("quarter", "year"),
        }


@dataclass(frozen=True, slots=True)
class PeriodCalendarManifestV1:
    """Current certified snapshot pointer for one authored calendar definition."""

    calendar_ref: Ref[PeriodCalendarKind]
    definition_digest: str
    snapshot_digest: str
    schema: Literal["period-calendar-manifest/v1"] = "period-calendar-manifest/v1"


class TemporalSnapshotStore:
    """Project-local atomic persistence for certified period authorities."""

    __slots__ = ("_root",)

    def __init__(self, project_root: Path) -> None:
        self._root = project_root / ".marivo" / "temporal" / "period-calendars"

    def publish(
        self,
        snapshot: PeriodCalendarSnapshotV1,
        *,
        definition_digest: str,
    ) -> PeriodCalendarManifestV1:
        """Atomically publish a new current manifest without deleting prior snapshots."""
        if type(definition_digest) is not str or not definition_digest:
            raise ValueError("definition_digest must be a non-empty string")
        directory = self._directory(snapshot.calendar_ref)
        directory.mkdir(parents=True, exist_ok=True)
        self._write_json(
            directory / f"{snapshot.snapshot_digest}.json",
            _snapshot_json(snapshot),
        )
        manifest = PeriodCalendarManifestV1(
            calendar_ref=snapshot.calendar_ref,
            definition_digest=definition_digest,
            snapshot_digest=snapshot.snapshot_digest,
        )
        self._write_json(directory / "current.json", _manifest_json(manifest))
        return manifest

    def load_current(
        self,
        calendar_ref: Ref[PeriodCalendarKind],
        *,
        definition_digest: str,
    ) -> PeriodCalendarSnapshotV1 | None:
        """Load the current snapshot only when it matches this exact definition."""
        status, snapshot = self.inspect_current(
            calendar_ref,
            definition_digest=definition_digest,
        )
        return snapshot if status == "current" else None

    def inspect_current(
        self,
        calendar_ref: Ref[PeriodCalendarKind],
        *,
        definition_digest: str,
    ) -> tuple[Literal["missing", "current", "stale", "invalid"], PeriodCalendarSnapshotV1 | None]:
        """Classify project-local current state without leaking parse failures.

        Readiness and catalog details need to distinguish a missing authority
        from a stale declaration and a corrupted manifest/payload.  The old
        ``load_current`` API intentionally remains a compact current-or-None
        projection for callers that do not need that distinction.
        """
        directory = self._directory(calendar_ref)
        manifest_path = directory / "current.json"
        if not manifest_path.exists():
            return "missing", None
        try:
            manifest = _manifest_from_json(_read_json(manifest_path))
        except (OSError, TypeError, ValueError, KeyError, IndexError):
            return "invalid", None
        if manifest.calendar_ref != calendar_ref:
            return "invalid", None
        if manifest.definition_digest != definition_digest:
            return "stale", None
        snapshot_path = directory / f"{manifest.snapshot_digest}.json"
        if not snapshot_path.exists():
            return "invalid", None
        try:
            snapshot = _snapshot_from_json(_read_json(snapshot_path))
        except (OSError, TypeError, ValueError, KeyError, IndexError):
            return "invalid", None
        if (
            snapshot.calendar_ref != calendar_ref
            or snapshot.snapshot_digest != manifest.snapshot_digest
        ):
            return "invalid", None
        return "current", snapshot

    def _directory(self, calendar_ref: Ref[PeriodCalendarKind]) -> Path:
        token = hashlib.sha256(calendar_ref.key.encode()).hexdigest()
        return self._root / token

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, object]) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)


def _snapshot_json(snapshot: PeriodCalendarSnapshotV1) -> dict[str, object]:
    return {
        **_snapshot_payload(
            calendar_ref=snapshot.calendar_ref,
            boundary_timezone=snapshot.boundary_timezone,
            coverage=snapshot.coverage,
            levels=snapshot.levels,
            periods=snapshot.periods,
            containments=snapshot.containments,
            correspondences=snapshot.correspondences,
        ),
        "snapshot_digest": snapshot.snapshot_digest,
    }


def _manifest_json(manifest: PeriodCalendarManifestV1) -> dict[str, object]:
    return {
        "schema": manifest.schema,
        "calendar_ref": manifest.calendar_ref.path,
        "definition_digest": manifest.definition_digest,
        "snapshot_digest": manifest.snapshot_digest,
    }


def _read_json(path: Path) -> Mapping[str, object]:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"temporal payload at {path} must be an object")
    return cast("Mapping[str, object]", payload)


def _manifest_from_json(payload: Mapping[str, object]) -> PeriodCalendarManifestV1:
    if payload.get("schema") != "period-calendar-manifest/v1":
        raise ValueError("unsupported period calendar manifest schema")
    calendar_path = payload.get("calendar_ref")
    definition_digest = payload.get("definition_digest")
    snapshot_digest = payload.get("snapshot_digest")
    if not all(
        type(value) is str and value
        for value in (calendar_path, definition_digest, snapshot_digest)
    ):
        raise ValueError("period calendar manifest fields are invalid")
    return PeriodCalendarManifestV1(
        calendar_ref=ref_factory_period_calendar(cast("str", calendar_path)),
        definition_digest=cast("str", definition_digest),
        snapshot_digest=cast("str", snapshot_digest),
    )


def _snapshot_from_json(payload: Mapping[str, object]) -> PeriodCalendarSnapshotV1:
    if payload.get("schema") != "period-calendar-snapshot/v1":
        raise ValueError("unsupported period calendar snapshot schema")
    raw_ref = payload.get("calendar_ref")
    raw_coverage = payload.get("coverage")
    raw_levels = payload.get("levels")
    raw_periods = payload.get("periods")
    raw_containments = payload.get("containments")
    raw_correspondences = payload.get("correspondences", [])
    digest = payload.get("snapshot_digest")
    timezone = payload.get("boundary_timezone")
    if (
        type(raw_ref) is not str
        or not isinstance(raw_coverage, list)
        or not isinstance(raw_levels, list)
        or not isinstance(raw_periods, list)
        or not isinstance(raw_containments, list)
        or not isinstance(raw_correspondences, list)
        or type(digest) is not str
        or type(timezone) is not str
        or len(raw_coverage) != 2
    ):
        raise ValueError("period calendar snapshot payload fields are invalid")
    coverage = tuple(date.fromisoformat(cast("str", value)) for value in raw_coverage)
    periods = tuple(
        PeriodRecord(
            level_name=cast("str", item[0]),
            key=json.loads(cast("str", item[1])),
            start_date=date.fromisoformat(cast("str", item[2])),
            end_date=date.fromisoformat(cast("str", item[3])),
            global_ordinal=cast("int", item[4]),
        )
        for item in cast("list[list[object]]", raw_periods)
    )
    containments = tuple(
        ContainmentRecord(
            source_level=cast("str", item[0]),
            target_level=cast("str", item[1]),
            source_key=json.loads(cast("str", item[2])),
            target_key=json.loads(cast("str", item[3])),
            ordinal_in_target=cast("int", item[4]),
        )
        for item in cast("list[list[object]]", raw_containments)
    )
    correspondences = tuple(
        CorrespondenceRecord(
            name=cast("str", item[0]),
            level_name=cast("str", item[1]),
            current_key=json.loads(cast("str", item[2])),
            baseline_key=(json.loads(cast("str", item[3])) if item[3] is not None else None),
        )
        for item in cast("list[list[object]]", raw_correspondences)
    )
    calendar_key_prefix = "period_calendar:"
    if not raw_ref.startswith(calendar_key_prefix):
        raise ValueError("period calendar snapshot calendar_ref is invalid")
    return PeriodCalendarSnapshotV1(
        calendar_ref=ref_factory_period_calendar(raw_ref.removeprefix(calendar_key_prefix)),
        boundary_timezone=timezone,
        coverage=cast("tuple[date, date]", coverage),
        levels=tuple(cast("str", value) for value in raw_levels),
        periods=periods,
        containments=containments,
        snapshot_digest=digest,
        correspondences=correspondences,
    )


def ref_factory_period_calendar(path: str) -> Ref[PeriodCalendarKind]:
    """Late import helper that keeps the resolver independent from semantic modules."""
    from marivo.refs import ref

    return ref.period_calendar(path)
