from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _compare_scope() -> dict[str, object]:
    return {
        "mode": "compare",
        "grain": "day",
        "current": {"start": "2026-02-28", "end": "2026-03-06"},
        "baseline": {"start": "2026-02-22", "end": "2026-02-28"},
    }


def _metric_query_payload(metric: str) -> dict[str, object]:
    return {
        "table": "analytics.watch_events",
        "metric": metric,
        "time_scope": _compare_scope(),
    }


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

        # Seed a published metric for metric_query
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
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_mvp"),
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

    def test_session_debug_endpoint_returns_summary(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and explain evidence upgrades."},
        ).json()["session_id"]

        entity_id = self.client.post("/semantic/entities", json={
            "name": "session_debug_entity",
            "display_name": "Session Debug Entity",
            "keys": ["session_id"],
        }).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post("/semantic/metrics", json={
            "name": "watch_time_debug_metric",
            "display_name": "Watch Time Debug",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        }).json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_debug_metric"),
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        resp = self.client.get(f"/sessions/{session_id}/debug")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["session_id"], session_id)
        self.assertIn("relation_discovery", payload)
        self.assertIn("checker_logs", payload)
        self.assertIsInstance(payload["checker_logs"], list)
        self.assertGreater(len(payload["checker_logs"]), 0)

    def test_session_debug_endpoint_returns_404_for_missing_session(self) -> None:
        resp = self.client.get("/sessions/sess_missing_debug/debug")
        self.assertEqual(resp.status_code, 404)

    def test_evidence_graph_claims_only_confirmed_filters_tentative_subgraph(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop before synthesis."},
        ).json()["session_id"]

        entity_id = self.client.post("/semantic/entities", json={
            "name": "session_tentative_entity",
            "display_name": "Session Tentative Entity",
            "keys": ["session_id"],
        }).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post("/semantic/metrics", json={
            "name": "watch_time_tentative_metric",
            "display_name": "Watch Time Tentative",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        }).json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_tentative_metric"),
        )

        resp = self.client.get(f"/sessions/{session_id}/evidence?claims_only=confirmed")
        self.assertEqual(resp.status_code, 200)
        graph = resp.json()
        self.assertEqual(graph["claims"], [])
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["recommendations"], [])
        self.assertGreaterEqual(len(graph["observations"]), 1)

    def test_evidence_graph_edge_types_filter_is_applied(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and filter graph edges."},
        ).json()["session_id"]

        entity_id = self.client.post("/semantic/entities", json={
            "name": "session_edge_filter_entity",
            "display_name": "Session Edge Filter Entity",
            "keys": ["session_id"],
        }).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post("/semantic/metrics", json={
            "name": "watch_time_edge_filter_metric",
            "display_name": "Watch Time Edge Filter",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        }).json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_edge_filter_metric"),
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        resp = self.client.get(f"/sessions/{session_id}/evidence?edge_types=supports")
        self.assertEqual(resp.status_code, 200)
        graph = resp.json()
        self.assertGreater(len(graph["edges"]), 0)
        self.assertTrue(all(edge["edge_type"] == "supports" for edge in graph["edges"]))

    def test_evidence_graph_include_debug_attaches_debug_payload(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop with debug payload."},
        ).json()["session_id"]

        entity_id = self.client.post("/semantic/entities", json={
            "name": "session_include_debug_entity",
            "display_name": "Session Include Debug Entity",
            "keys": ["session_id"],
        }).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post("/semantic/metrics", json={
            "name": "watch_time_include_debug_metric",
            "display_name": "Watch Time Include Debug",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        }).json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_include_debug_metric"),
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        resp = self.client.get(f"/sessions/{session_id}/evidence?include_debug=true")
        self.assertEqual(resp.status_code, 200)
        graph = resp.json()
        self.assertIn("debug", graph)
        self.assertIn("checker_logs", graph["debug"])
        self.assertIn("relation_discovery", graph["debug"])

    def test_evidence_graph_include_debug_respects_filtered_edges(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop with filtered debug payload."},
        ).json()["session_id"]

        entity_id = self.client.post("/semantic/entities", json={
            "name": "session_filtered_debug_entity",
            "display_name": "Session Filtered Debug Entity",
            "keys": ["session_id"],
        }).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post("/semantic/metrics", json={
            "name": "watch_time_filtered_debug_metric",
            "display_name": "Watch Time Filtered Debug",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        }).json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_filtered_debug_metric"),
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        resp = self.client.get(f"/sessions/{session_id}/evidence?edge_types=supports&include_debug=true")
        self.assertEqual(resp.status_code, 200)
        graph = resp.json()
        self.assertTrue(all(edge["edge_type"] == "supports" for edge in graph["edges"]))
        self.assertEqual(graph["debug"]["relation_discovery"]["relations_emitted"], 0)


if __name__ == "__main__":
    unittest.main()
