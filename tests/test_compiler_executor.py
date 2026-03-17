from __future__ import annotations

import unittest

from app.analysis_core.compiler import build_comparison_query, compile_step
from app.analysis_core.executor import execute_compiled
from app.analysis_core.ir import AnalysisStepIR


class FakeEngine:
    def __init__(self) -> None:
        self.last_sql: str | None = None
        self.last_params: list[object] | None = None

    def query_rows(self, sql: str, params: list[object] | None = None) -> list[dict[str, object]]:
        self.last_sql = sql
        self.last_params = params
        return [{"ok": 1}]


class CompilerTests(unittest.TestCase):
    def test_compile_sample_rows(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(index=0, step_type="sample_rows", params={"table_name": "analytics.watch_events", "limit": 5}),
            engine_type="duckdb",
        )

        self.assertIn("analytics.watch_events", compiled.sql)
        self.assertIn("LIMIT 5", compiled.sql)
        self.assertEqual(compiled.metadata["engine_type"], "duckdb")

    def test_compile_compare_metric(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="compare_metric",
                params={"metric_name": "watch_time", "table_name": "analytics.watch_events"},
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
        self.assertEqual(len(compiled.params), 6)

    def test_build_comparison_query_helper(self) -> None:
        query = build_comparison_query(
            metric_name="watch_time",
            table_name="analytics.watch_events",
            metric_sql="avg(play_duration_seconds)",
            dimensions=["platform", "app_version"],
        )

        self.assertIn("delta_pct", query)
        self.assertIn("analytics.watch_events", query)

    def test_build_comparison_query_empty_dimensions(self) -> None:
        """Empty dimensions should produce aggregate-only SQL with no GROUP BY on dims."""
        query = build_comparison_query(
            metric_name="failure_rate",
            table_name="ods_trino_query_info",
            metric_sql="avg(CASE WHEN state='FAILED' THEN 1 ELSE 0 END)",
            dimensions=[],
        )

        self.assertIn("delta_pct", query)
        self.assertIn("ods_trino_query_info", query)
        # Should have GROUP BY period but NOT GROUP BY <dim_cols>
        self.assertIn("GROUP BY period", query)
        # The pivoted CTE should have no GROUP BY clause (aggregate-only)
        # Check it does not contain "GROUP BY \n" with dimension columns
        self.assertNotIn("GROUP BY period,", query)

    def test_compile_compare_metric_empty_dimensions(self) -> None:
        """compile_step should handle empty dimensions in semantic_context."""
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="compare_metric",
                params={"metric_name": "failure_rate", "table_name": "ods_trino_query_info"},
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
        self.assertEqual(len(compiled.params), 6)

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

    def test_build_comparison_query_with_filter(self) -> None:
        query = build_comparison_query(
            metric_name="failure_rate",
            table_name="ods_trino_query_info",
            metric_sql="avg(CASE WHEN state='FAILED' THEN 1 ELSE 0 END)",
            dimensions=["cluster"],
            filter_expr="cluster = 'k8soneservice-oneservice'",
        )
        self.assertIn("AND cluster = 'k8soneservice-oneservice'", query)
        self.assertIn("delta_pct", query)

    def test_compile_compare_metric_custom_order(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="compare_metric",
                params={
                    "metric_name": "watch_time",
                    "table_name": "analytics.watch_events",
                    "order": "DESC",
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

    def test_compile_compare_metric_invalid_order_raises(self) -> None:
        with self.assertRaises(ValueError):
            compile_step(
                AnalysisStepIR(
                    index=0,
                    step_type="compare_metric",
                    params={
                        "metric_name": "watch_time",
                        "table_name": "analytics.watch_events",
                        "order": "DROP TABLE",
                    },
                ),
                engine_type="duckdb",
                semantic_context={
                    "metric_sql": "avg(play_duration_seconds)",
                    "dimensions": ["platform"],
                    "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
                },
            )

    def test_compare_metric_default_limit_is_10(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="compare_metric",
                params={"metric_name": "watch_time", "table_name": "analytics.watch_events"},
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )
        self.assertIn("LIMIT 10", compiled.sql)

    def test_compile_compare_metric_with_filter(self) -> None:
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="compare_metric",
                params={
                    "metric_name": "watch_time",
                    "table_name": "analytics.watch_events",
                    "filter": "platform = 'android'",
                },
            ),
            engine_type="duckdb",
            semantic_context={
                "metric_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
                "period_params": ["c1", "c2", "b1", "b2", "b1", "c2"],
            },
        )
        self.assertIn("AND platform = 'android'", compiled.sql)

    def test_compile_unsupported_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            compile_step(
                AnalysisStepIR(index=0, step_type="nonexistent_step", params={}),
                engine_type="duckdb",
            )


class ExecutorTests(unittest.TestCase):
    def test_execute_compiled_translates_sql(self) -> None:
        engine = FakeEngine()
        compiled = compile_step(
            AnalysisStepIR(index=0, step_type="sample_rows", params={"table_name": "analytics.watch_events", "limit": 1}),
            engine_type="trino",
        )
        compiled.sql = "SELECT play_duration_seconds::DOUBLE FROM analytics.watch_events LIMIT 1"

        result = execute_compiled(engine, compiled)

        self.assertEqual(result.rows, [{"ok": 1}])
        self.assertIsNotNone(engine.last_sql)
        self.assertIn("CAST(play_duration_seconds AS DOUBLE)", engine.last_sql)

    def test_execute_compiled_translates_compare_metric(self) -> None:
        engine = FakeEngine()
        compiled = compile_step(
            AnalysisStepIR(
                index=0,
                step_type="compare_metric",
                params={"metric_name": "watch_time", "table_name": "analytics.watch_events"},
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
