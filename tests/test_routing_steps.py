from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


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

    def test_individual_steps_with_query_router(self) -> None:
        """Each generic step type should work when QueryRouter is present."""
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test individual steps with router."},
        ).json()["session_id"]

        # profile_table
        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200, "Step profile_table failed")
        self.assertEqual(resp.json()["step_type"], "profile_table")

        # sample_rows
        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events", "limit": 5},
        )
        self.assertEqual(resp.status_code, 200, "Step sample_rows failed")
        self.assertEqual(resp.json()["step_type"], "sample_rows")

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
        result = svc.run_step(
            session["session_id"],
            "profile_table",
            {"table_name": "analytics.watch_events"},
        )
        self.assertEqual(result["step_type"], "profile_table")
        self.assertIn("summary", result)

    def test_resolve_engine_returns_tuple(self) -> None:
        """_resolve_engine should return (engine, engine_type, qualified_names) tuple."""
        service = self.client.app.state.service
        result = service._resolve_engine(["watch_events"])
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        engine, engine_type, qualified = result
        self.assertIsInstance(engine_type, str)
        # Default fallback should be duckdb
        self.assertEqual(engine_type, "duckdb")
        self.assertIsInstance(qualified, dict)

    def test_provenance_uses_resolved_engine_type(self) -> None:
        """Step provenance should reflect the resolved engine type."""
        session = self.client.post(
            "/sessions",
            json={"goal": "Test provenance engine type."},
        ).json()
        session_id = session["session_id"]
        # Run a step and check evidence provenance
        self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
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
            "/sessions",
            json={"goal": "Test profile_table."},
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
            "/sessions",
            json={"goal": "Test missing param."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_sample_rows(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test sample_rows."},
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
            "/sessions",
            json={"goal": "Test default limit."},
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
            "/sessions",
            json={"goal": "Test missing param."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
