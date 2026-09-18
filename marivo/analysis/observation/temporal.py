"""Immutable report and resolved source time authority for private observations."""

from datetime import date, datetime, time, tzinfo
from typing import Literal
from zoneinfo import ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


def time_zone(name: str) -> tzinfo:
    """Decode persisted IANA or explicit fixed-offset authority without host state."""
    from marivo.datasource.timezone import parse_timezone

    try:
        return parse_timezone(name)[1]
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("invalid persisted temporal timezone") from None


class ReportTimeAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    timezone: str = "UTC"
    resolution: Literal["iana", "fixed_offset"] = "iana"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        time_zone(value)
        return value


class SourceTimeAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    axis: str
    physical_type: str
    kind: Literal["instant", "localizable", "civil_date"]
    read_timezone: str | None
    source: Literal["physical", "declared", "engine", "system_fallback", "civil_date"]
    boundary_timezone: str

    @field_validator("read_timezone", "boundary_timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            time_zone(value)
        return value

    @model_validator(mode="after")
    def coherent_kind(self) -> "SourceTimeAuthority":
        if not self.axis or not self.physical_type:
            raise ValueError("missing exact time source identity")
        if self.kind == "civil_date":
            if self.read_timezone is not None or self.source != "civil_date":
                raise ValueError("civil dates cannot acquire a read timezone")
        elif self.read_timezone is None or self.source == "civil_date":
            raise ValueError("instant interpretation requires a read authority")
        elif (self.kind == "instant") != (self.source == "physical"):
            raise ValueError("physical instant and localizable authority differ")
        return self


class TemporalExecution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    report: ReportTimeAuthority
    axes: tuple[SourceTimeAuthority, ...]

    @model_validator(mode="after")
    def exact_axes(self) -> "TemporalExecution":
        if not self.axes or len({(axis.axis, axis.boundary_timezone) for axis in self.axes}) != len(
            self.axes
        ):
            raise ValueError("expected nonempty unique temporal execution axes")
        return self


def civil_bound(
    value: date | datetime, *, report: str, boundary: str, civil_date: bool = False
) -> date | datetime:
    """Interpret a literal report endpoint in the selected boundary coordinate space."""
    if civil_date:
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(time_zone(report)).replace(tzinfo=None)
        return value
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(time_zone(boundary)).replace(tzinfo=None)
    if report == boundary:
        return value
    local = value if isinstance(value, datetime) else datetime.combine(value, time())
    return (
        local.replace(tzinfo=time_zone(report)).astimezone(time_zone(boundary)).replace(tzinfo=None)
    )
