"""Typed calendar data contracts shared by runtime and MCP surfaces."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CalendarDayKind = Literal["holiday", "adjusted_workday"]


class CalendarDataRow(BaseModel):
    """Sparse calendar annotation row used for holiday-aware alignment."""

    model_config = ConfigDict(extra="forbid")

    calendar_date: date = Field(
        ...,
        description="Calendar date for the holiday or adjusted-workday annotation.",
    )
    day_kind: CalendarDayKind = Field(
        ...,
        description="Sparse calendar row kind: holiday or adjusted_workday.",
    )
    holiday_name: str | None = Field(
        default=None,
        description="Human-readable holiday name when known.",
    )
    holiday_group_id: str = Field(
        default="",
        description=(
            "Stable holiday window identifier. Required and non-empty for holiday rows; "
            "usually empty for adjusted_workday rows."
        ),
    )
    year_relative_holiday_key: str | None = Field(
        default=None,
        description="Year-specific relative key within the holiday window, when known.",
    )

    @model_validator(mode="after")
    def _validate_holiday_group(self) -> CalendarDataRow:
        self.holiday_group_id = self.holiday_group_id.strip()
        if self.day_kind == "holiday" and not self.holiday_group_id:
            raise ValueError("holiday rows require non-empty holiday_group_id")
        return self


class CalendarDataQuery(BaseModel):
    """Query sparse calendar rows by optional half-open date range and row filters."""

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = Field(
        default=None,
        description="Inclusive lower bound for calendar_date.",
    )
    end_date: date | None = Field(
        default=None,
        description="Exclusive upper bound for calendar_date.",
    )
    day_kind: CalendarDayKind | None = Field(
        default=None,
        description="Optional row kind filter.",
    )
    holiday_group_id: str | None = Field(
        default=None,
        description="Optional exact holiday_group_id filter.",
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum rows to return.",
    )

    @model_validator(mode="after")
    def _validate_window(self) -> CalendarDataQuery:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date >= self.end_date
        ):
            raise ValueError("start_date must be before end_date")
        if self.holiday_group_id is not None:
            self.holiday_group_id = self.holiday_group_id.strip()
        return self


class CalendarDataListResponse(BaseModel):
    """Rows returned by a calendar data query."""

    model_config = ConfigDict(extra="forbid")

    rows: list[CalendarDataRow] = Field(description="Sparse calendar rows matching the query.")
    row_count: int = Field(ge=0, description="Number of returned rows.")
    query: CalendarDataQuery = Field(description="Normalized query used to read the rows.")


class CalendarDataUpdateRequest(BaseModel):
    """Incrementally upsert sparse calendar rows."""

    model_config = ConfigDict(extra="forbid")

    rows: list[CalendarDataRow] = Field(
        ...,
        min_length=1,
        description="Sparse calendar rows to insert or update without deleting other rows.",
    )

    @model_validator(mode="after")
    def _reject_duplicate_keys(self) -> CalendarDataUpdateRequest:
        seen: set[tuple[str, str, str]] = set()
        duplicates: list[str] = []
        for row in self.rows:
            key = (row.calendar_date.isoformat(), row.day_kind, row.holiday_group_id)
            if key in seen:
                duplicates.append("|".join(key))
            seen.add(key)
        if duplicates:
            joined = ", ".join(sorted(duplicates))
            raise ValueError(f"duplicate calendar row keys in request: {joined}")
        return self


class CalendarDataUpdateResponse(BaseModel):
    """Summary of an incremental calendar data upsert."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["updated"] = Field(description="Calendar update status.")
    row_count: int = Field(ge=0, description="Total rows processed.")
    inserted_count: int = Field(ge=0, description="Rows inserted because the key was absent.")
    updated_count: int = Field(ge=0, description="Rows updated because the key already existed.")


class CalendarMcpError(BaseModel):
    """Structured MCP error payload."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable error category.")
    message: str = Field(description="Human-readable error message.")


class CalendarDataListEnvelope(BaseModel):
    """MCP response envelope for list_calendar_data."""

    model_config = ConfigDict(extra="forbid")

    data: CalendarDataListResponse | None
    error: CalendarMcpError | None


class CalendarDataUpdateEnvelope(BaseModel):
    """MCP response envelope for update_calendar_data."""

    model_config = ConfigDict(extra="forbid")

    data: CalendarDataUpdateResponse | None
    error: CalendarMcpError | None
