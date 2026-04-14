from __future__ import annotations

import unittest

from app.time_contracts import (
    bucket_window,
    normalize_hour_boundary,
    normalize_timestamp_format,
    previous_adjacent_window,
)


class TimeContractsTests(unittest.TestCase):
    def test_normalize_timestamp_format_accepts_custom_strftime(self) -> None:
        self.assertEqual(normalize_timestamp_format("%Y%m%d %H:%M:%S"), "%Y%m%d %H:%M:%S")

    def test_normalize_timestamp_format_rejects_unsupported_directive(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported strftime directive"):
            normalize_timestamp_format("%Y-%m-%d %z")

    def test_normalize_hour_boundary_rejects_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "without timezone"):
            normalize_hour_boundary("2024-01-01T00:00:00+08:00", label="time_scope.start")

    def test_bucket_window_preserves_hour_boundaries(self) -> None:
        self.assertEqual(
            bucket_window("2024-01-01 03:00:00", "hour"),
            {"start": "2024-01-01T03:00:00", "end": "2024-01-01T04:00:00"},
        )

    def test_previous_adjacent_window_preserves_hour_grain(self) -> None:
        self.assertEqual(
            previous_adjacent_window(
                "2024-01-01T03:00:00",
                "2024-01-01T05:00:00",
                grain="hour",
            ),
            {"start": "2024-01-01T01:00:00", "end": "2024-01-01T03:00:00"},
        )
