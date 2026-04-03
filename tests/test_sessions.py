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

    def test_list_sessions_empty(self) -> None:
        """GET /sessions should return a list."""
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_get_session_after_create(self) -> None:
        """GET /sessions/{id} should return canonical AnalysisSession root (Phase 5a)."""
        create_resp = self.client.post("/sessions", json={"goal": "Test session"})
        session_id = create_resp.json()["session_id"]
        resp = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["session_id"], session_id)
        # goal is structured in canonical shape
        self.assertIsInstance(data["goal"], dict)
        self.assertEqual(data["goal"]["question"], "Test session")
        # lifecycle carries status
        self.assertIn("lifecycle", data)
        self.assertEqual(data["lifecycle"]["status"], "open")
        self.assertIsNone(data["lifecycle"]["terminal_reason"])
        self.assertIsNone(data["lifecycle"]["ended_at"])
        self.assertIsNone(data["lifecycle"]["rollover_from_session_id"])
        # governance present
        self.assertIn("governance", data)
        self.assertIn("budget", data["governance"])
        self.assertIn("warnings", data["governance"])
        # state_summary entry handle
        self.assertIn("state_summary", data)
        self.assertIn("state_view_ref", data["state_summary"])
        self.assertEqual(data["state_summary"]["state_view_ref"]["session_id"], session_id)
        self.assertEqual(data["state_summary"]["state_view_ref"]["view_type"], "session_state_view")
        # schema_version
        self.assertEqual(data["schema_version"], "analysis_session.v1")
        # timestamps
        self.assertIn("created_at", data)
        self.assertIn("updated_at", data)
        # legacy flat fields must NOT appear at top level
        self.assertNotIn("status", data)
        self.assertNotIn("constraints", data)

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
        """GET /sessions?status=open should filter; returned items have canonical shape."""
        self.client.post("/sessions", json={"goal": "Status filter test"})
        resp = self.client.get("/sessions?status=open")
        self.assertEqual(resp.status_code, 200)
        for s in resp.json():
            self.assertEqual(s["lifecycle"]["status"], "open")
            self.assertIn("goal", s)
            self.assertIn("question", s["goal"])
            self.assertIn("governance", s)
            self.assertIn("state_summary", s)
            self.assertEqual(s["schema_version"], "analysis_session.v1")
            # legacy flat fields must not appear
            self.assertNotIn("status", s)
            self.assertNotIn("constraints", s)

    def test_get_session_runtime_status_idle(self) -> None:
        """Newly created session with no work should return idle runtime status."""
        create_resp = self.client.post("/sessions", json={"goal": "Runtime status test"})
        session_id = create_resp.json()["session_id"]
        resp = self.client.get(f"/sessions/{session_id}/runtime-status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["session_id"], session_id)
        self.assertEqual(data["overall_status"], "idle")
        self.assertIsNone(data["last_successful_stage"])
        self.assertEqual(data["blocked_reason"], "none")
        self.assertEqual(data["schema_version"], "session_runtime_status.v1")
        self.assertIn("backlog_summary", data)
        summary = data["backlog_summary"]
        self.assertEqual(summary["queued_artifacts"], 0)
        self.assertEqual(summary["queued_propositions"], 0)
        self.assertEqual(summary["backpressured_propositions"], 0)
        self.assertEqual(summary["failed_items"], 0)
        self.assertIn("updated_at", data)

    def test_get_session_runtime_status_not_found(self) -> None:
        """GET /sessions/{id}/runtime-status with unknown ID should 404."""
        resp = self.client.get("/sessions/sess_nonexistent/runtime-status")
        self.assertEqual(resp.status_code, 404)

    def test_session_debug_endpoint_returns_summary(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and explain evidence upgrades."},
        ).json()["session_id"]

        entity_id = self.client.post(
            "/semantic/entities",
            json={
                "name": "session_debug_entity",
                "display_name": "Session Debug Entity",
                "keys": ["session_id"],
            },
        ).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time_debug_metric",
                "display_name": "Watch Time Debug",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "entity_id": entity_id,
            },
        ).json()["metric_id"]
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

    def test_evidence_graph_include_debug_attaches_debug_payload(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop with debug payload."},
        ).json()["session_id"]

        entity_id = self.client.post(
            "/semantic/entities",
            json={
                "name": "session_include_debug_entity",
                "display_name": "Session Include Debug Entity",
                "keys": ["session_id"],
            },
        ).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time_include_debug_metric",
                "display_name": "Watch Time Include Debug",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "entity_id": entity_id,
            },
        ).json()["metric_id"]
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

        entity_id = self.client.post(
            "/semantic/entities",
            json={
                "name": "session_filtered_debug_entity",
                "display_name": "Session Filtered Debug Entity",
                "keys": ["session_id"],
            },
        ).json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_id = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time_filtered_debug_metric",
                "display_name": "Watch Time Filtered Debug",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "entity_id": entity_id,
            },
        ).json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json=_metric_query_payload("watch_time_filtered_debug_metric"),
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        resp = self.client.get(
            f"/sessions/{session_id}/evidence?edge_types=supports&include_debug=true"
        )
        self.assertEqual(resp.status_code, 200)
        graph = resp.json()
        self.assertTrue(all(edge["edge_type"] == "supports" for edge in graph["edges"]))
        self.assertEqual(graph["debug"]["relation_discovery"]["relations_emitted"], 0)


if __name__ == "__main__":
    unittest.main()
