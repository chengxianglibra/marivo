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

    def test_list_sessions_returns_paged_envelope(self) -> None:
        """GET /sessions should return the paged list contract."""
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn("items", payload)
        self.assertIn("next_page_token", payload)
        self.assertIsInstance(payload["items"], list)

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
        self.assertEqual(data["scope"]["constraints"], {})
        # lifecycle carries status
        self.assertIn("lifecycle", data)
        self.assertEqual(data["lifecycle"]["status"], "open")
        self.assertIsNone(data["lifecycle"]["terminal_reason"])
        self.assertIsNone(data["lifecycle"]["ended_at"])
        self.assertIsNone(data["lifecycle"]["rollover_from_session_id"])
        self.assertEqual(data["execution_identity"], {})
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

    def test_create_session_rejects_step_level_execution_constraints(self) -> None:
        resp = self.client.post(
            "/sessions",
            json={
                "goal": "Reject session execution scope",
                "constraints": {"region": "us"},
                "raw_filter": "device = 'mobile'",
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_create_session_with_execution_identity_round_trips(self) -> None:
        create_resp = self.client.post(
            "/sessions",
            json={
                "goal": "Execution identity session",
                "execution_identity": {
                    "session_user": "alice",
                    "actor_ref": "agent.alice",
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        session_id = create_resp.json()["session_id"]

        detail = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            detail.json()["execution_identity"],
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

        listed = self.client.get(f"/sessions?session_id={session_id}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["items"]), 1)
        self.assertEqual(
            listed.json()["items"][0]["execution_identity"],
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

    def test_create_session_trims_execution_identity_fields(self) -> None:
        create_resp = self.client.post(
            "/sessions",
            json={
                "goal": "Trim execution identity session",
                "execution_identity": {
                    "session_user": " alice ",
                    "actor_ref": " agent.alice ",
                },
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        self.assertEqual(
            create_resp.json()["execution_identity"],
            {"session_user": "alice", "actor_ref": "agent.alice"},
        )

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
        ids = [s["session_id"] for s in resp.json()["items"]]
        self.assertIn(session_id, ids)

    def test_list_sessions_filter_by_status(self) -> None:
        """GET /sessions?status=open should filter; returned items have canonical shape."""
        self.client.post("/sessions", json={"goal": "Status filter test"})
        resp = self.client.get("/sessions?status=open")
        self.assertEqual(resp.status_code, 200)
        for s in resp.json()["items"]:
            self.assertEqual(s["lifecycle"]["status"], "open")
            self.assertIn("goal", s)
            self.assertIn("question", s["goal"])
            self.assertIn("scope", s)
            self.assertIn("execution_identity", s)
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

    def test_list_sessions_supports_session_id_filter(self) -> None:
        create_resp = self.client.post("/sessions", json={"goal": "Filter by id"})
        session_id = create_resp.json()["session_id"]

        resp = self.client.get(f"/sessions?session_id={session_id[:10]}")

        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual([item["session_id"] for item in items], [session_id])

    def test_list_sessions_supports_limit_and_page_token(self) -> None:
        self.client.post("/sessions", json={"goal": "Page one"})
        self.client.post("/sessions", json={"goal": "Page two"})

        first_page = self.client.get("/sessions?limit=1")

        self.assertEqual(first_page.status_code, 200)
        first_payload = first_page.json()
        self.assertEqual(len(first_payload["items"]), 1)
        self.assertIsNotNone(first_payload["next_page_token"])

        second_page = self.client.get(
            f"/sessions?limit=1&page_token={first_payload['next_page_token']}"
        )
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.json()["items"]), 1)


class SessionCloseTests(unittest.TestCase):
    """Phase 8.1: POST /sessions/{id}/close contract tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "close_test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_session(self, goal: str = "close test session") -> str:
        r = self.client.post("/sessions", json={"goal": goal})
        self.assertEqual(r.status_code, 200)
        return r.json()["session_id"]

    def test_close_session_success(self) -> None:
        """POST /sessions/{id}/close transitions status to 'closed'."""
        session_id = self._create_session()
        r = self.client.post(
            f"/sessions/{session_id}/terminate", json={"terminal_reason": "test_done"}
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["session_id"], session_id)
        self.assertEqual(data["lifecycle"]["status"], "closed")
        self.assertEqual(data["lifecycle"]["terminal_reason"], "test_done")
        self.assertIsNotNone(data["lifecycle"]["ended_at"])

    def test_close_session_default_reason(self) -> None:
        """close without explicit reason uses default 'user_closed'."""
        session_id = self._create_session()
        r = self.client.post(f"/sessions/{session_id}/terminate", json={})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["lifecycle"]["terminal_reason"], "user_closed")

    def test_close_session_unknown_returns_404(self) -> None:
        r = self.client.post(
            "/sessions/sess_nonexistent/terminate", json={"terminal_reason": "gone"}
        )
        self.assertEqual(r.status_code, 404)

    def test_close_session_already_closed_returns_409(self) -> None:
        """Closing an already-closed session returns 409 Conflict."""
        session_id = self._create_session()
        self.client.post(f"/sessions/{session_id}/terminate", json={})
        r = self.client.post(f"/sessions/{session_id}/terminate", json={})
        self.assertEqual(r.status_code, 409)

    def test_get_session_reflects_closed_status(self) -> None:
        """GET /sessions/{id} returns closed lifecycle after close."""
        session_id = self._create_session()
        self.client.post(f"/sessions/{session_id}/terminate", json={"terminal_reason": "verified"})
        r = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["lifecycle"]["status"], "closed")

    def test_list_sessions_filter_by_closed(self) -> None:
        """GET /sessions?status=closed returns only closed sessions."""
        session_id = self._create_session("to be closed")
        self.client.post(f"/sessions/{session_id}/terminate", json={})
        r = self.client.get("/sessions?status=closed")
        self.assertEqual(r.status_code, 200)
        ids = [s["session_id"] for s in r.json()["items"]]
        self.assertIn(session_id, ids)
        for s in r.json()["items"]:
            self.assertEqual(s["lifecycle"]["status"], "closed")


if __name__ == "__main__":
    unittest.main()
