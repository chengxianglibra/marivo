from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from marivo.contracts.errors import ExecutionError
from marivo.runtime.intents.decompose import (
    _extract_date_range,
    _infer_compare_grain,
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

    def test_time_series_compare_input_prefers_side_specific_matched_time_scopes(self) -> None:
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
                    "pairing_basis": "calendar_aligned_observation_windows",
                    "pairing_rule": "calendar_aligned_bucket_pairing",
                    "matched_left_time_scope": {
                        "kind": "range",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                    "matched_right_time_scope": {
                        "kind": "range",
                        "start": "2023-01-03",
                        "end": "2023-01-05",
                    },
                },
            }
        )

        self.assertEqual(
            normalized["left_time_scope"],
            {"kind": "range", "start": "2024-01-02", "end": "2024-01-04"},
        )
        self.assertEqual(
            normalized["right_time_scope"],
            {"kind": "range", "start": "2023-01-03", "end": "2023-01-05"},
        )
        self.assertEqual(
            normalized["analytical_metadata"]["source_pairing_basis"],
            "calendar_aligned_observation_windows",
        )
        self.assertEqual(
            normalized["analytical_metadata"]["source_pairing_rule"],
            "calendar_aligned_bucket_pairing",
        )

    def test_run_segmented_query_uses_hour_grain_with_datetime_boundaries(self) -> None:
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
            rows, query_hash = _run_segmented_query(
                _FakeRuntime(),
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


# ── P4: Decompose additivity gate tests ────────────────────────────────────────


def _make_compare_artifact(
    additive_dimensions: list[str] | None = None,
    time_scope_field: str = "time.date",
) -> dict:
    """Build a minimal scalar_delta compare artifact for decompose gate testing."""
    am: dict = {}
    if additive_dimensions is not None:
        am["additive_dimensions"] = additive_dimensions
    return {
        "comparison_type": "scalar_delta",
        "metric": "m1",
        "unit": None,
        "left_value": 100.0,
        "right_value": 90.0,
        "absolute_delta": 10.0,
        "relative_delta": 0.111,
        "direction": "increase",
        "lineage": {
            "left_source_ref": {"step_id": "step_obs_left", "session_id": "session_1"},
            "right_source_ref": {"step_id": "step_obs_right", "session_id": "session_1"},
        },
        "resolved_input_summary": {
            "left_time_scope": {
                "field": time_scope_field,
                "kind": "range",
                "start": "2024-01-01",
                "end": "2024-01-08",
            },
            "right_time_scope": {
                "field": time_scope_field,
                "kind": "range",
                "start": "2023-12-25",
                "end": "2024-01-01",
            },
        },
        "analytical_metadata": am,
    }


def _make_time_series_compare_artifact(
    additive_dimensions: list[str] | None = None,
) -> dict:
    """Build a minimal time_series_delta compare artifact for decompose gate testing."""
    am: dict = {}
    if additive_dimensions is not None:
        am["additive_dimensions"] = additive_dimensions
    return {
        "comparison_type": "time_series_delta",
        "metric": "m1",
        "unit": None,
        "summary_left_value": 100.0,
        "summary_right_value": 90.0,
        "summary_absolute_delta": 10.0,
        "summary_relative_delta": 0.111,
        "summary_direction": "increase",
        "granularity": "day",
        "lineage": {
            "left_source_ref": {"step_id": "step_obs_left", "session_id": "session_1"},
            "right_source_ref": {"step_id": "step_obs_right", "session_id": "session_1"},
        },
        "resolved_input_summary": {
            "left_time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
        },
        "analytical_metadata": am,
    }


def _make_mock_metric(
    additive_dimensions: list[str] | None = None,
    aggregation_semantics: str = "sum",
    dimensions: list[str] | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.additive_dimensions = additive_dimensions
    mock.aggregation_semantics = aggregation_semantics
    dims = dimensions or ["dimension.country"]
    mock.allowed_dimensions = dims
    mock.dimensions = dims
    mock.grain = "day"
    # Semantic object header carries additive_dimensions for the runtime path
    header: dict = {
        "aggregation_semantics": aggregation_semantics,
    }
    if additive_dimensions is not None:
        header["additive_dimensions"] = additive_dimensions
    mock.semantic_object = {"header": header}
    return mock


def _build_decompose_success_runtime(
    additive_dimensions: list[str] | None,
    aggregation_semantics: str = "sum",
    dimensions: list[str] | None = None,
    time_scope_field: str = "time.date",
) -> MagicMock:
    """Build mock runtime that allows decompose to succeed through the execution path."""
    compare_artifact = _make_compare_artifact(
        additive_dimensions=additive_dimensions, time_scope_field=time_scope_field
    )
    mock_metric = _make_mock_metric(
        additive_dimensions=additive_dimensions,
        aggregation_semantics=aggregation_semantics,
        dimensions=dimensions,
    )
    runtime = MagicMock()
    runtime.core = MagicMock()
    runtime.resolve_artifact_for_ref.return_value = compare_artifact
    runtime.resolve_artifact_id_for_step.return_value = "art_fake"
    runtime.resolve_metric.return_value = mock_metric
    runtime.resolve_metric_dimensions.return_value = dimensions or ["dimension.country"]
    runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"
    runtime.resolve_metric_table.return_value = "src.metrics"
    runtime.resolve_engine.return_value = (MagicMock(), "duckdb", {"metrics": "src.metrics"})
    runtime.compile_step.return_value = MagicMock()
    runtime.build_scoped_query.return_value = None
    return runtime


class DecomposeAdditivityGateTests(unittest.TestCase):
    """P4: Test decompose additivity gate — error payloads and artifact metadata."""

    # ── Error gate tests ────────────────────────────────────────────────────

    def test_empty_additive_dimensions_metric_decompose_fails(self) -> None:
        runtime = MagicMock()
        runtime.core = MagicMock()
        compare_artifact = _make_compare_artifact(additive_dimensions=[])
        mock_metric = _make_mock_metric(additive_dimensions=[])
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime.resolve_metric.return_value = mock_metric

        with self.assertRaises(ExecutionError) as ctx:
            run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        exc = ctx.exception
        self.assertEqual(exc.category, "compatibility")
        self.assertEqual(exc.code, "ADDITIVITY_CONSTRAINT")
        compat = exc.detail["compatibility_error"]
        self.assertIn("blocker", compat)

    def test_empty_additive_dimensions_fails(self) -> None:
        """Missing or empty additive_dimensions both result in non-additive gate failure."""
        # Case 1: no additive_dimensions key at all in artifact metadata
        runtime = MagicMock()
        runtime.core = MagicMock()
        compare_artifact = _make_compare_artifact()  # no additive_dimensions in am
        mock_metric = _make_mock_metric(additive_dimensions=None)
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime.resolve_metric.return_value = mock_metric

        with self.assertRaises(ExecutionError) as ctx:
            run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        exc = ctx.exception
        self.assertEqual(exc.category, "compatibility")
        self.assertEqual(exc.code, "ADDITIVITY_CONSTRAINT")
        compat = exc.detail["compatibility_error"]
        self.assertIn("ADDITIVITY_NONE", compat.get("blocker", ""))

        # Case 2: empty list = non-additive (same result)
        runtime2 = MagicMock()
        runtime2.core = MagicMock()
        compare_artifact2 = _make_compare_artifact(additive_dimensions=[])
        mock_metric2 = _make_mock_metric(additive_dimensions=[])
        runtime2.resolve_artifact_for_ref.return_value = compare_artifact2
        runtime2.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime2.resolve_metric.return_value = mock_metric2

        with self.assertRaises(ExecutionError) as ctx2:
            run_decompose_intent(
                runtime2,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        exc2 = ctx2.exception
        self.assertEqual(exc2.category, "compatibility")
        self.assertEqual(exc2.code, "ADDITIVITY_CONSTRAINT")
        compat2 = exc2.detail["compatibility_error"]
        self.assertIn("ADDITIVITY_NONE", compat2.get("blocker", ""))

    def test_subset_metric_decompose_fails_on_disallowed_dimension(self) -> None:
        runtime = MagicMock()
        runtime.core = MagicMock()
        compare_artifact = _make_compare_artifact(additive_dimensions=["dimension.country"])
        mock_metric = _make_mock_metric(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country", "dimension.product"],
        )
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime.resolve_metric.return_value = mock_metric
        runtime.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ExecutionError) as ctx:
            run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        exc = ctx.exception
        self.assertEqual(exc.category, "compatibility")
        self.assertEqual(exc.code, "ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED")
        compat = exc.detail["compatibility_error"]
        self.assertIn("dimension.product", compat.get("disallowed_dimensions", []))

    def test_error_payload_includes_disallowed_dimensions_as_list(self) -> None:
        runtime = MagicMock()
        runtime.core = MagicMock()
        compare_artifact = _make_compare_artifact(additive_dimensions=["dimension.country"])
        mock_metric = _make_mock_metric(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country", "dimension.product"],
        )
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime.resolve_metric.return_value = mock_metric
        runtime.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ExecutionError) as ctx:
            run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        exc = ctx.exception
        compat = exc.detail["compatibility_error"]
        self.assertEqual(compat["disallowed_dimensions"], ["dimension.product"])

    def test_error_payload_includes_additive_dimensions(self) -> None:
        runtime = MagicMock()
        runtime.core = MagicMock()
        compare_artifact = _make_compare_artifact(additive_dimensions=["dimension.country"])
        mock_metric = _make_mock_metric(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country", "dimension.product"],
        )
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime.resolve_metric.return_value = mock_metric
        runtime.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ExecutionError) as ctx:
            run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        exc = ctx.exception
        compat = exc.detail["compatibility_error"]
        self.assertEqual(compat["additive_dimensions"], ["dimension.country"])

    # ── Success path + artifact metadata tests ─────────────────────────────

    def test_all_additive_dimensions_metric_decompose_succeeds(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country", "time.date"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertIn("rows", result)

    def test_subset_metric_decompose_succeeds_on_allowed_dimension(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertIn("rows", result)

    def test_artifact_metadata_includes_additive_dimensions_top_level(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        am = result["analytical_metadata"]
        self.assertEqual(am["additive_dimensions"], ["dimension.country"])

    def test_artifact_metadata_includes_time_boundary_constraint(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country", "time.date"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        tbc = result["analytical_metadata"]["time_boundary_constraint"]
        self.assertEqual(tbc["scope"], "frozen_compare_window")
        self.assertFalse(tbc["time_rollup_implied"])

    # ── Frozen additive_dimensions tests ────────────────────────────────────

    def test_frozen_dimensions_prefer_compare_lineage_over_current_metric(self) -> None:
        """When compare artifact carries frozen additive_dimensions, decompose
        should use those (not the metric's current state) for the gate."""
        runtime = _build_decompose_success_runtime(
            # Metric's CURRENT state has only dimension.country
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country"],
        )
        # Override the compare artifact to carry FROZEN dimensions from lineage
        # that include dimension.country and time.date (the metric state when compare was run)
        frozen_dimensions = ["dimension.country", "time.date"]
        compare_artifact = _make_compare_artifact(additive_dimensions=frozen_dimensions)
        runtime.resolve_artifact_for_ref.return_value = compare_artifact

        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        am = result["analytical_metadata"]
        self.assertEqual(am["additive_dimensions_source"], "compare_artifact_lineage")
        self.assertEqual(am["additive_dimensions"], ["dimension.country", "time.date"])

    def test_frozen_empty_dimensions_blocks_decompose_even_if_metric_changed(self) -> None:
        """If compare artifact froze empty additive_dimensions but metric has since
        changed to have additive dimensions, decompose must still reject based on
        frozen dimensions."""
        runtime = MagicMock()
        runtime.core = MagicMock()
        compare_artifact = _make_compare_artifact(additive_dimensions=[])
        # Metric's CURRENT state has additive dimensions
        mock_metric = _make_mock_metric(
            additive_dimensions=["dimension.country", "time.date"],
        )
        runtime.resolve_artifact_for_ref.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake"
        runtime.resolve_metric.return_value = mock_metric

        with self.assertRaises(ExecutionError) as ctx:
            run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        exc = ctx.exception
        self.assertEqual(exc.category, "compatibility")
        self.assertEqual(exc.code, "ADDITIVITY_CONSTRAINT")
        compat = exc.detail["compatibility_error"]
        self.assertEqual(compat["gate_source"], "compare_artifact_lineage")

    def test_time_series_delta_compare_propagates_frozen_dimensions(self) -> None:
        """time_series_delta compare artifacts should also propagate frozen
        additive_dimensions through the decompose gate."""
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country"],
        )
        # Override compare artifact with time_series_delta + frozen dimensions
        frozen_dimensions = ["dimension.country", "time.date"]
        compare_artifact = _make_time_series_compare_artifact(additive_dimensions=frozen_dimensions)
        runtime.resolve_artifact_for_ref.return_value = compare_artifact

        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        am = result["analytical_metadata"]
        self.assertEqual(am["additive_dimensions_source"], "compare_artifact_lineage")
        self.assertEqual(am["additive_dimensions"], ["dimension.country", "time.date"])

    # ── time_rollup_allowed metadata tests ───────────────────────────────────

    def test_time_rollup_allowed_true_when_time_field_in_additive_dimensions(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country", "time.date"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertTrue(result["analytical_metadata"]["time_rollup_allowed"])

    def test_time_rollup_allowed_false_when_time_ref_not_in_additive_dimensions(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertFalse(result["analytical_metadata"]["time_rollup_allowed"])

    def test_subset_policy_time_rollup_false_when_time_ref_not_additive(self) -> None:
        runtime = _build_decompose_success_runtime(
            additive_dimensions=["dimension.country"],
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("marivo.runtime.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                runtime,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertFalse(result["analytical_metadata"]["time_rollup_allowed"])


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
