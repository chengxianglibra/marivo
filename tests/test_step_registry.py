from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.main import create_app
from app.planning import VALID_STEP_TYPES
from fastapi.testclient import TestClient
from tests.shared_fixtures import get_seeded_duckdb_path


class StepRegistryWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "step_registry.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_service_exposes_supported_step_types(self) -> None:
        service = self.client.app.state.service
        supported = service.step_registry.supported_step_types()

        self.assertEqual(set(supported), VALID_STEP_TYPES)
        self.assertIn("compare_watch_time", supported)
        self.assertIn("sample_rows", supported)

    def test_run_step_rejects_unknown_step_type(self) -> None:
        session_id = self.client.post("/sessions", json={"goal": "Unknown step guard"}).json()["session_id"]

        response = self.client.post(f"/sessions/{session_id}/steps/not_a_real_step")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported step type", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
