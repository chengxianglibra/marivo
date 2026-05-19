from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

from marivo.time_contracts import previous_adjacent_window
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

    def test_normalizers_do_not_infer_scope_time_predicates_from_column_names(self) -> None:
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
                "scope": {"predicate": "event_time = '2026-03-01'"},
            }
        )

        self.assertEqual(resolved.scope.predicate, "event_time = '2026-03-01'")

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

    def test_hour_grain_accepts_whole_hour_boundaries(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "hour",
                "current": {
                    "start": "2026-03-25 10:00:00",
                    "end": "2026-03-25T14:00:00",
                },
            }
        )
        self.assertEqual(resolved.current.start, "2026-03-25T10:00:00")
        self.assertEqual(resolved.current.end, "2026-03-25T14:00:00")

    def test_hour_grain_rejects_non_whole_hour_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "must align to hour grain"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "hour",
                    "current": {
                        "start": "2026-03-25T10:00:00.999999",
                        "end": "2026-03-25T14:00:00",
                    },
                }
            )

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

    def test_exact_boundary_mode_omits_grain_and_preserves_datetime_boundaries(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "boundary_mode": "exact",
                "current": {
                    "start": "2026-03-25T10:15:30",
                    "end": "2026-03-25T14:45:00",
                },
            }
        )

        self.assertIsNone(resolved.grain)
        self.assertEqual(resolved.boundary_mode, "exact")
        self.assertEqual(resolved.current.start, "2026-03-25T10:15:30")
        self.assertEqual(resolved.current.end, "2026-03-25T14:45:00")

    def test_exact_boundary_mode_rejects_grain(self) -> None:
        with self.assertRaisesRegex(ValueError, "grain must be omitted"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "boundary_mode": "exact",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                }
            )

    def test_exact_boundary_mode_is_single_window_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "only allowed with mode='single_window'"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "compare",
                    "boundary_mode": "exact",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                    "baseline": {"start": "2026-03-24", "end": "2026-03-25"},
                }
            )

    def test_exact_boundary_mode_rejects_start_greater_than_end(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires start < end"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "boundary_mode": "exact",
                    "current": {
                        "start": "2026-03-25T14:00:00",
                        "end": "2026-03-25T10:00:00",
                    },
                }
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

    def test_time_scope_accepts_aligned_quarter_and_year_grains(self) -> None:
        quarter = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "quarter",
                "current": {"start": "2026-01-01", "end": "2026-07-01"},
            }
        )
        year = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "year",
                "current": {"start": "2025-01-01", "end": "2027-01-01"},
            }
        )

        self.assertEqual(quarter.current.start, "2026-01-01")
        self.assertEqual(quarter.current.end, "2026-07-01")
        self.assertEqual(year.current.start, "2025-01-01")
        self.assertEqual(year.current.end, "2027-01-01")

    def test_time_scope_rejects_unaligned_date_like_boundaries(self) -> None:
        cases = [
            ("week", "2026-01-01", "2026-01-08", "week"),
            ("month", "2026-01-02", "2026-02-01", "month"),
            ("quarter", "2026-02-01", "2026-04-01", "quarter"),
            ("year", "2026-04-01", "2027-01-01", "year"),
        ]

        for grain, start, end, message in cases:
            with self.subTest(grain=grain), self.assertRaisesRegex(ValueError, message):
                TimeScopeResolver(step_type="metric_query").resolve(
                    {
                        "mode": "single_window",
                        "grain": grain,
                        "current": {"start": start, "end": end},
                    }
                )

    def test_date_like_datetime_boundaries_truncate_then_validate_alignment(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "week",
                "current": {
                    "start": "2026-01-05T12:34:00",
                    "end": "2026-01-12T23:59:59",
                },
            }
        )

        self.assertEqual(resolved.current.start, "2026-01-05")
        self.assertEqual(resolved.current.end, "2026-01-12")

    def test_quarter_compare_windows_use_bucket_count_for_duration(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "compare",
                "grain": "quarter",
                "current": {"start": "2026-01-01", "end": "2026-04-01"},
                "baseline": {"start": "2026-04-01", "end": "2026-07-01"},
            }
        )

        self.assertEqual(resolved.warnings, [])

    def test_previous_adjacent_window_preserves_calendar_grain_alignment(self) -> None:
        cases = [
            ("week", "2026-01-05", "2026-01-19", {"start": "2025-12-22", "end": "2026-01-05"}),
            ("month", "2026-03-01", "2026-05-01", {"start": "2026-01-01", "end": "2026-03-01"}),
            (
                "quarter",
                "2026-01-01",
                "2026-04-01",
                {"start": "2025-10-01", "end": "2026-01-01"},
            ),
            ("year", "2025-01-01", "2026-01-01", {"start": "2024-01-01", "end": "2025-01-01"}),
        ]

        for grain, start, end, expected in cases:
            with self.subTest(grain=grain):
                baseline = previous_adjacent_window(start, end, grain=grain)

                self.assertEqual(baseline, expected)
                TimeScopeResolver(step_type="metric_query").resolve(
                    {
                        "mode": "single_window",
                        "grain": grain,
                        "current": baseline,
                    }
                )


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

    def _exact_request(
        self,
        *,
        start: str,
        end: str,
        time_axis: dict[str, object] | None = None,
    ):
        payload: dict[str, object] = {
            "table": "iceberg.analytics.query_events",
            "metric": "queued_time",
            "time_scope": {
                "mode": "single_window",
                "boundary_mode": "exact",
                "current": {"start": start, "end": end},
            },
        }
        if time_axis is not None:
            payload["time_axis"] = time_axis
        return normalize_metric_query_request(payload)

    def test_exact_midnight_datetime_window_can_use_day_time_field(self) -> None:
        request = self._exact_request(
            start="2026-03-25T00:00:00",
            end="2026-03-26T00:00:00",
            time_axis={"analysis_time": {"column": "log_date"}},
        )

        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["log_date"],
            time_field_data_types={"log_date": "date"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()

        self.assertIsNone(resolved.observation_grain)
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date >= '2026-03-25' AND log_date < '2026-03-26'",
        )

    def test_exact_subday_window_requires_hour_capable_time_field(self) -> None:
        request = self._exact_request(
            start="2026-03-25T10:15:00",
            end="2026-03-25T14:45:00",
            time_axis={"analysis_time": {"column": "log_date"}},
        )

        with self.assertRaisesRegex(ValueError, "hour-compatible"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["log_date"],
                time_field_data_types={"log_date": "date"},
                time_field_support_min_granularities={"log_date": "day"},
            ).resolve()

    def test_resolver_does_not_infer_timestamp_axis_from_column_name(self) -> None:
        request = self._compare_request()
        with self.assertRaisesRegex(ValueError, "could not resolve a time axis"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["event_time", "platform"],
                time_field_support_min_granularities={"event_time": "hour"},
            ).resolve()

    def test_resolver_hour_override_on_date_column_rejects_unsupported_grain(self) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "log_date"}},
        )

        with self.assertRaisesRegex(ValueError, "requires explicit time field"):
            TimeAxisResolver(
                request=request,
                engine_type="trino",
                available_columns=["log_date", "log_hour"],
                time_field_support_min_granularities={"log_date": "day"},
            ).resolve()

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
            time_field_support_min_granularities={"query_time": "hour"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, expression)

    def test_resolver_rejects_date_time_scope_field_for_hour_axis(self) -> None:
        request = self._compare_request(
            grain="hour",
            time_axis={"analysis_time": {"column": "analysis_date"}},
        )

        with self.assertRaisesRegex(ValueError, "hour-compatible"):
            TimeAxisResolver(
                request=request,
                engine_type="trino",
                available_columns=["analysis_date", "log_date", "log_hour"],
                time_field_expressions={"analysis_date": "log_date"},
                time_field_data_types={"analysis_date": "date"},
                time_field_support_min_granularities={"analysis_date": "hour"},
            ).resolve()

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
                time_field_support_min_granularities={"business_date": "hour"},
            ).resolve()

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
            time_field_support_min_granularities={"create_date": "day"},
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
            time_field_support_min_granularities={"business_date": "day"},
        ).resolve()

        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_expr, "CAST(business_date AS DATE)")

    def test_resolver_rejects_hour_grain_without_hour_capable_axis(self) -> None:
        request = self._compare_request()
        with self.assertRaisesRegex(ValueError, "could not resolve a time axis"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["log_date"],
            ).resolve()


