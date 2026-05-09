from __future__ import annotations

import unittest
from datetime import date
from typing import Any, cast

from marivo.config import CalendarConfig
from marivo.runtime.semantic.calendar_data_runtime import (
    _RESOLVED_CALENDAR_SOURCE,
    CalendarDataReader,
    CalendarDataResolutionError,
)
from marivo.storage.metadata import MetadataStore


def _calendar_row(
    day: str,
    *,
    holiday_group_id: str | None = None,
    year_relative_holiday_key: str | None = None,
    event_group_id: str | None = None,
    year_relative_event_key: str | None = None,
    calendar_version: str = "cn_2026q2_v1",
) -> dict[str, Any]:
    day_value = date.fromisoformat(day)
    return {
        "calendar_date": day,
        "region_code": "CN",
        "calendar_version": calendar_version,
        "weekday": day_value.weekday() + 1,
        "is_weekend": 1 if day_value.weekday() >= 5 else 0,
        "is_workday": 1 if day_value.weekday() < 5 else 0,
        "holiday_name": None,
        "holiday_group_id": holiday_group_id,
        "year_relative_holiday_key": year_relative_holiday_key,
        "event_group_id": event_group_id,
        "year_relative_event_key": year_relative_event_key,
    }


class _FakeMetadata:
    def __init__(self, calendar_rows: list[dict[str, Any]] | None = None) -> None:
        self._calendar_rows = calendar_rows or []
        self._versions: list[str] = sorted({row["calendar_version"] for row in self._calendar_rows})

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        params = params or []
        if "FROM calendar" in sql and "MAX(calendar_version)" in sql:
            if not self._versions:
                return []
            return [{"max_version": self._versions[-1]}]
        if "FROM calendar" in sql:
            calendar_version = params[0] if params else ""
            region_code = params[1] if len(params) > 1 else "CN"
            read_start = params[2] if len(params) > 2 else ""
            read_end = params[3] if len(params) > 3 else ""
            return [
                row
                for row in self._calendar_rows
                if row["calendar_version"] == calendar_version
                and row["region_code"] == region_code
                and read_start <= row["calendar_date"] < read_end
            ]
        raise AssertionError(f"Unexpected query: {sql}")

    def query_one(self, sql: str, params: list[Any] | None = None) -> dict[str, Any] | None:
        params = params or []
        if "MAX(calendar_version)" in sql:
            rows = self.query_rows(sql, params)
            return rows[0] if rows else None
        raise AssertionError(f"Unexpected query: {sql}")


def _metadata_store(
    calendar_rows: list[dict[str, Any]] | None = None,
) -> MetadataStore:
    return cast("MetadataStore", _FakeMetadata(calendar_rows))


