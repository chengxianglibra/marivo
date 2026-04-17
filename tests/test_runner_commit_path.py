"""Runner integration tests for Phase 4c-2: mandatory extraction runner wiring.

Acceptance criteria:
- All 7 mandatory-extraction runners call _commit_artifact_with_extraction instead of
  _insert_artifact directly.
- Each call carries the correct step_type keyword argument.

Strategy: Mock the SemanticLayerService (MagicMock, no spec) to avoid a live analytics
engine, patch execute_compiled in runner modules where needed, and assert that
_commit_artifact_with_extraction is called with the expected step_type.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

# ── helpers ──────────────────────────────────────────────────────────────────

_SESSION = "sess_4c2_test"
_FAKE_ARTIFACT_ID = "art_fake4c2001"


def _make_svc() -> MagicMock:
    """Return a MagicMock svc with sensible defaults for common svc methods."""
    svc = MagicMock()
    svc._new_step_id.return_value = "step_4c2_001"
    svc._commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
    svc._insert_step.return_value = None
    svc._make_provenance.return_value = {"query_hash": "testhash"}
    return svc


def _make_compiled_mock() -> MagicMock:
    m = MagicMock()
    m.sql = "SELECT 1"
    m.params = []
    return m


def _scalar_observation(metric: str = "m1") -> dict[str, Any]:
    return {
        "observation_type": "scalar",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "value": 42.0,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "metric_additivity": "additive",
            "row_count": 10,
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        "scope": {},
    }


# ── observe ───────────────────────────────────────────────────────────────────


class TestObserveRunnerCommitPath(unittest.TestCase):
    """run_observe_intent must call _commit_artifact_with_extraction(step_type='observe')."""

    def _run_scalar(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.observe import run_observe_intent

        svc._resolve_metric_table.return_value = "src.metrics"
        svc.resolve_metric_sql.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        svc._build_scoped_query.return_value = None
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        }
        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = []
            return run_observe_intent(svc, _SESSION, params)

    def test_observe_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_scalar(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_observe_passes_step_type_observe(self) -> None:
        svc = _make_svc()
        self._run_scalar(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "observe")

    def test_observe_artifact_type_is_observation(self) -> None:
        svc = _make_svc()
        self._run_scalar(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        # positional: session_id, step_id, artifact_type, name, content
        self.assertEqual(args[2], "observation")

    def test_observe_returns_artifact_id(self) -> None:
        svc = _make_svc()
        result = self._run_scalar(svc)
        self.assertEqual(result["artifact_id"], _FAKE_ARTIFACT_ID)

    def test_observe_hour_granularity_rejects_date_only_range(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        with self.assertRaisesRegex(
            ValueError, "time_scope.start must be a naive datetime string for hour grain"
        ):
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "m1",
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-02"},
                    "granularity": "hour",
                },
            )

    def test_observe_rejects_unknown_calendar_policy_ref(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        with self.assertRaisesRegex(
            ValueError,
            "observe: INVALID_ARGUMENT - Unknown calendar_policy_ref",
        ):
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "m1",
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                    "calendar_policy_ref": "calendar_policy.not_real",
                },
            )

    def test_observe_forwards_calendar_policy_ref_to_internal_compile_step(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        captured: dict[str, Any] = {}
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        svc._resolve_windowed_query_time_axis.return_value = None
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2024-01-01", "end": "2024-01-08"},
        }

        def _capture_compile(step: Any, *, engine_type: str, semantic_context: Any) -> MagicMock:
            captured["calendar_policy_ref"] = step.params.get("calendar_policy_ref")
            captured["time_scope"] = step.params.get("time_scope")
            return _make_compiled_mock()

        svc._compile_step_with_feedback.side_effect = _capture_compile

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = []
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

        self.assertEqual(captured["calendar_policy_ref"], "calendar_policy.weekday_yoy")
        self.assertEqual(captured["time_scope"]["grain"], "day")

    def test_observe_hour_granularity_uses_hour_internal_grain(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        captured: dict[str, Any] = {}
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        svc._resolve_windowed_query_time_axis.return_value = None

        def _capture_scoped_query(
            session_id: str, resolved: Any, *, engine_type: str
        ) -> dict[str, Any]:
            captured["grain"] = resolved.time_scope.grain
            return {
                "mode": resolved.time_scope.mode,
                "engine_type": engine_type,
                "analysis_time_expr": "event_time",
                "current": {
                    "start": resolved.time_scope.current.start,
                    "end": resolved.time_scope.current.end,
                },
            }

        svc._build_scoped_query.side_effect = _capture_scoped_query
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        params = {
            "metric": "metric.m1",
            "time_scope": {
                "kind": "range",
                "start": "2024-01-01T00:00:00",
                "end": "2024-01-01T02:00:00",
            },
            "granularity": "hour",
        }
        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = []
            run_observe_intent(svc, _SESSION, params)

        self.assertEqual(captured["grain"], "hour")

    def _run_numeric_summary(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.observe import run_observe_intent

        svc._resolve_metric_table.return_value = "src.metrics"
        svc.resolve_metric_sql.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        svc._build_scoped_query.return_value = None
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "result_mode": "numeric_sample_summary",
        }
        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [
                {"n": 5, "mean": 10.0, "variance": 1.0, "std": 1.0, "min_val": 8.0, "max_val": 12.0}
            ]
            return run_observe_intent(svc, _SESSION, params)

    def test_observe_numeric_summary_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_numeric_summary(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_observe_numeric_summary_passes_step_type_observe(self) -> None:
        svc = _make_svc()
        self._run_numeric_summary(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "observe")

    def test_observe_numeric_summary_artifact_type_is_observation(self) -> None:
        svc = _make_svc()
        self._run_numeric_summary(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "observation")

    def _run_rate_summary(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.observe import run_observe_intent

        svc._resolve_metric_table.return_value = "src.metrics"
        svc.resolve_metric_sql.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        svc._build_scoped_query.return_value = None
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "result_mode": "rate_sample_summary",
        }
        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [{"n": 100, "k": 50.0}]
            return run_observe_intent(svc, _SESSION, params)

    def test_observe_rate_summary_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_rate_summary(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_observe_rate_summary_passes_step_type_observe(self) -> None:
        svc = _make_svc()
        self._run_rate_summary(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "observe")

    def test_observe_rate_summary_artifact_type_is_observation(self) -> None:
        svc = _make_svc()
        self._run_rate_summary(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "observation")


# ── compare ───────────────────────────────────────────────────────────────────


class TestCompareRunnerCommitPath(unittest.TestCase):
    """run_compare_intent must call _commit_artifact_with_extraction(step_type='compare')."""

    def _run_scalar_compare(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.compare import run_compare_intent

        svc._resolve_artifact_for_ref.side_effect = [
            _scalar_observation("m1"),
            _scalar_observation("m1"),
        ]
        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION, "step_type": "observe"},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION, "step_type": "observe"},
        }
        return run_compare_intent(svc, _SESSION, params)

    def test_compare_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_scalar_compare(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_compare_passes_step_type_compare(self) -> None:
        svc = _make_svc()
        self._run_scalar_compare(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "compare")

    def test_compare_artifact_type_is_compare_artifact(self) -> None:
        svc = _make_svc()
        self._run_scalar_compare(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "compare_artifact")


# ── decompose ─────────────────────────────────────────────────────────────────


class TestDecomposeRunnerCommitPath(unittest.TestCase):
    """run_decompose_intent must call _commit_artifact_with_extraction(step_type='decompose')."""

    def _run_decompose(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.decompose import run_decompose_intent

        compare_artifact = {
            "comparison_type": "scalar_delta",
            "metric": "m1",
            "unit": None,
            "left_value": 100.0,
            "right_value": 90.0,
            "absolute_delta": 10.0,
            "relative_delta": 0.111,
            "direction": "increase",
            "lineage": {
                "left_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                "right_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
            },
            "resolved_input_summary": {
                "left_time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
                "left_scope": {},
                "right_scope": {},
            },
        }
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake_ref001"

        # Configure resolved_metric with real values so validation passes
        resolved_metric = MagicMock()
        resolved_metric.measure_type = "additive"
        resolved_metric.allowed_dimensions = ["dim1"]
        resolved_metric.dimensions = ["dim1"]
        resolved_metric.grain = "day"
        svc.semantic_repository.resolve_metric.return_value = resolved_metric
        svc.resolve_metric_dimensions.return_value = ["dim1"]
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"

        svc._resolve_metric_table.return_value = "src.metrics"
        svc._resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )

        # _run_segmented_query calls _compile_step_with_feedback + execute_compiled
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()
        svc._build_scoped_query.return_value = None

        params = {
            "compare_ref": {"step_id": "step_compare", "session_id": _SESSION},
            "dimension": "dim1",
        }
        with patch("app.intents.decompose.execute_compiled") as mock_exec:
            # Return 1 row for both left and right segmented queries.
            # Configure metadata.get() to return None so the query_hash branch skips.
            mock_result = MagicMock()
            mock_result.rows = [{"dim1": "segment_a", "current_value": 50.0}]
            mock_result.metadata.get.return_value = None
            mock_exec.return_value = mock_result
            return run_decompose_intent(svc, _SESSION, params)

    def test_decompose_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_decompose(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_decompose_passes_step_type_decompose(self) -> None:
        svc = _make_svc()
        self._run_decompose(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "decompose")

    def test_decompose_artifact_type_is_delta_decomposition(self) -> None:
        svc = _make_svc()
        self._run_decompose(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "delta_decomposition")


# ── detect ────────────────────────────────────────────────────────────────────


class TestDetectRunnerCommitPath(unittest.TestCase):
    """run_detect_intent must call _commit_artifact_with_extraction(step_type='detect')."""

    def _run_detect(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.detect import run_detect_intent

        svc._resolve_metric_table.return_value = "src.metrics"
        svc.resolve_metric_sql.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        svc._build_scoped_query.return_value = None
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2024-01-01", "end": "2024-01-31"},
            },
        }
        with patch("app.intents.detect.execute_compiled") as mock_exec:
            # 9 points with one spike (day 5 = 200) to produce ≥1 anomaly candidate.
            # mean≈111, std≈31, z(200)≈2.83 > balanced threshold 2.0.
            mock_exec.return_value.rows = [
                {"bucket_start": f"2024-01-{d:02d}", "value": 200.0 if d == 5 else 100.0}
                for d in range(1, 10)
            ]
            return run_detect_intent(svc, _SESSION, params)

    def test_detect_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_detect(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_detect_passes_step_type_detect(self) -> None:
        svc = _make_svc()
        self._run_detect(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "detect")

    def test_detect_artifact_type_is_anomaly_candidates(self) -> None:
        svc = _make_svc()
        self._run_detect(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "anomaly_candidates")

    def test_detect_artifact_id_patched_in_result(self) -> None:
        # After _commit_artifact_with_extraction returns, detect.py patches artifact_id
        # into result["candidates"][*]["candidate_ref"]["artifact_ref"]["artifact_id"]
        # and result["artifact_id"].  Verify both are populated with the committed id.
        svc = _make_svc()
        result = self._run_detect(svc)
        self.assertEqual(result["artifact_id"], _FAKE_ARTIFACT_ID)
        candidates = result.get("candidates", [])
        self.assertTrue(len(candidates) > 0, "expected at least one candidate in result")
        for c in candidates:
            self.assertEqual(
                c["candidate_ref"]["artifact_ref"]["artifact_id"],
                _FAKE_ARTIFACT_ID,
            )


# ── correlate ─────────────────────────────────────────────────────────────────


class TestCorrelateRunnerCommitPath(unittest.TestCase):
    """run_correlate_intent must call _commit_artifact_with_extraction(step_type='correlate')."""

    def _make_ts_artifact(self, metric: str = "m1") -> dict[str, Any]:
        return {
            "observation_type": "time_series",
            "metric": metric,
            "granularity": "day",
            "series": [
                {
                    "window": {"start": f"2024-01-{d:02d}", "end": f"2024-01-{d + 1:02d}"},
                    "value": float(d * 10),
                }
                for d in range(1, 8)  # 7 aligned pairs
            ],
        }

    def _run_correlate(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.correlate import run_correlate_intent

        svc._resolve_artifact_for_ref.side_effect = [
            self._make_ts_artifact("m1"),
            self._make_ts_artifact("m2"),
        ]
        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION},
        }
        return run_correlate_intent(svc, _SESSION, params)

    def test_correlate_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_correlate(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_correlate_passes_step_type_correlate(self) -> None:
        svc = _make_svc()
        self._run_correlate(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "correlate")

    def test_correlate_artifact_type_is_pairwise_ts_association(self) -> None:
        svc = _make_svc()
        self._run_correlate(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "pairwise_time_series_association")


# ── test ──────────────────────────────────────────────────────────────────────


class TestTestRunnerCommitPath(unittest.TestCase):
    """run_test_intent must call _commit_artifact_with_extraction(step_type='test')."""

    def _make_rate_artifact(self, metric: str = "m1") -> dict[str, Any]:
        return {
            "observation_type": "rate_sample_summary",
            "metric": metric,
            "schema_version": "1.0",
            "sample_summary": {"successes": 50, "trials": 100},
        }

    def _run_test(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.test import run_test_intent

        left_id, right_id = "art_left001", "art_right001"
        svc._resolve_artifact_with_id.side_effect = [
            (left_id, self._make_rate_artifact("m1")),
            (right_id, self._make_rate_artifact("m1")),
        ]
        params = {
            "left_ref": {
                "step_id": "step_left",
                "session_id": _SESSION,
                "step_type": "observe",
                "artifact_id": left_id,
                "observation_type": "rate_sample_summary",
            },
            "right_ref": {
                "step_id": "step_right",
                "session_id": _SESSION,
                "step_type": "observe",
                "artifact_id": right_id,
                "observation_type": "rate_sample_summary",
            },
        }
        return run_test_intent(svc, _SESSION, params)

    def test_test_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_test(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_test_passes_step_type_test(self) -> None:
        svc = _make_svc()
        self._run_test(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "test")

    def test_test_artifact_type_is_hypothesis_test(self) -> None:
        svc = _make_svc()
        self._run_test(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "hypothesis_test")


# ── forecast ──────────────────────────────────────────────────────────────────


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

    def _run_forecast(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.forecast import run_forecast_intent

        artifact_id = "art_ts001"
        svc._resolve_artifact_with_id.return_value = (artifact_id, self._make_ts_artifact())
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
        return run_forecast_intent(svc, _SESSION, params)

    def test_forecast_calls_commit_artifact_with_extraction(self) -> None:
        svc = _make_svc()
        self._run_forecast(svc)
        svc._commit_artifact_with_extraction.assert_called_once()

    def test_forecast_passes_step_type_forecast(self) -> None:
        svc = _make_svc()
        self._run_forecast(svc)
        _, kwargs = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "forecast")

    def test_forecast_artifact_type_is_forecast_series(self) -> None:
        svc = _make_svc()
        self._run_forecast(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "forecast_series")
