from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from tests.runtime.intents._runner_fixtures import (
    _FAKE_ARTIFACT_ID,
    _SESSION,
    _make_compiled_mock,
    _set_resolved_time_axis,
)


class TestObserveRunnerCommitPath(unittest.TestCase):
    """run_observe_intent must call _commit_artifact_with_extraction(step_type='observe')."""

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        runtime.make_provenance.return_value = {"query_hash": "testhash"}
        return runtime

    def _run_scalar(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.resolve_metric_dimensions.return_value = []
        runtime.resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        runtime.build_scoped_query.return_value = None
        runtime.compile_step.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        }
        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = []
            return run_observe_intent(runtime, _SESSION, params)

    def test_observe_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_observe_passes_step_type_observe(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "observe")

    def test_observe_artifact_type_is_observation(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        # positional: session_id, step_id, artifact_type, name, content
        self.assertEqual(args[2], "observation")

    def test_observe_returns_artifact_id(self) -> None:
        runtime = self._make_runtime()
        result = self._run_scalar(runtime)
        self.assertEqual(result["artifact_id"], _FAKE_ARTIFACT_ID)

    def test_observe_omits_resolved_policy_summary_without_alignment(self) -> None:
        runtime = self._make_runtime()
        result = self._run_scalar(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        self.assertNotIn("resolved_policy_summary", result)
        self.assertNotIn("resolved_policy_summary", artifact_payload)

    def test_observe_hour_granularity_rejects_date_only_range(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        with self.assertRaisesRegex(
            ValueError, "time_scope.start must be a naive datetime string for hour grain"
        ):
            run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "m1",
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-02"},
                    "granularity": "hour",
                },
            )

    def test_observe_rejects_calendar_policy_ref(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        with self.assertRaisesRegex(
            ValueError,
            "calendar_policy_ref is no longer supported",
        ):
            run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "m1",
                    "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
                    "calendar_policy_ref": "calendar_policy.not_real",
                },
            )

    def test_observe_rejects_legacy_time_scope_kinds(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        for kind in ("snapshot_" + "now", "latest_" + "available", "as_" + "of"):
            with self.subTest(kind=kind):
                runtime = self._make_runtime()
                with self.assertRaisesRegex(
                    ValueError,
                    f"unsupported time_scope.kind='{kind}'",
                ):
                    run_observe_intent(
                        runtime,
                        _SESSION,
                        {
                            "metric": "m1",
                            "time_scope": {"kind": kind},
                        },
                    )

    def test_observe_segmented_omits_segmented_yoy_without_calendar_alignment(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.resolve_metric_dimensions.return_value = ["platform"]
        runtime.resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"src.metrics": "src.metrics"},
        )
        _set_resolved_time_axis(runtime, "event_date")
        runtime.build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        runtime.compile_step.return_value = _make_compiled_mock()

        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(rows=[{"platform": "web", "current_value": 120.0}])
            result = run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "dimensions": ["platform"],
                },
            )

        self.assertNotIn("segmented_yoy", result)

    def test_observe_time_series_without_rows_marks_backfilled_buckets_incomplete(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.resolve_metric_dimensions.return_value = []
        runtime.resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"src.metrics": "src.metrics"},
        )
        _set_resolved_time_axis(runtime, "event_date")
        runtime.build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-03"},
        }
        runtime.compile_step.return_value = _make_compiled_mock()

        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(rows=[])
            result = run_observe_intent(
                runtime,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-03"},
                    "granularity": "day",
                },
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

    def test_observe_hour_granularity_uses_hour_internal_grain(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        captured: dict[str, Any] = {}
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.resolve_metric_dimensions.return_value = []
        runtime.resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"src.metrics": "src.metrics"},
        )
        _set_resolved_time_axis(runtime, "event_time", kind="timestamp_field")

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

        runtime.build_scoped_query.side_effect = _capture_scoped_query
        runtime.compile_step.return_value = _make_compiled_mock()

        params = {
            "metric": "metric.m1",
            "time_scope": {
                "kind": "range",
                "start": "2024-01-01T00:00:00",
                "end": "2024-01-01T02:00:00",
            },
            "granularity": "hour",
        }
        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = []
            run_observe_intent(runtime, _SESSION, params)

        self.assertEqual(captured["grain"], "hour")

    def test_observe_aoi_filter_is_consumed_as_scope_predicate(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        captured: dict[str, Any] = {}
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.resolve_metric_dimensions.return_value = []
        runtime.resolve_engine.return_value = (
            MagicMock(),
            "duckdb",
            {"src.metrics": "src.metrics"},
        )
        _set_resolved_time_axis(runtime, "event_date")

        def _capture_scoped_query(
            session_id: str, resolved: Any, *, engine_type: str
        ) -> dict[str, Any]:
            captured["scope_predicate"] = resolved.scope.predicate
            return {
                "mode": resolved.time_scope.mode,
                "engine_type": engine_type,
                "analysis_time_expr": "event_date",
                "scope_predicate_filter": resolved.scope.predicate,
                "current": {
                    "start": resolved.time_scope.current.start,
                    "end": resolved.time_scope.current.end,
                },
            }

        runtime.build_scoped_query.side_effect = _capture_scoped_query
        runtime.compile_step.return_value = _make_compiled_mock()

        params = {
            "metric": "metric.m1",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "filter": {
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "region = 'US'"},
                ]
            },
        }
        with patch("marivo.runtime.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            run_observe_intent(runtime, _SESSION, params)

        self.assertEqual(captured["scope_predicate"], "region = 'US'")
        args, _ = runtime.commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        self.assertEqual(artifact_payload["scope"], {"predicate": "region = 'US'"})

    def test_observe_malformed_aoi_filter_raises_invalid_argument(self) -> None:
        from marivo.runtime.intents.observe import run_observe_intent

        runtime = self._make_runtime()
        runtime.core.normalize_intent_metric_ref.side_effect = lambda metric: metric
        runtime.core.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix(
            "metric."
        )
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric.return_value = None

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
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "filter": {"dialects": []},
                },
            )
