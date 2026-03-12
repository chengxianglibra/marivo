from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.mcp_client import OmniDBApiClient
from app.mcp_server import ResponseFormat, format_tool_response, render_catalog_markdown
from tests.shared_fixtures import get_seeded_duckdb_path


class DuckDBMvpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_catalog_exposes_duckdb_assets(self) -> None:
        response = self.client.get("/catalog")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["engine"], "duckdb")
        self.assertTrue(any(metric["id"] == "watch_time" for metric in payload["metrics"]))
        self.assertTrue(all(asset["row_count"] > 0 for asset in payload["assets"]))

    def test_watch_time_workflow_generates_claims_and_recommendations(self) -> None:
        session_response = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and recommend fixes."},
        )
        self.assertEqual(session_response.status_code, 200)
        session_id = session_response.json()["session_id"]

        workflow_response = self.client.post(f"/sessions/{session_id}/workflow/watch-time-drop")
        self.assertEqual(workflow_response.status_code, 200)
        payload = workflow_response.json()
        self.assertEqual(payload["workflow"], "watch_time_drop")
        self.assertGreaterEqual(len(payload["steps"]), 5)
        self.assertTrue(any("Android 8.3.1" in claim["text"] for claim in payload["claims"]))
        self.assertGreaterEqual(len(payload["recommendations"]), 2)

    def test_evidence_graph_contains_support_edges(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and recommend fixes."},
        ).json()["session_id"]
        self.client.post(f"/sessions/{session_id}/workflow/watch-time-drop")

        graph_response = self.client.get(f"/sessions/{session_id}/evidence")
        self.assertEqual(graph_response.status_code, 200)
        graph = graph_response.json()
        self.assertGreaterEqual(len(graph["observations"]), 10)
        self.assertGreaterEqual(len(graph["claims"]), 1)
        self.assertTrue(any(edge["edge_type"] == "supports" for edge in graph["edges"]))
        self.assertGreaterEqual(len(graph["recommendations"]), 2)

    def test_list_sessions_empty(self) -> None:
        """GET /sessions should return a list."""
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_get_session_after_create(self) -> None:
        """GET /sessions/{id} should return session details."""
        create_resp = self.client.post("/sessions", json={"goal": "Test session"})
        session_id = create_resp.json()["session_id"]
        resp = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["session_id"], session_id)
        self.assertEqual(data["goal"], "Test session")
        self.assertEqual(data["status"], "open")
        self.assertIn("created_at", data)

    def test_get_session_not_found(self) -> None:
        """GET /sessions/{id} with unknown ID should 404."""
        resp = self.client.get("/sessions/sess_nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_list_sessions_includes_created(self) -> None:
        """GET /sessions should include recently created session."""
        create_resp = self.client.post("/sessions", json={"goal": "Listed session"})
        session_id = create_resp.json()["session_id"]
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        ids = [s["session_id"] for s in resp.json()]
        self.assertIn(session_id, ids)

    def test_list_sessions_filter_by_status(self) -> None:
        """GET /sessions?status=open should filter."""
        self.client.post("/sessions", json={"goal": "Status filter test"})
        resp = self.client.get("/sessions?status=open")
        self.assertEqual(resp.status_code, 200)
        for s in resp.json():
            self.assertEqual(s["status"], "open")


class QueryRouterWiredServiceTests(unittest.TestCase):
    """Tests that SemanticLayerService works with QueryRouter wired in."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "router_test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_workflow_succeeds_with_query_router(self) -> None:
        """The workflow should produce valid results when QueryRouter is wired
        in, even if it falls back to the default analytics engine."""
        session = self.client.post(
            "/sessions",
            json={"goal": "Test QueryRouter wiring."},
        ).json()
        session_id = session["session_id"]

        resp = self.client.post(f"/sessions/{session_id}/workflow/watch-time-drop")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["workflow"], "watch_time_drop")
        self.assertGreaterEqual(len(payload["claims"]), 1)
        self.assertGreaterEqual(len(payload["recommendations"]), 2)

    def test_individual_steps_with_query_router(self) -> None:
        """Each step type should work when QueryRouter is present."""
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test individual steps with router."},
        ).json()["session_id"]

        step_types = [
            "compare_watch_time",
            "analyze_qoe",
            "analyze_ads",
            "analyze_recommendation",
        ]
        for step_type in step_types:
            resp = self.client.post(f"/sessions/{session_id}/steps/{step_type}")
            self.assertEqual(resp.status_code, 200, f"Step {step_type} failed")
            result = resp.json()
            self.assertEqual(result["step_type"], step_type)
            self.assertIn("summary", result)

    def test_service_has_query_router_attribute(self) -> None:
        """Verify that create_app wires the QueryRouter into the service."""
        service = self.client.app.state.service
        self.assertIsNotNone(service.query_router)

    def test_service_without_query_router_still_works(self) -> None:
        """SemanticLayerService should work without QueryRouter (backward compat)."""
        from app.service import SemanticLayerService

        # Reuse the already-initialized engine from the test app to avoid a
        # costly DuckDB re-initialization (~45 s).
        app = self.client.app
        meta = app.state.metadata_store
        analytics = app.state.analytics_engine
        svc = SemanticLayerService(meta, analytics)  # no query_router
        self.assertIsNone(svc.query_router)

        session = svc.create_session("Test no router", {}, {}, {})
        result = svc.run_step(session["session_id"], "compare_watch_time")
        self.assertEqual(result["step_type"], "compare_watch_time")
        self.assertIn("summary", result)

    def test_resolve_engine_returns_tuple(self) -> None:
        """_resolve_engine should return (engine, engine_type) tuple."""
        service = self.client.app.state.service
        result = service._resolve_engine(["watch_events"])
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        engine, engine_type = result
        self.assertIsInstance(engine_type, str)
        # Default fallback should be duckdb
        self.assertEqual(engine_type, "duckdb")

    def test_provenance_uses_resolved_engine_type(self) -> None:
        """Step provenance should reflect the resolved engine type."""
        session = self.client.post(
            "/sessions",
            json={"goal": "Test provenance engine type."},
        ).json()
        session_id = session["session_id"]
        # Run a step and check evidence provenance
        self.client.post(f"/sessions/{session_id}/steps/compare_watch_time")
        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        steps = evidence.get("steps", [])
        self.assertGreater(len(steps), 0)
        provenance = steps[0].get("provenance", {})
        self.assertIn("engine", provenance)
        # With default setup, engine type should be duckdb
        self.assertEqual(provenance["engine"], "duckdb")


class GenericStepTypeTests(unittest.TestCase):
    """Tests for profile_table and sample_rows step types."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "generic_steps.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_profile_table(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test profile_table."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "profile_table")
        self.assertIn("profile", result)
        self.assertGreater(result["profile"]["row_count"], 0)
        self.assertGreater(len(result["profile"]["columns"]), 0)

    def test_profile_table_missing_param(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing param."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_sample_rows(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test sample_rows."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events", "limit": 5},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "sample_rows")
        self.assertEqual(len(result["rows"]), 5)

    def test_sample_rows_default_limit(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test default limit."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.player_qoe"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(len(result["rows"]), 10)

    def test_sample_rows_missing_param(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing param."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={},
        )
        self.assertEqual(resp.status_code, 400)


class MetricResolutionTests(unittest.TestCase):
    """Tests for resolving metrics from semantic layer and compare_metric step."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "metric_res.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Seed a published metric via the semantic API
        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "session",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "watch_time",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        cls.metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{cls.metric_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_resolve_metric_sql(self) -> None:
        service = self.client.app.state.service
        sql = service.resolve_metric_sql("watch_time")
        self.assertEqual(sql, "avg(play_duration_seconds)")

    def test_resolve_metric_dimensions(self) -> None:
        service = self.client.app.state.service
        dims = service.resolve_metric_dimensions("watch_time")
        self.assertEqual(dims, ["platform", "app_version", "network_type", "content_type"])

    def test_resolve_metric_not_found(self) -> None:
        service = self.client.app.state.service
        self.assertIsNone(service.resolve_metric_sql("nonexistent_metric"))
        self.assertIsNone(service.resolve_metric_dimensions("nonexistent_metric"))

    def test_compare_metric_step(self) -> None:
        session = self.client.post(
            "/sessions", json={"goal": "Test compare_metric step."},
        ).json()
        session_id = session["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time",
                "table_name": "analytics.watch_events",
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "compare_metric")
        self.assertEqual(result["metric_name"], "watch_time")
        self.assertIn("summary", result)
        self.assertGreaterEqual(len(result["observations"]), 1)

    def test_compare_metric_missing_params(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing params."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_compare_metric_unpublished_metric(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test unpublished metric."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={"metric_name": "nonexistent", "table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_build_comparison_query(self) -> None:
        service = self.client.app.state.service
        query = service.build_comparison_query(
            metric_name="watch_time",
            table_name="analytics.watch_events",
            metric_sql="avg(play_duration_seconds)",
            dimensions=["platform", "app_version"],
        )
        self.assertIn("current_value", query)
        self.assertIn("baseline_value", query)
        self.assertIn("delta_pct", query)
        self.assertIn("analytics.watch_events", query)


class MCPWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "mcp-test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.test_app = create_app(db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_mcp_client_can_call_catalog_and_workflow(self) -> None:
        async def exercise_client() -> None:
            transport = httpx.ASGITransport(app=self.test_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                api_client = OmniDBApiClient(client=client)
                catalog = await api_client.get_catalog()
                self.assertEqual(catalog["engine"], "duckdb")

                session = await api_client.create_session("Investigate watch time drop for MCP.")
                workflow = await api_client.run_watch_time_workflow(session["session_id"])
                self.assertTrue(any("Android 8.3.1" in claim["text"] for claim in workflow["claims"]))

                evidence = await api_client.get_evidence(session["session_id"])
                self.assertTrue(any(edge["edge_type"] == "supports" for edge in evidence["edges"]))

        asyncio.run(exercise_client())

    def test_tool_response_formatting_supports_markdown(self) -> None:
        catalog_data = {
            "engine": "duckdb",
            "metrics": [{"id": "watch_time", "definition": "avg(play_duration_seconds)"}],
            "assets": [{"id": "watch_events", "kind": "table", "row_count": 10}],
        }
        response = format_tool_response(
            ResponseFormat.MARKDOWN,
            "Catalog returned 1 metric and 1 asset.",
            catalog_data,
            render_catalog_markdown(catalog_data),
        )
        self.assertIn("markdown", response)
        self.assertIn("# OmniDB catalog", response["markdown"])
        self.assertEqual(response["data"]["engine"], "duckdb")


if __name__ == "__main__":
    unittest.main()
