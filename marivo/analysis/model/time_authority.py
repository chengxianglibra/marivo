"""Immutable report and resolved source time authority for graph and remaining domain consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal

from marivo._kernel.timezones import normalize_timezone, same_timezone


def _require_canonical_timezone(value: str) -> None:
    if normalize_timezone(value).name != value:
        raise ValueError("expected a canonical timezone name")


def _require_source_origin(origin: str) -> None:
    if origin not in ("field", "datasource"):
        raise ValueError("expected source time declared by a field or datasource")


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportTimeAuthority:
    timezone: str = "UTC"
    resolution: Literal["iana", "fixed_offset"] = "iana"

    def __post_init__(self) -> None:
        if self.resolution not in ("iana", "fixed_offset"):
            raise ValueError("expected an IANA or fixed-offset report time resolution")
        _require_canonical_timezone(self.timezone)


@dataclass(frozen=True, slots=True, kw_only=True)
class _SourceTime:
    axis: str
    physical_type: str

    def __post_init__(self) -> None:
        if type(self.axis) is not str or type(self.physical_type) is not str:
            raise ValueError("expected source axis and physical type text")


@dataclass(frozen=True, slots=True, kw_only=True)
class InstantSourceTime(_SourceTime):
    kind: Literal["instant"] = "instant"

    def __post_init__(self) -> None:
        _SourceTime.__post_init__(self)
        if self.kind != "instant":
            raise ValueError("expected instant source time")


@dataclass(frozen=True, slots=True, kw_only=True)
class WallClockSourceTime(_SourceTime):
    kind: Literal["wall_clock"] = "wall_clock"
    timezone: str
    origin: Literal["field", "datasource"]
    inherited_from: str | None = None

    def __post_init__(self) -> None:
        _SourceTime.__post_init__(self)
        if self.kind != "wall_clock":
            raise ValueError("expected wall-clock source time")
        _require_source_origin(self.origin)
        if self.inherited_from is not None and type(self.inherited_from) is not str:
            raise ValueError("expected the inherited source as text")
        _require_canonical_timezone(self.timezone)


@dataclass(frozen=True, slots=True, kw_only=True)
class CivilDateSourceTime(_SourceTime):
    kind: Literal["civil_date"] = "civil_date"
    timezone: str
    origin: Literal["field", "datasource"]

    def __post_init__(self) -> None:
        _SourceTime.__post_init__(self)
        if self.kind != "civil_date":
            raise ValueError("expected civil-date source time")
        _require_source_origin(self.origin)
        _require_canonical_timezone(self.timezone)


SourceTimeAuthority = InstantSourceTime | WallClockSourceTime | CivilDateSourceTime


@dataclass(frozen=True, slots=True, kw_only=True)
class TemporalExecution:
    report: ReportTimeAuthority
    axes: tuple[SourceTimeAuthority, ...]

    def __post_init__(self) -> None:
        if type(self.report) is not ReportTimeAuthority:
            raise ValueError("expected a report time authority")
        if type(self.axes) is not tuple or any(
            type(axis) not in (InstantSourceTime, WallClockSourceTime, CivilDateSourceTime)
            for axis in self.axes
        ):
            raise ValueError("expected an immutable tuple of source time authorities")
        if not self.axes or len(set(self.axes)) != len(self.axes):
            raise ValueError("expected nonempty unique temporal execution axes")


def civil_bound(
    value: date | datetime, *, report: str, boundary: str, civil_date: bool = False
) -> date | datetime:
    """Interpret a literal report endpoint in the selected boundary coordinate space."""
    if civil_date:
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(normalize_timezone(report).tz).replace(tzinfo=None)
        return value
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(normalize_timezone(boundary).tz).replace(tzinfo=None)
    if same_timezone(report, boundary):
        return value
    local = value if isinstance(value, datetime) else datetime.combine(value, time())
    return (
        local.replace(tzinfo=normalize_timezone(report).tz)
        .astimezone(normalize_timezone(boundary).tz)
        .replace(tzinfo=None)
    )
