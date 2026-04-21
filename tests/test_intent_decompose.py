from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.intents.decompose import (
    _extract_date_range,
    _infer_compare_grain,
    _normalize_decompose_compare_input,
    _run_segmented_query,
    run_decompose_intent,
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


# ── P4: Decompose additivity gate tests ────────────────────────────────────────


def _make_compare_artifact(
    additivity_constraints: dict | None = None,
) -> dict:
    """Build a minimal scalar_delta compare artifact for decompose gate testing."""
    am: dict = {}
    if additivity_constraints is not None:
        am["additivity_constraints"] = additivity_constraints
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
            "left_time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-08"},
            "right_time_scope": {"kind": "range", "start": "2023-12-25", "end": "2024-01-01"},
        },
        "analytical_metadata": am,
    }


def _make_mock_metric(
    additivity_constraints: dict | None = None,
    primary_time_ref: str = "time.date",
    sample_kind: str = "numeric",
    dimensions: list[str] | None = None,
) -> MagicMock:
    mock = MagicMock()
    mock.additivity_constraints = additivity_constraints
    mock.primary_time_ref = primary_time_ref
    mock.sample_kind = sample_kind
    dims = dimensions or ["dimension.country"]
    mock.allowed_dimensions = dims
    mock.dimensions = dims
    mock.grain = "day"
    return mock


def _build_decompose_success_svc(
    additivity_constraints: dict | None,
    primary_time_ref: str = "time.date",
    sample_kind: str = "numeric",
    dimensions: list[str] | None = None,
) -> MagicMock:
    """Build a mock service that allows decompose to succeed through the execution path."""
    compare_artifact = _make_compare_artifact(additivity_constraints=additivity_constraints)
    mock_metric = _make_mock_metric(
        additivity_constraints=additivity_constraints,
        primary_time_ref=primary_time_ref,
        sample_kind=sample_kind,
        dimensions=dimensions,
    )
    svc = MagicMock()
    svc._resolve_artifact_for_ref.return_value = compare_artifact
    svc._resolve_artifact_id_for_step.return_value = "art_fake"
    svc.semantic_repository.resolve_metric.return_value = mock_metric
    svc.resolve_metric_dimensions.return_value = dimensions or ["dimension.country"]
    svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
    svc._resolve_metric_table.return_value = "src.metrics"
    svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"metrics": "src.metrics"})
    svc._compile_step_with_feedback.return_value = MagicMock()
    svc._build_scoped_query.return_value = None
    return svc


