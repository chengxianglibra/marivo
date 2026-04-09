"""Tests for the `forecast` atomic intent runner (Phase 3b-6).

Covers:
  - run_forecast_intent: level profile carry-forward produces correct bucket count
  - run_forecast_intent: trend (OLS) profile with trend extrapolation
  - run_forecast_intent: auto profile selects trend when history is sufficient
  - run_forecast_intent: auto profile falls back to level on minimal history
  - run_forecast_intent: artifact schema required fields present
  - run_forecast_intent: step is committed and retrievable via _resolve_artifact_with_id
  - run_forecast_intent: history_summary fields correct
  - run_forecast_intent: bucket_index sequential starting at 1
  - run_forecast_intent: long horizon → needs_attention + long_horizon_warning
  - run_forecast_intent: forecastable status when horizon is moderate
  - run_forecast_intent: interval_level accepted as input parameter
  - run_forecast_intent: insufficient history raises ValueError
  - run_forecast_intent: cross-session ref raises ValueError
  - run_forecast_intent: non-observe step_type raises ValueError
  - run_forecast_intent: wrong observation_type raises ValueError
  - run_forecast_intent: invalid horizon raises ValueError
  - run_forecast_intent: invalid profile raises ValueError
  - run_forecast_intent: seasonal profile raises UNSUPPORTED_OPERATION
  - run_forecast_intent: artifact_id mismatch raises ValueError
  - run_forecast_intent: step not found raises STEP_NOT_FOUND
  - HTTP endpoint: valid forecast returns 200 with forecast buckets
  - HTTP endpoint: naive profile endpoint (level)
  - HTTP endpoint: missing session returns 404
  - HTTP endpoint: missing source_ref returns 422
  - HTTP endpoint: invalid horizon returns 422
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient

from app.main import create_app
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import (
    ensure_published_typed_metric,
    ensure_published_typed_metric_binding,
)

# ── Constants ──────────────────────────────────────────────────────────────────

_METRIC = "forecast_dau"
_GRANULARITY = "day"
_SERIES_START = "2026-01-01"
_SERIES_END = "2026-01-15"  # 14 daily buckets


# ── Seeding helpers ───────────────────────────────────────────────────────────


def _seed_forecast_table(db_path: Path) -> None:
    """Create a simple daily time-series table with a linear upward trend.

    14 days (Jan 1–14 2026): value = 100 + i * 10  (100, 110, 120, …, 230)
    """
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS analytics")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics.forecast_events (
                event_date DATE   NOT NULL,
                value      DOUBLE NOT NULL
            )
            """
        )
        base = _date.fromisoformat(_SERIES_START)
        rows = []
        for i in range(14):
            d = base + timedelta(days=i)
            v = 100.0 + i * 10.0
            rows.append((d.isoformat(), v))
        con.executemany("INSERT INTO analytics.forecast_events VALUES (?, ?)", rows)
    finally:
        con.close()


def _seed_metadata(
    meta: SQLiteMetadataStore, *, suffix: str = "01", metric_name: str = _METRIC
) -> str:
    """Insert minimal metadata records so observe can resolve metric → table."""
    now = datetime.now(UTC).isoformat()
    src_id = f"src_forecast{suffix}"
    obj_id = f"obj_forecast{suffix}"
    met_id = f"met_forecast{suffix}"
    map_id = f"map_forecast{suffix}"

    meta.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, display_name, connection_json, capabilities_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [src_id, "duckdb", "Forecast Test Source", "{}", "{}", now, now],
    )
    meta.execute(
        "INSERT OR IGNORE INTO source_objects "
        "(object_id, source_id, object_type, native_name, fqn, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [obj_id, src_id, "table", "forecast_events", "analytics.forecast_events", now, now],
    )
    ensure_published_typed_metric(
        meta,
        metric_name=metric_name,
        display_name=metric_name,
        grain="day",
        dimensions=["event_date"],
        definition_sql="SUM(value)",
        measure_type="sum",
    )
    ensure_published_typed_metric_binding(
        meta,
        metric_name=metric_name,
        carrier_locator="analytics.forecast_events",
        source_object_ref=obj_id,
    )
    return metric_name


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
    svc: SemanticLayerService,
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
    step_id = svc._new_step_id()
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
    artifact_id = svc._insert_artifact(
        session_id, step_id, "time_series", f"{metric}_observe_time_series", artifact_content
    )
    svc._insert_step(
        step_id, session_id, "observe", f"observe {metric}", {"artifact_id": artifact_id}
    )
    return step_id, artifact_id


