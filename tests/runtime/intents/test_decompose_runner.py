from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from marivo.runtime.intents.decompose import (
    _extract_date_range,
    _normalize_decompose_compare_input,
    _run_segmented_query,
    run_decompose_intent,
)
from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _make_compiled_mock,
)


class DecomposeHourWindowTests(unittest.TestCase):
    def test_extract_date_range_preserves_hour_boundaries(self) -> None:
        self.assertEqual(
            _extract_date_range(
                {
                    "field": "time",
                    "start": "2024-01-01T01:00:00",
                    "end": "2024-01-01T03:00:00",
                }
            ),
            ("2024-01-01T01:00:00", "2024-01-01T03:00:00"),
        )

    def test_scalar_delta_read_from_series_format(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "scalar_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "axes": [],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "current_value": 100.0,
                                "baseline_value": 90.0,
                                "delta": 10.0,
                                "delta_pct": 10.0 / 90.0,
                                "direction": "increase",
                            }
                        ],
                    }
                ],
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-12-25",
                        "end": "2024-01-01",
                    },
                },
            }
        )

        self.assertEqual(normalized["comparison_type"], "scalar_delta")
        self.assertEqual(normalized["scope_current_value"], 100.0)
        self.assertEqual(normalized["scope_baseline_value"], 90.0)
        self.assertEqual(normalized["scope_absolute_delta"], 10.0)
        self.assertEqual(normalized["source_observation_type"], "scalar")

    def test_scalar_delta_fallback_to_top_level_fields(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "scalar_delta",
                "metric": "m1",
                "current_value": 50.0,
                "baseline_value": 40.0,
                "absolute_delta": 10.0,
                "relative_delta": 0.25,
                "direction": "increase",
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-12-25",
                        "end": "2024-01-01",
                    },
                },
            }
        )

        self.assertEqual(normalized["scope_current_value"], 50.0)
        self.assertEqual(normalized["scope_baseline_value"], 40.0)
        self.assertEqual(normalized["scope_absolute_delta"], 10.0)

    def test_axes_determine_scalar_observation_type(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "scalar_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "axes": [],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "current_value": 100.0,
                                "baseline_value": 90.0,
                                "delta": 10.0,
                                "delta_pct": 10.0 / 90.0,
                                "direction": "increase",
                            }
                        ],
                    }
                ],
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-12-25",
                        "end": "2024-01-01",
                    },
                },
            }
        )

        self.assertEqual(normalized["source_observation_type"], "scalar")

    def test_axes_determine_time_series_observation_type(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "time_series_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "axes": [{"kind": "time", "grain": "day"}],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "window": {"start": "2024-01-01", "end": "2024-01-02"},
                                "current_value": 30.0,
                                "baseline_value": 23.0,
                                "delta": 7.0,
                                "delta_pct": 7.0 / 23.0,
                                "direction": "increase",
                                "presence": "both",
                            }
                        ],
                    }
                ],
                "summary_current_value": 30.0,
                "summary_baseline_value": 23.0,
                "summary_absolute_delta": 7.0,
                "summary_relative_delta": 7.0 / 23.0,
                "summary_direction": "increase",
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
                "analytical_metadata": {
                    "matched_bucket_count": 1,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                },
            }
        )

        self.assertEqual(normalized["source_observation_type"], "time_series")

    def test_time_series_compare_input_aggregates_from_series_points(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "time_series_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "axes": [{"kind": "time", "grain": "day"}],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "window": {"start": "2024-01-01", "end": "2024-01-02"},
                                "current_value": 20.0,
                                "baseline_value": 15.0,
                                "delta": 5.0,
                                "delta_pct": 5.0 / 15.0,
                                "direction": "increase",
                                "presence": "both",
                            },
                            {
                                "window": {"start": "2024-01-02", "end": "2024-01-03"},
                                "current_value": 10.0,
                                "baseline_value": 8.0,
                                "delta": 2.0,
                                "delta_pct": 2.0 / 8.0,
                                "direction": "increase",
                                "presence": "both",
                            },
                        ],
                    }
                ],
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
                "analytical_metadata": {
                    "matched_bucket_count": 2,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                },
            }
        )

        self.assertEqual(normalized["comparison_type"], "time_series_delta")
        # Aggregated from series points: 20+10=30, 15+8=23, delta=7
        self.assertEqual(normalized["scope_current_value"], 30.0)
        self.assertEqual(normalized["scope_baseline_value"], 23.0)
        self.assertEqual(normalized["scope_absolute_delta"], 7.0)
        self.assertEqual(normalized["source_observation_type"], "time_series")
        self.assertEqual(
            normalized["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )
        self.assertEqual(normalized["analytical_metadata"]["source_granularity"], "day")

    def test_time_series_compare_input_uses_matched_time_scope(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "time_series_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "axes": [{"kind": "time", "grain": "day"}],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "window": {"start": "2024-01-02", "end": "2024-01-04"},
                                "current_value": 30.0,
                                "baseline_value": 23.0,
                                "delta": 7.0,
                                "delta_pct": 7.0 / 23.0,
                                "direction": "increase",
                                "presence": "both",
                            }
                        ],
                    }
                ],
                "summary_current_value": 30.0,
                "summary_baseline_value": 23.0,
                "summary_absolute_delta": 7.0,
                "summary_relative_delta": 7.0 / 23.0,
                "summary_direction": "increase",
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
                "analytical_metadata": {
                    "matched_bucket_count": 2,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                },
            }
        )

        self.assertEqual(normalized["comparison_type"], "time_series_delta")
        self.assertEqual(normalized["scope_absolute_delta"], 7.0)
        self.assertEqual(normalized["source_observation_type"], "time_series")
        self.assertEqual(
            normalized["current_time_scope"],
            {"field": "time", "start": "2024-01-02", "end": "2024-01-04"},
        )
        self.assertEqual(
            normalized["baseline_time_scope"],
            {"field": "time", "start": "2024-01-02", "end": "2024-01-04"},
        )
        self.assertEqual(
            normalized["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )

    def test_time_series_compare_input_prefers_side_specific_matched_time_scopes(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "time_series_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "axes": [{"kind": "time", "grain": "day"}],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "window": {"start": "2024-01-02", "end": "2024-01-04"},
                                "current_value": 30.0,
                                "baseline_value": 23.0,
                                "delta": 7.0,
                                "delta_pct": 7.0 / 23.0,
                                "direction": "increase",
                                "presence": "both",
                            }
                        ],
                    }
                ],
                "summary_current_value": 30.0,
                "summary_baseline_value": 23.0,
                "summary_absolute_delta": 7.0,
                "summary_relative_delta": 7.0 / 23.0,
                "summary_direction": "increase",
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
                "analytical_metadata": {
                    "matched_bucket_count": 2,
                    "pairing_basis": "calendar_aligned_observation_windows",
                    "pairing_rule": "calendar_aligned_bucket_pairing",
                    "matched_current_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                    "matched_baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-03",
                        "end": "2023-01-05",
                    },
                },
            }
        )

        self.assertEqual(
            normalized["current_time_scope"],
            {"field": "time", "start": "2024-01-02", "end": "2024-01-04"},
        )
        self.assertEqual(
            normalized["baseline_time_scope"],
            {"field": "time", "start": "2023-01-03", "end": "2023-01-05"},
        )
        self.assertEqual(
            normalized["analytical_metadata"]["source_pairing_basis"],
            "calendar_aligned_observation_windows",
        )
        self.assertEqual(
            normalized["analytical_metadata"]["source_pairing_rule"],
            "calendar_aligned_bucket_pairing",
        )

    def test_run_segmented_query_uses_exact_window_with_datetime_boundaries(self) -> None:
        captured: dict[str, object] = {}

        class _FakeRuntime:
            @staticmethod
            def resolve_windowed_query_time_axis(
                request: object,
                *,
                engine_type: str,
                metric_name: str | None = None,
                fallback_columns: list[str] | None = None,
            ) -> None:
                _ = (request, engine_type, metric_name, fallback_columns)

            @staticmethod
            def build_scoped_query(session_id: str, resolved: object, *, engine_type: str) -> dict:
                _ = (session_id, resolved, engine_type)
                return {"sql": "SELECT 1"}

            @staticmethod
            def compile_step(
                step: object,
                *,
                engine_type: str,
                semantic_context: dict,
            ) -> SimpleNamespace:
                _ = (step, engine_type, semantic_context)
                return SimpleNamespace(sql="SELECT 1", params={})

        def _capture_normalize(params: dict) -> SimpleNamespace:
            captured["params"] = params
            return SimpleNamespace(table="analytics.attr_events")

        with (
            patch(
                "marivo.runtime.intents.decompose.normalize_metric_query_request",
                side_effect=_capture_normalize,
            ),
            patch(
                "marivo.runtime.intents.decompose.execute_compiled",
                return_value=SimpleNamespace(rows=[], metadata={"translated_sql": "SELECT 1"}),
            ),
        ):
            rows, sql_text, query_hash, elapsed_ms = _run_segmented_query(
                _FakeRuntime(),
                "sess_decompose_hour",
                "metric.attr_hourly",
                "SUM(value)",
                "analytics.attr_events",
                "channel",
                ["event_time", "channel"],
                {
                    "field": "time",
                    "start": "2024-01-01T01:00:00",
                    "end": "2024-01-01T03:00:00",
                },
                {},
                object(),
                "duckdb",
            )

        self.assertEqual(rows, [])
        self.assertIsNotNone(query_hash)
        self.assertEqual(
            captured["params"],
            {
                "table": "analytics.attr_events",
                "metric": "metric.attr_hourly",
                "time_scope": {
                    "mode": "single_window",
                    "boundary_mode": "exact",
                    "current": {
                        "start": "2024-01-01T01:00:00",
                        "end": "2024-01-01T03:00:00",
                    },
                },
                "dimensions": ["channel"],
                "time_scope_field": "time",
            },
        )

    def test_run_segmented_query_does_not_infer_hour_from_midnight_datetime(self) -> None:
        captured: dict[str, object] = {}

        class _FakeRuntime:
            @staticmethod
            def resolve_windowed_query_time_axis(
                request: object,
                *,
                engine_type: str,
                metric_name: str | None = None,
                fallback_columns: list[str] | None = None,
            ) -> None:
                _ = (request, engine_type, metric_name, fallback_columns)

            @staticmethod
            def build_scoped_query(session_id: str, resolved: object, *, engine_type: str) -> dict:
                _ = (session_id, resolved, engine_type)
                return {"sql": "SELECT 1"}

            @staticmethod
            def compile_step(
                step: object,
                *,
                engine_type: str,
                semantic_context: dict,
            ) -> SimpleNamespace:
                _ = (step, engine_type, semantic_context)
                return SimpleNamespace(sql="SELECT 1", params={})

        def _capture_normalize(params: dict) -> SimpleNamespace:
            captured["params"] = params
            return SimpleNamespace(table="analytics.attr_events")

        with (
            patch(
                "marivo.runtime.intents.decompose.normalize_metric_query_request",
                side_effect=_capture_normalize,
            ),
            patch(
                "marivo.runtime.intents.decompose.execute_compiled",
                return_value=SimpleNamespace(rows=[], metadata={"translated_sql": "SELECT 1"}),
            ),
        ):
            rows, sql_text, query_hash, elapsed_ms = _run_segmented_query(
                _FakeRuntime(),
                "sess_decompose_midnight",
                "metric.attr_daily",
                "SUM(value)",
                "analytics.attr_events",
                "cluster",
                ["log_date", "cluster"],
                {
                    "field": "log_date",
                    "start": "2026-05-10T00:00:00+08:00",
                    "end": "2026-05-17T00:00:00+08:00",
                },
                {},
                object(),
                "duckdb",
            )

        self.assertEqual(rows, [])
        self.assertIsNotNone(query_hash)
        params = captured["params"]
        self.assertIsInstance(params, dict)
        time_scope = params["time_scope"]
        self.assertIsInstance(time_scope, dict)
        self.assertEqual(time_scope["boundary_mode"], "exact")
        self.assertNotIn("grain", time_scope)
        self.assertEqual(params["time_scope_field"], "log_date")


