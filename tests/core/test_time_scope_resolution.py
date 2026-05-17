from __future__ import annotations

import unittest
from datetime import datetime

from marivo.time_scope import (
    AdHocAggregateValueSpec,
    SemanticMetricValueSpec,
    TimeAxisResolver,
    TimeScopeResolver,
    normalize_aggregate_query_request,
    normalize_metric_query_request,
)


class TimeScopeNormalizationTests(unittest.TestCase):
    def _local_naive_datetime(self, value: str) -> str:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone()
            .replace(tzinfo=None, microsecond=0)
            .isoformat(timespec="seconds")
        )

    def test_metric_query_normalizes_to_shared_request(self) -> None:
        resolved = normalize_metric_query_request(
            {
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "dimensions": ["platform"],
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
            }
        )
        self.assertEqual(resolved.table, "analytics.watch_events")
        self.assertEqual(resolved.compare_kind, "semantic_metric")
        self.assertEqual(resolved.grouping, ["platform"])
        self.assertIsInstance(resolved.value_spec, SemanticMetricValueSpec)
        self.assertEqual(resolved.value_spec.metric, "watch_time")
        self.assertEqual(resolved.resolved_time_axis.observation_grain, "day")
        self.assertEqual(resolved.time_scope.current.start, "2026-03-10")
        self.assertEqual(resolved.time_scope.current.end, "2026-03-17")
        self.assertEqual(resolved.time_scope.warnings, [])

    def test_aggregate_query_normalizes_measures_and_time_axis_override(self) -> None:
        resolved = normalize_aggregate_query_request(
            {
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                },
                "time_axis": {
                    "analysis_time": {"column": "event_date"},
                    "partition_pruning": {"date_column": "log_date", "hour_column": "log_hour"},
                },
            }
        )
        self.assertEqual(resolved.compare_kind, "ad_hoc_aggregate")
        self.assertIsInstance(resolved.value_spec, AdHocAggregateValueSpec)
        self.assertEqual(resolved.value_spec.measures[0].alias, "query_count")
        self.assertEqual(resolved.resolved_time_axis.override_analysis_time_column, "event_date")
        self.assertEqual(resolved.resolved_time_axis.override_partition_date_column, "log_date")
        self.assertEqual(resolved.resolved_time_axis.override_partition_hour_column, "log_hour")

    def test_missing_optional_scope_and_time_axis_get_empty_defaults(self) -> None:
        resolved = normalize_aggregate_query_request(
            {
                "table": "analytics.watch_events",
                "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                },
            }
        )
        self.assertEqual(resolved.scope.constraints, {})
        self.assertIsNone(resolved.scope.predicate)
        self.assertIsNone(resolved.resolved_time_axis.override_analysis_time_column)
        self.assertEqual(resolved.resolved_time_axis.observation_grain, "day")

    def test_normalizers_reject_time_predicates_in_scope(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "scope.predicate must not contain time-axis predicates"
        ):
            normalize_metric_query_request(
                {
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                    "scope": {"predicate": "event_time >= TIMESTAMP '2026-03-01 00:00:00'"},
                }
            )

    def test_normalizers_allow_non_axis_suffix_predicates_in_scope(self) -> None:
        resolved = normalize_metric_query_request(
            {
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
                "scope": {"predicate": "business_hour = 9 AND state_date = '2026-03-01'"},
            }
        )
        self.assertEqual(
            resolved.scope.predicate, "business_hour = 9 AND state_date = '2026-03-01'"
        )

    def test_day_grain_normalizes_datetime_boundaries_to_dates(self) -> None:
        resolved = normalize_metric_query_request(
            {
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {
                        "start": "2026-03-10T08:15:00",
                        "end": "2026-03-17 00:00:00",
                    },
                },
            }
        )
        self.assertEqual(resolved.time_scope.current.start, "2026-03-10")
        self.assertEqual(resolved.time_scope.current.end, "2026-03-17")

    def test_hour_grain_normalizes_to_second_precision(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "hour",
                "current": {
                    "start": "2026-03-25 10:00:00.999999",
                    "end": "2026-03-25T14:00:00",
                },
            }
        )
        self.assertEqual(resolved.current.start, "2026-03-25T10:00:00")
        self.assertEqual(resolved.current.end, "2026-03-25T14:00:00")

    def test_hour_grain_accepts_date_only_boundaries(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "hour",
                "current": {"start": "2026-03-25", "end": "2026-03-26"},
            }
        )

        self.assertEqual(resolved.current.start, "2026-03-25T00:00:00")
        self.assertEqual(resolved.current.end, "2026-03-26T00:00:00")

    def test_hour_grain_accepts_timezone_aware_boundaries(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "hour",
                "current": {
                    "start": "2026-03-25T10:00:00+08:00",
                    "end": "2026-03-25T14:00:00+08:00",
                },
            }
        )

        self.assertEqual(
            resolved.current.start,
            self._local_naive_datetime("2026-03-25T10:00:00+08:00"),
        )
        self.assertEqual(
            resolved.current.end,
            self._local_naive_datetime("2026-03-25T14:00:00+08:00"),
        )

    def test_compare_mode_requires_baseline_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "time_scope.baseline is required"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                }
            )

    def test_single_window_rejects_baseline_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "only allowed when mode='compare'"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                    "baseline": {"start": "2026-03-24", "end": "2026-03-25"},
                }
            )

    def test_time_scope_rejects_non_increasing_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "start < end"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-25"},
                }
            )

    def test_compare_mode_keeps_unequal_windows_and_adds_warning(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "compare",
                "grain": "day",
                "current": {"start": "2026-03-10", "end": "2026-03-17"},
                "baseline": {"start": "2026-03-01", "end": "2026-03-03"},
            }
        )
        self.assertEqual(len(resolved.warnings), 1)
        self.assertEqual(resolved.warnings[0]["code"], "window_length_mismatch")
        self.assertEqual(resolved.warnings[0]["current_duration"], 7)
        self.assertEqual(resolved.warnings[0]["baseline_duration"], 2)


