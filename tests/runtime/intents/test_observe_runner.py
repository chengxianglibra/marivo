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
        runtime.commit_artifact_with_extraction.side_effect = lambda *args, **kwargs: kwargs.get(
            "artifact_id", _FAKE_ARTIFACT_ID
        )
        runtime.insert_step.return_value = None
        runtime.resolve_metric_execution_context.return_value = SimpleNamespace(
            table_name="src.metrics",
        )
        runtime.resolve_metric.return_value = SimpleNamespace(
            semantic_object={"header": {"decomposition_semantics": "sum"}}
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

    def _artifact(self, result: dict[str, Any]) -> dict[str, Any]:
        artifact = result["result"]
        self.assertIsInstance(artifact, dict)
        return artifact

    def _observe_metadata(self, result: dict[str, Any]) -> dict[str, Any]:
        metadata = result["product_metadata"]["observe_metadata"]
        self.assertIsInstance(metadata, dict)
        return metadata

    def _inserted_envelope(self, runtime: MagicMock) -> dict[str, Any]:
        envelope = runtime.insert_step.call_args.args[4]
        self.assertIsInstance(envelope, dict)
        return envelope

    def _assert_no_legacy_public_fields(self, artifact: dict[str, Any]) -> None:
        for field in (
            "schema_version",
            "observation_type",
            "metric",
            "time_scope",
            "scope",
            "predicate_filter_lineage",
            "unit",
            "series",
            "analytical_metadata",
            "execution_metadata",
        ):
            self.assertNotIn(field, artifact)

    def test_scalar_observe_commits_metric_frame_payload(self) -> None:
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

        artifact = self._artifact(result)
        self.assertEqual(result["artifact_id"], artifact["artifact_id"])
        self.assertEqual(artifact["artifact_family"], "metric_frame")
        self.assertEqual(artifact["shape"], "scalar")
        self.assertEqual(
            artifact["subject"],
            {
                "kind": "metric",
                "metric_ref": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2024-01-01T00:00:00Z",
                    "end": "2024-01-08T00:00:00Z",
                },
                "scope": {},
            },
        )
        self.assertEqual(artifact["axes"], [])
        self.assertEqual(artifact["payload"]["series"][0]["points"][0]["value"], 42.5)
        self._assert_no_legacy_public_fields(artifact)
        observe_metadata = self._observe_metadata(result)
        self.assertIsNone(observe_metadata["predicate_filter_lineage"])
        self.assertEqual(
            observe_metadata["analytical_metadata"],
            {
                "decomposition_semantics": "sum",
                "timezone": None,
                "data_complete": None,
                "quality_status": "ready",
                "row_count": 7,
                "sample_size": 7,
                "null_rate": None,
            },
        )
        self.assertEqual(observe_metadata["execution_metadata"]["engine"], "duckdb")
        self.assertEqual(
            observe_metadata["execution_metadata"]["query_hash"],
            "e004ebd5b5532a4b",
        )
        self.assertIsInstance(observe_metadata["execution_metadata"]["executed_at"], str)
        inserted_envelope = self._inserted_envelope(runtime)
        self.assertEqual(
            inserted_envelope["product_metadata"]["observe_metadata"],
            observe_metadata,
        )
        args, kwargs = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "metric_frame")
        self.assertEqual(kwargs["step_type"], "observe")
        self.assertEqual(args[4]["artifact_family"], "metric_frame")

    def test_scalar_observe_uses_exact_internal_window_without_grain(self) -> None:
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

        scoped_call = runtime.build_scoped_query.call_args.args[1]
        self.assertEqual(scoped_call.time_scope.boundary_mode, "exact")
        self.assertIsNone(scoped_call.time_scope.grain)
        compiled_call = runtime.compile_step.call_args.args[0]
        self.assertEqual(
            compiled_call.params["time_scope"],
            {
                "mode": "single_window",
                "boundary_mode": "exact",
                "current": {"start": "2024-01-01", "end": "2024-01-08"},
            },
        )
        artifact = self._artifact(result)
        self.assertEqual(artifact["shape"], "scalar")
        self.assertNotIn("granularity", artifact)

    def test_scalar_observe_midnight_datetime_window_is_not_treated_as_hour(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2024-01-01T00:00:00",
                    "end": "2024-01-08T00:00:00",
                },
            },
            rows=[{"current_value": "42.5", "current_sessions": "7"}],
        )

        scoped_call = runtime.build_scoped_query.call_args.args[1]
        self.assertEqual(scoped_call.time_scope.boundary_mode, "exact")
        self.assertIsNone(scoped_call.time_scope.grain)
        self.assertEqual(scoped_call.time_scope.current.start, "2024-01-01T00:00:00")
        self.assertEqual(scoped_call.time_scope.current.end, "2024-01-08T00:00:00")
        artifact = self._artifact(result)
        self.assertEqual(
            artifact["subject"]["time_scope"]["start"],
            "2024-01-01T00:00:00Z",
        )
        self.assertNotIn("granularity", artifact)

    def test_scalar_observe_subday_datetime_window_preserves_exact_boundaries(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_time",
                    "start": "2024-01-01T10:15:00",
                    "end": "2024-01-01T14:45:00",
                },
            },
            rows=[{"current_value": "42.5", "current_sessions": "7"}],
            time_axis="event_time",
            time_axis_kind="timestamp",
        )

        scoped_call = runtime.build_scoped_query.call_args.args[1]
        self.assertEqual(scoped_call.time_scope.boundary_mode, "exact")
        self.assertIsNone(scoped_call.time_scope.grain)
        self.assertEqual(scoped_call.time_scope.current.start, "2024-01-01T10:15:00")
        self.assertEqual(scoped_call.time_scope.current.end, "2024-01-01T14:45:00")
        artifact = self._artifact(result)
        self.assertEqual(
            artifact["subject"]["time_scope"]["start"],
            "2024-01-01T10:15:00Z",
        )
        self.assertNotIn("granularity", artifact)

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
        self.assertEqual(args[4]["subject"]["scope"], {"predicate": "region = 'US'"})

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

        artifact = self._artifact(result)
        self.assertEqual(artifact["shape"], "time_series")
        self.assertEqual(artifact["artifact_family"], "metric_frame")
        compiled_call = runtime.compile_step.call_args.args[0]
        self.assertEqual(compiled_call.params["limit"], 1000)
        self.assertEqual(artifact["axes"], [{"kind": "time", "grain": "day"}])
        self.assertEqual(
            artifact["payload"]["series"][0]["points"],
            [
                {
                    "window": {
                        "start": "2026-04-01T00:00:00Z",
                        "end": "2026-04-02T00:00:00Z",
                    },
                    "value": 10.0,
                },
                {
                    "window": {
                        "start": "2026-04-02T00:00:00Z",
                        "end": "2026-04-03T00:00:00Z",
                    },
                    "value": None,
                },
            ],
        )
        self._assert_no_legacy_public_fields(artifact)
        observe_metadata = self._observe_metadata(result)
        self.assertIsNone(observe_metadata["predicate_filter_lineage"])
        self.assertEqual(
            observe_metadata["analytical_metadata"],
            {
                "decomposition_semantics": "sum",
                "timezone": None,
                "data_complete": False,
                "quality_status": "needs_attention",
                "row_count": 1,
                "sample_size": 1,
                "null_rate": None,
            },
        )
        self.assertEqual(observe_metadata["execution_metadata"]["engine"], "duckdb")
        self.assertEqual(
            observe_metadata["execution_metadata"]["query_hash"],
            "e004ebd5b5532a4b",
        )
        self.assertIsInstance(observe_metadata["execution_metadata"]["executed_at"], str)
        inserted_envelope = self._inserted_envelope(runtime)
        self.assertEqual(
            inserted_envelope["product_metadata"]["observe_metadata"],
            observe_metadata,
        )

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

        artifact = self._artifact(result)
        self.assertEqual(
            artifact["payload"]["series"][0]["points"],
            [
                {
                    "window": {
                        "start": "2026-04-01T00:00:00Z",
                        "end": "2026-04-02T00:00:00Z",
                    },
                    "value": None,
                },
                {
                    "window": {
                        "start": "2026-04-02T00:00:00Z",
                        "end": "2026-04-03T00:00:00Z",
                    },
                    "value": None,
                },
            ],
        )

    def test_time_series_observe_supports_quarter_and_year_buckets(self) -> None:
        cases = [
            (
                "quarter",
                "2026-01-01",
                "2026-07-01",
                [
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-04-01T00:00:00Z",
                        },
                        "value": None,
                    },
                    {
                        "window": {
                            "start": "2026-04-01T00:00:00Z",
                            "end": "2026-07-01T00:00:00Z",
                        },
                        "value": None,
                    },
                ],
            ),
            (
                "year",
                "2025-01-01",
                "2027-01-01",
                [
                    {
                        "window": {
                            "start": "2025-01-01T00:00:00Z",
                            "end": "2026-01-01T00:00:00Z",
                        },
                        "value": None,
                    },
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2027-01-01T00:00:00Z",
                        },
                        "value": None,
                    },
                ],
            ),
        ]
        for granularity, start, end, expected_points in cases:
            with self.subTest(granularity=granularity):
                runtime, result = self._run_observe(
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
                scoped_call = runtime.build_scoped_query.call_args.args[1]
                self.assertEqual(scoped_call.time_scope.grain, granularity)
                artifact = self._artifact(result)
                self.assertEqual(artifact["payload"]["series"][0]["points"], expected_points)

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

    def test_observe_rejects_unaligned_time_series_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "must align to month grain"):
            self._run_observe(
                {
                    "metric": "metric.m1",
                    "time_scope": {
                        "field": "event_date",
                        "start": "2026-01-15",
                        "end": "2026-03-01",
                    },
                    "granularity": "month",
                },
                rows=[],
            )

    def test_segmented_observe_builds_sorted_series(self) -> None:
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

        artifact = self._artifact(result)
        self.assertEqual(artifact["shape"], "segmented")
        self.assertEqual(artifact["artifact_family"], "metric_frame")
        compiled_call = runtime.compile_step.call_args.args[0]
        self.assertEqual(compiled_call.params["limit"], 1000)
        self.assertEqual(artifact["axes"], [{"kind": "dimension", "name": "platform"}])
        self.assertEqual(
            artifact["payload"]["series"],
            [
                {"keys": {"platform": "web"}, "points": [{"value": 20.0}]},
                {"keys": {"platform": "mobile"}, "points": [{"value": 10.0}]},
            ],
        )
        self._assert_no_legacy_public_fields(artifact)

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

        artifact = self._artifact(result)
        self.assertEqual(artifact["payload"]["series"], [])

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
        artifact = self._artifact(result)
        self.assertEqual(artifact["shape"], "segmented")
        self.assertEqual(artifact["axes"], [{"kind": "dimension", "name": "log_hour"}])

    def test_observe_granularity_plus_dimensions_produces_panel(self) -> None:
        runtime, result = self._run_observe(
            {
                "metric": "metric.m1",
                "time_scope": {
                    "field": "event_date",
                    "start": "2026-04-01",
                    "end": "2026-04-03",
                },
                "granularity": "day",
                "dimensions": ["platform"],
            },
            dimensions=["platform"],
            rows=[
                {"bucket_start": "2026-04-01", "platform": "web", "value": "10"},
                {"bucket_start": "2026-04-01", "platform": "mobile", "value": "5"},
            ],
        )

        artifact = self._artifact(result)
        self.assertEqual(artifact["shape"], "panel")
        self.assertEqual(artifact["artifact_family"], "metric_frame")
        self.assertEqual(
            artifact["axes"],
            [
                {"kind": "time", "grain": "day"},
                {"kind": "dimension", "name": "platform"},
            ],
        )
        # Panel mode produces series grouped by dimension keys
        self.assertTrue(len(artifact["payload"]["series"]) >= 1)
        # Each series has keys and points with window+value
        for s in artifact["payload"]["series"]:
            self.assertIn("keys", s)
            self.assertIn("points", s)
            for pt in s["points"]:
                self.assertIn("window", pt)
                self.assertIn("value", pt)

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

        artifact = self._artifact(result)
        self.assertEqual(artifact["shape"], "time_series")
        self.assertEqual(artifact["axes"], [{"kind": "time", "grain": "hour"}])

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
