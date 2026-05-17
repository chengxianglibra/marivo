from __future__ import annotations

import unittest

from marivo.core.semantic.compiler import (
    build_metric_query,
)
from marivo.core.semantic.ir import AnalysisStepIR
from marivo.runtime.semantic.compile_step import compile_step
from marivo.runtime.semantic.executor import execute_compiled


class FakeEngine:
    def __init__(self) -> None:
        self.last_sql: str | None = None
        self.last_params: list[object] | None = None

    def query_rows(self, sql: str, params: list[object] | None = None) -> list[dict[str, object]]:
        self.last_sql = sql
        self.last_params = params
        return [{"ok": 1}]


def _compare_scoped_query() -> dict[str, object]:
    return {
        "mode": "compare",
        "analysis_time_expr": "event_time",
        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
        "baseline": {"start": "2026-03-25T06:00:00", "end": "2026-03-25T10:00:00"},
    }


class CompilerTests(unittest.TestCase):
    def test_compile_sample_rows(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={"table_name": "analytics.watch_events", "limit": 5},
            ),
            engine_type="duckdb",
        )

        self.assertIn("analytics.watch_events", compiled.sql)
        self.assertIn("LIMIT 5", compiled.sql)
        self.assertEqual(compiled.metadata["engine_type"], "duckdb")

    def test_compile_metric_query(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": _compare_scoped_query(),
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )

        self.assertIn("current_value", compiled.sql)
        self.assertIn("baseline_value", compiled.sql)
        self.assertIn("analytics.watch_events", compiled.sql)
        self.assertEqual(len(compiled.params), 8)

    def test_build_metric_query_helper_compare_mode(self) -> None:
        query = build_metric_query(
            metric_name="watch_time",
            table_name="analytics.watch_events",
            metric_sql="avg(play_duration_seconds)",
            dimensions=["platform", "app_version"],
            scoped_query=_compare_scoped_query(),
        )

        self.assertIn("delta_pct", query)
        self.assertIn("analytics.watch_events", query)

    def test_build_metric_query_helper_single_window_mode(self) -> None:
        query = build_metric_query(
            metric_name="watch_time",
            table_name="analytics.watch_events",
            metric_sql="avg(play_duration_seconds)",
            dimensions=["platform", "app_version"],
            order="CURRENT_VALUE DESC",
            scoped_query={
                "mode": "single_window",
                "analysis_time_expr": "event_time",
                "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
            },
        )

        self.assertIn("ROUND(avg(play_duration_seconds), 2) AS current_value", query)
        self.assertIn("COUNT(*) AS current_sessions", query)
        self.assertIn("GROUP BY platform, app_version", query)
        self.assertIn("ORDER BY current_value DESC", query)
        self.assertNotIn("baseline_value", query)
        self.assertNotIn("delta_pct", query)

    def test_build_metric_query_empty_dimensions(self) -> None:
        """Empty dimensions should produce aggregate-only SQL with no GROUP BY on dims."""
        query = build_metric_query(
            metric_name="failure_rate",
            table_name="ods_trino_query_info",
            metric_sql="avg(CASE WHEN state='FAILED' THEN 1 ELSE 0 END)",
            dimensions=[],
            scoped_query=_compare_scoped_query(),
        )

        self.assertIn("delta_pct", query)
        self.assertIn("ods_trino_query_info", query)
        # Should have GROUP BY period but NOT GROUP BY <dim_cols>
        self.assertIn("GROUP BY period", query)
        # The pivoted CTE should have no GROUP BY clause (aggregate-only)
        # Check it does not contain "GROUP BY \n" with dimension columns
        self.assertNotIn("GROUP BY period,", query)

    def test_compile_metric_query_empty_dimensions(self) -> None:
        """compile_step should handle empty dimensions in semantic_context."""
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "failure_rate",
                    "table": "ods_trino_query_info",
                    "scoped_query": _compare_scoped_query(),
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(CASE WHEN state='FAILED' THEN 1 ELSE 0 END)",
                "dimensions": [],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )

        self.assertIn("current_value", compiled.sql)
        self.assertIn("baseline_value", compiled.sql)
        self.assertEqual(len(compiled.params), 8)

    def test_compile_metric_query_single_window_scoped_query(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "order": "CURRENT_VALUE DESC",
                    "scoped_query": {
                        "mode": "single_window",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version"],
            },
        )

        self.assertIn("ROUND(avg(play_duration_seconds), 2) AS current_value", compiled.sql)
        self.assertIn("COUNT(*) AS current_sessions", compiled.sql)
        self.assertIn("GROUP BY platform, app_version", compiled.sql)
        self.assertIn("ORDER BY current_value DESC", compiled.sql)
        self.assertNotIn("baseline_value", compiled.sql)
        self.assertNotIn("baseline_sessions", compiled.sql)
        self.assertNotIn("delta_pct", compiled.sql)
        self.assertEqual(compiled.params, ["2026-03-25T10:00:00", "2026-03-25T14:00:00"])

    def test_compile_metric_query_single_window_trino_timestamp_scoped_query_formats_params(
        self,
    ) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "order": "CURRENT_VALUE DESC",
                    "scoped_query": {
                        "mode": "single_window",
                        "engine_type": "trino",
                        "analysis_time_kind": "timestamp",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                    },
                },
            ),
            engine_type="trino",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version"],
            },
        )

        self.assertEqual(compiled.params, [])
        self.assertIn("event_time >= TIMESTAMP '2026-03-25 10:00:00'", compiled.sql)
        self.assertIn("event_time < TIMESTAMP '2026-03-25 14:00:00'", compiled.sql)

    def test_compile_aggregate_query_single_window_trino_partition_fields_scoped_query_inlines_literals(
        self,
    ) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": "analytics.watch_events",
                    "measures": [{"expr": "COUNT(*)", "as": "value"}],
                    "group_by": [],
                    "order": "value DESC",
                    "scoped_query": {
                        "mode": "single_window",
                        "engine_type": "trino",
                        "analysis_time_kind": "partition_fields",
                        "analysis_time_expr": "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP)",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                    },
                },
            ),
            engine_type="trino",
        )

        self.assertEqual(compiled.params, [])
        self.assertIn(
            "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP) >= TIMESTAMP '2026-03-25 10:00:00'",
            compiled.sql,
        )
        self.assertIn(
            "CAST(CONCAT(log_date, ' ', log_hour, ':00:00') AS TIMESTAMP) < TIMESTAMP '2026-03-25 14:00:00'",
            compiled.sql,
        )

    def test_compile_aggregate_query_timestamp_expression_keeps_partition_pruning(
        self,
    ) -> None:
        expression = "DATE_PARSE(CAST(create_time AS VARCHAR), '%Y-%m-%d %H:%i:%s')"
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": "analytics.watch_events",
                    "measures": [{"expr": "COUNT(*)", "as": "value"}],
                    "group_by": [],
                    "order": "value DESC",
                    "scoped_query": {
                        "mode": "single_window",
                        "engine_type": "trino",
                        "analysis_time_kind": "timestamp",
                        "analysis_time_expr": expression,
                        "partition_pruning_predicate": "log_date = '20260515' AND log_hour >= '00' AND log_hour < '24'",
                        "current": {"start": "2026-05-15T00:00:00", "end": "2026-05-16T00:00:00"},
                    },
                },
            ),
            engine_type="trino",
        )

        self.assertEqual(compiled.params, [])
        self.assertIn(f"{expression} >= TIMESTAMP '2026-05-15 00:00:00'", compiled.sql)
        self.assertIn(f"{expression} < TIMESTAMP '2026-05-16 00:00:00'", compiled.sql)
        self.assertIn(
            "(log_date = '20260515' AND log_hour >= '00' AND log_hour < '24')",
            compiled.sql,
        )

    def test_compile_aggregate_query_uses_derived_date_expression_for_scoped_where(
        self,
    ) -> None:
        expression = "CAST(SUBSTRING(create_time, 1, 10) AS DATE)"
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": "analytics.watch_events",
                    "measures": [{"expr": "COUNT(*)", "as": "value"}],
                    "group_by": [],
                    "scoped_query": {
                        "mode": "single_window",
                        "engine_type": "trino",
                        "analysis_time_kind": "date_expression",
                        "analysis_time_expr": expression,
                        "current": {"start": "2026-03-25", "end": "2026-03-26"},
                    },
                },
            ),
            engine_type="trino",
        )

        self.assertIn(f"{expression} >= ?", compiled.sql)
        self.assertIn(f"{expression} < ?", compiled.sql)
        self.assertNotIn("CAST(create_time AS DATE)", compiled.sql)
        self.assertEqual(compiled.params, ["2026-03-25", "2026-03-26"])

    def test_compile_metric_query_scoped_query_requires_valid_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "scoped_query.mode must be"):
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "scoped_query": {
                            "analysis_time_expr": "event_time",
                            "current": {
                                "start": "2026-03-25T10:00:00",
                                "end": "2026-03-25T14:00:00",
                            },
                        },
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "metric_sql": "avg(play_duration_seconds)",
                    "dimensions": ["platform"],
                },
            )

    def test_compile_sample_rows_with_filter(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={
                    "table_name": "analytics.watch_events",
                    "limit": 5,
                    "filter": "status = 'active'",
                },
            ),
            engine_type="duckdb",
        )
        self.assertIn("WHERE status = 'active'", compiled.sql)
        self.assertIn("LIMIT 5", compiled.sql)

    def test_compile_sample_rows_with_columns(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={
                    "table_name": "analytics.watch_events",
                    "limit": 3,
                    "columns": ["session_id", "platform"],
                },
            ),
            engine_type="duckdb",
        )
        self.assertIn("session_id, platform", compiled.sql)
        self.assertNotIn("SELECT *", compiled.sql)

    def test_compile_sample_rows_with_date_filter(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={
                    "table_name": "analytics.watch_events",
                    "limit": 10,
                    "date_column": "log_date",
                    "date_value": "20260301",
                },
            ),
            engine_type="duckdb",
        )
        self.assertIn("log_date = '20260301'", compiled.sql)

    def test_compile_sample_rows_with_filter_and_date(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={
                    "table_name": "analytics.watch_events",
                    "limit": 10,
                    "filter": "platform = 'ios'",
                    "date_column": "log_date",
                    "date_value": "20260301",
                },
            ),
            engine_type="duckdb",
        )
        self.assertIn("platform = 'ios'", compiled.sql)
        self.assertIn("log_date = '20260301'", compiled.sql)
        self.assertIn(" AND ", compiled.sql)

    def test_build_metric_query_with_filter(self) -> None:
        query = build_metric_query(
            metric_name="failure_rate",
            table_name="ods_trino_query_info",
            metric_sql="avg(CASE WHEN state='FAILED' THEN 1 ELSE 0 END)",
            dimensions=["cluster"],
            scoped_query={
                **_compare_scoped_query(),
                "scope_predicate_filter": "cluster = 'k8soneservice-oneservice'",
            },
        )
        self.assertIn("cluster = 'k8soneservice-oneservice'", query)
        self.assertIn("delta_pct", query)

    def test_compile_metric_query_custom_order(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "order": "DESC",
                    "scoped_query": _compare_scoped_query(),
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )
        self.assertIn("ORDER BY delta_pct DESC", compiled.sql)

    def test_compile_metric_query_single_window_current_sessions_order(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "order": "CURRENT_SESSIONS ASC",
                    "scoped_query": {
                        "mode": "single_window",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )

        self.assertIn("ORDER BY current_sessions ASC", compiled.sql)

    def test_compile_metric_query_single_window_default_order_is_current_value_desc(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": {
                        "mode": "single_window",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )

        self.assertIn("ORDER BY current_value DESC", compiled.sql)

    def test_compile_metric_query_with_scoped_query_uses_ordered_filters(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": {
                        "mode": "compare",
                        "analysis_time_expr": "event_time",
                        "partition_pruning_predicate": "log_date >= '20260325'",
                        "session_constraints_filter": "platform = 'android'",
                        "session_raw_filter": "country = 'US'",
                        "scope_constraints_filter": "region = 'us-east'",
                        "scope_predicate_filter": "device_type = 'phone'",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                        "baseline": {"start": "2026-03-25T06:00:00", "end": "2026-03-25T10:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )

        self.assertIn("event_time >= ? AND event_time < ?", compiled.sql)
        self.assertIn("CASE", compiled.sql)
        self.assertIn("FROM scoped", compiled.sql)
        self.assertEqual(
            compiled.params,
            [
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
            ],
        )
        window_idx = compiled.sql.index(
            "((event_time >= ? AND event_time < ?) OR (event_time >= ? AND event_time < ?))"
        )
        pruning_idx = compiled.sql.index("(log_date >= '20260325')")
        session_idx = compiled.sql.index("(platform = 'android')")
        raw_filter_idx = compiled.sql.index("(country = 'US')")
        scope_constraints_idx = compiled.sql.index("(region = 'us-east')")
        scope_predicate_idx = compiled.sql.index("(device_type = 'phone')")
        self.assertLess(window_idx, pruning_idx)
        self.assertLess(pruning_idx, session_idx)
        self.assertLess(session_idx, raw_filter_idx)
        self.assertLess(raw_filter_idx, scope_constraints_idx)
        self.assertLess(scope_constraints_idx, scope_predicate_idx)

    def test_compile_metric_query_timestamp_only_scoped_query_omits_pruning(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": {
                        "mode": "compare",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                        "baseline": {"start": "2026-03-25T06:00:00", "end": "2026-03-25T10:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )

        self.assertIn("event_time >= ? AND event_time < ?", compiled.sql)
        self.assertNotIn("log_date", compiled.sql)
        self.assertEqual(
            compiled.params,
            [
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
            ],
        )

    def test_compile_metric_query_formats_date_field_bounds_to_resolved_encoding(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": {
                        "mode": "compare",
                        "analysis_time_kind": "date_field",
                        "analysis_time_expr": "log_date",
                        "analysis_time_format": "yyyymmdd",
                        "current": {"start": "2026-03-25", "end": "2026-03-26"},
                        "baseline": {"start": "2026-03-24", "end": "2026-03-25"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )

        self.assertIn("log_date >= ? AND log_date < ?", compiled.sql)
        self.assertEqual(
            compiled.params,
            [
                "20260325",
                "20260326",
                "20260324",
                "20260325",
                "20260325",
                "20260326",
                "20260324",
                "20260325",
            ],
        )

    def test_compile_metric_query_invalid_order_raises(self) -> None:
        with self.assertRaises(ValueError):
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "order": "DROP TABLE",
                        "scoped_query": _compare_scoped_query(),
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "metric_sql": "avg(play_duration_seconds)",
                    "dimensions": ["platform"],
                    "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
                },
            )

    def test_compile_metric_query_single_window_invalid_order_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "current_value/current_sessions ASC or DESC"):
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="metric_query",
                    params={
                        "metric": "watch_time",
                        "table": "analytics.watch_events",
                        "order": "DELTA_PCT DESC",
                        "scoped_query": {
                            "mode": "single_window",
                            "analysis_time_expr": "event_time",
                            "current": {
                                "start": "2026-03-25T10:00:00",
                                "end": "2026-03-25T14:00:00",
                            },
                        },
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "metric_sql": "avg(play_duration_seconds)",
                    "dimensions": ["platform"],
                },
            )

    def test_metric_query_default_limit_is_10(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": _compare_scoped_query(),
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )
        self.assertIn("LIMIT 10", compiled.sql)

    def test_compile_metric_query_with_session_constraints_filter(self) -> None:
        """Session constraints flow through the shared scoped-query contract."""
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": {
                        "mode": "single_window",
                        "analysis_time_expr": "event_date",
                        "session_constraints_filter": "cluster = 'k8sbi-bi1'",
                        "current": {"start": "2026-03-01", "end": "2026-03-08"},
                    },
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )
        self.assertIn("(cluster = 'k8sbi-bi1')", compiled.sql)
        self.assertEqual(compiled.params, ["2026-03-01", "2026-03-08"])

    def test_compile_unsupported_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            compile_step(
                AnalysisStepIR(index=0, step_type="nonexistent_step", params={}),
                engine_type="duckdb",
            )


