from __future__ import annotations

import unittest

from marivo.time_axis_metadata import (
    normalize_time_capabilities,
)


class TimeCapabilitiesSchemaTests(unittest.TestCase):
    def test_normalize_time_capabilities_accepts_minimal_schema(self) -> None:
        normalized = normalize_time_capabilities(
            {
                "analysis_time": {
                    "timestamp_column": "event_time",
                    "timestamp_format": "native",
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
                "default_compare_grain": "day",
            }
        )
        self.assertEqual(
            normalized,
            {
                "analysis_time": {
                    "timestamp_column": "event_time",
                    "timestamp_format": "native",
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
                "default_compare_grain": "day",
            },
        )

    def test_normalize_time_capabilities_rejects_partition_hour_without_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "hour_column requires date_column"):
            normalize_time_capabilities({"partition_time": {"hour_column": "log_hour"}})

    def test_normalize_time_capabilities_rejects_analysis_hour_without_date(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "fallback_hour_column requires fallback_date_column"
        ):
            normalize_time_capabilities({"analysis_time": {"fallback_hour_column": "log_hour"}})

    def test_normalize_time_capabilities_accepts_custom_timestamp_format(self) -> None:
        normalized = normalize_time_capabilities(
            {
                "analysis_time": {
                    "timestamp_column": "create_time",
                    "timestamp_format": "%Y%m%d %H:%M:%S",
                }
            }
        )
        self.assertEqual(
            normalized,
            {
                "analysis_time": {
                    "timestamp_column": "create_time",
                    "timestamp_format": "%Y%m%d %H:%M:%S",
                }
            },
        )
