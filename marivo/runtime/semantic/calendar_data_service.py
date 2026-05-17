"""Calendar data service for MCP-managed sparse holiday rows."""

from __future__ import annotations

from typing import Any

from marivo.adapters.metadata import MetadataStore
from marivo.contracts.calendar import (
    CalendarDataListResponse,
    CalendarDataQuery,
    CalendarDataRow,
    CalendarDataUpdateRequest,
    CalendarDataUpdateResponse,
)

_COLUMNS = [
    "calendar_date",
    "day_kind",
    "holiday_name",
    "holiday_group_id",
    "year_relative_holiday_key",
]


class CalendarDataService:
    """Read and incrementally upsert sparse calendar metadata rows."""

    def __init__(self, metadata: MetadataStore) -> None:
        self._metadata = metadata

    def list_calendar_data(self, input: CalendarDataQuery) -> CalendarDataListResponse:
        where: list[str] = []
        params: list[Any] = []
        if input.start_date is not None:
            where.append("calendar_date >= ?")
            params.append(input.start_date.isoformat())
        if input.end_date is not None:
            where.append("calendar_date < ?")
            params.append(input.end_date.isoformat())
        if input.day_kind is not None:
            where.append("day_kind = ?")
            params.append(input.day_kind)
        if input.holiday_group_id is not None:
            where.append("holiday_group_id = ?")
            params.append(input.holiday_group_id)

        sql = (
            "SELECT calendar_date, day_kind, holiday_name, "
            "holiday_group_id, year_relative_holiday_key FROM calendar"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY calendar_date, day_kind, holiday_group_id LIMIT ?"
        params.append(input.limit)

        rows = [
            CalendarDataRow.model_validate(row) for row in self._metadata.query_rows(sql, params)
        ]
        return CalendarDataListResponse(rows=rows, row_count=len(rows), query=input)

    def update_calendar_data(self, input: CalendarDataUpdateRequest) -> CalendarDataUpdateResponse:
        insert_sql = self._metadata.dialect.upsert_sql(
            "calendar",
            _COLUMNS,
            ["calendar_date", "day_kind", "holiday_group_id"],
            ["holiday_name", "year_relative_holiday_key"],
        )
        inserted_count = 0
        updated_count = 0
        with self._metadata.transaction() as tx:
            for row in input.rows:
                values = _row_values(row)
                existing = tx.query_one(
                    "SELECT calendar_date FROM calendar "
                    "WHERE calendar_date = ? AND day_kind = ? AND holiday_group_id = ?",
                    [values[0], values[1], values[3]],
                )
                if existing is None:
                    inserted_count += 1
                else:
                    updated_count += 1
                tx.execute(insert_sql, values)

        return CalendarDataUpdateResponse(
            status="updated",
            row_count=len(input.rows),
            inserted_count=inserted_count,
            updated_count=updated_count,
        )


def _row_values(row: CalendarDataRow) -> list[Any]:
    return [
        row.calendar_date.isoformat(),
        row.day_kind,
        row.holiday_name,
        row.holiday_group_id,
        row.year_relative_holiday_key,
    ]
