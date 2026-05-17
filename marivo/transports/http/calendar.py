"""Calendar data API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from marivo.transports.http.deps import get_services
from marivo.transports.http.models.calendar import (
    CalendarDataLoadRequest,
    CalendarDataLoadResponse,
)

router = APIRouter(prefix="/calendar", tags=["calendar"])

_COLUMNS = (
    "calendar_date",
    "day_kind",
    "holiday_name",
    "holiday_group_id",
    "year_relative_holiday_key",
)

_INSERT_SQL = (
    "INSERT INTO calendar "
    "(calendar_date, day_kind, holiday_name, "
    "holiday_group_id, year_relative_holiday_key) "
    "VALUES (?, ?, ?, ?, ?)"
)


@router.put("/data", response_model=CalendarDataLoadResponse)
def load_calendar_data(
    request: Request, payload: CalendarDataLoadRequest
) -> CalendarDataLoadResponse:
    """Replace all calendar data with the provided rows.

    This is a destructive PUT — existing calendar data is deleted
    before the new rows are inserted.
    """
    services = get_services(request)
    store = services.metadata_store
    store.execute("DELETE FROM calendar")
    params = [
        (
            row.calendar_date,
            row.day_kind,
            row.holiday_name,
            row.holiday_group_id,
            row.year_relative_holiday_key,
        )
        for row in payload.rows
    ]
    store.execute_many(_INSERT_SQL, params)
    return CalendarDataLoadResponse(status="loaded", row_count=len(params))
