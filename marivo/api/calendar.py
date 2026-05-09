"""Calendar data API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from marivo.api.deps import get_services
from marivo.api.models.calendar import (
    CalendarDataLoadRequest,
    CalendarDataLoadResponse,
    CalendarVersionItem,
)

router = APIRouter()

_CALENDAR_COLUMNS = [
    "calendar_date",
    "region_code",
    "calendar_version",
    "weekday",
    "is_weekend",
    "is_workday",
    "holiday_name",
    "holiday_group_id",
    "year_relative_holiday_key",
    "event_group_id",
    "year_relative_event_key",
]

_INSERT_SQL = (
    "INSERT INTO calendar ("
    + ", ".join(_CALENDAR_COLUMNS)
    + ") VALUES ("
    + ", ".join("?" for _ in _CALENDAR_COLUMNS)
    + ")"
)


@router.post("/calendar/data", response_model=CalendarDataLoadResponse)
def load_calendar_data(
    payload: CalendarDataLoadRequest, request: Request
) -> CalendarDataLoadResponse:
    """Load calendar data rows into the metadata store.

    Returns 409 if the calendar_version already exists.
    """
    services = get_services(request)
    store = services.metadata_store

    # Check if version already exists
    existing = store.query_one(
        "SELECT 1 AS cnt FROM calendar WHERE calendar_version = ? LIMIT 1",
        [payload.calendar_version],
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"calendar_version '{payload.calendar_version}' already exists",
        )

    rows = [
        (
            row.calendar_date,
            row.region_code,
            payload.calendar_version,
            row.weekday,
            row.is_weekend,
            row.is_workday,
            row.holiday_name,
            row.holiday_group_id,
            row.year_relative_holiday_key,
            row.event_group_id,
            row.year_relative_event_key,
        )
        for row in payload.rows
    ]

    store.execute_many(_INSERT_SQL, rows)

    return CalendarDataLoadResponse(
        status="loaded",
        calendar_version=payload.calendar_version,
        row_count=len(rows),
    )


@router.get("/calendar/versions", response_model=list[CalendarVersionItem])
def list_calendar_versions(request: Request) -> list[CalendarVersionItem]:
    """List loaded calendar versions with their region codes."""
    services = get_services(request)
    rows = services.metadata_store.query_rows(
        "SELECT DISTINCT calendar_version, region_code FROM calendar ORDER BY calendar_version, region_code"
    )
    return [CalendarVersionItem(**row) for row in rows]
