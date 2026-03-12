from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.session import SessionManager
from app.storage.sqlite_metadata import SQLiteMetadataStore


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        metadata_path = Path(self.temp_dir.name) / "sessions.meta.sqlite"
        self.metadata = SQLiteMetadataStore(metadata_path)
        self.metadata.initialize()
        self.manager = SessionManager(self.metadata)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_create_and_get_session(self) -> None:
        created = self.manager.create_session(
            "Investigate watch time regression",
            {"region": "all"},
            {"max_latency_sec": 120},
            {"aggregate_only": True},
        )

        loaded = self.manager.get_session(created["session_id"])

        self.assertEqual(loaded["session_id"], created["session_id"])
        self.assertEqual(loaded["goal"], "Investigate watch time regression")
        self.assertEqual(loaded["constraints"], {"region": "all"})
        self.assertEqual(loaded["budget"], {"max_latency_sec": 120})
        self.assertEqual(loaded["policy"], {"aggregate_only": True})

    def test_list_sessions_with_status_filter(self) -> None:
        open_session = self.manager.create_session("Open session", {}, {}, {})
        closed_session = self.manager.create_session("Closed session", {}, {}, {})
        self.metadata.execute(
            "UPDATE sessions SET status = 'closed' WHERE session_id = ?",
            [closed_session["session_id"]],
        )

        open_sessions = self.manager.list_sessions(status="open")
        closed_sessions = self.manager.list_sessions(status="closed")

        self.assertIn(open_session["session_id"], [session["session_id"] for session in open_sessions])
        self.assertNotIn(closed_session["session_id"], [session["session_id"] for session in open_sessions])
        self.assertEqual([session["session_id"] for session in closed_sessions], [closed_session["session_id"]])

    def test_assert_session_exists_raises_for_unknown_session(self) -> None:
        with self.assertRaises(KeyError):
            self.manager.assert_session_exists("sess_missing")


if __name__ == "__main__":
    unittest.main()
