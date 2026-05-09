from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from marivo.storage.repositories import SessionRepository
from marivo.storage.sqlite_metadata import SQLiteMetadataStore


class RepositorySeamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteMetadataStore(Path(self.temp_dir.name) / "meta.sqlite")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_session_repository_get(self) -> None:
        self.store.execute(
            "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, status) VALUES (?, ?, ?, ?, ?)",
            ["sess_test", "repo session", "{}", "{}", "open"],
        )
        repository = SessionRepository(self.store)
        session = repository.get("sess_test")
        self.assertIsNotNone(session)
        self.assertEqual(session["goal"], "repo session")
