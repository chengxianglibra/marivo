"""Immutable report and resolved source time authority for graph and remaining domain consumers."""

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from marivo._timezones import normalize_timezone, same_timezone


class ReportTimeAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    timezone: str = "UTC"
    resolution: Literal["iana", "fixed_offset"] = "iana"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return normalize_timezone(value).name


class _SourceTime(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    axis: str
    physical_type: str


class InstantSourceTime(_SourceTime):
    kind: Literal["instant"] = "instant"


class WallClockSourceTime(_SourceTime):
    kind: Literal["wall_clock"] = "wall_clock"
    timezone: str
    origin: Literal["field", "datasource"]
    inherited_from: str | None = None

    @field_validator("timezone")
    @classmethod
    def normalized_timezone(cls, value: str) -> str:
        return normalize_timezone(value).name


class CivilDateSourceTime(_SourceTime):
    kind: Literal["civil_date"] = "civil_date"
    timezone: str
    origin: Literal["field", "datasource"]

    @field_validator("timezone")
    @classmethod
    def normalized_timezone(cls, value: str) -> str:
        return normalize_timezone(value).name


SourceTimeAuthority = InstantSourceTime | WallClockSourceTime | CivilDateSourceTime


class TemporalExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    report: ReportTimeAuthority
    axes: tuple[SourceTimeAuthority, ...]

    @model_validator(mode="after")
    def exact_axes(self) -> "TemporalExecution":
        if not self.axes or len({axis.model_dump_json() for axis in self.axes}) != len(self.axes):
            raise ValueError("expected nonempty unique temporal execution axes")
        return self


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
