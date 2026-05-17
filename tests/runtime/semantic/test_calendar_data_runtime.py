from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from marivo.adapters.metadata import MetadataStore
from marivo.runtime.semantic.calendar_data_runtime import (
    CalendarDataReader,
    CalendarDataReadResult,
    CalendarDataResolutionError,
)


def _calendar_row(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
) -> dict[str, Any]:
    day_value = date.fromisoformat(day)
    return {
        "calendar_date": day,
        "weekday": day_value.weekday() + 1,
        "is_weekend": 1 if day_value.weekday() >= 5 else 0,
        "is_workday": 1 if day_value.weekday() < 5 else 0,
        "holiday_name": None,
        "holiday_group_id": holiday_group_id or "",
        "year_relative_holiday_key": year_relative_holiday_key,
    }


class _FakeMetadata:
    def __init__(self, calendar_rows: list[dict[str, Any]] | None = None) -> None:
        self._calendar_rows = calendar_rows or []

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        params = params or []
        if "FROM calendar" in sql and "WHERE calendar_date >= ?" in sql:
            read_start = params[0] if params else ""
            read_end = params[1] if len(params) > 1 else ""
            return [
                row for row in self._calendar_rows if read_start <= row["calendar_date"] < read_end
            ]
        raise AssertionError(f"Unexpected query: {sql}")


def _metadata_store(
    calendar_rows: list[dict[str, Any]] | None = None,
) -> MetadataStore:
    return cast("MetadataStore", _FakeMetadata(calendar_rows))


class CalendarDataReaderTests(unittest.TestCase):
    def _make_reader(
        self,
        calendar_rows: list[dict[str, Any]] | None = None,
    ) -> CalendarDataReader:
        return CalendarDataReader(metadata=_metadata_store(calendar_rows))

    def test_read_for_alignment_returns_annotation_rows(self) -> None:
        rows = [
            _calendar_row(
                "2025-04-01",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_2025",
            ),
            _calendar_row(
                "2025-04-02",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_2025",
            ),
            _calendar_row(
                "2026-04-01",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_2026",
            ),
            _calendar_row(
                "2026-04-02",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_2026",
            ),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 3)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 3)),
        )

        self.assertIsInstance(result, CalendarDataReadResult)
        self.assertEqual(len(result.annotation_rows), 4)
        self.assertEqual(result.annotation_rows[0].holiday_group_id, "qingming")

    def test_read_for_alignment_multi_holiday_date(self) -> None:
        rows = [
            {
                "calendar_date": "2024-10-01",
                "weekday": 2,
                "is_weekend": 0,
                "is_workday": 0,
                "holiday_name": "国庆节",
                "holiday_group_id": "national_day",
                "year_relative_holiday_key": "national_day_2024",
            },
            {
                "calendar_date": "2024-10-01",
                "weekday": 2,
                "is_weekend": 0,
                "is_workday": 0,
                "holiday_name": "中秋节",
                "holiday_group_id": "mid_autumn",
                "year_relative_holiday_key": "mid_autumn_2024",
            },
        ]
        reader = self._make_reader(rows)
        result = reader.read_for_alignment(
            current_window=(date(2024, 10, 1), date(2024, 10, 2)),
            baseline_window=(date(2023, 9, 30), date(2023, 10, 1)),
        )
        oct1 = next(r for r in result.annotation_rows if r.calendar_date == date(2024, 10, 1))
        self.assertEqual(oct1.holiday_group_id, "national_day")
        self.assertIn("mid_autumn", oct1.extra_holiday_group_ids)

    def test_read_for_alignment_raises_when_no_data(self) -> None:
        reader = self._make_reader([])

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

        self.assertIn("calendar data unavailable", str(ctx.exception).lower())

    def test_source_lineage(self) -> None:
        rows = [
            _calendar_row("2025-04-01"),
            _calendar_row("2026-04-01"),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertEqual(result.source_lineage["table"], "calendar")


if __name__ == "__main__":
    unittest.main()
