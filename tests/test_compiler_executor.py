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

    def test_compile_complex_step_queries(self) -> None:
        period_params = ["c1", "c2", "b1", "b2", "b1", "c2"]

        watch = compile_step(
            AnalysisStepIR(index=0, step_type="compare_watch_time_top_slices", params={}),
            engine_type="duckdb",
            semantic_context={"period_params": period_params},
        )
        qoe = compile_step(
            AnalysisStepIR(index=1, step_type="analyze_qoe", params={}),
            engine_type="duckdb",
            semantic_context={"period_params": period_params},
        )
        rec = compile_step(
            AnalysisStepIR(index=2, step_type="analyze_recommendation", params={}),
            engine_type="duckdb",
            semantic_context={"period_params": period_params},
        )

        self.assertIn("current_watch_time", watch.sql)
        self.assertIn("analytics.watch_events", watch.sql)
        self.assertEqual(watch.params, period_params)
        self.assertIn("current_first_frame_ms", qoe.sql)
        self.assertIn("analytics.player_qoe", qoe.sql)
        self.assertIn("delta_ctr_pct", rec.sql)
        self.assertIn("analytics.recommendation_events", rec.sql)


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

    def test_execute_compiled_translates_complex_ads_query(self) -> None:
        engine = FakeEngine()
        compiled = compile_step(
            AnalysisStepIR(index=0, step_type="analyze_ads", params={}),
            engine_type="trino",
            semantic_context={"period_params": ["c1", "c2", "b1", "b2", "b1", "c2"]},
        )

        execute_compiled(engine, compiled)

        self.assertIsNotNone(engine.last_sql)
        self.assertIn("CAST(preroll_timeout AS DOUBLE)", engine.last_sql)


if __name__ == "__main__":
    unittest.main()
