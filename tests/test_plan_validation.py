from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.engines import EngineService
from app.main import create_app
from app.planning import PlanningService
from app.routing import QueryRouter
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class AdvancedPlanValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "advanced_validation.duckdb"
        get_seeded_duckdb_path(self.db_path)
        self.client = TestClient(create_app(self.db_path))
        self._seed_published_metric()

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def _seed_published_metric(self) -> None:
        entity = self.client.post(
            "/semantic/entities",
            json={"name": "session", "display_name": "Session", "keys": ["session_id"]},
        ).json()
        self.client.post(f"/semantic/entities/{entity['entity_id']}/publish")

        metric = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "entity_id": entity["entity_id"],
            },
        ).json()
        self.client.post(f"/semantic/metrics/{metric['metric_id']}/publish")

    def test_validate_plan_rejects_unpublished_metric(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "semantic validation"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": {
                            "metric_name": "missing_metric",
                            "table_name": "analytics.watch_events",
                        },
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("semantic_metric_not_found", [issue["code"] for issue in result["issues"]])

    def test_validate_plan_rejects_governance_blocker(self) -> None:
        governance = self.client.app.state.governance_service
        assert governance is not None
        policy = governance.create_policy("aggregate_only_validation", "aggregate_only")

        session_id = self.client.post("/sessions", json={"goal": "governance validation"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={"steps": [{"step_type": "sample_rows", "params": {"table_name": "analytics.watch_events"}}]},
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        governance.delete_policy(policy["policy_id"])

        self.assertFalse(result["valid"])
        self.assertIn(
            "aggregate_only_forbids_sample_rows",
            [issue["code"] for issue in result["issues"]],
        )

    def test_validate_plan_rejects_budget_exceeded(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "budget validation", "budget": {"max_rows_scanned": 1}},
        ).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={"steps": [{"step_type": "compare_watch_time"}]},
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("budget_rows_exceeded", [issue["code"] for issue in result["issues"]])
        self.assertGreater(result["cost_estimates"][0]["estimated_rows"], 0)

    def test_validate_plan_warns_when_router_requires_fallback(self) -> None:
        meta_path = Path(self.temp_dir.name) / "fallback.meta.sqlite"
        duck_path = Path(self.temp_dir.name) / "fallback.duckdb"
        metadata = SQLiteMetadataStore(meta_path)
        metadata.initialize()
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        analytics.initialize()
        service = SemanticLayerService(metadata, analytics)
        router = QueryRouter(metadata, EngineService(metadata))
        planning = PlanningService(
            metadata,
            analytics_engine=analytics,
            query_router=router,
        )

        session = service.create_session("routing fallback validation", {}, {}, {})
        plan = planning.draft_plan(session["session_id"], [{"step_type": "compare_watch_time"}])
        result = planning.validate_plan(plan["plan_id"])

        self.assertTrue(result["valid"])
        self.assertIn("routing_table_unresolved", [issue["code"] for issue in result["issues"]])


if __name__ == "__main__":
    unittest.main()
