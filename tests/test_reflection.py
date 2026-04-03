"""Tests for M-11 Reflection Context API (Phase 6 stub).

Tests cover:
  - build_reflection_context() function (unit)
  - GET /sessions/{id}/reflection-context endpoint (HTTP)
  - reflection.enabled config gate
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.reflection.context import build_reflection_context
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class ReflectionContextUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "refl.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "refl.duckdb"
        cls.store = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.store.initialize()
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.store, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        session = self.service.create_session("reflection test", {}, {}, {})
        return session["session_id"]

    def test_build_reflection_context_required_keys(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        for key in (
            "session_id",
            "plan_id",
            "tentative_claims",
            "evidence_gaps",
            "available_step_types",
        ):
            self.assertIn(key, ctx, f"Missing key: {key}")
        self.assertEqual(ctx["session_id"], session_id)
        self.assertIsNone(ctx["plan_id"])

    def test_tentative_claims_is_empty_list(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        self.assertEqual(ctx["tentative_claims"], [])

    def test_evidence_gaps_is_empty_list(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        self.assertEqual(ctx["evidence_gaps"], [])

    def test_plan_id_forwarded(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id, plan_id="plan_abc")
        self.assertEqual(ctx["plan_id"], "plan_abc")

    def test_available_step_types_all_present(self) -> None:
        session_id = self._new_session()
        ctx = build_reflection_context(self.store, session_id)
        expected = {
            "metric_query",
            "profile_table",
            "sample_rows",
            "aggregate_query",
            "attribute_change",
        }
        self.assertEqual(set(ctx["available_step_types"]), expected)

    def test_unknown_session_raises(self) -> None:
        with self.assertRaises(KeyError):
            build_reflection_context(self.store, "sess_doesnotexist")


class ReflectionContextHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "http_refl.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        resp = cls.client.post(
            "/sessions",
            json={"goal": "HTTP reflection test", "constraints": {}, "budget": {}, "policy": {}},
        )
        cls.session_id = resp.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_get_reflection_context_returns_200(self) -> None:
        resp = self.client.get(f"/sessions/{self.session_id}/reflection-context")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in (
            "session_id",
            "plan_id",
            "tentative_claims",
            "evidence_gaps",
            "available_step_types",
        ):
            self.assertIn(key, data)

    def test_unknown_session_returns_404(self) -> None:
        resp = self.client.get("/sessions/sess_doesnotexist/reflection-context")
        self.assertEqual(resp.status_code, 404)

    def test_reflection_disabled_returns_404(self) -> None:
        config_dir = tempfile.TemporaryDirectory()
        config_file = Path(config_dir.name) / "factum.yaml"
        config_file.write_text("reflection:\n  enabled: false\n")
        db_path = Path(config_dir.name) / "disabled.duckdb"
        get_seeded_duckdb_path(db_path)
        app = create_app(db_path, config_path=str(config_file))
        client = TestClient(app)
        sess_resp = client.post(
            "/sessions",
            json={"goal": "test", "constraints": {}, "budget": {}, "policy": {}},
        )
        session_id = sess_resp.json()["session_id"]
        resp = client.get(f"/sessions/{session_id}/reflection-context")
        self.assertEqual(resp.status_code, 404)
        config_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