class TimeAxisResolverTests(unittest.TestCase):
    def _compare_request(self, *, grain: str = "hour", time_axis: dict[str, object] | None = None):
        payload: dict[str, object] = {
            "table": "iceberg.analytics.query_events",
            "metric": "queued_time",
            "time_scope": {
                "mode": "compare",
                "grain": grain,
                "current": {
                    "start": "2026-03-25T10:00:00" if grain == "hour" else "2026-03-25",
                    "end": "2026-03-25T14:00:00" if grain == "hour" else "2026-03-26",
                },
                "baseline": {
                    "start": "2026-03-25T06:00:00" if grain == "hour" else "2026-03-24",
                    "end": "2026-03-25T10:00:00" if grain == "hour" else "2026-03-25",
                },
            },
        }
        if time_axis is not None:
            payload["time_axis"] = time_axis
        return normalize_metric_query_request(payload)

    def test_resolver_prefers_timestamp_analysis_with_partition_pruning_for_mixed_layout(
        self,
    ) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "log_date", "log_hour"],
            entity_time_capabilities={
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
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "event_time")
        self.assertIn("log_date = '20260325'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '06'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '14'", resolved.partition_pruning_predicate)

    def test_resolver_builds_partition_only_hour_expression(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertIn("CAST(CONCAT(", resolved.analysis_time_expr)
        self.assertIn("SUBSTR(CAST(log_date AS VARCHAR), 1, 4)", resolved.analysis_time_expr)
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date = '20260325' AND log_hour >= '06' AND log_hour < '14'",
        )

    def test_resolver_reuses_metadata_date_format_for_partition_only_hour_expression(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["ds", "hh"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "ds",
                    "fallback_hour_column": "hh",
                },
                "partition_time": {
                    "date_column": "ds",
                    "date_format": "yyyymmdd",
                    "hour_column": "hh",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.analysis_time_format, "yyyymmdd")
        self.assertIn("SUBSTR(CAST(ds AS VARCHAR), 1, 4)", resolved.analysis_time_expr)
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "ds = '20260325' AND hh >= '06' AND hh < '14'",
        )

    def test_resolver_falls_back_to_date_field_for_day_partition_layout(self) -> None:
        request = self._compare_request(grain="day")
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["log_date", "resource_group"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        # log_date defaults to yyyymmdd format, so analysis_time_expr should be a CAST expression
        self.assertEqual(
            resolved.analysis_time_expr,
            "CAST(CONCAT(SUBSTR(CAST(log_date AS VARCHAR), 1, 4), '-', SUBSTR(CAST(log_date AS VARCHAR), 5, 2), '-', SUBSTR(CAST(log_date AS VARCHAR), 7, 2)) AS DATE)",
        )
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date >= '20260324' AND log_date < '20260326'",
        )

    def test_resolver_reuses_metadata_date_format_for_day_field_analysis(self) -> None:
        request = self._compare_request(grain="day")
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["ds", "resource_group"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "ds",
                },
                "partition_time": {
                    "date_column": "ds",
                    "date_format": "yyyymmdd",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        # yyyymmdd format requires CAST expression for DATE_TRUNC compatibility
        self.assertEqual(
            resolved.analysis_time_expr,
            "CAST(CONCAT(SUBSTR(CAST(ds AS VARCHAR), 1, 4), '-', SUBSTR(CAST(ds AS VARCHAR), 5, 2), '-', SUBSTR(CAST(ds AS VARCHAR), 7, 2)) AS DATE)",
        )
        self.assertEqual(resolved.analysis_time_format, "yyyymmdd")
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "ds >= '20260324' AND ds < '20260326'",
        )

    def test_resolver_day_only_pruning_uses_current_window_for_single_window_mode(self) -> None:
        request = normalize_metric_query_request(
            {
                "table": "iceberg.analytics.query_events",
                "metric": "queued_time",
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-28"},
                },
            }
        )
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "resource_group"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date >= '20260325' AND log_date < '20260328'",
        )

    def test_resolver_builds_cross_day_hour_partition_pruning(self) -> None:
        request = normalize_metric_query_request(
            {
                "table": "iceberg.analytics.query_events",
                "metric": "queued_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "hour",
                    "current": {
                        "start": "2026-03-25T22:00:00",
                        "end": "2026-03-26T02:00:00",
                    },
                    "baseline": {
                        "start": "2026-03-24T22:00:00",
                        "end": "2026-03-25T02:00:00",
                    },
                },
            }
        )
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "(log_date = '20260324' AND log_hour >= '22') OR "
            "(log_date > '20260324' AND log_date < '20260326') OR "
            "(log_date = '20260326' AND log_hour < '02')",
        )

    def test_resolver_builds_midnight_terminated_cross_day_hour_pruning(self) -> None:
        request = normalize_metric_query_request(
            {
                "table": "iceberg.analytics.query_events",
                "metric": "queued_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "hour",
                    "current": {
                        "start": "2026-03-25T22:00:00",
                        "end": "2026-03-26T00:00:00",
                    },
                    "baseline": {
                        "start": "2026-03-24T22:00:00",
                        "end": "2026-03-25T00:00:00",
                    },
                },
            }
        )
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "(log_date = '20260324' AND log_hour >= '22') OR (log_date = '20260325')",
        )

    def test_resolver_heuristics_prefer_timestamp_when_mixed_columns_exist(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "log_date", "log_hour"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "event_time")
        self.assertIsNotNone(resolved.partition_pruning_predicate)

    def test_resolver_keeps_timestamp_only_axis_without_partition_pruning(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "platform"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "event_time")
        self.assertIsNone(resolved.partition_pruning_predicate)

    def test_resolver_prefers_metadata_over_timestamp_heuristic_candidates(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "created_at"],
            source_time_capabilities={
                "analysis_time": {
                    "timestamp_column": "created_at",
                    "timestamp_format": "native",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "created_at")

    def test_resolver_parses_iso8601_naive_timestamp_columns(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["create_time", "log_date"],
            source_time_capabilities={
                "analysis_time": {
                    "timestamp_column": "create_time",
                    "timestamp_format": "iso8601_t_naive",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(
            resolved.analysis_time_expr,
            "CAST(REPLACE(CAST(create_time AS VARCHAR), 'T', ' ') AS TIMESTAMP)",
        )

    def test_resolver_parses_custom_format_timestamp_columns(self) -> None:
        """Custom strftime format strings are parsed via STRPTIME family."""
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["create_time", "log_date"],
            source_time_capabilities={
                "analysis_time": {
                    "timestamp_column": "create_time",
                    "timestamp_format": "%Y%m%d %H:%M:%S",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        # Trino uses DATE_PARSE(col, format) for custom formats
        self.assertEqual(
            resolved.analysis_time_expr,
            "DATE_PARSE(CAST(create_time AS VARCHAR), '%Y%m%d %H:%i:%s')",
        )

    def test_resolver_request_override_beats_metadata(self) -> None:
        request = self._compare_request(time_axis={"analysis_time": {"column": "created_at"}})
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "created_at"],
            entity_time_capabilities={"analysis_time": {"timestamp_column": "event_time"}},
        ).resolve()
        self.assertEqual(resolved.analysis_time_expr, "created_at")

    def test_resolver_hour_override_on_date_column_uses_partition_hour_axis(self) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "log_date"}},
        )

        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertIn("SUBSTR(CAST(log_date AS VARCHAR), 1, 4)", resolved.analysis_time_expr)
        self.assertIn("log_hour", resolved.analysis_time_expr)
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date = '20260325' AND log_hour >= '06' AND log_hour < '14'",
        )

    def test_resolver_expands_time_scope_field_timestamp_expression(self) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "query_time"}},
        )
        expression = "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP)"

        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["query_time", "log_date", "log_hour"],
            time_field_expressions={"query_time": expression},
            time_field_data_types={"query_time": "timestamp"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, expression)

    def test_resolver_expands_date_time_scope_field_to_partition_hour_axis(self) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "analysis_date"}},
        )

        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["analysis_date", "log_date", "log_hour"],
            time_field_expressions={"analysis_date": "log_date"},
            time_field_data_types={"analysis_date": "date"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.override_analysis_time_column, "analysis_date")
        self.assertIn("log_date", resolved.analysis_time_expr)
        self.assertIn("log_hour", resolved.analysis_time_expr)

    def test_resolver_rejects_unpaired_date_time_scope_field_for_hour_axis(self) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "business_date"}},
        )

        with self.assertRaisesRegex(ValueError, "hour-compatible"):
            TimeAxisResolver(
                request=request,
                engine_type="trino",
                available_columns=["business_date", "log_hour"],
                time_field_expressions={"business_date": "business_date"},
                time_field_data_types={"business_date": "date"},
            ).resolve()

    def test_resolver_rewrites_trino_ansi_timestamp_cast_and_keeps_partition_pruning(
        self,
    ) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "query_start_time"}},
        )

        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["query_start_time", "create_time", "log_date", "log_hour"],
            time_field_expressions={"query_start_time": "CAST(create_time AS TIMESTAMP)"},
            time_field_data_types={"query_start_time": "timestamp"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(
            resolved.analysis_time_expr,
            "DATE_PARSE(CAST(create_time AS VARCHAR), '%Y-%m-%d %H:%i:%s')",
        )
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date = '20260325' AND log_hour >= '06' AND log_hour < '14'",
        )

    def test_resolver_expands_time_scope_field_date_expression_without_recasting(self) -> None:
        request = self._compare_request(
            grain="day",
            time_axis={"analysis_time": {"column": "create_date"}},
        )
        expression = "CAST(SUBSTRING(create_time, 1, 10) AS DATE)"

        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["create_date", "create_time"],
            time_field_expressions={"create_date": expression},
            time_field_data_types={"create_date": "date"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "date_expression")
        self.assertEqual(resolved.analysis_time_expr, expression)

    def test_resolver_uses_time_field_data_type_for_bare_date_expression(self) -> None:
        request = self._compare_request(
            grain="day",
            time_axis={"analysis_time": {"column": "business_date"}},
        )

        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["business_date"],
            time_field_expressions={"business_date": "business_date"},
            time_field_data_types={"business_date": "DATE"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_expr, "CAST(business_date AS DATE)")

    def test_resolver_rejects_metadata_columns_not_present_in_known_schema(self) -> None:
        request = self._compare_request(grain="day")
        with self.assertRaisesRegex(ValueError, "unknown column 'event_time'"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["event_date"],
                source_time_capabilities={"analysis_time": {"timestamp_column": "event_time"}},
            ).resolve()

    def test_resolver_rejects_hour_grain_without_hour_capable_axis(self) -> None:
        request = self._compare_request()
        with self.assertRaisesRegex(ValueError, "hour-compatible time axis"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["log_date"],
            ).resolve()
