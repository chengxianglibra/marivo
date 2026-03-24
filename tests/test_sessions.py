from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class SessionAPITests(unittest.TestCase):
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

    def test_catalog_exposes_dynamic_catalog(self) -> None:
        response = self.client.get("/catalog")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Top-level keys are present
        self.assertIn("entities", payload)
        self.assertIn("metrics", payload)
        self.assertIn("assets", payload)
        self.assertIn("policies", payload)
        # Lists are returned (may be empty in a fresh test DB)
        self.assertIsInstance(payload["entities"], list)
        self.assertIsInstance(payload["metrics"], list)
        self.assertIsInstance(payload["assets"], list)
        self.assertIsInstance(payload["policies"], list)

    def test_evidence_graph_contains_support_edges(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and recommend fixes."},
        ).json()["session_id"]

        # Seed a published metric for compare_metric
        entity_resp = self.client.post("/semantic/entities", json={
            "name": "session_mvp",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_resp = self.client.post("/semantic/metrics", json={
            "name": "watch_time_mvp",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        metric_id = metric_resp.json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={"metric_name": "watch_time_mvp", "table_name": "analytics.watch_events"},
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        graph_response = self.client.get(f"/sessions/{session_id}/evidence")
        self.assertEqual(graph_response.status_code, 200)
        graph = graph_response.json()
        self.assertGreaterEqual(len(graph["observations"]), 1)
        self.assertGreaterEqual(len(graph["claims"]), 1)
        self.assertTrue(any(edge["edge_type"] == "supports" for edge in graph["edges"]))
        self.assertGreaterEqual(len(graph["recommendations"]), 1)

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


if __name__ == "__main__":
    unittest.main()