class AggregateQueryTests(unittest.TestCase):
    def test_compile_typed_aggregate_query_single_window_uses_measures(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": "events",
                    "group_by": ["platform"],
                    "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                    "order": "query_count DESC",
                    "scoped_query": {
                        "mode": "single_window",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
        )
        self.assertIn("COUNT(*) AS query_count", compiled.sql)
        self.assertIn("FROM scoped GROUP BY platform ORDER BY query_count DESC", compiled.sql)
        self.assertEqual(compiled.params, ["2026-03-25T10:00:00", "2026-03-25T14:00:00"])

    def test_compile_typed_aggregate_query_compare_mode_emits_delta_columns(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": "events",
                    "group_by": ["platform"],
                    "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                    "scoped_query": {
                        "mode": "compare",
                        "analysis_time_expr": "event_time",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                        "baseline": {"start": "2026-03-25T06:00:00", "end": "2026-03-25T10:00:00"},
                    },
                },
            ),
            engine_type="duckdb",
        )
        self.assertIn("query_count_current", compiled.sql)
        self.assertIn("query_count_baseline", compiled.sql)
        self.assertIn("query_count_delta_pct", compiled.sql)
        self.assertEqual(
            compiled.params,
            [
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
            ],
        )

    def test_compile_typed_aggregate_query_mixed_layout_uses_timestamp_correctness_and_partition_pruning(
        self,
    ) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="aggregate_query",
                params={
                    "table": "events",
                    "group_by": ["platform"],
                    "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                    "scoped_query": {
                        "mode": "compare",
                        "analysis_time_kind": "timestamp",
                        "analysis_time_expr": "event_time",
                        "partition_pruning_predicate": "log_date = '20260325' AND log_hour >= '06' AND log_hour < '14'",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                        "baseline": {"start": "2026-03-25T06:00:00", "end": "2026-03-25T10:00:00"},
                    },
                },
            ),
            engine_type="trino",
        )

        self.assertIn("event_time >= ? AND event_time < ?", compiled.sql)
        self.assertIn(
            "(log_date = '20260325' AND log_hour >= '06' AND log_hour < '14')", compiled.sql
        )
        self.assertIn("FROM scoped", compiled.sql)
        self.assertIn("query_count_current", compiled.sql)
        self.assertIn("query_count_baseline", compiled.sql)
        self.assertEqual(
            compiled.params,
            [
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T10:00:00",
                "2026-03-25T14:00:00",
                "2026-03-25T06:00:00",
                "2026-03-25T10:00:00",
            ],
        )


class ExecutorTests(unittest.TestCase):
    def test_execute_compiled_translates_sql(self) -> None:
        engine = FakeEngine()
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="sample_rows",
                params={"table_name": "analytics.watch_events", "limit": 1},
            ),
            engine_type="trino",
        )
        compiled.sql = "SELECT play_duration_seconds::DOUBLE FROM analytics.watch_events LIMIT 1"

        result = execute_compiled(engine, compiled)

        self.assertEqual(result.rows, [{"ok": 1}])
        self.assertIsNotNone(engine.last_sql)
        self.assertIn("CAST(play_duration_seconds AS DOUBLE)", engine.last_sql)

    def test_execute_compiled_translates_metric_query(self) -> None:
        engine = FakeEngine()
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="metric_query",
                params={
                    "metric": "watch_time",
                    "table": "analytics.watch_events",
                    "scoped_query": _compare_scoped_query(),
                },
            ),
            engine_type="trino",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )

        execute_compiled(engine, compiled)

        self.assertIsNotNone(engine.last_sql)
        self.assertIn("analytics.watch_events", engine.last_sql)


if __name__ == "__main__":
    unittest.main()