# ── Direct-service tests ──────────────────────────────────────────────────────


class ForecastRunnerServiceTests(unittest.TestCase):
    """Tests that call run_forecast_intent through SemanticLayerService directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "forecast_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "forecast_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        r = self.service.create_session("forecast test session", {}, {}, {})
        return r["session_id"]

    def _run_forecast(
        self,
        session_id: str,
        step_id: str,
        artifact_id: str,
        *,
        horizon: int = 7,
        profile: str = "trend",
        observation_type: str = "time_series",
        interval_level: float | None = None,
    ) -> dict:
        body: dict = {
            "source_ref": {
                "step_type": "observe",
                "session_id": session_id,
                "step_id": step_id,
                "artifact_id": artifact_id,
                "observation_type": observation_type,
            },
            "horizon": horizon,
            "profile": profile,
        }
        if interval_level is not None:
            body["interval_level"] = interval_level
        return self.service.run_intent(session_id, "forecast", body)

    # ── Success paths ──────────────────────────────────────────────────────────

    def test_level_profile_valid(self) -> None:
        """level: carries last value forward for all horizon buckets."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=3, profile="level")

        self.assertEqual(result["intent_type"], "forecast")
        self.assertEqual(result["observation_type"], "forecast_series")
        buckets = result["forecast"]
        self.assertEqual(len(buckets), 3)
        # Last observed value is 100 + 13 * 10 = 230
        for bucket in buckets:
            self.assertAlmostEqual(bucket["point_forecast"], 230.0)
            self.assertIsNone(bucket["prediction_interval"])

    def test_trend_profile_produces_rising_forecast(self) -> None:
        """trend (OLS): forecast values should increase along the fitted trend."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=7, profile="trend")

        self.assertEqual(result["observation_type"], "forecast_series")
        buckets = result["forecast"]
        self.assertEqual(len(buckets), 7)
        # Trend is +10/day; forecast should continue rising
        for i in range(1, len(buckets)):
            self.assertGreater(
                buckets[i]["point_forecast"],
                buckets[i - 1]["point_forecast"],
                msg=f"Expected rising forecast at bucket {i}",
            )

    def test_auto_profile_selects_trend_with_sufficient_history(self) -> None:
        """auto with 14 points selects trend (OLS) and returns rising forecast."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=3, profile="auto")

        self.assertEqual(result["observation_type"], "forecast_series")
        # auto resolved to trend; analytical_metadata should reflect it
        self.assertEqual(result["analytical_metadata"]["trend_assumption"], "included")
        self.assertEqual(len(result["forecast"]), 3)

    def test_auto_profile_falls_back_to_level_on_minimal_history(self) -> None:
        """auto with 1 point falls back to level (carry-forward)."""
        sid = self._make_session()
        series = _make_synthetic_series(n=1)
        step_id, artifact_id = _inject_observe_artifact(self.service, sid, series=series)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=2, profile="auto")

        self.assertEqual(result["analytical_metadata"]["trend_assumption"], "none")
        self.assertEqual(len(result["forecast"]), 2)
        for bucket in result["forecast"]:
            self.assertAlmostEqual(bucket["point_forecast"], 100.0)

    def test_artifact_schema_required_fields(self) -> None:
        """Committed artifact must contain all required top-level keys."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=3)

        required_keys = {
            "observation_type",
            "artifact_schema_version",
            "derivation_version",
            "metric",
            "source_ref",
            "source_granularity",
            "profile",
            "interval_level",
            "forecastability",
            "history_summary",
            "forecast",
            "source_lineage",
            "analytical_metadata",
            "execution_metadata",
            "artifact_id",
            "step_ref",
        }
        for key in required_keys:
            self.assertIn(key, result, msg=f"Missing key: {key}")

    def test_execution_metadata_shape(self) -> None:
        """execution_metadata must contain exactly engine/executed_at/model_family."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id)

        em = result["execution_metadata"]
        self.assertIn("engine", em)
        self.assertIn("executed_at", em)
        self.assertIn("model_family", em)
        self.assertNotIn("query_hash", em)

    def test_step_committed_and_retrievable(self) -> None:
        """After run_forecast, the step artifact is resolvable from metadata store."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id)

        forecast_step_id = result["step_ref"]["step_id"]
        resolved = self.service._resolve_artifact_with_id(sid, forecast_step_id)
        self.assertIsNotNone(resolved)
        resolved_aid, content = resolved
        self.assertEqual(resolved_aid, result["artifact_id"])
        self.assertEqual(content["observation_type"], "forecast_series")

    def test_history_summary_fields(self) -> None:
        """history_summary must report usable_points and last_observed_window."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id)

        hs = result["history_summary"]
        self.assertEqual(hs["observed_points"], 14)
        self.assertEqual(hs["usable_points"], 14)
        self.assertEqual(hs["dropped_points"], 0)
        self.assertIn("start", hs["last_observed_window"])
        self.assertIn("end", hs["last_observed_window"])

    def test_bucket_index_sequential(self) -> None:
        """forecast buckets must have sequential bucket_index starting at 1."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=5)

        for i, bucket in enumerate(result["forecast"], start=1):
            self.assertEqual(bucket["bucket_index"], i)

    def test_long_horizon_needs_attention(self) -> None:
        """horizon > 2 × usable_points → needs_attention + long_horizon_warning."""
        sid = self._make_session()
        # Only 3 usable points; horizon=7 triggers warning (7 > 3*2=6)
        series = _make_synthetic_series(n=3)
        step_id, artifact_id = _inject_observe_artifact(self.service, sid, series=series)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=7)

        fc = result["forecastability"]
        self.assertEqual(fc["status"], "needs_attention")
        codes = [i["code"] for i in fc["issues"]]
        self.assertIn("long_horizon_warning", codes)

    def test_forecastable_when_horizon_ok(self) -> None:
        """With sufficient history and moderate horizon, status = forecastable."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(sid, step_id, artifact_id, horizon=3)

        self.assertEqual(result["forecastability"]["status"], "forecastable")

    def test_interval_level_accepted_as_input(self) -> None:
        """interval_level passed as input is reflected in the artifact."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        result = self._run_forecast(
            sid, step_id, artifact_id, horizon=3, profile="trend", interval_level=0.90
        )
        self.assertAlmostEqual(result["interval_level"], 0.90)
        # OLS with std > 0 should produce a non-null interval
        for bucket in result["forecast"]:
            if bucket["prediction_interval"] is not None:
                self.assertAlmostEqual(bucket["prediction_interval"]["level"], 0.90)

    # ── Failure paths ──────────────────────────────────────────────────────────

    def test_insufficient_history_raises(self) -> None:
        """Fewer usable points than profile minimum raises ValueError."""
        sid = self._make_session()
        # Only 1 usable point; trend requires 3
        series = [_make_synthetic_series(n=3)[0]]  # single bucket
        step_id, artifact_id = _inject_observe_artifact(self.service, sid, series=series)
        with self.assertRaises(ValueError) as ctx:
            self._run_forecast(sid, step_id, artifact_id, horizon=3, profile="trend")
        self.assertIn("INSUFFICIENT_HISTORY", str(ctx.exception))

    def test_cross_session_ref_raises(self) -> None:
        """source_ref.session_id != current session_id raises ValueError."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "forecast",
                {
                    "source_ref": {
                        "step_type": "observe",
                        "session_id": "other_session_id",
                        "step_id": step_id,
                        "artifact_id": artifact_id,
                        "observation_type": "time_series",
                    },
                    "horizon": 3,
                    "profile": "level",
                },
            )
        self.assertIn("CROSS_SESSION_NOT_ALLOWED", str(ctx.exception))

    def test_non_observe_step_type_raises(self) -> None:
        """source_ref.step_type != 'observe' raises ValueError."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "forecast",
                {
                    "source_ref": {
                        "step_type": "compare",
                        "session_id": sid,
                        "step_id": step_id,
                        "artifact_id": artifact_id,
                        "observation_type": "time_series",
                    },
                    "horizon": 3,
                    "profile": "level",
                },
            )
        self.assertIn("INVALID_ARGUMENT", str(ctx.exception))

    def test_wrong_observation_type_raises(self) -> None:
        """Artifact with observation_type != 'time_series' raises ValueError."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(
            self.service, sid, observation_type="scalar"
        )
        with self.assertRaises(ValueError) as ctx:
            self._run_forecast(sid, step_id, artifact_id)
        self.assertIn("INVALID_ARGUMENT", str(ctx.exception))
        self.assertIn("time_series", str(ctx.exception))

    def test_invalid_horizon_zero_raises(self) -> None:
        """horizon=0 raises ValueError."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self._run_forecast(sid, step_id, artifact_id, horizon=0)
        self.assertIn("horizon", str(ctx.exception).lower())

    def test_invalid_profile_raises(self) -> None:
        """Unsupported profile name raises ValueError."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "forecast",
                {
                    "source_ref": {
                        "step_type": "observe",
                        "session_id": sid,
                        "step_id": step_id,
                        "artifact_id": artifact_id,
                        "observation_type": "time_series",
                    },
                    "horizon": 3,
                    "profile": "arima",
                },
            )
        self.assertIn("INVALID_ARGUMENT", str(ctx.exception))
        self.assertIn("profile", str(ctx.exception))

    def test_seasonal_profile_raises_unsupported(self) -> None:
        """profile='seasonal' raises UNSUPPORTED_OPERATION (not supported in v1)."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self._run_forecast(sid, step_id, artifact_id, profile="seasonal")
        self.assertIn("UNSUPPORTED_OPERATION", str(ctx.exception))

    def test_artifact_id_mismatch_raises(self) -> None:
        """source_ref.artifact_id that doesn't match committed artifact raises ValueError."""
        sid = self._make_session()
        step_id, _ = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "forecast",
                {
                    "source_ref": {
                        "step_type": "observe",
                        "session_id": sid,
                        "step_id": step_id,
                        "artifact_id": "wrong_artifact_id",
                        "observation_type": "time_series",
                    },
                    "horizon": 3,
                    "profile": "level",
                },
            )
        self.assertIn("INVALID_ARGUMENT", str(ctx.exception))
        self.assertIn("artifact_id", str(ctx.exception))

    def test_step_not_found_raises(self) -> None:
        """Nonexistent step_id raises ValueError with STEP_NOT_FOUND."""
        sid = self._make_session()
        with self.assertRaises(ValueError) as ctx:
            self.service.run_intent(
                sid,
                "forecast",
                {
                    "source_ref": {
                        "step_type": "observe",
                        "session_id": sid,
                        "step_id": "step_nonexistent",
                        "observation_type": "time_series",
                    },
                    "horizon": 3,
                    "profile": "level",
                },
            )
        self.assertIn("STEP_NOT_FOUND", str(ctx.exception))

    def test_invalid_interval_level_raises(self) -> None:
        """interval_level outside (0, 1) raises ValueError."""
        sid = self._make_session()
        step_id, artifact_id = _inject_observe_artifact(self.service, sid)
        with self.assertRaises(ValueError) as ctx:
            self._run_forecast(sid, step_id, artifact_id, horizon=3, interval_level=1.5)
        self.assertIn("interval_level", str(ctx.exception).lower())


