"""Calendar data API models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CalendarDataRow(BaseModel):
    """A single row of calendar data."""

    model_config = {"extra": "forbid"}

    calendar_date: str = Field(..., description="Date in YYYY-MM-DD format")
    weekday: int = Field(..., ge=1, le=7, description="Day of week, 1=Monday .. 7=Sunday")
    is_weekend: int = Field(..., ge=0, le=1, description="1 if weekend, 0 otherwise")
    is_workday: int = Field(..., ge=0, le=1, description="1 if workday, 0 otherwise")
    holiday_name: str | None = Field(default=None, description="Holiday name, if applicable")
    holiday_group_id: str = Field(
        default="", description="Holiday group identifier (empty string for non-holiday rows)"
    )
    year_relative_holiday_key: str | None = Field(
        default=None, description="Year-relative holiday key"
    )


class CalendarDataLoadRequest(BaseModel):
    """Request body for PUT /calendar/data."""

    model_config = {"extra": "forbid"}

    rows: list[CalendarDataRow] = Field(..., min_length=1, description="Calendar data rows")


class CalendarDataLoadResponse(BaseModel):
    """Response body for PUT /calendar/data."""

    model_config = {"extra": "forbid"}

    status: str
    row_count: int