class DecomposeAdditivityGateTests(unittest.TestCase):
    """P4: Test decompose additivity gate — error payloads and artifact metadata."""

    # ── Error gate tests ────────────────────────────────────────────────────

    def test_none_policy_metric_decompose_fails(self) -> None:
        svc = MagicMock()
        compare_artifact = _make_compare_artifact(
            additivity_constraints={
                "dimension_policy": "none",
                "time_axis_policy": "non_additive",
            }
        )
        mock_metric = _make_mock_metric(
            additivity_constraints={
                "dimension_policy": "none",
                "time_axis_policy": "non_additive",
            }
        )
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"
        svc.semantic_repository.resolve_metric.return_value = mock_metric

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("ADDITIVITY_CONSTRAINT", msg)
        self.assertIn("blocker=", msg)

    def test_missing_additivity_constraints_fails(self) -> None:
        svc = MagicMock()
        compare_artifact = _make_compare_artifact()  # no additivity_constraints in am
        mock_metric = _make_mock_metric(additivity_constraints=None)
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"
        svc.semantic_repository.resolve_metric.return_value = mock_metric

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("ADDITIVITY_CONSTRAINT", msg)
        self.assertIn("ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING", msg)

    def test_empty_additivity_constraints_fails(self) -> None:
        svc = MagicMock()
        compare_artifact = _make_compare_artifact(additivity_constraints={})
        mock_metric = _make_mock_metric(additivity_constraints={})
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"
        svc.semantic_repository.resolve_metric.return_value = mock_metric

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("ADDITIVITY_CONSTRAINT", msg)
        self.assertIn("ADDITIVITY_CONSTRAINTS_DIMENSION_POLICY_MISSING", msg)

    def test_subset_metric_decompose_fails_on_disallowed_dimension(self) -> None:
        svc = MagicMock()
        compare_artifact = _make_compare_artifact(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            }
        )
        mock_metric = _make_mock_metric(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country", "dimension.product"],
        )
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"
        svc.semantic_repository.resolve_metric.return_value = mock_metric
        svc.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("ADDITIVITY_CONSTRAINT_DIMENSION_NOT_ALLOWED", msg)
        self.assertIn("dimension.product", msg)

    def test_error_payload_includes_disallowed_dimensions_as_list(self) -> None:
        svc = MagicMock()
        compare_artifact = _make_compare_artifact(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            }
        )
        mock_metric = _make_mock_metric(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country", "dimension.product"],
        )
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"
        svc.semantic_repository.resolve_metric.return_value = mock_metric
        svc.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("Disallowed:", msg)
        self.assertIn("['dimension.product']", msg)

    def test_error_payload_includes_time_axis_policy(self) -> None:
        svc = MagicMock()
        compare_artifact = _make_compare_artifact(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            }
        )
        mock_metric = _make_mock_metric(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country", "dimension.product"],
        )
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake"
        svc.semantic_repository.resolve_metric.return_value = mock_metric
        svc.resolve_metric_dimensions.return_value = ["dimension.country", "dimension.product"]

        with self.assertRaises(ValueError) as ctx:
            run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.product",
                },
            )
        msg = str(ctx.exception)
        self.assertIn("time_axis_policy='non_additive'", msg)

    # ── Success path + artifact metadata tests ─────────────────────────────

    def test_all_policy_metric_decompose_succeeds(self) -> None:
        svc = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "all",
                "time_axis_policy": "additive",
            },
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertIn("rows", result)

    def test_subset_metric_decompose_succeeds_on_allowed_dimension(self) -> None:
        svc = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        self.assertIn("rows", result)

    def test_artifact_metadata_includes_dimension_policy_top_level(self) -> None:
        svc = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        am = result["analytical_metadata"]
        self.assertEqual(am["dimension_policy"], "subset")

    def test_artifact_metadata_includes_decomposition_constraint(self) -> None:
        # subset → "dimension_must_be_allowed"
        svc = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        am = result["analytical_metadata"]
        self.assertEqual(am["decomposition_constraint"], "dimension_must_be_allowed")

        # all → "all_dimensions_allowed"
        svc2 = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "all",
                "time_axis_policy": "additive",
            },
            dimensions=["dimension.country"],
        )
        mock_result2 = MagicMock()
        mock_result2.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result2.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result2):
            result2 = run_decompose_intent(
                svc2,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        am2 = result2["analytical_metadata"]
        self.assertEqual(am2["decomposition_constraint"], "all_dimensions_allowed")

    def test_artifact_metadata_includes_allowed_dimension_basis(self) -> None:
        svc = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "subset",
                "time_axis_policy": "non_additive",
                "additive_dimensions": ["dimension.country"],
            },
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        basis = result["analytical_metadata"]["allowed_dimension_basis"]
        self.assertEqual(basis["dimension"], "dimension.country")
        self.assertEqual(basis["basis"], "additive_dimensions_list")

    def test_artifact_metadata_includes_time_boundary_constraint(self) -> None:
        svc = _build_decompose_success_svc(
            additivity_constraints={
                "dimension_policy": "all",
                "time_axis_policy": "additive",
            },
            dimensions=["dimension.country"],
        )
        mock_result = MagicMock()
        mock_result.rows = [{"dimension.country": "US", "current_value": 50.0}]
        mock_result.metadata.get.return_value = None

        with patch("app.intents.decompose.execute_compiled", return_value=mock_result):
            result = run_decompose_intent(
                svc,
                "session_1",
                {
                    "compare_ref": {"step_id": "step_compare", "session_id": "session_1"},
                    "dimension": "dimension.country",
                },
            )
        tbc = result["analytical_metadata"]["time_boundary_constraint"]
        self.assertEqual(tbc["scope"], "frozen_compare_window")
        self.assertFalse(tbc["time_rollup_implied"])
