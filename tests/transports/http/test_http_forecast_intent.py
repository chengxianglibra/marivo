from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from marivo.main import create_app


class ForecastIntentEndpointTests(unittest.TestCase):
    """Thin HTTP boundary tests for /sessions/{id}/intents/forecast."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "forecast_http.duckdb"
        cls.client = TestClient(create_app(db_path), headers={"X-Marivo-User": "test_user"})

        response = cls.client.post("/sessions", json={"goal": "forecast HTTP test"})
        assert response.status_code == 200, response.text
        cls.session_id = response.json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_missing_session_returns_404(self) -> None:
        response = self.client.post(
            "/sessions/sess_doesnotexist/intents/forecast",
            json={"source_artifact_id": "art_timeseries", "horizon": 7},
        )

        self.assertEqual(response.status_code, 404)

    def test_missing_source_artifact_id_returns_422(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={"horizon": 7},
        )

        self.assertEqual(response.status_code, 422)

    def test_invalid_horizon_returns_422(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={"source_artifact_id": "art_timeseries", "horizon": 0},
        )

        self.assertEqual(response.status_code, 422)

    def test_nonexistent_source_artifact_returns_422(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={"source_artifact_id": "art_timeseries", "horizon": 7},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("ARTIFACT_NOT_FOUND", response.json()["detail"])
