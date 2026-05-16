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
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

from marivo.core.semantic.calendar import CalendarAnnotationRow
from marivo.runtime.semantic.calendar_data_runtime import CalendarDataReadResult
from marivo.time_scope import ResolvedTimeAxis

# ── helpers ──────────────────────────────────────────────────────────────────

_SESSION = "sess_4c2_test"
_FAKE_ARTIFACT_ID = "art_fake4c2001"


def _make_compiled_mock() -> MagicMock:
    m = MagicMock()
    m.sql = "SELECT 1"
    m.params = []
    m.metadata = {}
    return m


def _set_resolved_time_axis(runtime: MagicMock, expr: str, *, kind: str = "date_field") -> None:
    def _resolve_time_axis(resolved: Any, **_: Any) -> ResolvedTimeAxis:
        axis = ResolvedTimeAxis(
            observation_grain=resolved.time_scope.grain,
            analysis_time_kind=kind,
            analysis_time_expr=expr,
        )
        resolved.resolved_time_axis = axis
        return axis

    runtime.resolve_windowed_query_time_axis.side_effect = _resolve_time_axis


def _scalar_observation(metric: str = "m1") -> dict[str, Any]:
    return {
        "observation_type": "scalar",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "value": 42.0,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": 10,
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
        "scope": {},
    }