class CalendarDataReaderTests(unittest.TestCase):
    def _make_reader(
        self,
        calendar_rows: list[dict[str, Any]] | None = None,
        config: CalendarConfig | None = None,
    ) -> CalendarDataReader:
        return CalendarDataReader(
            metadata=_metadata_store(calendar_rows),
            config=config or CalendarConfig(),
        )

    def test_read_for_alignment_reads_single_table(self) -> None:
        rows = [
            _calendar_row(
                "2025-04-01",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-3",
                event_group_id="member_day",
                year_relative_event_key="member_day_d-1",
            ),
            _calendar_row(
                "2025-04-02",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-2",
                event_group_id="member_day",
                year_relative_event_key="member_day_d+0",
            ),
            _calendar_row(
                "2026-04-01",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-3",
                event_group_id="member_day",
                year_relative_event_key="member_day_d-1",
            ),
            _calendar_row(
                "2026-04-02",
                holiday_group_id="qingming",
                year_relative_holiday_key="qingming_d-2",
                event_group_id="member_day",
                year_relative_event_key="member_day_d+0",
            ),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 3)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 3)),
        )

        self.assertEqual(result.resolved_calendar_source, _RESOLVED_CALENDAR_SOURCE)
        self.assertEqual(result.resolved_calendar_version, "cn_2026q2_v1")
        self.assertEqual(len(result.annotation_rows), 4)
        self.assertEqual(result.annotation_rows[0].holiday_group_id, "qingming")
        self.assertEqual(result.annotation_rows[0].event_group_id, "member_day")

    def test_read_for_alignment_with_pinned_version(self) -> None:
        rows = [
            _calendar_row("2025-04-01", calendar_version="cn_2026q1_v1"),
            _calendar_row("2025-04-01", calendar_version="cn_2026q2_v1"),
            _calendar_row("2026-04-01", calendar_version="cn_2026q1_v1"),
            _calendar_row("2026-04-01", calendar_version="cn_2026q2_v1"),
        ]
        config = CalendarConfig(calendar_version="cn_2026q1_v1")
        reader = self._make_reader(rows, config=config)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertEqual(result.resolved_calendar_version, "cn_2026q1_v1")

    def test_read_for_alignment_discovers_latest_version(self) -> None:
        rows = [
            _calendar_row("2025-04-01", calendar_version="cn_2026q1_v1"),
            _calendar_row("2025-04-01", calendar_version="cn_2026q2_v1"),
            _calendar_row("2026-04-01", calendar_version="cn_2026q1_v1"),
            _calendar_row("2026-04-01", calendar_version="cn_2026q2_v1"),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertEqual(result.resolved_calendar_version, "cn_2026q2_v1")

    def test_read_for_alignment_raises_when_no_data(self) -> None:
        reader = self._make_reader([])

        with self.assertRaises(CalendarDataResolutionError) as ctx:
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

        self.assertIn("no calendar data", str(ctx.exception).lower())

    def test_read_for_alignment_raises_when_pinned_version_missing(self) -> None:
        rows = [_calendar_row("2026-04-01", calendar_version="cn_2026q2_v1")]
        config = CalendarConfig(calendar_version="cn_2026q1_v1")
        reader = self._make_reader(rows, config=config)

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_read_for_alignment_rejects_invalid_weekday(self) -> None:
        rows = [
            {
                "calendar_date": "2026-04-01",
                "region_code": "CN",
                "calendar_version": "cn_2026q2_v1",
                "weekday": 8,
                "is_weekend": 0,
                "is_workday": 1,
                "holiday_name": None,
                "holiday_group_id": None,
                "year_relative_holiday_key": None,
                "event_group_id": None,
                "year_relative_event_key": None,
            },
        ]
        reader = self._make_reader(rows)

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_read_for_alignment_rejects_missing_weekend_workday(self) -> None:
        rows = [
            {
                "calendar_date": "2026-04-01",
                "region_code": "CN",
                "calendar_version": "cn_2026q2_v1",
                "weekday": 2,
                "is_weekend": None,
                "is_workday": 1,
                "holiday_name": None,
                "holiday_group_id": None,
                "year_relative_holiday_key": None,
                "event_group_id": None,
                "year_relative_event_key": None,
            },
        ]
        reader = self._make_reader(rows)

        with self.assertRaises(CalendarDataResolutionError):
            reader.read_for_alignment(
                current_window=(date(2026, 4, 1), date(2026, 4, 2)),
                baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
            )

    def test_source_lineage_contains_table_and_version(self) -> None:
        rows = [
            _calendar_row("2025-04-01"),
            _calendar_row("2026-04-01"),
        ]
        reader = self._make_reader(rows)

        result = reader.read_for_alignment(
            current_window=(date(2026, 4, 1), date(2026, 4, 2)),
            baseline_window=(date(2025, 4, 1), date(2025, 4, 2)),
        )

        self.assertIn("table_fqn", result.source_lineage)
        self.assertEqual(result.source_lineage["table_fqn"], "calendar")
        self.assertIn("calendar_version", result.source_lineage)


if __name__ == "__main__":
    unittest.main()
