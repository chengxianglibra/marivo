from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _make_compiled_mock,
    _set_resolved_time_axis,
)


class TestObserveRunner(unittest.TestCase):
    def _make_runtime(
        self,
        *,
        dimensions: list[str] | None = None,
        time_axis: str = "event_date",
        time_axis_kind: str = "date_field",
    ) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        runtime.resolve_metric_execution_context.return_value = SimpleNamespace(
            table_name="src.metrics",
            additive_dimensions=["event_date"],
        )
        runtime.resolve_metric.return_value = SimpleNamespace(
            semantic_object={"header": {"aggregation_semantics": "sum"}}
        )
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.resolve_metric_dimensions.return_value = dimensions or []
        engine_resolution = (
            MagicMock(),
            "duckdb",
            {"src.metrics": "src.metrics"},
        )
        runtime.resolve_engine_for_session.return_value = engine_resolution
        runtime.resolve_engine.return_value = engine_resolution
        _set_resolved_time_axis(runtime, time_axis, kind=time_axis_kind)
        runtime.build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": time_axis,
            "analysis_time_kind": time_axis_kind,
        }
        runtime.compile_step.return_value = _make_compiled_mock()
        return runtime

    def _run_observe(
        self,
        params: dict[str, Any],
        *,
        rows: list[dict[str, Any]],
        dimensions: list[str] | None = None,
        time_axis: str = "event_date",
        time_axis_kind: str = "date_field",
    ) -> tuple[MagicMock, dict[str, Any]]:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime(
            dimensions=dimensions,
            time_axis=time_axis,
            time_axis_kind=time_axis_kind,
        )
        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(rows=rows)
            result = run_observe_intent(runtime, _SESSION, params)
        return runtime, result

    def test_scalar_observe_commits_observation_payload(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                },
            },
            rows=[{"current_value": "42.5", "current_sessions": "7"}],
        )

        self.assertEqual(result["artifact_id"], _FAKE_ARTIFACT_ID)
        self.assertEqual(result["observation_type"], "scalar")
        self.assertEqual(result["metric"], "m1")
        self.assertEqual(result["value"], 42.5)
        self.assertEqual(result["scope"], {})
        self.assertEqual(result["analytical_metadata"]["row_count"], 7)
        self.assertEqual(result["analytical_metadata"]["quality_status"], "ready")
        args, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "observation")
        self.assertEqual(kwargs["step_type"], "observe")
        self.assertEqual(args[4]["observation_type"], "scalar")

    def test_observe_aoi_filter_is_consumed_as_scope_predicate(self) -> None:
        runtime = self._make_runtime()
        captured: dict[str, Any] = {}

        def _capture_scoped_query(
            session_id: str, resolved: Any, *, engine_type: str
        ) -> dict[str, Any]:
            captured["scope_predicate"] = resolved.scope.predicate
            return {
                "mode": resolved.time_scope.mode,
                "engine_type": engine_type,
                "analysis_time_expr": "event_date",
                "scope_predicate_filter": resolved.scope.predicate,
            }

        runtime.build_scoped_query.side_effect = _capture_scoped_query

        from marivo.runtime.intents.observe import run_observe_intent

        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(rows=[{"current_value": 42.0}])
            run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {
                        "field": "event_date",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "filter": {
                        "dialects": [
                            {"dialect": "ANSI_SQL", "expression": "region = 'US'"},
                        ]
                    },
                },
            )

        self.assertEqual(captured["scope_predicate"], "region = 'US'")
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[4]["scope"], {"predicate": "region = 'US'"})

    def test_time_series_observe_builds_dense_series_and_quality(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-04-01",
                    "end": "2026-04-03",
                },
                "granularity": "day",
            },
            rows=[{"bucket_start": "2026-04-01", "value": "10"}],
        )

        self.assertEqual(result["observation_type"], "time_series")
        compiled_call = runtime.compile_step.call_args.args[0]
        self.assertEqual(compiled_call.params["limit"], 1000)
        self.assertEqual(result["granularity"], "day")
        self.assertEqual(
            result["series"],
            [
                {"window": {"start": "2026-04-01", "end": "2026-04-02"}, "value": 10.0},
                {"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": None},
            ],
        )
        self.assertFalse(result["analytical_metadata"]["data_complete"])
        self.assertEqual(result["analytical_metadata"]["quality_status"], "needs_attention")

    def test_time_series_observe_marks_empty_dense_series_not_ready(self) -> None:
        _, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-04-01",
                    "end": "2026-04-03",
                },
                "granularity": "day",
            },
            rows=[],
        )

        self.assertEqual(
            result["series"],
            [
                {"window": {"start": "2026-04-01", "end": "2026-04-02"}, "value": None},
                {"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": None},
            ],
        )
        self.assertFalse(result["analytical_metadata"]["data_complete"])
        self.assertEqual(result["analytical_metadata"]["quality_status"], "not_ready")

    def test_time_series_observe_supports_quarter_and_year_buckets(self) -> None:
        cases = [
            (
                "quarter",
                "2026-01-01",
                "2026-07-01",
                [
                    {"window": {"start": "2026-01-01", "end": "2026-04-01"}, "value": None},
                    {"window": {"start": "2026-04-01", "end": "2026-07-01"}, "value": None},
                ],
            ),
            (
                "year",
                "2025-01-01",
                "2027-01-01",
                [
                    {"window": {"start": "2025-01-01", "end": "2026-01-01"}, "value": None},
                    {"window": {"start": "2026-01-01", "end": "2027-01-01"}, "value": None},
                ],
            ),
        ]
        for granularity, start, end, expected_series in cases:
            with self.subTest(granularity=granularity):
                _, result = self._run_observe(
                    {
                        "metric": "metric.m1",
                        "time_scope": {
                            "field": "event_date",
                            "start": start,
                            "end": end,
                        },
                        "granularity": granularity,
                    },
                    rows=[],
                )
                self.assertEqual(result["series"], expected_series)

    def test_observe_hour_granularity_uses_hour_internal_grain(self) -> None:
        runtime, _ = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01T00:00:00",
                    "end": "2024-01-01T02:00:00",
                },
                "granularity": "hour",
            },
            rows=[],
            time_axis="event_time",
            time_axis_kind="timestamp_field",
        )

        scoped_call = runtime.build_scoped_query.call_args.args[1]
        self.assertEqual(scoped_call.time_scope.grain, "hour")

    def test_segmented_observe_builds_sorted_segments(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-04-01",
                    "end": "2026-04-08",
                },
                "dimensions": ["platform"],
            },
            dimensions=["platform"],
            rows=[
                {"platform": "mobile", "current_value": 10.0},
                {"platform": "web", "current_value": 20.0},
            ],
        )

        self.assertEqual(result["observation_type"], "segmented")
        compiled_call = runtime.compile_step.call_args.args[0]
        self.assertEqual(compiled_call.params["limit"], 1000)
        self.assertEqual(result["dimensions"], ["platform"])
        self.assertEqual(
            result["segments"],
            [
                {"keys": {"platform": "web"}, "value": 20.0, "share": None},
                {"keys": {"platform": "mobile"}, "value": 10.0, "share": None},
            ],
        )
        self.assertEqual(result["analytical_metadata"]["quality_status"], "ready")

    def test_segmented_observe_empty_rows_is_not_ready(self) -> None:
        _, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-04-01",
                    "end": "2026-04-08",
                },
                "dimensions": ["platform"],
            },
            dimensions=["platform"],
            rows=[],
        )

        self.assertEqual(result["segments"], [])
        self.assertEqual(result["analytical_metadata"]["quality_status"], "not_ready")

    def test_segmented_observe_with_datetime_bounds_uses_day_internal_grain(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "log_date",
                    "start": "2026-05-15T00:00:00",
                    "end": "2026-05-16T00:00:00",
                },
                "dimensions": ["log_hour"],
            },
            dimensions=["log_hour"],
            rows=[
                {"log_hour": "09", "current_value": 10.0},
                {"log_hour": "10", "current_value": 20.0},
            ],
            time_axis="log_date",
            time_axis_kind="date_field",
        )

        scoped_call = runtime.build_scoped_query.call_args.args[1]
        self.assertEqual(scoped_call.time_scope.grain, "day")
        compiled_call = runtime.compile_step.call_args.args[0]
        self.assertEqual(compiled_call.params["time_scope"]["grain"], "day")
        self.assertEqual(result["observation_type"], "segmented")
        self.assertEqual(result["dimensions"], ["log_hour"])

    def test_observe_rejects_granularity_plus_dimensions(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime(dimensions=["platform"])
        with self.assertRaisesRegex(ValueError, "granularity and dimensions cannot both be set"):
            run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {
                        "field": "event_date",
                        "start": "2026-04-01",
                        "end": "2026-04-08",
                    },
                    "granularity": "day",
                    "dimensions": ["platform"],
                },
            )

    def test_observe_hour_granularity_accepts_date_only_range(self) -> None:
        _, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01",
                    "end": "2024-01-02",
                },
                "granularity": "hour",
            },
            rows=[],
            time_axis="event_time",
            time_axis_kind="timestamp_field",
        )

        self.assertEqual(result["observation_type"], "time_series")
        self.assertEqual(result["granularity"], "hour")

    def test_observe_malformed_aoi_filter_raises_invalid_argument(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        with self.assertRaisesRegex(
            ValueError,
            "observe: INVALID_ARGUMENT - filter.dialects must be non-empty",
        ):
            run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {
                        "field": "event_date",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "filter": {"dialects": []},
                },
            )
