"""Dependency-neutral temporal values and certified period resolution.

This module deliberately has no datasource, semantic-registry, Ibis, pandas,
or analysis-session dependency.  Semantic authoring certifies a finite period
snapshot here; later analysis slices consume the exact same resolver.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol, cast, overload, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import core_schema

from marivo.refs import PeriodCalendarKind, Ref, TemporalSetKind

_GRAIN_UNITS = frozenset({"second", "minute", "hour", "day", "week", "month", "quarter", "year"})
_JSON_SCALAR = str | int | float | bool
_GRAIN_INTERNAL = ContextVar("marivo_grain_internal", default=False)
_TIME_SCOPE_INTERNAL = ContextVar("marivo_time_scope_internal", default=False)


@contextmanager
def _trusted_time_scope_validation() -> Iterator[None]:
    """Allow trusted persistence readers to rehydrate an already sealed scope."""

    token = _TIME_SCOPE_INTERNAL.set(True)
    try:
        yield
    finally:
        _TIME_SCOPE_INTERNAL.reset(token)


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


def _local_civil_datetime(
    value: date | datetime,
    *,
    boundary_timezone: str,
) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(ZoneInfo(boundary_timezone)).replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raise TypeError(f"expected date or datetime, got {type(value).__name__}")


@dataclass(frozen=True, slots=True, init=False)
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

    def __init__(
        self,
        kind: Literal["builtin", "semantic"] = "builtin",
        unit: str | None = None,
        count: int | None = 1,
        calendar: Ref[PeriodCalendarKind] | None = None,
        level: str | None = None,
    ) -> None:
        if not _GRAIN_INTERNAL.get():
            raise TypeError(
                "Grain values are returned by marivo.analysis.grain(...), "
                "ms.calendar_grain(...), or an exact catalog lookup; direct "
                "construction is not supported"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "count", count)
        object.__setattr__(self, "calendar", calendar)
        object.__setattr__(self, "level", level)
        self.__post_init__()

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        schema = handler(source)

        def _allow_trusted_validation(value: Any, validator: Any, _info: Any) -> Any:
            token = _GRAIN_INTERNAL.set(True)
            try:
                return validator(value)
            finally:
                _GRAIN_INTERNAL.reset(token)

        return core_schema.with_info_wrap_validator_function(
            _allow_trusted_validation,
            schema,
        )

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
    token = _GRAIN_INTERNAL.set(True)
    try:
        return Grain(kind="builtin", unit=unit, count=count)
    finally:
        _GRAIN_INTERNAL.reset(token)


def semantic_grain(*, calendar: Ref[PeriodCalendarKind], level: str) -> Grain:
    """Create the semantic Grain variant behind ``ms.calendar_grain``."""
    token = _GRAIN_INTERNAL.set(True)
    try:
        return Grain(kind="semantic", unit=None, count=None, calendar=calendar, level=level)
    finally:
        _GRAIN_INTERNAL.reset(token)


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


def temporal_set_definition_digest(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    boundary_timezone: str,
    coverage: tuple[str | date, str | date],
    occurrence_id: str,
    start: str,
    end: str,
    category: str | None = None,
    dependency_digest: str | None = None,
) -> str:
    """Return declaration identity used to reject stale temporal-set evidence."""
    payload = {
        "schema": "temporal-set-definition/v1",
        "temporal_set_ref": temporal_set_ref.key,
        "boundary_timezone": boundary_timezone,
        "coverage": [value.isoformat() if isinstance(value, date) else value for value in coverage],
        "occurrence_id": occurrence_id,
        "start": start,
        "end": end,
        "category": category,
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
    kind: Literal["absolute", "calendar_period", "temporal_occurrence"]
    start: date | datetime
    end: date | datetime
    calendar_ref: str | None = None
    temporal_set_ref: str | None = None
    snapshot_digest: str | None = None
    boundary_timezone: str | None = None
    level: str | None = None
    key: _JSON_SCALAR | None = None
    occurrence_category: str | None = None

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
            self.temporal_set_ref,
            self.occurrence_category,
        )
        if self.kind == "absolute":
            if any(value is not None for value in provenance):
                raise ValueError("absolute time-scope contract cannot contain period provenance")
            return self
        if self.kind == "calendar_period":
            if self.temporal_set_ref is not None or self.occurrence_category is not None:
                raise ValueError(
                    "calendar-period time-scope contract cannot contain occurrence provenance"
                )
            if type(self.start) is not date:
                raise ValueError("calendar-period time-scope contract bounds must be civil dates")
            if (
                any(type(value) is not str or not value for value in provenance[:4])
                or self.key is None
            ):
                raise ValueError("calendar-period time-scope contract requires complete provenance")
            canonical_key(self.key)
            return self
        if self.calendar_ref is not None or self.level is not None:
            raise ValueError(
                "temporal-occurrence time-scope contract cannot contain period provenance"
            )
        if (
            type(self.temporal_set_ref) is not str
            or not self.temporal_set_ref
            or type(self.snapshot_digest) is not str
            or not self.snapshot_digest
            or type(self.boundary_timezone) is not str
            or not self.boundary_timezone
            or self.key is None
        ):
            raise ValueError("temporal-occurrence time-scope contract requires complete provenance")
        canonical_key(self.key)
        if self.occurrence_category is not None and (
            type(self.occurrence_category) is not str or not self.occurrence_category
        ):
            raise ValueError("temporal-occurrence category must be a non-empty string or null")
        return self

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        kwargs.setdefault("exclude_none", True)
        payload = cast("dict[str, object]", super().model_dump(*args, **kwargs))
        # The occurrence variant has a closed nullable category field.  Keep
        # that tag explicit in direct contract serialization so an uncategorized
        # occurrence cannot be confused with a legacy/partial payload.
        if self.kind == "temporal_occurrence":
            payload.setdefault("occurrence_category", None)
        return payload


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
    temporal_set: Ref[TemporalSetKind] | None = None
    snapshot_digest: str | None = None
    boundary_timezone: str | None = None
    level: str | None = None
    key: _JSON_SCALAR | None = None
    ordinal: int | None = None
    occurrence_category: str | None = None

    def __init__(self, **data: Any) -> None:
        if not _TIME_SCOPE_INTERNAL.get():
            raise TypeError(
                "TimeScope values are returned by marivo.analysis.time_scope(...) "
                "or an exact catalog lookup; direct construction is not supported"
            )
        super().__init__(**data)

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
        schema = handler(source)

        def _allow_nested_validation(value: Any, validator: Any, info: Any) -> Any:
            # A parent Pydantic model may rehydrate a trusted TimeScope field
            # from its persisted JSON representation.  A direct
            # ``TimeScope.model_validate({...})`` has no field name and must
            # still hit the constructor seal below.
            if _TIME_SCOPE_INTERNAL.get() or info.field_name is not None:
                token = _TIME_SCOPE_INTERNAL.set(True)
                try:
                    return validator(value)
                finally:
                    _TIME_SCOPE_INTERNAL.reset(token)
            return validator(value)

        return core_schema.with_info_wrap_validator_function(
            _allow_nested_validation,
            schema,
        )

    @property
    def kind(self) -> Literal["absolute", "calendar_period", "temporal_occurrence"]:
        """Return the closed variant tag used by the current runtime."""
        if self.calendar is not None:
            return "calendar_period"
        if self.temporal_set is not None:
            return "temporal_occurrence"
        return "absolute"

    @model_validator(mode="after")
    def _validate_scope(self) -> TimeScope:
        provenance = (
            self.calendar,
            self.temporal_set,
            self.snapshot_digest,
            self.boundary_timezone,
            self.level,
            self.key,
            self.ordinal,
            self.occurrence_category,
        )
        if self.calendar is None:
            if self.temporal_set is None:
                if any(value is not None for value in provenance[2:]):
                    raise ValueError(
                        "absolute TimeScope cannot contain semantic temporal provenance"
                    )
            else:
                if (
                    type(self.temporal_set) is not Ref
                    or self.temporal_set.kind.value != "temporal_set"
                ):
                    raise TypeError("temporal occurrence TimeScope requires Ref[temporal_set]")
                if type(self.snapshot_digest) is not str or not self.snapshot_digest:
                    raise ValueError("temporal occurrence TimeScope requires snapshot_digest")
                if type(self.boundary_timezone) is not str or not self.boundary_timezone:
                    raise ValueError("temporal occurrence TimeScope requires boundary_timezone")
                if self.key is None or self.level is not None or self.ordinal is not None:
                    raise ValueError(
                        "temporal occurrence TimeScope requires key and no period provenance"
                    )
                if self.occurrence_category is not None and (
                    type(self.occurrence_category) is not str or not self.occurrence_category
                ):
                    raise ValueError(
                        "temporal occurrence category must be a non-empty string or null"
                    )
                canonical_key(self.key)
        else:
            if self.temporal_set is not None or self.occurrence_category is not None:
                raise ValueError("period TimeScope cannot contain occurrence provenance")
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
        _validate_time_scope_bounds(self.start, self.end)
        return self

    def __repr__(self) -> str:
        if self.kind == "absolute":
            return f"TimeScope([{_scope_bound_text(self.start)}, {_scope_bound_text(self.end)}))"
        if self.kind == "temporal_occurrence":
            assert self.temporal_set is not None
            return (
                f"TimeScope(occurrence={self.temporal_set.key!r}/{self.key!r}; "
                "call .show() for detail)"
            )
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
        elif self.temporal_set is not None:
            values.extend(
                (
                    f"temporal_set={self.temporal_set.key}",
                    f"snapshot={self.snapshot_digest}",
                    f"key={self.key!r}",
                    f"category={self.occurrence_category!r}",
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
        if self.kind == "temporal_occurrence":
            assert self.temporal_set is not None
            return TimeScopeContractV1(
                kind="temporal_occurrence",
                start=start,
                end=end,
                temporal_set_ref=self.temporal_set.path,
                snapshot_digest=self.snapshot_digest,
                boundary_timezone=self.boundary_timezone,
                key=self.key,
                occurrence_category=self.occurrence_category,
            )
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


def _new_time_scope(**data: Any) -> TimeScope:
    """Build a validated scope for trusted runtime/catalog paths."""

    with _trusted_time_scope_validation():
        return TimeScope(**data)


def _validate_time_scope_data(data: Mapping[str, Any]) -> TimeScope:
    """Decode one persisted scope without exposing model construction publicly."""

    with _trusted_time_scope_validation():
        return TimeScope.model_validate(dict(data))


def _time_scope_from_contract_data(data: Mapping[str, Any]) -> TimeScope:
    """Decode a persisted ``TimeScopeContractV1`` into a trusted scope.

    The public scope model intentionally stores typed ``Ref`` values while the
    artifact contract stores their path strings.  Recovery uses this private
    bridge so cold-start replay can retain exact semantic provenance without
    opening a public dictionary-input path.
    """
    contract = TimeScopeContractV1.model_validate(dict(data))
    if contract.kind == "absolute":
        return _new_time_scope(start=contract.start, end=contract.end)
    if contract.kind == "calendar_period":
        assert contract.calendar_ref is not None
        assert contract.snapshot_digest is not None
        assert contract.boundary_timezone is not None
        assert contract.level is not None
        return _new_time_scope(
            start=contract.start,
            end=contract.end,
            calendar=ref_factory_period_calendar(contract.calendar_ref),
            snapshot_digest=contract.snapshot_digest,
            boundary_timezone=contract.boundary_timezone,
            level=contract.level,
            key=contract.key,
            ordinal=0,
        )
    assert contract.temporal_set_ref is not None
    assert contract.snapshot_digest is not None
    assert contract.boundary_timezone is not None
    return _new_time_scope(
        start=contract.start,
        end=contract.end,
        temporal_set=ref_factory_temporal_set(contract.temporal_set_ref),
        snapshot_digest=contract.snapshot_digest,
        boundary_timezone=contract.boundary_timezone,
        key=contract.key,
        occurrence_category=contract.occurrence_category,
    )


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


class TemporalSetBindingV1(BaseModel):
    """Closed artifact identity for one certified temporal set snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["temporal_set"] = "temporal_set"
    temporal_set_ref: str
    snapshot_digest: str


