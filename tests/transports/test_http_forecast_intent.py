from __future__ import annotations

import tempfile
import unittest
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.main import create_app
from tests.shared_fixtures import get_named_seeded_duckdb_path


def _metric_ref(name: str) -> str:
    return f"metric.{name}"


# ── Constants ──────────────────────────────────────────────────────────────────

_METRIC = "forecast_dau"
_GRANULARITY = "day"
_SERIES_START = "2026-01-01"
_SERIES_END = "2026-01-15"  # 14 daily buckets


# ── Seeding helpers ───────────────────────────────────────────────────────────


def _seed_forecast_table(db_path: Path) -> None:
    """Copy the shared seeded analytics.forecast_events fixture into place."""
    get_named_seeded_duckdb_path(db_path, "forecast_intent")


def _make_synthetic_series(n: int = 14, start: str = _SERIES_START) -> list[dict]:
    """Return a list of time_series buckets with a linear trend."""
    base = _date.fromisoformat(start)
    series = []
    for i in range(n):
        d = base + timedelta(days=i)
        end_d = d + timedelta(days=1)
        series.append(
            {
                "window": {"start": d.isoformat(), "end": end_d.isoformat()},
                "value": 100.0 + i * 10.0,
            }
        )
    return series


def _inject_observe_artifact(
    runtime: Any,
    session_id: str,
    *,
    series: list[dict] | None = None,
    granularity: str = _GRANULARITY,
    metric: str = _METRIC,
    observation_type: str = "time_series",
) -> tuple[str, str]:
    """Insert a synthetic observe step + artifact; return (step_id, artifact_id)."""
    if series is None:
        series = _make_synthetic_series()
    step_id = f"step_{uuid4().hex[:12]}"
    artifact_content: dict = {
        "schema_version": "1.0",
        "observation_type": observation_type,
        "metric": metric,
        "granularity": granularity,
        "time_scope": {"kind": "range", "start": _SERIES_START, "end": _SERIES_END},
        "series": series,
        "analytical_metadata": {
            "timezone": None,
            "data_complete": None,
        },
    }
    artifact_id = runtime.insert_artifact(
        session_id, step_id, "time_series", f"{metric}_observe_time_series", artifact_content
    )
    runtime.insert_step(
        step_id, session_id, "observe", f"observe {metric}", {"artifact_id": artifact_id}
    )
    return step_id, artifact_id


class ForecastIntentEndpointTests(unittest.TestCase):
    """HTTP-level tests for /sessions/{id}/intents/forecast."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "forecast_http.duckdb"

        analytics = DuckDBAnalyticsEngine(str(db_path))
        analytics.initialize()

        meta_path = db_path.with_suffix(".meta.sqlite")
        metadata = SQLiteMetadataStore(str(meta_path))
        metadata.initialize()

        cls.client = TestClient(
            create_app(db_path=db_path, metadata_store=metadata, analytics_engine=analytics),
            headers={"X-Marivo-User": "test_user"},
        )

        # Create session and run observe intent to get a real time_series artifact
        r = cls.client.post("/sessions", json={"goal": "forecast HTTP test"})
        assert r.status_code == 200, r.text
        cls.session_id = r.json()["session_id"]
        cls.obs_step_id, cls.obs_artifact_id = _inject_observe_artifact(
            cls.client.app.state.services.runtime,
            cls.session_id,
            metric="http_forecast_dau",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _valid_body(self, **overrides: object) -> dict:
        body = {
            "source_artifact_id": self.obs_artifact_id,
            "horizon": 7,
        }
        body.update(overrides)
        return body

    def test_valid_forecast_returns_200(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json=self._valid_body(),
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        body = r.json()
        self.assertIn("artifact_id", body)
        self.assertIn("step_ref", body)
        result = body["result"]["result"]
        self.assertIn("points", result)
        self.assertEqual(len(result["points"]), 7)

    def test_short_horizon_endpoint(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json=self._valid_body(horizon=3),
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        result = r.json()["result"]["result"]
        self.assertEqual(len(result["points"]), 3)

    def test_missing_session_returns_404(self) -> None:
        body = self._valid_body()
        r = self.client.post(
            "/sessions/sess_doesnotexist/intents/forecast",
            json=body,
        )
        self.assertEqual(r.status_code, 404)

    def test_missing_source_ref_returns_422(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json={"horizon": 7},
        )
        self.assertEqual(r.status_code, 422)

    def test_invalid_horizon_returns_422(self) -> None:
        body = self._valid_body()
        body["horizon"] = 0  # violates ge=1
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json=body,
        )
        self.assertEqual(r.status_code, 422)