# ── HTTP endpoint tests ───────────────────────────────────────────────────────


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

        _seed_forecast_table(db_path)
        _seed_metadata(metadata, suffix="http01", metric_name="http_forecast_dau")

        cls.client = TestClient(
            create_app(db_path=db_path, metadata_store=metadata, analytics_engine=analytics)
        )

        # Create session and run observe intent to get a real time_series artifact
        r = cls.client.post("/sessions", json={"goal": "forecast HTTP test"})
        assert r.status_code == 200, r.text
        cls.session_id = r.json()["session_id"]

        r_obs = cls.client.post(
            f"/sessions/{cls.session_id}/intents/observe",
            json={
                "metric": "http_forecast_dau",
                "granularity": "day",
                "time_scope": {"kind": "range", "start": _SERIES_START, "end": _SERIES_END},
            },
        )
        assert r_obs.status_code == 200, r_obs.text
        obs_body = r_obs.json()
        cls.obs_step_id = obs_body["step_ref"]["step_id"]
        cls.obs_artifact_id = obs_body["artifact_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _valid_body(self, **overrides: object) -> dict:
        body = {
            "source_ref": {
                "step_type": "observe",
                "session_id": self.session_id,
                "step_id": self.obs_step_id,
                "artifact_id": self.obs_artifact_id,
                "observation_type": "time_series",
            },
            "horizon": 7,
            "profile": "trend",
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
        self.assertEqual(body["observation_type"], "forecast_series")
        self.assertIn("forecast", body)
        self.assertEqual(len(body["forecast"]), 7)
        self.assertIn("artifact_id", body)
        self.assertIn("step_ref", body)

    def test_level_profile_endpoint(self) -> None:
        r = self.client.post(
            f"/sessions/{self.session_id}/intents/forecast",
            json=self._valid_body(profile="level", horizon=3),
        )
        self.assertEqual(r.status_code, 200, msg=r.text)
        body = r.json()
        self.assertEqual(len(body["forecast"]), 3)

    def test_missing_session_returns_404(self) -> None:
        # source_ref.session_id must match the URL path to pass the same-session guard;
        # the 404 is then raised by the service when it looks up a nonexistent session.
        body = self._valid_body()
        body["source_ref"]["session_id"] = "sess_doesnotexist"
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


if __name__ == "__main__":
    unittest.main()
