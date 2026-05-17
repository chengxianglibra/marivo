"""Calendar data API models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CalendarDataRow(BaseModel):
    """A single row of calendar data."""

    model_config = {"extra": "forbid"}

    calendar_date: str = Field(..., description="Date in YYYY-MM-DD format")
    day_kind: Literal["holiday", "adjusted_workday"] = Field(
        ..., description="Sparse calendar row kind"
    )
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