class TestDecomposeRunnerCommitPath(unittest.TestCase):
    """run_decompose_intent must call _commit_artifact_with_extraction(step_type='decompose')."""

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        return runtime

    def _run_decompose(
        self, runtime: MagicMock, compare_artifact: dict[str, Any] | None = None
    ) -> dict[str, Any]:

        if compare_artifact is None:
            # v2.0 scalar_delta format
            compare_artifact = {
                "comparison_type": "scalar_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "unit": None,
                "axes": [],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "current_value": 100.0,
                                "baseline_value": 90.0,
                                "delta": 10.0,
                                "delta_pct": 10.0 / 90.0,
                                "direction": "increase",
                            }
                        ],
                    }
                ],
                # Top-level aliases for backward compat
                "current_value": 100.0,
                "baseline_value": 90.0,
                "absolute_delta": 10.0,
                "relative_delta": 10.0 / 90.0,
                "direction": "increase",
                "lineage": {
                    "current_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                    "baseline_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
                },
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-12-25",
                        "end": "2024-01-01",
                    },
                    "current_scope": {},
                    "baseline_scope": {},
                },
            }
        runtime.resolve_artifact_by_id.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake_ref001"

        # Configure resolved_metric with real values so validation passes
        resolved_metric = MagicMock()
        resolved_metric.semantic_object = {
            "header": {
                "aggregation_semantics": "ratio",
            },
            "payload": {
                "dimensions": ["dim1"],
            },
        }
        resolved_metric.aggregation_semantics = "ratio"
        resolved_metric.dimensions = ["dim1"]
        resolved_metric.grain = "day"
        runtime.resolve_metric.return_value = resolved_metric
        runtime.resolve_metric_dimensions.return_value = ["dim1"]
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"

        runtime.resolve_metric_table.return_value = "src.metrics"
        runtime.resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )

        # _run_segmented_query calls _compile_step_with_feedback + execute_compiled
        runtime.compile_step.return_value = _make_compiled_mock()
        runtime.build_scoped_query.return_value = None

        params = {"compare_artifact_id": "art_compare", "dimension": "dim1"}
        with patch("marivo.runtime.intents.decompose.execute_compiled") as mock_exec:
            # Return 1 row for both left and right segmented queries.
            # Configure metadata.get() to return None so the query_hash branch skips.
            mock_result = MagicMock()
            mock_result.rows = [{"dim1": "segment_a", "current_value": 50.0}]
            mock_result.metadata.get.return_value = None
            mock_exec.return_value = mock_result
            return run_decompose_intent(runtime, _SESSION, params)

    def test_decompose_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_decompose(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_decompose_passes_step_type_decompose(self) -> None:
        runtime = self._make_runtime()
        self._run_decompose(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "decompose")

    def test_decompose_artifact_type_is_delta_decomposition(self) -> None:
        runtime = self._make_runtime()
        self._run_decompose(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "delta_decomposition")

    def test_decompose_output_has_schema_version_2(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertEqual(result["schema_version"], "2.0")

    def test_decompose_output_has_axes_and_series(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertIsInstance(result["axes"], list)
        self.assertEqual(result["axes"], [{"kind": "dimension", "name": "dim1"}])
        self.assertIsInstance(result["series"], list)
        # The series should have entries for the decomposed dimension
        self.assertTrue(len(result["series"]) > 0)
        # Each series entry should have keys and points
        for entry in result["series"]:
            self.assertIn("keys", entry)
            self.assertIn("points", entry)
            self.assertIn("dim1", entry["keys"])

    def test_decompose_output_has_rows_backward_compat_alias(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        # v2.0 format uses series as canonical, but keeps rows as backward-compat alias
        self.assertIn("rows", result)
        self.assertIsInstance(result["rows"], list)

    def test_decompose_output_has_dimension_backward_compat_alias(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        # v2.0 format encodes dimension in axes, but keeps dimension as backward-compat alias
        self.assertIn("dimension", result)
        self.assertEqual(result["dimension"], "dim1")

    def test_decompose_output_scope_values_at_top_level(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertIn("scope_current_value", result)
        self.assertIn("scope_baseline_value", result)
        self.assertIn("scope_absolute_delta", result)
        self.assertIn("scope_relative_delta", result)
        self.assertIn("scope_direction", result)

    def test_decompose_time_series_delta_commits_summary_delta_decomposition(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(
            runtime,
            {
                "comparison_type": "time_series_delta",
                "schema_version": "2.0",
                "metric": "m1",
                "unit": None,
                "axes": [{"kind": "time", "grain": "day"}],
                "series": [
                    {
                        "keys": {},
                        "points": [
                            {
                                "window": {"start": "2024-01-01", "end": "2024-01-02"},
                                "current_value": 60.0,
                                "baseline_value": 45.0,
                                "delta": 15.0,
                                "delta_pct": 15.0 / 45.0,
                                "direction": "increase",
                                "presence": "both",
                            },
                            {
                                "window": {"start": "2024-01-02", "end": "2024-01-03"},
                                "current_value": 60.0,
                                "baseline_value": 45.0,
                                "delta": 15.0,
                                "delta_pct": 15.0 / 45.0,
                                "direction": "increase",
                                "presence": "both",
                            },
                        ],
                    }
                ],
                "summary_current_value": 120.0,
                "summary_baseline_value": 90.0,
                "summary_absolute_delta": 30.0,
                "summary_relative_delta": 0.333,
                "summary_direction": "increase",
                "lineage": {
                    "current_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                    "baseline_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
                },
                "resolved_input_summary": {
                    "current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                    "current_scope": {},
                    "baseline_scope": {},
                },
                "analytical_metadata": {
                    "pairing_basis": "calendar_aligned_observation_windows",
                    "pairing_rule": "calendar_aligned_bucket_pairing",
                    "matched_bucket_count": 7,
                    "dropped_current_buckets": 0,
                    "dropped_baseline_buckets": 0,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "matched_current_time_scope": {
                        "field": "time",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "matched_baseline_time_scope": {
                        "field": "time",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
            },
        )

        self.assertEqual(result["compare_ref"]["comparison_type"], "time_series_delta")
        self.assertEqual(result["current_ref"]["observation_type"], "time_series")
        self.assertEqual(result["baseline_ref"]["observation_type"], "time_series")
        self.assertEqual(result["scope_absolute_delta"], 30.0)
        self.assertEqual(result["schema_version"], "2.0")
        self.assertIsInstance(result["axes"], list)
        self.assertIsInstance(result["series"], list)
        self.assertEqual(
            result["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )
        self.assertEqual(result["analytical_metadata"]["source_granularity"], "day")
        self.assertEqual(
            result["analytical_metadata"]["source_pairing_basis"],
            "calendar_aligned_observation_windows",
        )


# -- detect --------------------------------------------------------------------