PeriodBindingV1 = BuiltinPeriodBindingV1 | SemanticPeriodBindingV1
TemporalAuthorityBindingV1 = PeriodBindingV1 | TemporalSetBindingV1


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


class AlignmentEvidenceV1(BaseModel):
    """Bounded evidence for one comparison pairing decision."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        serialize_by_alias=True,
    )

    schema_: Literal["alignment-evidence/v1"] = Field(
        default="alignment-evidence/v1",
        alias="schema",
        serialization_alias="schema",
    )
    candidate_current_points: int = Field(ge=0)
    candidate_baseline_points: int = Field(ge=0)
    paired_points: int = Field(ge=0)
    current_only_points: int = Field(ge=0)
    baseline_only_points: int = Field(ge=0)
    unmatched_points: int = Field(ge=0)
    dropped_points: int = Field(ge=0)
    dropped_reason: str | None = None
    execution_path: Literal["backend", "local"]
    backend_optimized: bool = False

    @model_validator(mode="after")
    def _validate_counts(self) -> AlignmentEvidenceV1:
        if self.paired_points + self.current_only_points > self.candidate_current_points:
            raise ValueError("current pairing counts exceed candidate current points")
        if self.paired_points + self.baseline_only_points > self.candidate_baseline_points:
            raise ValueError("baseline pairing counts exceed candidate baseline points")
        if self.unmatched_points != self.current_only_points + self.baseline_only_points:
            raise ValueError(
                "unmatched_points must equal current_only_points plus baseline_only_points"
            )
        if self.dropped_points > self.unmatched_points:
            raise ValueError("dropped_points cannot exceed unmatched_points")
        if self.dropped_points == 0 and self.dropped_reason is not None:
            raise ValueError("dropped_reason requires dropped_points")
        return self


class _WindowBucketAlignmentPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["window_bucket"] = "window_bucket"
    mode: Literal["ordinal_bucket", "calendar_bucket"] = "ordinal_bucket"
    strict_lengths: bool = False


class _DayOfWeekAlignmentPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["day_of_week"] = "day_of_week"
    within: Grain = Field(default_factory=lambda: builtin_grain("month"))
    unmatched: Literal["fail", "drop"] = "fail"


class _PeriodProgressAlignmentPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["period_progress"] = "period_progress"
    unmatched: Literal["fail", "drop"] = "fail"


class _PeriodCorrespondenceAlignmentPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["period_correspondence"] = "period_correspondence"
    correspondence: str
    unmatched: Literal["fail", "drop"] = "fail"

    @model_validator(mode="after")
    def _validate_correspondence(self) -> _PeriodCorrespondenceAlignmentPayloadV1:
        if not self.correspondence.strip():
            raise ValueError("correspondence must be a non-empty name")
        return self


class _OccurrenceProgressAlignmentPayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["occurrence_progress"] = "occurrence_progress"
    anchor: Literal["start", "end"] = "start"
    unmatched: Literal["fail", "drop"] = "fail"


_AlignmentPolicyPayloadV1 = Annotated[
    _WindowBucketAlignmentPayloadV1
    | _DayOfWeekAlignmentPayloadV1
    | _PeriodProgressAlignmentPayloadV1
    | _PeriodCorrespondenceAlignmentPayloadV1
    | _OccurrenceProgressAlignmentPayloadV1,
    Field(discriminator="kind"),
]


class ComparisonTemporalContractV1(BaseModel):
    """Closed temporal authority and pairing evidence for a comparison artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)

    schema_: Literal["comparison-temporal/v1"] = Field(
        default="comparison-temporal/v1",
        alias="schema",
    )
    current: FrameTemporalContractV1
    baseline: FrameTemporalContractV1
    alignment_policy: _AlignmentPolicyPayloadV1 | None = None
    resolved_target_period: PeriodBindingV1 | None = None
    work_schedule: dict[str, object] | None = None
    alignment_evidence: AlignmentEvidenceV1


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


