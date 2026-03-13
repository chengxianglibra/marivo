from __future__ import annotations

import unittest

from app.analysis_core.compiler import CompiledQuery
from app.analysis_core.executor import execute_compiled
from app.execution.errors import ExecutionFailure
from app.execution.federation import FederationPlanner, FederationRuntime
from app.execution.translation import DefaultQueryTranslator, request_from_compiled_query


class FakeEngine:
    def __init__(self) -> None:
        self.last_sql: str | None = None
        self.last_params: list[object] | None = None

    def query_rows(self, sql: str, params: list[object] | None = None) -> list[dict[str, object]]:
        self.last_sql = sql
        self.last_params = params
        return [{"ok": 1}]


class TranslationContractTests(unittest.TestCase):
    def test_default_translator_attaches_direct_plan(self) -> None:
        translator = DefaultQueryTranslator()
        compiled = CompiledQuery(
            "SELECT play_duration_seconds::DOUBLE FROM analytics.watch_events LIMIT 1",
            metadata={
                "engine_type": "trino",
                "step_type": "sample_rows",
                "table_name": "analytics.watch_events",
            },
        )

        result = translator.translate(request_from_compiled_query(compiled))

        self.assertEqual(result.strategy, "direct_sql")
        self.assertIn("CAST(play_duration_seconds AS DOUBLE)", result.sql)
        self.assertIsNotNone(result.federation_plan)
        assert result.federation_plan is not None
        self.assertEqual(result.federation_plan.mode, "single_engine")
        self.assertEqual(result.federation_plan.audit["stage_count"], 1)

    def test_federation_planner_builds_staged_handoff_plan(self) -> None:
        planner = FederationPlanner()

        plan = planner.build_plan(
            translated_sql="SELECT 1",
            target_engine_type="trino",
            metadata={
                "step_type": "compare_metric",
                "table_names": ["watch_events", "ad_events"],
                "federation": {
                    "required": True,
                    "reason": "cross-engine join",
                    "sources": [
                        {
                            "engine_id": "eng_duck",
                            "engine_type": "duckdb",
                            "table_names": ["watch_events"],
                        },
                        {
                            "engine_id": "eng_trino",
                            "engine_type": "trino",
                            "table_names": ["ad_events"],
                        },
                    ],
                    "merge_engine_type": "trino",
                    "merge_strategy": "hash_join",
                },
            },
        )

        self.assertEqual(plan.mode, "staged_handoff")
        self.assertEqual(len(plan.stages), 2)
        self.assertIsNotNone(plan.merge)
        assert plan.merge is not None
        self.assertEqual(plan.merge.strategy, "hash_join")
        self.assertTrue(plan.audit["requires_federation"])
        self.assertEqual(plan.provenance["federation_reason"], "cross-engine join")


class FederationRuntimeTests(unittest.TestCase):
    def test_execute_compiled_includes_translation_and_federation_metadata(self) -> None:
        engine = FakeEngine()
        compiled = CompiledQuery(
            "SELECT play_duration_seconds::DOUBLE FROM analytics.watch_events LIMIT 1",
            metadata={
                "engine_type": "trino",
                "step_type": "sample_rows",
                "table_name": "analytics.watch_events",
            },
        )

        result = execute_compiled(engine, compiled)

        self.assertEqual(result.rows, [{"ok": 1}])
        self.assertIsNotNone(engine.last_sql)
        self.assertEqual(result.metadata["translation"]["strategy"], "direct_sql")
        self.assertEqual(result.metadata["federation_plan"]["mode"], "single_engine")

    def test_federation_runtime_fails_honestly_for_staged_plan(self) -> None:
        runtime = FederationRuntime()
        plan = FederationPlanner().build_plan(
            translated_sql="SELECT 1",
            target_engine_type="trino",
            metadata={
                "step_type": "compare_metric",
                "federation": {
                    "required": True,
                    "sources": [
                        {"engine_type": "duckdb", "table_names": ["watch_events"]},
                        {"engine_type": "trino", "table_names": ["ad_events"]},
                    ],
                },
            },
        )

        with self.assertRaises(ExecutionFailure) as error:
            runtime.execute(FakeEngine(), translated_sql="SELECT 1", plan=plan)

        self.assertEqual(error.exception.code, "federation_not_implemented")
        self.assertEqual(error.exception.category, "federation")
        self.assertEqual(error.exception.detail["plan"]["mode"], "staged_handoff")


if __name__ == "__main__":
    unittest.main()
