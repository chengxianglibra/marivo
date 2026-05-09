"""Calendar data API models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CalendarDataRow(BaseModel):
    """A single row of calendar data."""

    calendar_date: str = Field(..., description="Date in YYYY-MM-DD format")
    region_code: str = Field(default="CN", description="Region code (e.g. CN)")
    weekday: int = Field(..., ge=1, le=7, description="Day of week, 1=Monday .. 7=Sunday")
    is_weekend: int = Field(..., ge=0, le=1, description="1 if weekend, 0 otherwise")
    is_workday: int = Field(..., ge=0, le=1, description="1 if workday, 0 otherwise")
    holiday_name: str | None = Field(default=None, description="Holiday name, if applicable")
    holiday_group_id: str | None = Field(default=None, description="Holiday group identifier")
    year_relative_holiday_key: str | None = Field(
        default=None, description="Year-relative holiday key"
    )
    event_group_id: str | None = Field(default=None, description="Event group identifier")
    year_relative_event_key: str | None = Field(default=None, description="Year-relative event key")


class CalendarDataLoadRequest(BaseModel):
    """Request body for POST /calendar/data."""

    calendar_version: str = Field(..., description="Version identifier for this calendar dataset")
    rows: list[CalendarDataRow] = Field(..., min_length=1, description="Calendar data rows")


class CalendarDataLoadResponse(BaseModel):
    """Response body for POST /calendar/data."""

    status: str
    calendar_version: str
    row_count: int


class CalendarVersionItem(BaseModel):
    """A single calendar version entry."""

    calendar_version: str
    region_code: str
