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
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from marivo.adapters.local.duckdb_analytics import DuckDBAnalyticsEngine
from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.runtime.intents.forecast import run_forecast_intent
from tests.runtime.intents._runner_fixtures import _FAKE_ARTIFACT_ID, _SESSION
from tests.semantic_test_helpers import build_runtime
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


# ── Direct-service tests ──────────────────────────────────────────────────────


class ForecastRunnerServiceTests(unittest.TestCase):
    """Tests that call run_forecast_intent through MarivoRuntime directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "forecast_svc.duckdb"
        meta_path = Path(cls.temp_dir.name) / "forecast_svc.meta.sqlite"

        cls.analytics = DuckDBAnalyticsEngine(str(db_path))
        cls.metadata = SQLiteMetadataStore(str(meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()

        cls.service = build_runtime(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _make_session(self) -> str:
        r = self.service.create_session("forecast test session")
        return r.session_id

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
        return run_forecast_intent(self.service, session_id, body)

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
        resolved = self.service.resolve_artifact_with_id(sid, forecast_step_id)
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
            run_forecast_intent(
                self.service,
                sid,
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
            run_forecast_intent(
                self.service,
                sid,
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
            run_forecast_intent(
                self.service,
                sid,
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
            run_forecast_intent(
                self.service,
                sid,
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
            run_forecast_intent(
                self.service,
                sid,
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


class TestForecastRunnerCommitPath(unittest.TestCase):
    """run_forecast_intent must call _commit_artifact_with_extraction(step_type='forecast')."""

    def _make_ts_artifact(self) -> dict[str, Any]:
        return {
            "observation_type": "time_series",
            "metric": "m1",
            "schema_version": "1.0",
            "granularity": "day",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "analytical_metadata": {"timezone": None, "data_complete": None},
            "series": [
                {
                    "window": {"start": f"2024-01-{d:02d}", "end": f"2024-01-{d + 1:02d}"},
                    "value": float(100 + d),
                }
                for d in range(1, 8)
            ],
        }

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        artifact_id = "art_ts001"
        runtime.resolve_artifact_with_id.return_value = (artifact_id, self._make_ts_artifact())
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        return runtime

    def _run_forecast(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.forecast import run_forecast_intent

        artifact_id = "art_ts001"
        params = {
            "source_ref": {
                "step_id": "step_obs",
                "session_id": _SESSION,
                "step_type": "observe",
                "observation_type": "time_series",
                "artifact_id": artifact_id,
            },
            "horizon": 3,
        }
        return run_forecast_intent(runtime, _SESSION, params)

    def test_forecast_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_forecast(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_forecast_passes_step_type_forecast(self) -> None:
        runtime = self._make_runtime()
        self._run_forecast(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "forecast")

    def test_forecast_artifact_type_is_forecast_series(self) -> None:
        runtime = self._make_runtime()
        self._run_forecast(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "forecast_series")
