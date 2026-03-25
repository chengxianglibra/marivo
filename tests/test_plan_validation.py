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


def _typed_compare_metric_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "table": "analytics.watch_events",
        "metric": "watch_time",
        "time_scope": {
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2026-03-01", "end": "2026-03-08"},
        },
    }
    params.update(overrides)
    return params


def _typed_aggregate_query_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "table": "analytics.watch_events",
        "group_by": ["platform"],
        "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
        "time_scope": {
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2026-03-01", "end": "2026-03-08"},
        },
    }
    params.update(overrides)
    return params


class AdvancedPlanValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "advanced_validation.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        cls._seed_published_metric_once(cls.client)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    @staticmethod
    def _seed_published_metric_once(client: TestClient) -> None:
        # Check if metric already exists (idempotent)
        metrics = client.get("/semantic/metrics").json()
        for m in metrics:
            if m.get("name") == "watch_time":
                return  # Already seeded

        entity = client.post(
            "/semantic/entities",
            json={"name": "session", "display_name": "Session", "keys": ["session_id"]},
        ).json()
        client.post(f"/semantic/entities/{entity['entity_id']}/publish")

        metric = client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "entity_id": entity["entity_id"],
            },
        ).json()
        client.post(f"/semantic/metrics/{metric['metric_id']}/publish")

    def test_validate_plan_rejects_unpublished_metric(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "semantic validation"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": _typed_compare_metric_params(metric="missing_metric"),
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

    def test_validate_plan_rejects_unsupported_metric_dimension(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "dimension validation"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": _typed_compare_metric_params(dimensions=["country"]),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("semantic_dimension_not_supported", [issue["code"] for issue in result["issues"]])

    def test_validate_plan_rejects_budget_exceeded(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "budget validation", "budget": {"max_rows_scanned": 1}},
        ).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={"steps": [{"step_type": "compare_metric", "params": _typed_compare_metric_params()}]},
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("budget_rows_exceeded", [issue["code"] for issue in result["issues"]])
        self.assertGreater(result["cost_estimates"][0]["estimated_rows"], 0)

    def test_validate_plan_rejects_legacy_compare_metric_params(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "legacy contract"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": _typed_compare_metric_params(filter="platform = 'android'"),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("legacy_param_not_supported", [issue["code"] for issue in result["issues"]])

    def test_validate_plan_rejects_legacy_aggregate_query_params(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "legacy aggregate contract"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "aggregate_query",
                        "params": _typed_aggregate_query_params(select=["platform", "COUNT(*) AS cnt"]),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("legacy_param_not_supported", [issue["code"] for issue in result["issues"]])

    def test_validate_plan_rejects_time_predicate_in_scope(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "time predicate contract"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": _typed_compare_metric_params(
                            scope={"predicate": "event_time >= TIMESTAMP '2026-03-01 00:00:00'"}
                        ),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("time_predicate_not_allowed_in_scope", [issue["code"] for issue in result["issues"]])

    def test_validate_aggregate_plan_rejects_time_predicate_in_scope(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "aggregate time predicate contract"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "aggregate_query",
                        "params": _typed_aggregate_query_params(
                            scope={"predicate": "event_time >= TIMESTAMP '2026-03-01 00:00:00'"}
                        ),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertFalse(result["valid"])
        self.assertIn("time_predicate_not_allowed_in_scope", [issue["code"] for issue in result["issues"]])

    def test_validate_plan_allows_non_time_scope_predicate(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "non time scope predicate"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": _typed_compare_metric_params(scope={"predicate": "platform = 'android'"}),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertTrue(result["valid"])

    def test_validate_aggregate_plan_allows_non_time_scope_predicate(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "aggregate non time scope predicate"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "aggregate_query",
                        "params": _typed_aggregate_query_params(scope={"predicate": "platform = 'android'"}),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertTrue(result["valid"])

    def test_validate_plan_allows_non_axis_suffix_columns_in_scope_predicate(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "suffix predicate"}).json()["session_id"]
        plan_id = self.client.post(
            f"/sessions/{session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "compare_metric",
                        "params": _typed_compare_metric_params(scope={"predicate": "business_hour = 9 AND state_date = '2026-03-01'"}),
                    }
                ]
            },
        ).json()["plan_id"]

        result = self.client.post(f"/sessions/{session_id}/plans/{plan_id}/validate").json()

        self.assertTrue(result["valid"])

    def test_validate_plan_warns_when_router_requires_fallback(self) -> None:
        meta_path = Path(self.temp_dir.name) / "fallback.meta.sqlite"
        duck_path = Path(self.temp_dir.name) / "fallback.duckdb"
        metadata = SQLiteMetadataStore(meta_path)
        metadata.initialize()
        get_seeded_duckdb_path(duck_path)
        analytics = DuckDBAnalyticsEngine(duck_path)
        analytics.initialize()
        # Seed a published metric so semantic validation passes
        from app.semantic import SemanticService
        semantic = SemanticService(metadata)
        entity = semantic.create_entity("session", "Session", ["session_id"])
        semantic.publish_entity(entity["entity_id"])
        metric = semantic.create_metric(
            "watch_time", "Watch Time", "avg(play_duration_seconds)",
            ["platform", "app_version", "network_type", "content_type"],
            entity_id=entity["entity_id"],
        )
        semantic.publish_metric(metric["metric_id"])
        service = SemanticLayerService(metadata, analytics)
        router = QueryRouter(metadata, EngineService(metadata))
        planning = PlanningService(
            metadata,
            analytics_engine=analytics,
            query_router=router,
        )

        session = service.create_session("routing fallback validation", {}, {}, {})
        plan = planning.draft_plan(session["session_id"], [{"step_type": "compare_metric", "params": _typed_compare_metric_params()}])
        result = planning.validate_plan(plan["plan_id"])

        self.assertTrue(result["valid"])
        self.assertIn("routing_table_unresolved", [issue["code"] for issue in result["issues"]])


if __name__ == "__main__":
    unittest.main()
