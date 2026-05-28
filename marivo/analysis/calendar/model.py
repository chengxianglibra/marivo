from __future__ import annotations

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

CalendarMode = Literal[
    "workday_aligned",
    "dow_aligned",
    "holiday_aligned",
    "holiday_and_dow_aligned",
]
AlignPeriod = Literal["day", "week", "month", "quarter", "year"]
CalendarFallback = Literal["drop", "nearest_prior_workday"]


class CalendarEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    name: str | None = None
    group_id: str | None = None

    @field_validator("date")
    @classmethod
    def validate_iso_date(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("date must be a valid ISO 8601 date (YYYY-MM-DD)") from exc
        if parsed.isoformat() != value:
            raise ValueError("date must be a valid ISO 8601 date (YYYY-MM-DD)")
        return value


class Calendar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    timezone: str
    holidays: list[CalendarEntry]
    adjusted_workdays: list[CalendarEntry] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"timezone '{value}' is not a valid IANA timezone") from exc
        return value


class CalendarPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: CalendarMode
    align_period: AlignPeriod
    fallback: CalendarFallback = "drop"


class CalendarInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calendar_name: str
    calendar_timezone: str
    session_timezone: str
    mode: CalendarMode
    align_period: AlignPeriod
    fallback: CalendarFallback
    matched_rows: int
    fallback_rows: int
    dropped_rows_a: int
    dropped_rows_b: int
