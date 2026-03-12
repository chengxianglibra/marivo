from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "semantic_runtime.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        entity = cls.client.post(
            "/semantic/entities",
            json={"name": "user", "display_name": "User", "description": "A platform user", "keys": ["user_id"]},
        ).json()
        cls.client.post(f"/semantic/entities/{entity['entity_id']}/publish")

        metric = cls.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "description": "Average play duration per session",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
            },
        ).json()
        cls.client.post(f"/semantic/metrics/{metric['metric_id']}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_semantic_resolver_resolves_published_metric(self) -> None:
        service = self.client.app.state.service

        resolved = service.semantic_resolver.resolve_metric("watch_time")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.definition_sql, "avg(play_duration_seconds)")
        self.assertEqual(
            resolved.dimensions,
            ["platform", "app_version", "network_type", "content_type"],
        )
        self.assertEqual(resolved.metadata["display_name"], "Watch Time")

    def test_planner_context_provider_includes_session_details(self) -> None:
        service = self.client.app.state.service
        session = service.create_session("Semantic runtime test", {}, {}, {})

        context = service.planner_context_provider.build_planner_context(session["session_id"])

        self.assertIn("session", context)
        self.assertEqual(context["session"]["session_id"], session["session_id"])
        self.assertEqual(context["session"]["goal"], "Semantic runtime test")
        self.assertTrue(any(metric["name"] == "watch_time" for metric in context["metrics"]))


if __name__ == "__main__":
    unittest.main()