def _time_series_observation(
    metric: str = "m1",
    *,
    granularity: str = "day",
    series: list[dict[str, Any]] | None = None,
    aligned_baseline_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if series is None:
        series = [
            {
                "window": {"start": "2024-01-01", "end": "2024-01-02"},
                "value": 10.0,
            },
            {
                "window": {"start": "2024-01-02", "end": "2024-01-03"},
                "value": 20.0,
            },
        ]
    return {
        "observation_type": "time_series",
        "metric": metric,
        "schema_version": "1.0",
        "unit": None,
        "granularity": granularity,
        "series": series,
        "analytical_metadata": {
            "aggregation_semantics": "sum",
            "additive_dimensions": ["country", "device", "date"],
            "row_count": len(series),
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-03"},
        "scope": {},
    }


class _FakeCalendarDataReader:
    def read_for_alignment(
        self,
        *,
        current_window: tuple[date, date],
        baseline_window: tuple[date, date],
        region_code: str | None = None,
    ) -> CalendarDataReadResult:
        return CalendarDataReadResult(
            annotation_rows=[
                CalendarAnnotationRow(
                    calendar_date=date(2025, 2, 20),
                    weekday=6,
                    holiday_group_id="spring_festival",
                    year_relative_holiday_key="spring_festival_d+3",
                ),
                CalendarAnnotationRow(
                    calendar_date=date(2026, 2, 20),
                    weekday=5,
                    holiday_group_id="spring_festival",
                    year_relative_holiday_key="spring_festival_d+3",
                ),
            ],
            resolved_calendar_source="calendar",
            resolved_calendar_version="cn_2026_v1",
            source_lineage={
                "table_fqn": "calendar",
                "calendar_version": "cn_2026_v1",
            },
        )


# ── observe ───────────────────────────────────────────────────────────────────


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


class TestCompareRunnerCommitPath(unittest.TestCase):
    """run_compare_intent must call _commit_artifact_with_extraction(step_type='compare')."""

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        return runtime

    def _run_scalar_compare(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime.resolve_artifact_for_ref.side_effect = [
            _scalar_observation("m1"),
            _scalar_observation("m1"),
        ]
        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION, "step_type": "observe"},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION, "step_type": "observe"},
        }
        return run_compare_intent(runtime, _SESSION, params)

    def test_compare_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar_compare(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_compare_passes_step_type_compare(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar_compare(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "compare")

    def test_compare_artifact_type_is_compare_artifact(self) -> None:
        runtime = self._make_runtime()
        self._run_scalar_compare(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "compare_artifact")

    def test_compare_type_non_normal_rejects_scalar_observations(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(ValueError, "compare_type 'yoy' requires time_series"):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "compare_type": "yoy",
                },
            )

    def test_compare_time_series_commits_time_series_delta(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.resolve_artifact_for_ref.side_effect = [
            _time_series_observation("m1"),
            _time_series_observation(
                "m1",
                series=[
                    {
                        "window": {"start": "2024-01-01", "end": "2024-01-02"},
                        "value": 8.0,
                    },
                    {
                        "window": {"start": "2024-01-02", "end": "2024-01-03"},
                        "value": 15.0,
                    },
                ],
            ),
        ]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
            },
        )

        self.assertEqual(result["comparison_type"], "time_series_delta")
        self.assertEqual(result["granularity"], "day")
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["summary_left_value"], 30.0)
        self.assertEqual(result["summary_right_value"], 23.0)
        self.assertEqual(result["analytical_metadata"]["pairing_basis"], "observed_series")
        self.assertEqual(
            result["analytical_metadata"]["pairing_rule"], "intersection_by_time_bucket"
        )

    def test_compare_type_yoy_aligns_time_series_by_baseline_window(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 10.0},
                {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 12.0},
            ],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-02-14", "end": "2026-02-16"}
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2025-02-14", "end": "2025-02-15"}, "value": 9.0},
                {"window": {"start": "2025-02-15", "end": "2025-02-16"}, "value": 11.0},
            ],
        )
        right["time_scope"] = {"kind": "range", "start": "2025-02-14", "end": "2025-02-16"}
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "yoy",
            },
        )

        self.assertEqual(
            result["analytical_metadata"]["pairing_basis"], "compare_type_calendar_alignment"
        )
        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "natural_date")
        self.assertEqual(result["analytical_metadata"]["compare_type"], "yoy")
        self.assertEqual(result["summary_left_value"], 22.0)
        self.assertEqual(result["summary_right_value"], 20.0)
        self.assertEqual(result["summary_absolute_delta"], 2.0)
        self.assertEqual(
            result["analytical_metadata"]["matched_left_time_scope"],
            {"kind": "range", "start": "2026-02-14", "end": "2026-02-16"},
        )
        self.assertEqual(
            result["analytical_metadata"]["matched_right_time_scope"],
            {"kind": "range", "start": "2025-02-14", "end": "2025-02-16"},
        )
        self.assertEqual(result["rows"][0]["left_value"], 10.0)
        self.assertEqual(result["rows"][0]["right_value"], 9.0)

    def test_compare_type_mom_aligns_time_series_to_previous_period(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 30.0},
                {"window": {"start": "2026-04-09", "end": "2026-04-10"}, "value": 40.0},
            ],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-10"}
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-06", "end": "2026-04-07"}, "value": 20.0},
                {"window": {"start": "2026-04-07", "end": "2026-04-08"}, "value": 25.0},
            ],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "mom",
            },
        )

        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "natural_date")
        self.assertEqual(result["summary_left_value"], 70.0)
        self.assertEqual(result["summary_right_value"], 45.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["baseline_window"],
            {"start": "2026-04-06", "end": "2026-04-08"},
        )

    def test_compare_type_wow_aligns_time_series_to_previous_week(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 30.0},
                {"window": {"start": "2026-04-09", "end": "2026-04-10"}, "value": 40.0},
            ],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-10"}
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-04-01", "end": "2026-04-02"}, "value": 20.0},
                {"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 25.0},
            ],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "wow",
            },
        )

        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "same_weekday")
        self.assertEqual(result["summary_right_value"], 45.0)
        self.assertEqual(
            result["analytical_metadata"]["matched_right_time_scope"],
            {"kind": "range", "start": "2026-04-01", "end": "2026-04-03"},
        )

    def test_compare_type_weekday_aligned_yoy_uses_nearest_weekday(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": 120.0}],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-02", "end": "2026-04-04"}
        right = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2025-04-03", "end": "2025-04-04"}, "value": 100.0}],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "weekday_aligned_yoy",
            },
        )

        self.assertEqual(result["analytical_metadata"]["pairing_rule"], "same_weekday")
        self.assertEqual(result["rows"][0]["right_value"], 100.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
                "pairing_reason"
            ],
            "same_weekday_nearest",
        )

    def test_compare_type_weekday_aligned_mom_falls_back_to_natural_date(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-04-08", "end": "2026-04-09"}, "value": 120.0}],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-04-08", "end": "2026-04-09"}
        right = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-04-07", "end": "2026-04-08"}, "value": 100.0}],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "weekday_aligned_mom",
            },
        )

        self.assertEqual(result["rows"][0]["right_value"], 100.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
                "pairing_reason"
            ],
            "natural_date_shift",
        )

    def test_compare_type_holiday_aligned_yoy_reads_calendar_data(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.calendar_data_reader = _FakeCalendarDataReader()
        left = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2026-02-20", "end": "2026-02-21"}, "value": 120.0}],
        )
        left["time_scope"] = {"kind": "range", "start": "2026-02-20", "end": "2026-02-21"}
        right = _time_series_observation(
            "m1",
            series=[{"window": {"start": "2025-02-20", "end": "2025-02-21"}, "value": 100.0}],
        )
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            runtime,
            _SESSION,
            {
                "left_ref": {
                    "step_id": "step_left",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "right_ref": {
                    "step_id": "step_right",
                    "session_id": _SESSION,
                    "step_type": "observe",
                },
                "compare_type": "holiday_aligned_yoy",
            },
        )

        self.assertEqual(result["rows"][0]["right_value"], 100.0)
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["resolved_calendar_version"],
            "cn_2026_v1",
        )
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["bucket_pairing"][0][
                "pairing_reason"
            ],
            "holiday_cluster",
        )

    def test_compare_type_holiday_aligned_yoy_requires_calendar_reader(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.calendar_data_reader = None
        left = _time_series_observation("m1")
        right = _time_series_observation("m1")
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(ValueError, "requires configured calendar data"):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "compare_type": "holiday_aligned_yoy",
                },
            )

    def test_compare_time_series_missing_granularity_fails(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        left = _time_series_observation("m1")
        right = _time_series_observation("m1")
        left["granularity"] = None
        runtime.resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError, "compare: NOT_COMPARABLE - time_series observations must include"
        ):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                },
            )

    def test_compare_time_series_empty_series_fails_before_commit(self) -> None:
        from marivo.runtime.intents.compare import run_compare_intent

        runtime = self._make_runtime()
        runtime.resolve_artifact_for_ref.side_effect = [
            _time_series_observation("m1", series=[]),
            _time_series_observation("m1", series=[]),
        ]

        with self.assertRaisesRegex(
            ValueError, "compare: NOT_COMPARABLE - no time-series buckets found"
        ):
            run_compare_intent(
                runtime,
                _SESSION,
                {
                    "left_ref": {
                        "step_id": "step_left",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                    "right_ref": {
                        "step_id": "step_right",
                        "session_id": _SESSION,
                        "step_type": "observe",
                    },
                },
            )
        runtime.commit_artifact_with_extraction.assert_not_called()

    # ── decompose ─────────────────────────────────────────────────────────────────


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
        from marivo.runtime.intents.decompose import run_decompose_intent

        if compare_artifact is None:
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
                    "left_time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "right_time_scope": {
                        "kind": "range",
                        "start": "2023-12-25",
                        "end": "2024-01-01",
                    },
                    "left_scope": {},
                    "right_scope": {},
                },
            }
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake_ref001"

        # Configure resolved_metric with real values so validation passes
        resolved_metric = MagicMock()
        resolved_metric.semantic_object = {
            "header": {
                "additive_dimensions": ["dim1", "time.default"],
                "aggregation_semantics": "ratio",
            },
            "payload": {
                "allowed_dimensions": ["dim1"],
                "dimensions": ["dim1"],
            },
        }
        resolved_metric.additive_dimensions = ["dim1", "time.default"]
        resolved_metric.aggregation_semantics = "ratio"
        resolved_metric.allowed_dimensions = ["dim1"]
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

        params = {
            "compare_ref": {"step_id": "step_compare", "session_id": _SESSION},
            "dimension": "dim1",
        }
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

    def test_decompose_time_series_delta_commits_summary_delta_decomposition(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(
            runtime,
            {
                "comparison_type": "time_series_delta",
                "metric": "m1",
                "unit": None,
                "granularity": "day",
                "summary_left_value": 120.0,
                "summary_right_value": 90.0,
                "summary_absolute_delta": 30.0,
                "summary_relative_delta": 0.333,
                "summary_direction": "increase",
                "lineage": {
                    "left_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                    "right_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
                },
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
                    "left_scope": {},
                    "right_scope": {},
                },
                "analytical_metadata": {
                    "pairing_basis": "calendar_aligned_observation_windows",
                    "pairing_rule": "calendar_aligned_bucket_pairing",
                    "matched_bucket_count": 7,
                    "dropped_left_buckets": 0,
                    "dropped_right_buckets": 0,
                    "matched_time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "matched_left_time_scope": {
                        "kind": "range",
                        "start": "2024-01-01",
                        "end": "2024-01-08",
                    },
                    "matched_right_time_scope": {
                        "kind": "range",
                        "start": "2023-01-01",
                        "end": "2023-01-08",
                    },
                },
            },
        )

        self.assertEqual(result["compare_ref"]["comparison_type"], "time_series_delta")
        self.assertEqual(result["left_ref"]["observation_type"], "time_series")
        self.assertEqual(result["right_ref"]["observation_type"], "time_series")
        self.assertEqual(result["scope_absolute_delta"], 30.0)
        self.assertEqual(
            result["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )
        self.assertEqual(result["analytical_metadata"]["source_granularity"], "day")
        self.assertEqual(
            result["analytical_metadata"]["source_pairing_basis"],
            "calendar_aligned_observation_windows",
        )


# ── detect ────────────────────────────────────────────────────────────────────


class TestDetectRunnerCommitPath(unittest.TestCase):
    """run_detect_intent must call _commit_artifact_with_extraction(step_type='detect')."""

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        runtime.make_provenance.return_value = {"query_hash": "testhash"}
        runtime.build_step_semantic_metadata.return_value = {}
        return runtime

    def _run_detect(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.detect import run_detect_intent

        runtime.core.normalize_intent_metric_ref.return_value = "m1"
        runtime.core.metric_name_from_ref.return_value = "m1"
        runtime.resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        runtime.resolve_metric_table.return_value = "src.metrics"
        runtime.resolve_metric_dimensions.return_value = []
        runtime.resolve_engine_for_session.return_value = (
            MagicMock(),
            "duckdb",
            {"metrics": "src.metrics"},
        )
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        runtime.build_scoped_query.return_value = None
        runtime.compile_step.return_value = _make_compiled_mock()

        params = {
            "metric": "m1",
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-31"},
            "granularity": "day",
            "strategy": "point_anomaly",
        }
        with patch("marivo.runtime.intents.detect.execute_compiled") as mock_exec:
            # 9 points with one spike (day 5 = 200) to produce ≥1 anomaly candidate.
            # mean≈111, std≈31, z(200)≈2.83 > balanced threshold 2.0.
            mock_exec.return_value.rows = [
                {"bucket_start": f"2024-01-{d:02d}", "value": 200.0 if d == 5 else 100.0}
                for d in range(1, 10)
            ]
            return run_detect_intent(runtime, _SESSION, params)

    def test_detect_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_detect(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_detect_passes_step_type_detect(self) -> None:
        runtime = self._make_runtime()
        self._run_detect(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "detect")

    def test_detect_artifact_type_is_anomaly_candidates(self) -> None:
        runtime = self._make_runtime()
        self._run_detect(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "anomaly_candidates")

    def test_detect_artifact_id_patched_in_result(self) -> None:
        # After _commit_artifact_with_extraction returns, detect.py patches artifact_id
        # into result["candidates"][*]["candidate_ref"]["artifact_ref"]["artifact_id"]
        # and result["artifact_id"].  Verify both are populated with the committed id.
        runtime = self._make_runtime()
        envelope = self._run_detect(runtime)
        result = envelope["result"]
        self.assertEqual(envelope["artifact_id"], _FAKE_ARTIFACT_ID)
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
    """run_correlate_intent must call commit_artifact_with_extraction(step_type='correlate')."""

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

    def _make_runtime(self) -> MagicMock:
        runtime = MagicMock()
        runtime.core = MagicMock()
        runtime.resolve_artifact_for_ref.side_effect = [
            self._make_ts_artifact("m1"),
            self._make_ts_artifact("m2"),
        ]
        runtime.new_step_id.return_value = "step_4c2_001"
        runtime.resolve_artifact_id_for_step.return_value = "art_left_001"
        runtime.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        runtime.insert_step.return_value = None
        return runtime

    def _run_correlate(self, runtime: MagicMock) -> dict[str, Any]:
        from marivo.runtime.intents.correlate import run_correlate_intent

        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION},
        }
        return run_correlate_intent(runtime, _SESSION, params)

    def test_correlate_calls_commit_artifact_with_extraction(self) -> None:
        runtime = self._make_runtime()
        self._run_correlate(runtime)
        runtime.commit_artifact_with_extraction.assert_called_once()

    def test_correlate_passes_step_type_correlate(self) -> None:
        runtime = self._make_runtime()
        self._run_correlate(runtime)
        _, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "correlate")

    def test_correlate_artifact_type_is_pairwise_ts_association(self) -> None:
        runtime = self._make_runtime()
        self._run_correlate(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "pairwise_time_series_association")


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