def _validate_time_scope_bounds(
    start: str | datetime | date,
    end: str | datetime | date,
) -> None:
    """Validate one half-open scope before it enters a temporal contract."""

    if isinstance(start, str) and isinstance(end, str):
        start_value, end_value = _parse_scope_strings(start, end)
    elif isinstance(start, str) or isinstance(end, str):
        raise ValueError("TimeScope cannot mix string and normalized bounds")
    else:
        start_value, end_value = start, end
        if type(start_value) is not type(end_value):
            raise ValueError("TimeScope cannot mix date and datetime bounds")
    try:
        if start_value >= end_value:
            raise ValueError("TimeScope requires start < end")
    except TypeError as exc:
        raise ValueError("TimeScope bounds must be comparable date or datetime values") from exc


def time_scope(
    *,
    start: date | datetime | str,
    end: date | datetime | str,
) -> TimeScope:
    """Construct one validated absolute public analysis scope.

    Public callers should use this helper instead of constructing ``TimeScope``
    directly. Calendar-period scopes are returned by certified catalog lookups.
    """

    _validate_time_scope_bounds(start, end)
    return _new_time_scope(start=start, end=end)


def absolute_time_scope(*, start: date, end: date) -> TimeScope:
    return time_scope(start=start, end=end)


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


def _builtin_periods_between(
    level: str,
    start: date,
    end: date,
) -> tuple[PeriodRecord, ...]:
    """Enumerate built-in periods wholly inside a target civil interval."""
    resolver = GregorianIsoResolver()
    periods: list[PeriodRecord] = []
    cursor = start
    while cursor < end:
        period = resolver.period_on(level, cursor)
        if period.start_date < start or period.end_date > end:
            raise KeyError(f"built-in {level}:{period.key!r} crosses target bounds")
        periods.append(period)
        cursor = period.end_date
    return tuple(periods)


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
class PeriodProgressCoordinate:
    """Local progress inside one certified period, independent of UTC duration."""

    day_ordinal: int
    microseconds_of_day: int = 0

    def __post_init__(self) -> None:
        if self.day_ordinal < 0 or not 0 <= self.microseconds_of_day < 86_400_000_000:
            raise ValueError("period progress coordinate is outside one civil day")


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
            return _new_time_scope(
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
                return _new_time_scope(
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


def _normalize_temporal_timestamp(value: datetime, *, boundary_timezone: str) -> datetime:
    """Normalize one timestamp occurrence to an aware UTC instant."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(boundary_timezone))
    return value.astimezone(ZoneInfo("UTC"))


@dataclass(frozen=True, slots=True)
class TemporalOccurrenceRecord:
    """One normalized named temporal occurrence."""

    key: _JSON_SCALAR
    start: date | datetime
    end: date | datetime
    category: str | None = None

    def __post_init__(self) -> None:
        canonical_key(self.key)
        if type(self.start) is not type(self.end) or self.start >= self.end:
            raise ValueError("temporal occurrence bounds must have one type and start < end")
        if self.category is not None and (type(self.category) is not str or not self.category):
            raise ValueError("temporal occurrence category must be a non-empty string or null")


@dataclass(frozen=True, slots=True)
class TemporalSetSnapshotV1:
    """Certified finite temporal-set occurrence authority."""

    temporal_set_ref: Ref[TemporalSetKind]
    boundary_timezone: str
    coverage: tuple[date, date]
    encoding: Literal["date", "timestamp"]
    occurrences: tuple[TemporalOccurrenceRecord, ...]
    snapshot_digest: str
    schema: Literal["temporal-set-snapshot/v1"] = "temporal-set-snapshot/v1"

    def __post_init__(self) -> None:
        if self.schema != "temporal-set-snapshot/v1":
            raise ValueError("unsupported temporal-set snapshot schema")
        if (
            type(self.temporal_set_ref) is not Ref
            or self.temporal_set_ref.kind.value != "temporal_set"
        ):
            raise TypeError("temporal_set_ref must be Ref[temporal_set]")
        _require_timezone(self.boundary_timezone)
        start, end = self.coverage
        if type(start) is not date or type(end) is not date or start >= end:
            raise ValueError("coverage must be a non-empty [start, end) civil-date interval")
        if self.encoding not in {"date", "timestamp"}:
            raise ValueError("temporal-set encoding must be date or timestamp")
        seen: set[str] = set()
        for occurrence in self.occurrences:
            occurrence_start = occurrence.start
            occurrence_end = occurrence.end
            if self.encoding == "date":
                if type(occurrence_start) is not date or type(occurrence_end) is not date:
                    raise ValueError("temporal-set occurrences must use one consistent encoding")
            else:
                if type(occurrence_start) is not datetime or type(occurrence_end) is not datetime:
                    raise ValueError("temporal-set occurrences must use one consistent encoding")
                if (
                    occurrence_start.tzinfo is None
                    or occurrence_end.tzinfo is None
                    or occurrence_start.utcoffset() is None
                    or occurrence_end.utcoffset() is None
                ):
                    raise ValueError(
                        "timestamp temporal-set occurrences must be timezone-aware instants"
                    )
            token = _key_token(occurrence.key)
            if token in seen:
                raise ValueError("temporal-set occurrence ids must be unique")
            seen.add(token)
            if self.encoding == "date":
                assert type(occurrence_start) is date and type(occurrence_end) is date
                inside = occurrence_start >= start and occurrence_end <= end
            else:
                assert type(occurrence_start) is datetime and type(occurrence_end) is datetime
                coverage_start = datetime.combine(
                    start, time.min, tzinfo=ZoneInfo(self.boundary_timezone)
                )
                coverage_end = datetime.combine(
                    end, time.min, tzinfo=ZoneInfo(self.boundary_timezone)
                )
                inside = occurrence_start >= coverage_start.astimezone(
                    ZoneInfo("UTC")
                ) and occurrence_end <= coverage_end.astimezone(ZoneInfo("UTC"))
            if not inside:
                raise ValueError(
                    f"temporal occurrence {occurrence.key!r} is outside declared coverage"
                )
        expected = _temporal_set_snapshot_digest(
            temporal_set_ref=self.temporal_set_ref,
            boundary_timezone=self.boundary_timezone,
            coverage=self.coverage,
            encoding=self.encoding,
            occurrences=self.occurrences,
        )
        if self.snapshot_digest != expected:
            raise ValueError("snapshot_digest does not match normalized temporal-set content")

    def occurrence_scope(self, key: _JSON_SCALAR) -> TimeScope:
        token = _key_token(canonical_key(key))
        for occurrence in self.occurrences:
            if _key_token(occurrence.key) == token:
                return _new_time_scope(
                    start=occurrence.start,
                    end=occurrence.end,
                    temporal_set=self.temporal_set_ref,
                    snapshot_digest=self.snapshot_digest,
                    boundary_timezone=self.boundary_timezone,
                    key=occurrence.key,
                    occurrence_category=occurrence.category,
                )
        raise KeyError(f"temporal occurrence {key!r} is not in certified temporal set")


def _temporal_set_snapshot_payload(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    encoding: Literal["date", "timestamp"],
    occurrences: tuple[TemporalOccurrenceRecord, ...],
) -> dict[str, object]:
    return {
        "schema": "temporal-set-snapshot/v1",
        "temporal_set_ref": temporal_set_ref.key,
        "boundary_timezone": boundary_timezone,
        "coverage": [coverage[0].isoformat(), coverage[1].isoformat()],
        "encoding": encoding,
        "occurrences": [
            [
                _key_token(item.key),
                item.start.isoformat(),
                item.end.isoformat(),
                item.category,
            ]
            for item in occurrences
        ],
    }


def _temporal_set_snapshot_digest(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    encoding: Literal["date", "timestamp"],
    occurrences: tuple[TemporalOccurrenceRecord, ...],
) -> str:
    payload = _temporal_set_snapshot_payload(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        encoding=encoding,
        occurrences=occurrences,
    )
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def certify_temporal_set(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    rows: Iterable[Mapping[str, object]],
    occurrence_id: str,
    start: str,
    end: str,
    category: str | None = None,
) -> TemporalSetSnapshotV1:
    """Certify named occurrences from one previously acquired value snapshot."""
    _require_timezone(boundary_timezone)
    if type(temporal_set_ref) is not Ref or temporal_set_ref.kind.value != "temporal_set":
        raise TypeError("temporal_set_ref must be Ref[temporal_set]")
    coverage_start, coverage_end = coverage
    if (
        type(coverage_start) is not date
        or type(coverage_end) is not date
        or coverage_start >= coverage_end
    ):
        raise ValueError("coverage must be a non-empty [start, end) civil-date interval")
    for field_name, field in (("occurrence_id", occurrence_id), ("start", start), ("end", end)):
        if type(field) is not str or not field:
            raise ValueError(f"{field_name} source column must be a non-empty string")
    if category is not None and (type(category) is not str or not category):
        raise ValueError("category source column must be a non-empty string or null")

    normalized: list[TemporalOccurrenceRecord] = []
    encoding: Literal["date", "timestamp"] | None = None
    seen: set[str] = set()
    for row in rows:
        if occurrence_id not in row or start not in row or end not in row:
            raise ValueError("temporal-set snapshot row is missing a required source column")
        key = canonical_key(row[occurrence_id])
        raw_start = row[start]
        raw_end = row[end]
        if raw_start is None or raw_end is None:
            raise ValueError(f"temporal occurrence {key!r} has null bounds")
        if type(raw_start) is date and type(raw_end) is date:
            current_encoding: Literal["date", "timestamp"] = "date"
            start_value: date | datetime = raw_start
            end_value: date | datetime = raw_end
        elif type(raw_start) is datetime and type(raw_end) is datetime:
            current_encoding = "timestamp"
            start_value = _normalize_temporal_timestamp(
                raw_start, boundary_timezone=boundary_timezone
            )
            end_value = _normalize_temporal_timestamp(raw_end, boundary_timezone=boundary_timezone)
        else:
            raise ValueError(
                "temporal-set start/end values must use matching date or datetime encoding"
            )
        if encoding is None:
            encoding = current_encoding
        elif encoding != current_encoding:
            raise ValueError("temporal-set occurrences cannot mix date and timestamp encoding")
        category_value = row.get(category) if category is not None else None
        if category is not None and category not in row:
            raise ValueError("temporal-set snapshot row is missing the category source column")
        if category_value is not None and (type(category_value) is not str or not category_value):
            raise ValueError("temporal occurrence category must be a non-empty string or null")
        token = _key_token(key)
        if token in seen:
            raise ValueError(f"temporal-set occurrence id {key!r} is duplicated")
        seen.add(token)
        normalized.append(
            TemporalOccurrenceRecord(
                key=key,
                start=start_value,
                end=end_value,
                category=category_value,
            )
        )
    if encoding is None:
        raise ValueError("temporal-set snapshot must contain at least one occurrence")
    ordered = tuple(
        sorted(normalized, key=lambda item: (item.start, item.end, _key_token(item.key)))
    )
    digest = _temporal_set_snapshot_digest(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        encoding=encoding,
        occurrences=ordered,
    )
    return TemporalSetSnapshotV1(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        encoding=encoding,
        occurrences=ordered,
        snapshot_digest=digest,
    )


_STRPTIME_TIME_DIRECTIVES = frozenset(
    {
        "%H",
        "%I",
        "%k",
        "%l",
        "%M",
        "%S",
        "%f",
        "%p",
        "%P",
    }
)


def _parse_persisted_temporal_value(
    value: object,
    *,
    parse: object | None = None,
    boundary_timezone: str | None = None,
) -> date | datetime:
    """Decode one retained source value under its authored time convention.

    Discovery snapshots persist values as JSON scalars, so a semantic time
    dimension's ``strptime``/timezone declaration must be reapplied before
    certification.  The helper intentionally uses the small value-object
    protocol (``kind``, ``format``, and ``timezone``) instead of importing the
    semantic layer into this dependency-neutral module.
    """
    parse_kind = getattr(parse, "kind", None)
    if parse_kind == "hour_prefix":
        raise ValueError("hour-prefix temporal-set bounds require a complete date-bearing source")
    if parse_kind == "date":
        if type(value) is date:
            return value
        if type(value) is datetime:
            if value.time() != time.min:
                raise ValueError("date temporal-set bounds must not contain a time component")
            return value.date()
        if type(value) is not str:
            raise TypeError("persisted date temporal-set bounds must be ISO date strings")
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"invalid persisted temporal date {value!r}") from exc
    if parse_kind in {"datetime", "timestamp"}:
        timezone = getattr(parse, "timezone", None) or boundary_timezone
        decoded = _parse_persisted_temporal_value(value, boundary_timezone=boundary_timezone)
        if type(decoded) is date:
            decoded = datetime.combine(decoded, time.min)
        assert type(decoded) is datetime
        if decoded.tzinfo is None:
            if not isinstance(timezone, str) or not timezone:
                raise ValueError("naive timestamp bounds require a declared or boundary timezone")
            decoded = decoded.replace(tzinfo=ZoneInfo(timezone))
        return decoded
    if parse_kind == "strptime":
        fmt = getattr(parse, "format", None)
        if type(fmt) is not str or not fmt:
            raise ValueError("strptime temporal-set bounds require a non-empty format")
        if type(value) not in {str, int, float}:
            raise TypeError("strptime temporal-set bounds must be string or numeric scalars")
        try:
            decoded = datetime.strptime(str(value), fmt)
        except ValueError as exc:
            raise ValueError(
                f"persisted temporal value {value!r} does not match strptime format {fmt!r}"
            ) from exc
        tokens = set(re.findall(r"%[a-zA-Z]", fmt))
        if not tokens.intersection(_STRPTIME_TIME_DIRECTIVES):
            return decoded.date()
        timezone = getattr(parse, "timezone", None) or boundary_timezone
        if not isinstance(timezone, str) or not timezone:
            raise ValueError("time-bearing strptime bounds require a declared or boundary timezone")
        return decoded.replace(tzinfo=ZoneInfo(timezone))
    if type(value) is date or type(value) is datetime:
        return value
    if type(value) is not str:
        raise TypeError("persisted temporal-set bounds must be ISO date or datetime strings")
    raw = value.strip()
    if len(raw) == 10 and "T" not in raw and " " not in raw:
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"invalid persisted temporal date {value!r}") from exc
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid persisted temporal datetime {value!r}") from exc


def certify_temporal_set_rows(
    *,
    temporal_set_ref: Ref[TemporalSetKind],
    boundary_timezone: str,
    coverage: tuple[date, date],
    columns: tuple[str, ...],
    retained_values: tuple[tuple[object, ...], ...],
    occurrence_id: str,
    start: str,
    end: str,
    category: str | None = None,
    start_parse: object | None = None,
    end_parse: object | None = None,
) -> TemporalSetSnapshotV1:
    """Certify rows retained by one persisted datasource acquisition."""
    positions = {name: index for index, name in enumerate(columns)}
    required = (occurrence_id, start, end, *((category,) if category is not None else ()))
    missing = tuple(name for name in required if name not in positions)
    if missing:
        raise ValueError(f"persisted snapshot is missing temporal-set columns {missing!r}")
    rows: list[dict[str, object]] = []
    for values in retained_values:
        if len(values) != len(columns):
            raise ValueError("persisted snapshot row width does not match selected columns")
        rows.append(
            {
                occurrence_id: values[positions[occurrence_id]],
                start: _parse_persisted_temporal_value(
                    values[positions[start]],
                    parse=start_parse,
                    boundary_timezone=boundary_timezone,
                ),
                end: _parse_persisted_temporal_value(
                    values[positions[end]],
                    parse=end_parse,
                    boundary_timezone=boundary_timezone,
                ),
                **({category: values[positions[category]]} if category is not None else {}),
            }
        )
    return certify_temporal_set(
        temporal_set_ref=temporal_set_ref,
        boundary_timezone=boundary_timezone,
        coverage=coverage,
        rows=rows,
        occurrence_id=occurrence_id,
        start=start,
        end=end,
        category=category,
    )


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

    def period_progress(
        self, level: str, instant_or_date: date | datetime
    ) -> PeriodProgressCoordinate: ...

    def containing_period(
        self, from_level: str, key: _JSON_SCALAR, to_level: str
    ) -> PeriodRecord: ...

    def ordinal_within(self, from_level: str, key: _JSON_SCALAR, to_level: str) -> int: ...

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

    def period_progress(
        self, level: str, instant_or_date: date | datetime
    ) -> PeriodProgressCoordinate:
        local = _local_civil_datetime(
            instant_or_date,
            boundary_timezone=self._snapshot.boundary_timezone,
        )
        period = self.period_on(level, local.date())
        return PeriodProgressCoordinate(
            day_ordinal=(local.date() - period.start_date).days,
            microseconds_of_day=(
                (local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000
                + local.microsecond
            ),
        )

    def containing_period(self, from_level: str, key: _JSON_SCALAR, to_level: str) -> PeriodRecord:
        if from_level == to_level:
            return self.period(from_level, key)
        token = _key_token(canonical_key(key))
        matches = [
            record
            for record in self._snapshot.containments
            if record.source_level == from_level
            and record.target_level == to_level
            and _key_token(record.source_key) == token
        ]
        if len(matches) != 1:
            raise KeyError(
                f"{from_level}:{key!r} has no unique certified containing {to_level!r} period"
            )
        return self.period(to_level, matches[0].target_key)

    def ordinal_within(self, from_level: str, key: _JSON_SCALAR, to_level: str) -> int:
        if from_level == to_level:
            self.period(from_level, key)
            return 0
        token = _key_token(canonical_key(key))
        matches = [
            record
            for record in self._snapshot.containments
            if record.source_level == from_level
            and record.target_level == to_level
            and _key_token(record.source_key) == token
        ]
        if len(matches) != 1:
            raise KeyError(f"{from_level}:{key!r} has no unique certified ordinal in {to_level!r}")
        return matches[0].ordinal_in_target

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

    __slots__ = ("_boundary_timezone",)

    def __init__(self, boundary_timezone: str = "UTC") -> None:
        _require_timezone(boundary_timezone)
        self._boundary_timezone = boundary_timezone

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

    def period_progress(
        self, level: str, instant_or_date: date | datetime
    ) -> PeriodProgressCoordinate:
        local = _local_civil_datetime(
            instant_or_date,
            boundary_timezone=self._boundary_timezone,
        )
        period = self.period_on(level, local.date())
        return PeriodProgressCoordinate(
            day_ordinal=(local.date() - period.start_date).days,
            microseconds_of_day=(
                (local.hour * 3600 + local.minute * 60 + local.second) * 1_000_000
                + local.microsecond
            ),
        )

    def containing_period(self, from_level: str, key: _JSON_SCALAR, to_level: str) -> PeriodRecord:
        source = self.period(from_level, key)
        if from_level == to_level:
            return source
        target = self.period_on(to_level, source.start_date)
        if target.start_date > source.start_date or source.end_date > target.end_date:
            raise KeyError(f"{from_level}:{key!r} has no unique containing {to_level!r} period")
        return target

    def ordinal_within(self, from_level: str, key: _JSON_SCALAR, to_level: str) -> int:
        source = self.period(from_level, key)
        target = self.containing_period(from_level, key, to_level)
        if from_level == to_level:
            return 0
        periods = _builtin_periods_between(from_level, target.start_date, target.end_date)
        for ordinal, period in enumerate(periods):
            if period.key == source.key:
                return ordinal
        raise KeyError(f"{from_level}:{key!r} is not contained in {to_level}:{target.key!r}")

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

    def load_exact(
        self,
        calendar_ref: Ref[PeriodCalendarKind],
        *,
        snapshot_digest: str,
    ) -> PeriodCalendarSnapshotV1:
        """Load one immutable snapshot by its persisted authority identity."""
        if type(snapshot_digest) is not str or not snapshot_digest:
            raise ValueError("snapshot_digest must be a non-empty string")
        snapshot_path = self._directory(calendar_ref) / f"{snapshot_digest}.json"
        if not snapshot_path.is_file():
            raise KeyError(
                f"certified snapshot {snapshot_digest!r} for {calendar_ref.path!r} is unavailable"
            )
        snapshot = _snapshot_from_json(_read_json(snapshot_path))
        if snapshot.calendar_ref != calendar_ref or snapshot.snapshot_digest != snapshot_digest:
            raise ValueError(
                "persisted temporal snapshot identity does not match requested binding"
            )
        return snapshot

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


@dataclass(frozen=True, slots=True)
class TemporalSetManifestV1:
    """Current certified snapshot pointer for one temporal set declaration."""

    temporal_set_ref: Ref[TemporalSetKind]
    definition_digest: str
    snapshot_digest: str
    schema: Literal["temporal-set-manifest/v1"] = "temporal-set-manifest/v1"


class TemporalSetSnapshotStore:
    """Project-local atomic persistence for certified temporal-set authorities."""

    __slots__ = ("_root",)

    def __init__(self, project_root: Path) -> None:
        self._root = project_root / ".marivo" / "temporal" / "temporal-sets"

    def publish(
        self,
        snapshot: TemporalSetSnapshotV1,
        *,
        definition_digest: str,
    ) -> TemporalSetManifestV1:
        if type(definition_digest) is not str or not definition_digest:
            raise ValueError("definition_digest must be a non-empty string")
        directory = self._directory(snapshot.temporal_set_ref)
        directory.mkdir(parents=True, exist_ok=True)
        self._write_json(
            directory / f"{snapshot.snapshot_digest}.json",
            _temporal_set_snapshot_json(snapshot),
        )
        manifest = TemporalSetManifestV1(
            temporal_set_ref=snapshot.temporal_set_ref,
            definition_digest=definition_digest,
            snapshot_digest=snapshot.snapshot_digest,
        )
        self._write_json(directory / "current.json", _temporal_set_manifest_json(manifest))
        return manifest

    def load_current(
        self,
        temporal_set_ref: Ref[TemporalSetKind],
        *,
        definition_digest: str,
    ) -> TemporalSetSnapshotV1 | None:
        status, snapshot = self.inspect_current(
            temporal_set_ref,
            definition_digest=definition_digest,
        )
        return snapshot if status == "current" else None

    def load_exact(
        self,
        temporal_set_ref: Ref[TemporalSetKind],
        *,
        snapshot_digest: str,
    ) -> TemporalSetSnapshotV1:
        if type(snapshot_digest) is not str or not snapshot_digest:
            raise ValueError("snapshot_digest must be a non-empty string")
        path = self._directory(temporal_set_ref) / f"{snapshot_digest}.json"
        if not path.is_file():
            raise KeyError(
                f"certified snapshot {snapshot_digest!r} for {temporal_set_ref.path!r} is unavailable"
            )
        snapshot = _temporal_set_snapshot_from_json(_read_json(path))
        if (
            snapshot.temporal_set_ref != temporal_set_ref
            or snapshot.snapshot_digest != snapshot_digest
        ):
            raise ValueError(
                "persisted temporal-set snapshot identity does not match requested binding"
            )
        return snapshot

    def inspect_current(
        self,
        temporal_set_ref: Ref[TemporalSetKind],
        *,
        definition_digest: str,
    ) -> tuple[Literal["missing", "current", "stale", "invalid"], TemporalSetSnapshotV1 | None]:
        directory = self._directory(temporal_set_ref)
        manifest_path = directory / "current.json"
        if not manifest_path.exists():
            return "missing", None
        try:
            manifest = _temporal_set_manifest_from_json(_read_json(manifest_path))
        except (OSError, TypeError, ValueError, KeyError, IndexError):
            return "invalid", None
        if manifest.temporal_set_ref != temporal_set_ref:
            return "invalid", None
        if manifest.definition_digest != definition_digest:
            return "stale", None
        snapshot_path = directory / f"{manifest.snapshot_digest}.json"
        if not snapshot_path.exists():
            return "invalid", None
        try:
            snapshot = _temporal_set_snapshot_from_json(_read_json(snapshot_path))
        except (OSError, TypeError, ValueError, KeyError, IndexError):
            return "invalid", None
        if (
            snapshot.temporal_set_ref != temporal_set_ref
            or snapshot.snapshot_digest != manifest.snapshot_digest
        ):
            return "invalid", None
        return "current", snapshot

    def _directory(self, temporal_set_ref: Ref[TemporalSetKind]) -> Path:
        token = hashlib.sha256(temporal_set_ref.key.encode()).hexdigest()
        return self._root / token

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, object]) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)


def _temporal_set_manifest_json(manifest: TemporalSetManifestV1) -> dict[str, object]:
    return {
        "schema": manifest.schema,
        "temporal_set_ref": manifest.temporal_set_ref.path,
        "definition_digest": manifest.definition_digest,
        "snapshot_digest": manifest.snapshot_digest,
    }


def _temporal_set_manifest_from_json(payload: Mapping[str, object]) -> TemporalSetManifestV1:
    if payload.get("schema") != "temporal-set-manifest/v1":
        raise ValueError("unsupported temporal set manifest schema")
    raw_ref = payload.get("temporal_set_ref")
    definition_digest = payload.get("definition_digest")
    snapshot_digest = payload.get("snapshot_digest")
    if not all(
        type(value) is str and value for value in (raw_ref, definition_digest, snapshot_digest)
    ):
        raise ValueError("temporal set manifest fields are invalid")
    return TemporalSetManifestV1(
        temporal_set_ref=ref_factory_temporal_set(cast("str", raw_ref)),
        definition_digest=cast("str", definition_digest),
        snapshot_digest=cast("str", snapshot_digest),
    )


def _temporal_set_snapshot_json(snapshot: TemporalSetSnapshotV1) -> dict[str, object]:
    return {
        **_temporal_set_snapshot_payload(
            temporal_set_ref=snapshot.temporal_set_ref,
            boundary_timezone=snapshot.boundary_timezone,
            coverage=snapshot.coverage,
            encoding=snapshot.encoding,
            occurrences=snapshot.occurrences,
        ),
        "snapshot_digest": snapshot.snapshot_digest,
    }


def _temporal_set_snapshot_from_json(payload: Mapping[str, object]) -> TemporalSetSnapshotV1:
    if payload.get("schema") != "temporal-set-snapshot/v1":
        raise ValueError("unsupported temporal set snapshot schema")
    raw_ref = payload.get("temporal_set_ref")
    raw_timezone = payload.get("boundary_timezone")
    raw_coverage = payload.get("coverage")
    raw_encoding = payload.get("encoding")
    raw_occurrences = payload.get("occurrences")
    digest = payload.get("snapshot_digest")
    if (
        type(raw_ref) is not str
        or type(raw_timezone) is not str
        or not isinstance(raw_coverage, list)
        or len(raw_coverage) != 2
        or raw_encoding not in {"date", "timestamp"}
        or not isinstance(raw_occurrences, list)
        or type(digest) is not str
    ):
        raise ValueError("temporal set snapshot payload fields are invalid")
    if not raw_ref.startswith("temporal_set:"):
        raise ValueError("temporal set snapshot ref must use temporal_set: prefix")
    coverage = (
        date.fromisoformat(cast("str", raw_coverage[0])),
        date.fromisoformat(cast("str", raw_coverage[1])),
    )
    occurrences: list[TemporalOccurrenceRecord] = []
    for raw in raw_occurrences:
        if not isinstance(raw, list) or len(raw) != 4 or not isinstance(raw[0], str):
            raise ValueError("temporal set occurrence payload is invalid")
        key = json.loads(raw[0])
        start = (
            date.fromisoformat(cast("str", raw[1]))
            if raw_encoding == "date"
            else datetime.fromisoformat(cast("str", raw[1]))
        )
        end = (
            date.fromisoformat(cast("str", raw[2]))
            if raw_encoding == "date"
            else datetime.fromisoformat(cast("str", raw[2]))
        )
        category = raw[3]
        if category is not None and type(category) is not str:
            raise ValueError("temporal set occurrence category payload is invalid")
        occurrences.append(
            TemporalOccurrenceRecord(
                key=canonical_key(key),
                start=start,
                end=end,
                category=category,
            )
        )
    return TemporalSetSnapshotV1(
        temporal_set_ref=ref_factory_temporal_set(raw_ref.removeprefix("temporal_set:")),
        boundary_timezone=raw_timezone,
        coverage=coverage,
        encoding=raw_encoding,
        occurrences=tuple(occurrences),
        snapshot_digest=digest,
    )


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


def ref_factory_temporal_set(path: str) -> Ref[TemporalSetKind]:
    """Late import helper for dependency-neutral temporal-set persistence."""
    from marivo.refs import ref

    return ref.temporal_set(path)
