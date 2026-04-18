from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.intents.decompose import (
    _extract_date_range,
    _infer_compare_grain,
    _normalize_decompose_compare_input,
    _run_segmented_query,
)


class DecomposeHourWindowTests(unittest.TestCase):
    def test_infer_compare_grain_prefers_hour_for_datetime_windows_over_metric_day(self) -> None:
        self.assertEqual(
            _infer_compare_grain(
                left_time_scope={
                    "kind": "range",
                    "start": "2024-01-01T01:00:00",
                    "end": "2024-01-01T03:00:00",
                },
                right_time_scope={
                    "kind": "range",
                    "start": "2024-01-01T00:00:00",
                    "end": "2024-01-01T01:00:00",
                },
                fallback_grain="day",
            ),
            "hour",
        )

    def test_infer_compare_grain_falls_back_to_metric_grain_for_date_windows(self) -> None:
        self.assertEqual(
            _infer_compare_grain(
                left_time_scope={"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                right_time_scope={"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
                fallback_grain="day",
            ),
            "day",
        )

    def test_extract_date_range_preserves_hour_boundaries(self) -> None:
        self.assertEqual(
            _extract_date_range(
                {
                    "kind": "range",
                    "start": "2024-01-01T01:00:00",
                    "end": "2024-01-01T03:00:00",
                }
            ),
            ("2024-01-01T01:00:00", "2024-01-01T03:00:00"),
        )

    def test_time_series_compare_input_uses_matched_time_scope(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "comparison_type": "time_series_delta",
                "metric": "m1",
                "summary_left_value": 30.0,
                "summary_right_value": 23.0,
                "summary_absolute_delta": 7.0,
                "summary_relative_delta": 7.0 / 23.0,
                "summary_direction": "increase",
                "granularity": "day",
                "resolved_input_summary": {
                    "left_time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "right_time_scope": {
                        "kind": "range",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
                "analytical_metadata": {
                    "matched_bucket_count": 2,
                    "matched_time_scope": {
                        "kind": "range",
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
            normalized["left_time_scope"],
            {"kind": "range", "start": "2024-01-02", "end": "2024-01-04"},
        )
        self.assertEqual(
            normalized["right_time_scope"],
            {"kind": "range", "start": "2024-01-02", "end": "2024-01-04"},
        )
        self.assertEqual(
            normalized["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )

    def test_run_segmented_query_uses_hour_grain_with_datetime_boundaries(self) -> None:
        captured: dict[str, object] = {}

        class _FakeService:
            @staticmethod
            def _resolve_windowed_query_time_axis(
                request: object,
                *,
                engine_type: str,
                metric_name: str | None = None,
                fallback_columns: list[str] | None = None,
            ) -> None:
                _ = (request, engine_type, metric_name, fallback_columns)

            @staticmethod
            def _build_scoped_query(session_id: str, resolved: object, *, engine_type: str) -> dict:
                _ = (session_id, resolved, engine_type)
                return {"sql": "SELECT 1"}

            @staticmethod
            def _compile_step_with_feedback(
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
                "app.intents.decompose.normalize_metric_query_request",
                side_effect=_capture_normalize,
            ),
            patch(
                "app.intents.decompose.execute_compiled",
                return_value=SimpleNamespace(rows=[], metadata={"translated_sql": "SELECT 1"}),
            ),
        ):
            rows, query_hash = _run_segmented_query(
                _FakeService(),
                "sess_decompose_hour",
                "metric.attr_hourly",
                "SUM(value)",
                "analytics.attr_events",
                "channel",
                ["event_time", "channel"],
                {
                    "kind": "range",
                    "start": "2024-01-01T01:00:00",
                    "end": "2024-01-01T03:00:00",
                },
                {},
                object(),
                "duckdb",
                "hour",
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
                    "grain": "hour",
                    "current": {
                        "start": "2024-01-01T01:00:00",
                        "end": "2024-01-01T03:00:00",
                    },
                },
                "dimensions": ["channel"],
            },
        )
