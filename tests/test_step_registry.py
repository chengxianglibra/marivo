from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from marivo.analysis_core import (
    ATOMIC_INTENT_TYPES,
    COMPOSITE_STEP_TYPES,
    DERIVED_INTENT_TYPES,
    PRIMITIVE_STEP_TYPES,
    SUPPORTED_INTENT_TYPES,
    SUPPORTED_STEP_TYPES,
)
from marivo.analysis_core.step_runners import build_service_step_registry
from marivo.main import create_app
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
        runtime = self.client.app.state.services.runtime
        registry = build_service_step_registry(runtime)
        supported = registry.supported_step_types()

        self.assertEqual(set(supported), set(SUPPORTED_STEP_TYPES))
        self.assertIn("metric_query", supported)
        self.assertIn("sample_rows", supported)
        self.assertTrue(set(PRIMITIVE_STEP_TYPES).issubset(set(supported)))
        self.assertTrue(set(COMPOSITE_STEP_TYPES).issubset(set(supported)))

    def test_intent_taxonomy_contains_expected_types(self) -> None:
        self.assertEqual(
            set(ATOMIC_INTENT_TYPES),
            {"observe", "compare", "decompose", "correlate", "detect", "test", "forecast"},
        )
        self.assertEqual(
            set(DERIVED_INTENT_TYPES),
            {"attribute", "diagnose", "validate"},
        )
        self.assertEqual(len(SUPPORTED_INTENT_TYPES), 10)

    def test_run_step_rejects_unknown_step_type(self) -> None:
        runtime = self.client.app.state.services.runtime
        registry = build_service_step_registry(runtime)
        session_id = self.client.post("/sessions", json={"goal": "Unknown step guard"}).json()[
            "session_id"
        ]

        with self.assertRaises(KeyError):
            registry.run(session_id, "not_a_real_step")


if __name__ == "__main__":
    unittest.main()
