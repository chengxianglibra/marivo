from __future__ import annotations

import json
import sys
import types
import unittest
from typing import Any

from marivo.time_axis_metadata import (
    TimeAxisMetadataContext,
    TimeAxisMetadataProvider,
    normalize_time_capabilities,
)
from marivo.time_scope import normalize_aggregate_query_request


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
                    "support_min_granularity": "hour",
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
                    "support_min_granularity": "day",
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
                    "support_min_granularity": None,
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
                    "support_min_granularity": "hour",
                    "is_time": 1,
                },
            ]
        return []


class _DialectMetadataStub:
    def __init__(self, dialects: list[dict[str, str]]) -> None:
        self._dialects = dialects

    def query_rows(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        if "JOIN semantic_metrics m" in sql:
            return [
                {
                    "name": "query_time",
                    "expression": json.dumps({"dialects": self._dialects}),
                    "data_type": "timestamp",
                    "support_min_granularity": "hour",
                    "is_time": 1,
                }
            ]
        return []


class _RecordingTimeProvider:
    def __init__(self) -> None:
        self.engine_type: str | None = None

    def load_for_windowed_query(
        self,
        *,
        table_name: str,
        metric_name: str | None = None,
        engine_type: str = "duckdb",
    ) -> TimeAxisMetadataContext:
        del table_name, metric_name
        self.engine_type = engine_type
        return TimeAxisMetadataContext(
            entity_time_capabilities={"analysis_time": {"timestamp_column": "event_time"}},
            available_columns=["event_time"],
            time_field_support_min_granularities={"event_time": "hour"},
        )

    def load_available_columns(self, table_name: str) -> list[str]:
        del table_name
        return ["event_time"]


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
        self.assertEqual(context.time_field_support_min_granularities["query_time"], "hour")
        self.assertEqual(context.time_field_support_min_granularities["create_date"], "day")
        self.assertTrue(context.has_time_binding)

    def test_load_for_windowed_query_falls_back_to_source_fields_without_metric(self) -> None:
        context = TimeAxisMetadataProvider(_MetadataStub()).load_for_windowed_query(
            table_name="analytics.events"
        )

        self.assertEqual(context.available_columns, ["query_time"])
        self.assertEqual(context.time_field_expressions["query_time"], "wrong_model_time")

    def test_trino_engine_prefers_trino_expression(self) -> None:
        context = TimeAxisMetadataProvider(
            _DialectMetadataStub(
                [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": "CAST(log_date AS DATE)",
                    },
                    {
                        "dialect": " trino ",
                        "expression": "date_parse(log_date, '%Y%m%d')",
                    },
                ]
            )
        ).load_for_windowed_query(
            table_name="analytics.events",
            metric_name="metric.event_count",
            engine_type="trino",
        )

        self.assertEqual(
            context.time_field_expressions["query_time"],
            "date_parse(log_date, '%Y%m%d')",
        )

    def test_default_engine_prefers_ansi_expression(self) -> None:
        context = TimeAxisMetadataProvider(
            _DialectMetadataStub(
                [
                    {
                        "dialect": "TRINO",
                        "expression": "date_parse(log_date, '%Y%m%d')",
                    },
                    {
                        "dialect": "ANSI_SQL",
                        "expression": "CAST(log_date AS DATE)",
                    },
                ]
            )
        ).load_for_windowed_query(
            table_name="analytics.events",
            metric_name="metric.event_count",
        )

        self.assertEqual(context.time_field_expressions["query_time"], "CAST(log_date AS DATE)")

    def test_trino_engine_falls_back_to_ansi_expression(self) -> None:
        context = TimeAxisMetadataProvider(
            _DialectMetadataStub(
                [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": "CAST(log_date AS DATE)",
                    }
                ]
            )
        ).load_for_windowed_query(
            table_name="analytics.events",
            metric_name="metric.event_count",
            engine_type="trino",
        )

        self.assertEqual(context.time_field_expressions["query_time"], "CAST(log_date AS DATE)")

    def test_trino_engine_falls_back_to_first_valid_expression(self) -> None:
        context = TimeAxisMetadataProvider(
            _DialectMetadataStub(
                [
                    {
                        "dialect": "SNOWFLAKE",
                        "expression": "TO_DATE(log_date, 'YYYYMMDD')",
                    }
                ]
            )
        ).load_for_windowed_query(
            table_name="analytics.events",
            metric_name="metric.event_count",
            engine_type="trino",
        )

        self.assertEqual(
            context.time_field_expressions["query_time"],
            "TO_DATE(log_date, 'YYYYMMDD')",
        )

    def test_resolve_windowed_query_time_axis_forwards_engine_type_to_provider(self) -> None:
        feedback_module = types.ModuleType("marivo.runtime.semantic.feedback")
        feedback_module.compile_failure_from_error = lambda *args, **kwargs: None
        previous_feedback_module = sys.modules.get("marivo.runtime.semantic.feedback")
        previous_semantic_ops_module = sys.modules.get("marivo.runtime.semantic_ops")
        sys.modules["marivo.runtime.semantic.feedback"] = feedback_module
        try:
            from marivo.runtime.semantic_ops import resolve_windowed_query_time_axis

            provider = _RecordingTimeProvider()
            runtime = type("RuntimeStub", (), {"time_axis_metadata_provider": provider})()
            request = normalize_aggregate_query_request(
                {
                    "table": "analytics.events",
                    "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                }
            )

            resolve_windowed_query_time_axis(
                runtime,
                request,
                engine_type="trino",
                metric_name="metric.event_count",
            )

            self.assertEqual(provider.engine_type, "trino")
            self.assertEqual(request.resolved_time_axis.analysis_time_expr, "event_time")
        finally:
            if previous_feedback_module is None:
                sys.modules.pop("marivo.runtime.semantic.feedback", None)
            else:
                sys.modules["marivo.runtime.semantic.feedback"] = previous_feedback_module
            if previous_semantic_ops_module is None:
                sys.modules.pop("marivo.runtime.semantic_ops", None)
            else:
                sys.modules["marivo.runtime.semantic_ops"] = previous_semantic_ops_module