class StringIntegerTimeFieldResolverTests(unittest.TestCase):
    """Tests for string-type and integer-type time field resolution."""

    def _compare_request(
        self,
        grain: str = "day",
        time_axis: dict | None = None,
    ) -> Any:
        from marivo.time_scope import normalize_metric_query_request

        payload: dict[str, Any] = {
            "table": "iceberg.analytics.query_events",
            "metric": "queued_time",
            "time_scope": {
                "mode": "compare",
                "grain": grain,
                "current": {
                    "start": "2026-03-25" if grain != "hour" else "2026-03-25T10:00:00",
                    "end": "2026-03-26" if grain != "hour" else "2026-03-25T14:00:00",
                },
                "baseline": {
                    "start": "2026-03-24" if grain != "hour" else "2026-03-25T06:00:00",
                    "end": "2026-03-25" if grain != "hour" else "2026-03-25T10:00:00",
                },
            },
        }
        if time_axis is not None:
            payload["time_axis"] = time_axis
        return normalize_metric_query_request(payload)

    def test_string_yyyymmdd_day_grain(self) -> None:
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date"],
            time_field_data_types={"log_date": "string"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertIn("CAST", resolved.analysis_time_expr)
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_date >= '20260324'", resolved.partition_pruning_predicate)
        self.assertIn("log_date < '20260326'", resolved.partition_pruning_predicate)

    def test_string_yyyy_mm_dd_day_grain(self) -> None:
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date"],
            time_field_data_types={"log_date": "string"},
            time_field_formats={"log_date": "yyyy-mm-dd"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("CAST", resolved.analysis_time_expr)
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_date >= '2026-03-24'", resolved.partition_pruning_predicate)
        self.assertIn("log_date < '2026-03-26'", resolved.partition_pruning_predicate)

    def test_string_yyyymmdd_without_format_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires format"):
            TimeAxisResolver(
                request=self._compare_request(
                    grain="day",
                    time_axis={"analysis_time": {"column": "log_date"}},
                ),
                engine_type="duckdb",
                available_columns=["log_date"],
                time_field_data_types={"log_date": "string"},
                time_field_formats={"log_date": None},
                time_field_support_min_granularities={"log_date": "day"},
            ).resolve()

    def test_string_yyyymmdd_hour_grain_with_hour_column(self) -> None:
        """String yyyymmdd date + hh hour via required_prefix at hour grain."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string", "log_hour": "string"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "hh"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("CAST(CONCAT(", resolved.analysis_time_expr)
        self.assertEqual(resolved.partition_hour_data_type, "string")

    def test_integer_yyyymmdd_day_grain(self) -> None:
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date"],
            time_field_data_types={"log_date": "integer"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_data_type, "integer")
        self.assertIn("CAST", resolved.analysis_time_expr)
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_date >= 20260324", resolved.partition_pruning_predicate)
        self.assertIn("log_date < 20260326", resolved.partition_pruning_predicate)
        self.assertNotIn("'2026032", resolved.partition_pruning_predicate)

    def test_integer_without_format_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires format"):
            TimeAxisResolver(
                request=self._compare_request(
                    grain="day",
                    time_axis={"analysis_time": {"column": "log_date"}},
                ),
                engine_type="duckdb",
                available_columns=["log_date"],
                time_field_data_types={"log_date": "integer"},
                time_field_formats={"log_date": None},
                time_field_support_min_granularities={"log_date": "day"},
            ).resolve()

    def test_string_yyyymmdd_compare_mode_partition_period(self) -> None:
        """Compare mode with string partition uses partition-column predicates for _period."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date"],
            time_field_data_types={"log_date": "string"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()
        # Verify that compare mode no longer raises for date_field with CAST
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        # Verify partition pruning predicate exists
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertEqual(resolved.partition_date_column, "log_date")
        self.assertEqual(resolved.partition_date_data_type, "string")

    def test_string_expression_with_date_parse(self) -> None:
        """String field with a date_parse expression in Field.expression."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="trino",
            available_columns=["log_date"],
            time_field_expressions={"log_date": "date_parse(log_date, '%Y%m%d')"},
            time_field_data_types={"log_date": "string"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_data_type, "string")

    def test_string_yyyymmddhh_hour_grain(self) -> None:
        """String field with yyyymmddhh format at hour grain — single column with hour precision."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_hour"],
            time_field_data_types={"log_hour": "string"},
            time_field_formats={"log_hour": "yyyymmddhh"},
            time_field_support_min_granularities={"log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertEqual(resolved.analysis_time_format, "yyyymmddhh")
        self.assertIn("DATE_PARSE", resolved.analysis_time_expr)
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '2026032506'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '2026032514'", resolved.partition_pruning_predicate)

    def test_string_yyyymmddhh_hour_grain_duckdb(self) -> None:
        """String field with yyyymmddhh format at hour grain — DuckDB uses STRPTIME."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="duckdb",
            available_columns=["log_hour"],
            time_field_data_types={"log_hour": "string"},
            time_field_formats={"log_hour": "yyyymmddhh"},
            time_field_support_min_granularities={"log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("STRPTIME", resolved.analysis_time_expr)
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '2026032506'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '2026032514'", resolved.partition_pruning_predicate)

    def test_string_yyyy_mm_dd_hh_hour_grain(self) -> None:
        """String field with yyyy-mm-dd-hh format at hour grain."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_hour"],
            time_field_data_types={"log_hour": "string"},
            time_field_formats={"log_hour": "yyyy-mm-dd-hh"},
            time_field_support_min_granularities={"log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertEqual(resolved.analysis_time_format, "yyyy-mm-dd-hh")
        self.assertIn("DATE_PARSE", resolved.analysis_time_expr)
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '2026-03-25-06'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '2026-03-25-14'", resolved.partition_pruning_predicate)

    def test_string_yyyymmdd_hh_hour_grain(self) -> None:
        """String field with yyyymmdd-hh format at hour grain."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_hour"],
            time_field_data_types={"log_hour": "string"},
            time_field_formats={"log_hour": "yyyymmdd-hh"},
            time_field_support_min_granularities={"log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertEqual(resolved.analysis_time_format, "yyyymmdd-hh")
        self.assertIn("DATE_PARSE", resolved.analysis_time_expr)
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '20260325-06'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '20260325-14'", resolved.partition_pruning_predicate)

    def test_string_yyyymmdd_with_hh_hour_column(self) -> None:
        """String yyyymmdd date + hh hour via required_prefix at hour grain."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string", "log_hour": "string"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "hh"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("CAST(CONCAT(", resolved.analysis_time_expr)
        self.assertIn("SUBSTR(CAST(log_date AS VARCHAR), 1, 4)", resolved.analysis_time_expr)


class RequiredPrefixCompositeAxisTests(unittest.TestCase):
    """Tests for composite date+hour time axis via required_prefix."""

    def _compare_request(
        self,
        grain: str = "day",
        time_axis: dict | None = None,
    ) -> Any:
        from marivo.time_scope import normalize_metric_query_request

        payload: dict[str, Any] = {
            "table": "iceberg.analytics.query_events",
            "metric": "queued_time",
            "time_scope": {
                "mode": "compare",
                "grain": grain,
                "current": {
                    "start": "2026-03-25" if grain != "hour" else "2026-03-25T10:00:00",
                    "end": "2026-03-26" if grain != "hour" else "2026-03-25T14:00:00",
                },
                "baseline": {
                    "start": "2026-03-24" if grain != "hour" else "2026-03-25T06:00:00",
                    "end": "2026-03-25" if grain != "hour" else "2026-03-25T10:00:00",
                },
            },
        }
        if time_axis is not None:
            payload["time_axis"] = time_axis
        return normalize_metric_query_request(payload)

    def test_composite_hour_axis_via_required_prefix(self) -> None:
        """log_hour(hour, required_prefix=log_date) at hour grain resolves to composite."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string", "log_hour": "string"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "hh"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("CAST(CONCAT(", resolved.analysis_time_expr)
        self.assertIn("SUBSTR(CAST(log_date AS VARCHAR)", resolved.analysis_time_expr)
        self.assertIn("LPAD(CAST(log_hour AS VARCHAR), 2, '0')", resolved.analysis_time_expr)
        self.assertIn("log_date = '20260325'", resolved.partition_pruning_predicate)
        self.assertEqual(resolved.partition_hour_data_type, "string")

    def test_composite_hour_axis_day_grain_uses_only_date_field(self) -> None:
        """log_date(day) + log_hour(hour, required_prefix) at day grain — only log_date."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("CAST", resolved.analysis_time_expr)
        self.assertIn("log_date >= '20260324'", resolved.partition_pruning_predicate)
        self.assertIn("log_date < '20260326'", resolved.partition_pruning_predicate)

    def test_composite_hour_axis_week_grain(self) -> None:
        """log_date(day) + log_hour(hour, required_prefix) at week grain."""
        resolved = TimeAxisResolver(
            request=normalize_metric_query_request(
                {
                    "table": "iceberg.analytics.query_events",
                    "metric": "queued_time",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "week",
                        "current": {"start": "2026-03-23", "end": "2026-03-30"},
                    },
                    "time_axis": {"analysis_time": {"column": "log_date"}},
                }
            ),
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(resolved.analysis_time_data_type, "string")

    def test_hour_only_field_as_standalone_axis_rejected(self) -> None:
        """log_hour(hh) alone cannot be analysis axis without required_prefix."""
        with self.assertRaisesRegex(ValueError, "cannot be used as a standalone analysis axis"):
            TimeAxisResolver(
                request=self._compare_request(
                    grain="hour",
                    time_axis={"analysis_time": {"column": "log_hour"}},
                ),
                engine_type="trino",
                available_columns=["log_date", "log_hour"],
                time_field_data_types={"log_hour": "string"},
                time_field_formats={"log_hour": "hh"},
                time_field_support_min_granularities={"log_hour": "hour"},
            ).resolve()

    def test_hour_only_field_via_required_prefix_redirects(self) -> None:
        """analysis_time.column = 'log_hour' at hour grain — redirects to log_date + log_hour."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string", "log_hour": "string"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "hh"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.analysis_time_data_type, "string")
        self.assertIn("CAST(CONCAT(", resolved.analysis_time_expr)

    def test_date_field_alone_at_hour_grain_rejected(self) -> None:
        """log_date(day) alone at hour grain without required_prefix hour field — rejected."""
        with self.assertRaisesRegex(ValueError, "cannot satisfy requested granularity 'hour'"):
            TimeAxisResolver(
                request=self._compare_request(
                    grain="hour",
                    time_axis={"analysis_time": {"column": "log_date"}},
                ),
                engine_type="duckdb",
                available_columns=["log_date"],
                time_field_data_types={"log_date": "string"},
                time_field_formats={"log_date": "yyyymmdd"},
                time_field_support_min_granularities={"log_date": "day"},
            ).resolve()

    def test_date_field_at_hour_grain_rejected_even_with_required_prefix(self) -> None:
        """time_scope.field=log_date at hour grain rejected even when log_hour has required_prefix."""
        with self.assertRaisesRegex(ValueError, "cannot satisfy requested granularity 'hour'"):
            TimeAxisResolver(
                request=self._compare_request(
                    grain="hour",
                    time_axis={"analysis_time": {"column": "log_date"}},
                ),
                engine_type="duckdb",
                available_columns=["log_date", "log_hour"],
                time_field_data_types={"log_date": "string"},
                time_field_formats={"log_date": "yyyymmdd"},
                time_field_required_prefixes={"log_hour": "log_date"},
                time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
            ).resolve()


class PartitionPredicateDataTypeTests(unittest.TestCase):
    """Tests for per-column data_type quoting in partition pruning predicates."""

    def _compare_request(
        self,
        grain: str = "hour",
        time_axis: dict | None = None,
    ) -> Any:
        from marivo.time_scope import normalize_metric_query_request

        payload: dict[str, Any] = {
            "table": "iceberg.analytics.query_events",
            "metric": "queued_time",
            "time_scope": {
                "mode": "compare",
                "grain": grain,
                "current": {
                    "start": "2026-03-25" if grain != "hour" else "2026-03-25T10:00:00",
                    "end": "2026-03-26" if grain != "hour" else "2026-03-25T14:00:00",
                },
                "baseline": {
                    "start": "2026-03-24" if grain != "hour" else "2026-03-25T06:00:00",
                    "end": "2026-03-25" if grain != "hour" else "2026-03-25T10:00:00",
                },
            },
        }
        if time_axis is not None:
            payload["time_axis"] = time_axis
        return normalize_metric_query_request(payload)

    def test_integer_date_integer_hour_unquoted_predicate(self) -> None:
        """Integer date + integer hour columns produce unquoted literals in partition predicates."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "integer", "log_hour": "integer"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "h"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.partition_date_data_type, "integer")
        self.assertEqual(resolved.partition_hour_data_type, "integer")
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        # Date literal unquoted for integer column
        self.assertIn("log_date = 20260325", resolved.partition_pruning_predicate)
        # Hour literal unquoted for integer column
        self.assertIn("log_hour >= 6", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < 14", resolved.partition_pruning_predicate)
        # No single quotes on integer literals
        self.assertNotIn("'2026032", resolved.partition_pruning_predicate)
        self.assertNotIn("'6'", resolved.partition_pruning_predicate)
        self.assertNotIn("'14'", resolved.partition_pruning_predicate)

    def test_integer_date_hour_grain_day_range_partition_unquoted(self) -> None:
        """Integer date column with composite analysis axis but only date in partition."""
        # Analysis uses timestamp expression (hour-capable), but partition pruning
        # only has the integer date column — uses day-range predicate with unquoted literals.
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={
                    "analysis_time": {"column": "query_time"},
                    "partition_pruning": {"date_column": "log_date"},
                },
            ),
            engine_type="duckdb",
            available_columns=["query_time", "log_date"],
            time_field_data_types={
                "query_time": "timestamp",
                "log_date": "integer",
            },
            time_field_expressions={
                "query_time": "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP)",
            },
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_support_min_granularities={"query_time": "hour"},
        ).resolve()
        self.assertEqual(resolved.partition_date_data_type, "integer")
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        # Day-range predicate with unquoted integer literals
        self.assertIn("log_date >= 20260325", resolved.partition_pruning_predicate)
        self.assertNotIn("'2026032", resolved.partition_pruning_predicate)

    def test_mixed_layout_timestamp_analysis_integer_partition(self) -> None:
        """Timestamp analysis axis with integer partition columns — partition data_type is per-column."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={
                    "analysis_time": {"column": "query_time"},
                    "partition_pruning": {"date_column": "log_date", "hour_column": "log_hour"},
                },
            ),
            engine_type="duckdb",
            available_columns=["query_time", "log_date", "log_hour"],
            time_field_data_types={
                "query_time": "timestamp",
                "log_date": "integer",
                "log_hour": "integer",
            },
            time_field_expressions={
                "query_time": "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP)",
            },
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "h"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={
                "query_time": "hour",
                "log_date": "day",
                "log_hour": "hour",
            },
        ).resolve()
        # Analysis axis is timestamp, but partition columns have their own data types
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_data_type, "timestamp")
        self.assertEqual(resolved.partition_date_data_type, "integer")
        self.assertEqual(resolved.partition_hour_data_type, "integer")
        self.assertIsNotNone(resolved.partition_pruning_predicate)
        # Integer partition columns produce unquoted literals
        self.assertIn("log_date = 20260325", resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= 6", resolved.partition_pruning_predicate)

    def test_string_date_string_hour_quoted_predicate(self) -> None:
        """String date + string hour columns produce quoted literals (unchanged behavior)."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "string", "log_hour": "string"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "hh"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.partition_date_data_type, "string")
        self.assertEqual(resolved.partition_hour_data_type, "string")
        self.assertIn("log_date = '20260325'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '06'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '14'", resolved.partition_pruning_predicate)

    def test_integer_date_day_grain_unquoted(self) -> None:
        """Integer date column at day grain uses unquoted predicate."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="day",
                time_axis={"analysis_time": {"column": "log_date"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date"],
            time_field_data_types={"log_date": "integer"},
            time_field_formats={"log_date": "yyyymmdd"},
            time_field_support_min_granularities={"log_date": "day"},
        ).resolve()
        self.assertEqual(resolved.partition_date_data_type, "integer")
        self.assertIn("log_date >= 20260324", resolved.partition_pruning_predicate)
        self.assertIn("log_date < 20260326", resolved.partition_pruning_predicate)
        self.assertNotIn("'2026032", resolved.partition_pruning_predicate)

    def test_int_format_normalizes_to_h(self) -> None:
        """format='int' normalizes to 'h' and flows through hour-only field resolution."""
        resolved = TimeAxisResolver(
            request=self._compare_request(
                grain="hour",
                time_axis={"analysis_time": {"column": "log_hour"}},
            ),
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            time_field_data_types={"log_date": "integer", "log_hour": "integer"},
            time_field_formats={"log_date": "yyyymmdd", "log_hour": "int"},
            time_field_required_prefixes={"log_hour": "log_date"},
            time_field_support_min_granularities={"log_date": "day", "log_hour": "hour"},
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.partition_hour_data_type, "integer")
        self.assertIn("log_hour >= 6", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < 14", resolved.partition_pruning_predicate)
