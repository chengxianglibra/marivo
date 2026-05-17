from __future__ import annotations

import json
import unittest
from typing import Any

from marivo.time_axis_metadata import (
    TimeAxisMetadataProvider,
    normalize_time_capabilities,
)


class _MetadataStub:
    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        if "JOIN semantic_metrics m" in sql and params == ["analytics.events", "event_count"]:
            return [
                {
                    "name": "query_time",
                    "expression": json.dumps(
                        {
                            "dialects": [
                                {
                                    "dialect": "ANSI_SQL",
                                    "expression": "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP)",
                                }
                            ]
                        }
                    ),
                    "data_type": "timestamp",
                    "is_time": 1,
                },
                {
                    "name": "create_date",
                    "expression": json.dumps(
                        {
                            "dialects": [
                                {
                                    "dialect": "ANSI_SQL",
                                    "expression": "CAST(SUBSTRING(create_time, 1, 10) AS DATE)",
                                }
                            ]
                        }
                    ),
                    "data_type": "date",
                    "is_time": 1,
                },
                {
                    "name": "cluster",
                    "expression": json.dumps(
                        {
                            "dialects": [
                                {"dialect": "ANSI_SQL", "expression": "cluster"},
                            ]
                        }
                    ),
                    "data_type": "varchar",
                    "is_time": 0,
                },
            ]
        if "WHERE d.source = ? ORDER BY f.position" in sql:
            return [
                {
                    "name": "query_time",
                    "expression": json.dumps(
                        {
                            "dialects": [
                                {"dialect": "ANSI_SQL", "expression": "wrong_model_time"},
                            ]
                        }
                    ),
                    "data_type": "timestamp",
                    "is_time": 1,
                },
            ]
        return []


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


class TimeAxisMetadataProviderTests(unittest.TestCase):
    def test_load_for_windowed_query_exposes_time_field_expressions(self) -> None:
        context = TimeAxisMetadataProvider(_MetadataStub()).load_for_windowed_query(
            table_name="analytics.events",
            metric_name="metric.event_count",
        )

        self.assertEqual(context.available_columns, ["query_time", "create_date", "cluster"])
        self.assertEqual(
            context.time_field_expressions["query_time"],
            "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP)",
        )
        self.assertEqual(
            context.time_field_expressions["create_date"],
            "CAST(SUBSTRING(create_time, 1, 10) AS DATE)",
        )
        self.assertEqual(context.time_field_data_types["query_time"], "timestamp")
        self.assertTrue(context.has_time_binding)

    def test_load_for_windowed_query_falls_back_to_source_fields_without_metric(self) -> None:
        context = TimeAxisMetadataProvider(_MetadataStub()).load_for_windowed_query(
            table_name="analytics.events"
        )

        self.assertEqual(context.available_columns, ["query_time"])
        self.assertEqual(context.time_field_expressions["query_time"], "wrong_model_time")
