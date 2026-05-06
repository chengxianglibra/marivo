from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.identity import current_user
from app.session import SessionManager
from app.storage.sqlite_metadata import SQLiteMetadataStore


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        metadata_path = Path(self.temp_dir.name) / "sessions.meta.sqlite"
        self.metadata = SQLiteMetadataStore(metadata_path)
        self.metadata.initialize()
        self.manager = SessionManager(self.metadata)
        self._token = current_user.set("test_user")

    def tearDown(self) -> None:
        current_user.reset(self._token)
        self.temp_dir.cleanup()

    def test_create_and_get_session(self) -> None:
        created = self.manager.create_session(
            "Investigate watch time regression",
            {"region": "all"},
            {"max_latency_sec": 120},
        )

        loaded = self.manager.get_session(created["session_id"])

        self.assertEqual(loaded["session_id"], created["session_id"])
        self.assertEqual(loaded["goal"]["question"], "Investigate watch time regression")
        self.assertEqual(loaded["scope"]["constraints"], {"region": "all"})
        self.assertEqual(loaded["owner_user"], "test_user")
        self.assertEqual(loaded["lifecycle"]["status"], "open")
        self.assertEqual(loaded["schema_version"], "analysis_session.v1")

    def test_create_session_stores_owner_user(self) -> None:
        token = current_user.set("alice")
        try:
            created = self.manager.create_session(
                "Investigate auth user",
                {},
                {"max_latency_sec": 120},
            )
            loaded = self.manager.get_session(created["session_id"])
            self.assertEqual(loaded["owner_user"], "alice")
        finally:
            current_user.reset(token)

    def test_create_session_rejects_nil_user(self) -> None:
        token = current_user.set(None)
        old_env = os.environ.pop("MARIVO_DEFAULT_USER", None)
        try:
            with self.assertRaisesRegex(ValueError, "user_required"):
                self.manager.create_session("No user session")
        finally:
            if old_env is not None:
                os.environ["MARIVO_DEFAULT_USER"] = old_env
            current_user.reset(token)

    def test_list_sessions_with_status_filter(self) -> None:
        open_session = self.manager.create_session("Open session", {}, {})
        closed_session = self.manager.create_session("Closed session", {}, {})
        self.metadata.execute(
            "UPDATE sessions SET status = 'closed' WHERE session_id = ?",
            [closed_session["session_id"]],
        )

        open_sessions = self.manager.list_sessions(status="open")
        closed_sessions = self.manager.list_sessions(status="closed")

        self.assertIn(
            open_session["session_id"],
            [session["session_id"] for session in open_sessions["items"]],
        )
        self.assertNotIn(
            closed_session["session_id"],
            [session["session_id"] for session in open_sessions["items"]],
        )
        self.assertEqual(
            [session["session_id"] for session in closed_sessions["items"]],
            [closed_session["session_id"]],
        )

    def test_assert_session_exists_raises_for_unknown_session(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.assert_session_exists("sess_missing")

    def test_runtime_status_idle_empty_session(self) -> None:
        session = self.manager.create_session("Check idle", {}, {})
        status = self.manager.get_session_runtime_status(session["session_id"])

        self.assertEqual(status["session_id"], session["session_id"])
        self.assertEqual(status["overall_status"], "idle")
        self.assertIsNone(status["last_successful_stage"])
        self.assertEqual(status["blocked_reason"], "none")
        self.assertEqual(status["schema_version"], "session_runtime_status.v1")
        summary = status["backlog_summary"]
        self.assertEqual(summary["queued_artifacts"], 0)
        self.assertEqual(summary["queued_propositions"], 0)
        self.assertIsInstance(status["updated_at"], str)
        self.assertGreater(len(status["updated_at"]), 0)

    def test_runtime_status_running_after_artifact_commit(self) -> None:
        session = self.manager.create_session("Check running", {}, {})
        sid = session["session_id"]
        self.metadata.execute(
            "INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ["art_test_001", sid, "step_test_001", "compare_artifact", "cmp", "{}"],
        )

        status = self.manager.get_session_runtime_status(sid)
        self.assertEqual(status["overall_status"], "running")
        self.assertEqual(status["last_successful_stage"], "artifact_commit")
        self.assertEqual(status["backlog_summary"]["queued_artifacts"], 1)

    def test_runtime_status_idle_for_observation_artifact_with_no_findings(self) -> None:
        session = self.manager.create_session("Check D4", {}, {})
        sid = session["session_id"]
        self.metadata.execute(
            "INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ["art_obs_001", sid, "step_obs_001", "observation", "obs", "{}"],
        )

        status = self.manager.get_session_runtime_status(sid)
        self.assertEqual(status["last_successful_stage"], "artifact_commit")
        self.assertEqual(status["backlog_summary"]["queued_artifacts"], 0)
        self.assertEqual(status["overall_status"], "idle")

    def test_runtime_status_running_when_findings_but_no_propositions(self) -> None:
        session = self.manager.create_session("Check seeding", {}, {})
        sid = session["session_id"]
        self.metadata.execute(
            "INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            ["art_cmp_002", sid, "step_cmp_002", "compare_artifact", "cmp2", "{}"],
        )
        self.metadata.execute(
            """INSERT INTO findings
               (finding_id, session_id, artifact_id, step_ref_json, finding_type,
                canonical_item_key, subject_json, quality_json, provenance_json, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                "find_001",
                sid,
                "art_cmp_002",
                '{"step_id":"step_cmp_002","session_id":"' + sid + '"}',
                "delta",
                "result",
                '{"metric":"m"}',
                "{}",
                "{}",
                "{}",
            ],
        )

        status = self.manager.get_session_runtime_status(sid)
        self.assertEqual(status["overall_status"], "running")
        self.assertEqual(status["last_successful_stage"], "finding_extraction")
        self.assertEqual(status["backlog_summary"]["queued_artifacts"], 0)
        self.assertEqual(status["backlog_summary"]["queued_propositions"], 0)

    def test_runtime_status_raises_for_unknown_session(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.get_session_runtime_status("sess_missing")

    def test_list_sessions_canonical_shape(self) -> None:
        s = self.manager.create_session("Shape check", {}, {})
        payload = self.manager.list_sessions()
        match = next(x for x in payload["items"] if x["session_id"] == s["session_id"])
        self.assertIn("goal", match)
        self.assertIn("question", match["goal"])
        self.assertIn("scope", match)
        self.assertIn("owner_user", match)
        self.assertIn("lifecycle", match)
        self.assertIn("state_summary", match)
        self.assertEqual(match["schema_version"], "analysis_session.v1")
        self.assertNotIn("status", match)
        self.assertNotIn("constraints", match)
        self.assertNotIn("execution_identity", match)

    def test_list_sessions_supports_session_id_prefix_filter(self) -> None:
        matched = self.manager.create_session("Prefix match", {}, {})
        self.manager.create_session("Other session", {}, {})

        payload = self.manager.list_sessions(session_id=matched["session_id"][:8])

        self.assertEqual([item["session_id"] for item in payload["items"]], [matched["session_id"]])
        self.assertIsNone(payload["next_page_token"])

    def test_list_sessions_paginates_with_offset_tokens(self) -> None:
        first = self.manager.create_session("First", {}, {})
        second = self.manager.create_session("Second", {}, {})

        first_page = self.manager.list_sessions(limit=1)

        self.assertEqual(len(first_page["items"]), 1)
        self.assertIsNotNone(first_page["next_page_token"])

        second_page = self.manager.list_sessions(limit=1, page_token=first_page["next_page_token"])

        ids = [page["items"][0]["session_id"] for page in (first_page, second_page)]
        self.assertEqual(set(ids), {first["session_id"], second["session_id"]})

    def test_list_sessions_rejects_invalid_page_token(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.list_sessions(page_token="bad-token")

    def test_get_session_on_row_without_owner_user_gracefully_degrades(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_sessions.meta.sqlite"
        con = sqlite3.connect(legacy_path)
        try:
            con.execute(
                """
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    budget_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    raw_filter TEXT,
                    terminal_reason TEXT,
                    ended_at TEXT,
                    rollover_from_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                INSERT INTO sessions (
                    session_id, goal, constraints_json, budget_json,
                    status, raw_filter, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "sess_legacy",
                    "Legacy read",
                    json.dumps({"region": "all"}),
                    json.dumps({"max_latency_sec": 120}),
                    "open",
                    None,
                    "2026-04-24T00:00:00Z",
                    "2026-04-24T00:00:00Z",
                ),
            )
            con.commit()
        finally:
            con.close()

        token = current_user.set(None)
        old_env = os.environ.pop("MARIVO_DEFAULT_USER", None)
        try:
            legacy_manager = SessionManager(SQLiteMetadataStore(legacy_path))
            session = legacy_manager.get_session("sess_legacy")

            self.assertEqual(session["session_id"], "sess_legacy")
            self.assertEqual(session["goal"]["question"], "Legacy read")
            self.assertEqual(session["scope"]["constraints"], {"region": "all"})
            self.assertIsNone(session["owner_user"])
            self.assertEqual(session["lifecycle"]["status"], "open")
        finally:
            if old_env is not None:
                os.environ["MARIVO_DEFAULT_USER"] = old_env
            current_user.reset(token)


if __name__ == "__main__":
    unittest.main()
