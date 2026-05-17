from __future__ import annotations

import pytest

from marivo.contracts.calendar import (
    CalendarDataQuery,
    CalendarDataRow,
    CalendarDataUpdateRequest,
)
from marivo.runtime.semantic.calendar_data_service import CalendarDataService
from tests.shared_fixtures import make_temp_metadata_store


def _service() -> tuple[CalendarDataService, object]:
    metadata = make_temp_metadata_store()
    return CalendarDataService(metadata), metadata


def test_list_calendar_data_uses_half_open_date_range() -> None:
    service, metadata = _service()
    try:
        service.update_calendar_data(
            CalendarDataUpdateRequest(
                rows=[
                    CalendarDataRow(
                        calendar_date="2026-02-16",
                        day_kind="holiday",
                        holiday_name="Spring Festival",
                        holiday_group_id="spring_festival",
                        year_relative_holiday_key="spring_festival_d0",
                    ),
                    CalendarDataRow(
                        calendar_date="2026-02-17",
                        day_kind="holiday",
                        holiday_name="Spring Festival",
                        holiday_group_id="spring_festival",
                        year_relative_holiday_key="spring_festival_d1",
                    ),
                ]
            )
        )

        result = service.list_calendar_data(
            CalendarDataQuery(start_date="2026-02-16", end_date="2026-02-17")
        )

        assert result.row_count == 1
        assert result.rows[0].calendar_date.isoformat() == "2026-02-16"
    finally:
        metadata.close()  # type: ignore[attr-defined]


def test_list_calendar_data_filters_by_kind_and_group() -> None:
    service, metadata = _service()
    try:
        service.update_calendar_data(
            CalendarDataUpdateRequest(
                rows=[
                    CalendarDataRow(
                        calendar_date="2026-10-01",
                        day_kind="holiday",
                        holiday_name="National Day",
                        holiday_group_id="national_day",
                    ),
                    CalendarDataRow(
                        calendar_date="2026-10-10",
                        day_kind="adjusted_workday",
                    ),
                ]
            )
        )

        result = service.list_calendar_data(
            CalendarDataQuery(day_kind="holiday", holiday_group_id="national_day")
        )

        assert result.row_count == 1
        assert result.rows[0].day_kind == "holiday"
        assert result.rows[0].holiday_group_id == "national_day"
    finally:
        metadata.close()  # type: ignore[attr-defined]


def test_update_calendar_data_upserts_without_deleting_existing_rows() -> None:
    service, metadata = _service()
    try:
        first = service.update_calendar_data(
            CalendarDataUpdateRequest(
                rows=[
                    CalendarDataRow(
                        calendar_date="2026-05-01",
                        day_kind="holiday",
                        holiday_name="Labor Day",
                        holiday_group_id="labor_day",
                    )
                ]
            )
        )
        second = service.update_calendar_data(
            CalendarDataUpdateRequest(
                rows=[
                    CalendarDataRow(
                        calendar_date="2026-06-19",
                        day_kind="holiday",
                        holiday_name="Dragon Boat Festival",
                        holiday_group_id="dragon_boat",
                    ),
                    CalendarDataRow(
                        calendar_date="2026-05-01",
                        day_kind="holiday",
                        holiday_name="International Labor Day",
                        holiday_group_id="labor_day",
                    ),
                ]
            )
        )
        listed = service.list_calendar_data(CalendarDataQuery())

        assert first.inserted_count == 1
        assert second.inserted_count == 1
        assert second.updated_count == 1
        assert listed.row_count == 2
        rows_by_group = {row.holiday_group_id: row for row in listed.rows}
        assert rows_by_group["labor_day"].holiday_name == "International Labor Day"
        assert rows_by_group["dragon_boat"].holiday_name == "Dragon Boat Festival"
    finally:
        metadata.close()  # type: ignore[attr-defined]


def test_update_calendar_data_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="duplicate calendar row keys"):
        CalendarDataUpdateRequest(
            rows=[
                CalendarDataRow(
                    calendar_date="2026-10-01",
                    day_kind="holiday",
                    holiday_group_id="national_day",
                ),
                CalendarDataRow(
                    calendar_date="2026-10-01",
                    day_kind="holiday",
                    holiday_group_id="national_day",
                ),
            ]
        )


def test_holiday_rows_require_group_id() -> None:
    with pytest.raises(ValueError, match="holiday_group_id"):
        CalendarDataRow(calendar_date="2026-10-01", day_kind="holiday")
