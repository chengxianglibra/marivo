from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.analysis_core.compiler import CompiledQuery
from app.analysis_core.executor import execute_compiled
from app.execution.errors import ExecutionError
from app.execution.federation import FederationPlanner
from app.execution.feedback import federation_failure_from_plan
from app.execution.routing_runtime import RoutingRuntime
from app.main import create_app
from app.storage.analytics import AnalyticsEngine
from tests.shared_fixtures import get_seeded_duckdb_path


class FailingEngine(AnalyticsEngine):
    def initialize(self) -> None:
        return None

    def query_rows(self, sql: str, params=None):  # type: ignore[override]
        raise RuntimeError("engine offline")

    def table_exists(self, table_name: str) -> bool:
        return False

    def table_row_count(self, table_name: str) -> int:
        return 0


class FakeRouter:
    def resolve_tables(self, table_names: list[str]):
        raise ValueError(f"No common engine for tables {table_names}")


class ExecutionFeedbackTests(unittest.TestCase):
    def test_execute_compiled_wraps_translation_error(self) -> None:
        engine = FailingEngine()
        query = CompiledQuery("SELECT 1", metadata={"engine_type": "unknown"})

        with self.assertRaises(ExecutionError) as error:
            execute_compiled(engine, query)

        self.assertEqual(error.exception.code, "translation_error")
        self.assertIn("prefer_default_engine", error.exception.fallback_candidates)

    def test_execute_compiled_wraps_engine_error(self) -> None:
        engine = FailingEngine()
        query = CompiledQuery(
            "SELECT 1", metadata={"engine_type": "duckdb", "step_type": "sample_rows"}
        )

        with self.assertRaises(ExecutionError) as error:
            execute_compiled(engine, query)

        self.assertEqual(error.exception.code, "engine_query_failed")
        self.assertTrue(error.exception.replan_candidate)

    def test_routing_runtime_returns_structured_fallback_feedback(self) -> None:
        runtime = RoutingRuntime(FakeRouter(), FailingEngine())

        resolution = runtime.resolve_tables(["watch_events"])

        self.assertTrue(resolution.fallback_used)
        self.assertIsNotNone(resolution.feedback)
        self.assertEqual(resolution.feedback.code, "routing_no_common_engine")

    def test_federation_failure_includes_plan_detail(self) -> None:
        plan = FederationPlanner().build_plan(
            translated_sql="SELECT 1",
            target_engine_type="trino",
            metadata={
                "step_type": "metric_query",
                "federation": {
                    "required": True,
                    "sources": [
                        {"engine_type": "duckdb", "table_names": ["watch_events"]},
                        {"engine_type": "trino", "table_names": ["ad_events"]},
                    ],
                },
            },
        )

        failure = federation_failure_from_plan(plan)

        self.assertEqual(failure.code, "federation_not_implemented")
        self.assertEqual(failure.detail["plan"]["mode"], "staged_handoff")
        self.assertIn("prefer_single_engine_route", failure.fallback_candidates)


class ExecutionFeedbackIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "execution_feedback.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_make_provenance_includes_routing_feedback_context(self) -> None:
        service = self.client.app.state.service
        service._routing_feedback_context = {
            "code": "routing_no_common_engine",
            "category": "routing",
        }
        provenance = service._make_provenance("SELECT 1")
        self.assertIn("routing", provenance)
        self.assertEqual(provenance["routing"]["code"], "routing_no_common_engine")


if __name__ == "__main__":
    unittest.main()
