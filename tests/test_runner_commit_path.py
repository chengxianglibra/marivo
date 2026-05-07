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

from app.service import SemanticLayerService
from app.time_scope import ResolvedTimeAxis

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
    m.metadata = {}
    return m


def _make_compiled_mock_with_calendar_alignment() -> MagicMock:
    m = _make_compiled_mock()
    m.metadata = {
        "resolved_calendar_alignment": {
            "policy_ref": "calendar_policy.weekday_yoy",
            "comparison_basis": "yoy",
            "resolved_calendar_source": "calendar_data_cn_assembled",
            "resolved_calendar_version": "calendar_data_cn_2026q2_v1",
            "resolved_baseline_generation_rule": {
                "strategy": "previous_year",
                "offset_value": 1,
                "offset_unit": "year",
                "fixed_start": None,
                "fixed_end": None,
                "named_window_ref": None,
            },
            "current_window": {"start": "2026-04-01", "end": "2026-04-08"},
            "baseline_window": {"start": "2025-04-01", "end": "2025-04-08"},
            "bucket_pairing": [
                {
                    "current_bucket_start": "2026-04-01",
                    "baseline_bucket_start": "2025-04-02",
                    "pairing_reason": "same_weekday_nearest",
                    "shift_days": 1,
                    "issues": [],
                    "strictness_level": "strict",
                    "is_reused_baseline_bucket": False,
                }
            ],
            "rollup_safe": True,
            "coverage_summary": {
                "aligned_bucket_count": 1,
                "unpaired_bucket_count": 0,
                "aligned_ratio": 1.0,
            },
            "data_coverage_summary": {
                "expected_bucket_count": 1,
                "present_bucket_count": 1,
                "missing_bucket_count": 0,
                "coverage_ratio": 1.0,
                "aligned_expected_bucket_count": 1,
                "aligned_present_current_bucket_count": 1,
                "aligned_present_baseline_bucket_count": 1,
                "aligned_present_both_bucket_count": 1,
            },
            "comparability_warnings": [],
            "source_lineage": {
                "table_fqn": "calendar",
                "calendar_version": "cn_2026q2_v1",
            },
        }
    }
    return m


def _set_resolved_time_axis(svc: MagicMock, expr: str, *, kind: str = "date_field") -> None:
    def _resolve_time_axis(resolved: Any, **_: Any) -> ResolvedTimeAxis:
        axis = ResolvedTimeAxis(
            observation_grain=resolved.time_scope.grain,
            analysis_time_kind=kind,
            analysis_time_expr=expr,
        )
        resolved.resolved_time_axis = axis
        return axis

    svc._resolve_windowed_query_time_axis.side_effect = _resolve_time_axis


def _make_compiled_mock_with_holiday_only_calendar_alignment() -> MagicMock:
    m = _make_compiled_mock_with_calendar_alignment()
    m.metadata["resolved_calendar_alignment"]["policy_ref"] = "calendar_policy.calendar_yoy"
    m.metadata["resolved_calendar_alignment"]["source_lineage"] = {
        "table_fqn": "calendar",
        "calendar_version": "cn_public_holiday_2026_v1",
    }
    m.metadata["resolved_calendar_alignment"]["comparability_warnings"] = [
        "holiday_annotation_missing_fallback_used"
    ]
    return m


def _make_time_series_compiled_mock_with_calendar_alignment() -> MagicMock:
    m = _make_compiled_mock_with_calendar_alignment()
    m.sql = "SELECT bucket_start, value FROM series"
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
            "additivity_constraints": {"dimension_policy": "all", "time_axis_policy": "additive"},
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
            "additivity_constraints": {"dimension_policy": "all", "time_axis_policy": "additive"},
            "row_count": len(series),
        },
        "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-03"},
        "scope": {},
    }


def _resolved_policy_summary(
    *,
    policy_ref: str = "calendar_policy.weekday_yoy",
    comparison_basis: str = "yoy",
    resolved_calendar_source: str = "calendar_data_cn_assembled",
    resolved_calendar_version: str = "calendar_data_cn_2026q2_v1",
    current_window_start: str = "2026-04-01",
    current_window_end: str = "2026-04-08",
    baseline_window_start: str = "2025-04-01",
    baseline_window_end: str = "2025-04-08",
    current_bucket_start: str = "2026-04-01",
    baseline_bucket_start: str = "2025-04-02",
    aligned_bucket_count: int = 7,
    unpaired_bucket_count: int = 0,
    aligned_ratio: float = 1.0,
    expected_bucket_count: int = 7,
    present_bucket_count: int = 7,
    missing_bucket_count: int = 0,
    coverage_ratio: float = 1.0,
    comparability_warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "policy_ref": policy_ref,
        "comparison_basis": comparison_basis,
        "resolved_calendar_source": resolved_calendar_source,
        "resolved_calendar_version": resolved_calendar_version,
        "resolved_baseline_generation_rule": {
            "strategy": "previous_year",
            "offset_value": 1,
            "offset_unit": "year",
            "fixed_start": None,
            "fixed_end": None,
            "named_window_ref": None,
        },
        "current_window": {"start": current_window_start, "end": current_window_end},
        "baseline_window": {"start": baseline_window_start, "end": baseline_window_end},
        "bucket_pairing": [
            {
                "current_bucket_start": current_bucket_start,
                "baseline_bucket_start": baseline_bucket_start,
                "pairing_reason": "same_weekday_nearest",
                "shift_days": 1,
                "issues": [],
                "strictness_level": "strict",
                "is_reused_baseline_bucket": False,
            }
        ],
        "rollup_safe": True,
        "coverage_summary": {
            "aligned_bucket_count": aligned_bucket_count,
            "unpaired_bucket_count": unpaired_bucket_count,
            "aligned_ratio": aligned_ratio,
        },
        "data_coverage_summary": {
            "expected_bucket_count": expected_bucket_count,
            "present_bucket_count": present_bucket_count,
            "missing_bucket_count": missing_bucket_count,
            "coverage_ratio": coverage_ratio,
            "aligned_expected_bucket_count": expected_bucket_count,
            "aligned_present_current_bucket_count": present_bucket_count,
            "aligned_present_baseline_bucket_count": present_bucket_count,
            "aligned_present_both_bucket_count": present_bucket_count,
        },
        "comparability_warnings": list(comparability_warnings or []),
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

    def test_observe_includes_null_resolved_policy_summary_without_alignment(self) -> None:
        svc = _make_svc()
        result = self._run_scalar(svc)
        args, _ = svc._commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        self.assertIsNone(result["resolved_policy_summary"])
        self.assertIsNone(artifact_payload["resolved_policy_summary"])

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
        _set_resolved_time_axis(svc, "event_date")
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

    def test_observe_freezes_resolved_policy_summary_in_artifact(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "CAST(log_date AS DATE)")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        svc._compile_step_with_feedback.return_value = _make_compiled_mock_with_calendar_alignment()

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

        args, _ = svc._commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        self.assertEqual(
            result["resolved_policy_summary"],
            artifact_payload["resolved_policy_summary"],
        )
        self.assertEqual(
            result["resolved_policy_summary"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )
        self.assertEqual(result["resolved_policy_summary"]["comparison_basis"], "yoy")
        self.assertEqual(
            result["resolved_policy_summary"]["resolved_calendar_source"],
            "calendar_data_cn_assembled",
        )
        self.assertEqual(
            result["resolved_policy_summary"]["resolved_calendar_version"],
            "calendar_data_cn_2026q2_v1",
        )
        self.assertEqual(
            result["resolved_policy_summary"]["resolved_baseline_generation_rule"],
            {
                "strategy": "previous_year",
                "offset_value": 1,
                "offset_unit": "year",
                "fixed_start": None,
                "fixed_end": None,
                "named_window_ref": None,
            },
        )
        self.assertEqual(
            result["resolved_policy_summary"]["current_window"],
            {"start": "2026-04-01", "end": "2026-04-08"},
        )
        self.assertEqual(
            result["resolved_policy_summary"]["baseline_window"],
            {"start": "2025-04-01", "end": "2025-04-08"},
        )
        self.assertEqual(
            result["resolved_policy_summary"]["coverage_summary"],
            {
                "aligned_bucket_count": 1,
                "unpaired_bucket_count": 0,
                "aligned_ratio": 1.0,
            },
        )
        self.assertEqual(
            result["resolved_policy_summary"]["bucket_pairing"][0],
            {
                "current_bucket_start": "2026-04-01",
                "baseline_bucket_start": "2025-04-02",
                "pairing_reason": "same_weekday_nearest",
                "shift_days": 1,
                "issues": [],
                "strictness_level": "strict",
                "is_reused_baseline_bucket": False,
            },
        )
        self.assertTrue(result["resolved_policy_summary"]["rollup_safe"])
        self.assertEqual(result["resolved_policy_summary"]["comparability_warnings"], [])

    def test_observe_accepts_holiday_only_calendar_lineage(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        svc._compile_step_with_feedback.return_value = (
            _make_compiled_mock_with_holiday_only_calendar_alignment()
        )
        svc.build_step_semantic_metadata.side_effect = (
            SemanticLayerService.build_step_semantic_metadata.__get__(svc, SemanticLayerService)
        )
        svc._build_calendar_policy_binding.side_effect = (
            SemanticLayerService._build_calendar_policy_binding
        )

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.calendar_yoy",
                },
            )

        args, kwargs = svc._commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        insert_step_args, insert_step_kwargs = svc._insert_step.call_args
        semantic_metadata = insert_step_kwargs["semantic_metadata"]
        self.assertEqual(
            result["resolved_policy_summary"], artifact_payload["resolved_policy_summary"]
        )
        self.assertEqual(
            result["resolved_policy_summary"]["policy_ref"], "calendar_policy.calendar_yoy"
        )
        self.assertEqual(
            semantic_metadata["compile_context"]["calendar_policy_binding"]["source_lineage"],
            {
                "table_fqn": "calendar",
                "calendar_version": "cn_public_holiday_2026_v1",
            },
        )
        self.assertEqual(
            result["resolved_policy_summary"]["comparability_warnings"],
            ["holiday_annotation_missing_fallback_used"],
        )

    def test_observe_rejects_malformed_resolved_policy_summary_missing_field(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        compiled = _make_compiled_mock_with_calendar_alignment()
        del compiled.metadata["resolved_calendar_alignment"]["comparison_basis"]
        svc._compile_step_with_feedback.return_value = compiled

        with (
            patch("app.intents.observe.execute_compiled") as mock_exec,
            self.assertRaisesRegex(ValueError, "malformed resolved calendar alignment metadata"),
        ):
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

    def test_observe_rejects_malformed_resolved_policy_summary_extra_coverage_field(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        compiled = _make_compiled_mock_with_calendar_alignment()
        compiled.metadata["resolved_calendar_alignment"]["coverage_summary"][
            "total_bucket_count"
        ] = 1
        svc._compile_step_with_feedback.return_value = compiled

        with (
            patch("app.intents.observe.execute_compiled") as mock_exec,
            self.assertRaisesRegex(ValueError, "malformed resolved calendar alignment metadata"),
        ):
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

    def test_observe_rejects_malformed_resolved_policy_summary_bucket_pairing(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "CAST(log_date AS DATE)")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        compiled = _make_compiled_mock_with_calendar_alignment()
        del compiled.metadata["resolved_calendar_alignment"]["bucket_pairing"][0]["issues"]
        svc._compile_step_with_feedback.return_value = compiled

        with (
            patch("app.intents.observe.execute_compiled") as mock_exec,
            self.assertRaisesRegex(ValueError, "malformed resolved calendar alignment metadata"),
        ):
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

    def test_observe_rejects_malformed_resolved_policy_summary_inconsistent_coverage(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        compiled = _make_compiled_mock_with_calendar_alignment()
        compiled.metadata["resolved_calendar_alignment"]["coverage_summary"] = {
            "aligned_bucket_count": 1,
            "unpaired_bucket_count": 1,
            "aligned_ratio": 1.0,
        }
        svc._compile_step_with_feedback.return_value = compiled

        with (
            patch("app.intents.observe.execute_compiled") as mock_exec,
            self.assertRaisesRegex(ValueError, "malformed resolved calendar alignment metadata"),
        ):
            mock_exec.return_value.rows = [{"current_value": 42.0}]
            run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

    def test_observe_time_series_returns_aligned_baseline_and_yoy_series_for_day_grain(
        self,
    ) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_time", kind="timestamp_field")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        svc._compile_step_with_feedback.side_effect = [
            _make_time_series_compiled_mock_with_calendar_alignment(),
            _make_compiled_mock(),
        ]

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.side_effect = [
                MagicMock(
                    rows=[
                        {"bucket_start": "2026-04-01", "value": 120.0},
                    ]
                ),
                MagicMock(
                    rows=[
                        {"bucket_start": "2025-04-02", "value": 100.0},
                    ]
                ),
            ]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    "granularity": "day",
                },
            )

        self.assertEqual(
            result["resolved_policy_summary"]["policy_ref"], "calendar_policy.weekday_yoy"
        )
        self.assertEqual(
            result["aligned_baseline_series"],
            [
                {
                    "window": {"start": "2026-04-01", "end": "2026-04-02"},
                    "baseline_window": {"start": "2025-04-02", "end": "2025-04-03"},
                    "value": 100.0,
                }
            ],
        )
        self.assertEqual(
            result["yoy_series"],
            [
                {
                    "window": {"start": "2026-04-01", "end": "2026-04-02"},
                    "baseline_window": {"start": "2025-04-02", "end": "2025-04-03"},
                    "current_value": 120.0,
                    "baseline_value": 100.0,
                    "absolute_delta": 20.0,
                    "relative_delta": 0.2,
                }
            ],
        )

    def test_observe_segmented_returns_segmented_yoy_for_calendar_alignment(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = ["platform"]
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        svc._compile_step_with_feedback.return_value = _make_compiled_mock_with_calendar_alignment()

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(
                rows=[
                    {
                        "platform": "web",
                        "current_value": 120.0,
                        "baseline_value": 100.0,
                        "absolute_delta": 20.0,
                        "relative_delta": 0.2,
                    },
                    {
                        "platform": "app",
                        "current_value": 80.0,
                        "baseline_value": 100.0,
                        "absolute_delta": -20.0,
                        "relative_delta": -0.2,
                    },
                ]
            )
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    "dimensions": ["platform"],
                },
            )

        self.assertEqual(
            result["resolved_policy_summary"]["policy_ref"], "calendar_policy.weekday_yoy"
        )
        self.assertEqual(
            result["segments"],
            [
                {"keys": {"platform": "web"}, "value": 120.0, "share": None},
                {"keys": {"platform": "app"}, "value": 80.0, "share": None},
            ],
        )
        self.assertEqual(
            result["segmented_yoy"],
            [
                {
                    "keys": {"platform": "web"},
                    "current_value": 120.0,
                    "baseline_value": 100.0,
                    "absolute_delta": 20.0,
                    "relative_delta": 0.2,
                },
                {
                    "keys": {"platform": "app"},
                    "current_value": 80.0,
                    "baseline_value": 100.0,
                    "absolute_delta": -20.0,
                    "relative_delta": -0.2,
                },
            ],
        )

    def test_observe_segmented_omits_segmented_yoy_without_calendar_alignment(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = ["platform"]
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(rows=[{"platform": "web", "current_value": 120.0}])
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "dimensions": ["platform"],
                },
            )

        self.assertNotIn("segmented_yoy", result)

    def test_observe_time_series_rebuilds_baseline_scoped_query_for_partition_pruning(
        self,
    ) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "CAST(log_date AS DATE)")

        scoped_queries: list[dict[str, Any]] = []

        def _capture_scoped_query(
            session_id: str, resolved: Any, *, engine_type: str
        ) -> dict[str, Any]:
            scoped_query = {
                "mode": "single_window",
                "analysis_time_expr": "CAST(log_date AS DATE)",
                "analysis_time_kind": "date_field",
                "current": {
                    "start": resolved.time_scope.current.start,
                    "end": resolved.time_scope.current.end,
                },
                "partition_pruning_predicate": (
                    f"log_date >= '{resolved.time_scope.current.start}' "
                    f"AND log_date < '{resolved.time_scope.current.end}'"
                ),
            }
            scoped_queries.append(scoped_query)
            return scoped_query

        svc._build_scoped_query.side_effect = _capture_scoped_query
        svc._compile_step_with_feedback.side_effect = [
            _make_time_series_compiled_mock_with_calendar_alignment(),
            _make_compiled_mock(),
        ]

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.side_effect = [
                MagicMock(rows=[{"bucket_start": "2026-04-01", "value": 120.0}]),
                MagicMock(rows=[{"bucket_start": "2025-04-02", "value": 100.0}]),
            ]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    "granularity": "day",
                },
            )

        self.assertEqual(len(scoped_queries), 2)
        self.assertEqual(
            scoped_queries[0]["partition_pruning_predicate"],
            "log_date >= '2026-04-01' AND log_date < '2026-04-08'",
        )
        self.assertEqual(
            scoped_queries[1]["partition_pruning_predicate"],
            "log_date >= '2025-04-01' AND log_date < '2025-04-08'",
        )
        baseline_step_params = svc._compile_step_with_feedback.call_args_list[1].args[0].params
        self.assertEqual(
            baseline_step_params["scoped_query"]["partition_pruning_predicate"],
            "log_date >= '2025-04-01' AND log_date < '2025-04-08'",
        )
        self.assertEqual(result["aligned_baseline_series"][0]["value"], 100.0)
        self.assertEqual(
            result["resolved_policy_summary"]["data_coverage_summary"][
                "aligned_present_baseline_bucket_count"
            ],
            1,
        )

    def test_observe_time_series_backfills_missing_requested_bucket_and_records_data_coverage(
        self,
    ) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-03"},
        }
        svc._compile_step_with_feedback.side_effect = [
            _make_time_series_compiled_mock_with_calendar_alignment(),
            _make_compiled_mock(),
        ]

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.side_effect = [
                MagicMock(rows=[{"bucket_start": "2026-04-01", "value": 120.0}]),
                MagicMock(rows=[{"bucket_start": "2025-04-02", "value": 100.0}]),
            ]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-03"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    "granularity": "day",
                },
            )

        self.assertEqual(
            result["series"],
            [
                {"window": {"start": "2026-04-01", "end": "2026-04-02"}, "value": 120.0},
                {"window": {"start": "2026-04-02", "end": "2026-04-03"}, "value": None},
            ],
        )
        self.assertEqual(
            result["resolved_policy_summary"]["data_coverage_summary"],
            {
                "expected_bucket_count": 2,
                "present_bucket_count": 1,
                "missing_bucket_count": 1,
                "coverage_ratio": 0.5,
                "aligned_expected_bucket_count": 1,
                "aligned_present_current_bucket_count": 1,
                "aligned_present_baseline_bucket_count": 1,
                "aligned_present_both_bucket_count": 1,
            },
        )
        self.assertFalse(result["analytical_metadata"]["data_complete"])
        self.assertEqual(result["analytical_metadata"]["quality_status"], "needs_attention")

    def test_observe_time_series_sets_data_complete_true_when_all_requested_buckets_present(
        self,
    ) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-03"},
        }
        svc._compile_step_with_feedback.side_effect = [
            _make_time_series_compiled_mock_with_calendar_alignment(),
            _make_compiled_mock(),
        ]

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.side_effect = [
                MagicMock(
                    rows=[
                        {"bucket_start": "2026-04-01", "value": 120.0},
                        {"bucket_start": "2026-04-02", "value": 130.0},
                    ]
                ),
                MagicMock(rows=[{"bucket_start": "2025-04-02", "value": 100.0}]),
            ]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-03"},
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                    "granularity": "day",
                },
            )

        self.assertTrue(result["analytical_metadata"]["data_complete"])
        self.assertEqual(result["analytical_metadata"]["quality_status"], "ready")

    def test_observe_time_series_without_rows_marks_backfilled_buckets_incomplete(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_date")
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-03"},
        }
        svc._compile_step_with_feedback.return_value = _make_compiled_mock()

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value = MagicMock(rows=[])
            result = run_observe_intent(
                svc,
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
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        captured: dict[str, Any] = {}
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        _set_resolved_time_axis(svc, "event_time", kind="timestamp_field")

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

        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_value_sql_for_execution.return_value = "val"
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

    def test_observe_numeric_summary_forwards_and_freezes_calendar_policy(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        captured: dict[str, Any] = {}
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_value_sql_for_execution.return_value = "val"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        svc._resolve_windowed_query_time_axis.return_value = None
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }

        def _capture_compile(step: Any, *, engine_type: str, semantic_context: Any) -> MagicMock:
            captured["calendar_policy_ref"] = step.params.get("calendar_policy_ref")
            return _make_compiled_mock_with_calendar_alignment()

        svc._compile_step_with_feedback.side_effect = _capture_compile

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [
                {"n": 5, "mean": 10.0, "variance": 1.0, "std": 1.0, "min_val": 8.0, "max_val": 12.0}
            ]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "result_mode": "numeric_sample_summary",
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

        args, _ = svc._commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        self.assertEqual(captured["calendar_policy_ref"], "calendar_policy.weekday_yoy")
        self.assertEqual(
            result["resolved_policy_summary"],
            artifact_payload["resolved_policy_summary"],
        )
        self.assertEqual(
            result["resolved_policy_summary"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )

    def _run_rate_summary(self, svc: MagicMock) -> dict[str, Any]:
        from app.intents.observe import run_observe_intent

        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_value_sql_for_execution.return_value = "is_success"
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

    def test_observe_rate_summary_forwards_and_freezes_calendar_policy(self) -> None:
        from app.intents.observe import run_observe_intent

        svc = _make_svc()
        captured: dict[str, Any] = {}
        svc.normalize_intent_metric_ref.side_effect = lambda metric: metric
        svc.metric_name_from_ref.side_effect = lambda metric: metric.removeprefix("metric.")
        svc._resolve_metric_execution_context.return_value = MagicMock(table_name="src.metrics")
        svc.resolve_metric_sql_for_execution.return_value = "SUM(val)"
        svc.resolve_metric_value_sql_for_execution.return_value = "is_success"
        svc.resolve_metric_dimensions.return_value = []
        svc._resolve_engine.return_value = (MagicMock(), "duckdb", {"src.metrics": "src.metrics"})
        svc._resolve_windowed_query_time_axis.return_value = None
        svc._build_scoped_query.return_value = {
            "mode": "single_window",
            "analysis_time_expr": "event_date",
            "analysis_time_kind": "date_field",
            "current": {"start": "2026-04-01", "end": "2026-04-08"},
        }

        def _capture_compile(step: Any, *, engine_type: str, semantic_context: Any) -> MagicMock:
            captured["calendar_policy_ref"] = step.params.get("calendar_policy_ref")
            return _make_compiled_mock_with_calendar_alignment()

        svc._compile_step_with_feedback.side_effect = _capture_compile

        with patch("app.intents.observe.execute_compiled") as mock_exec:
            mock_exec.return_value.rows = [{"n": 100, "k": 50.0}]
            result = run_observe_intent(
                svc,
                _SESSION,
                {
                    "metric": "metric.m1",
                    "time_scope": {"kind": "range", "start": "2026-04-01", "end": "2026-04-08"},
                    "result_mode": "rate_sample_summary",
                    "calendar_policy_ref": "calendar_policy.weekday_yoy",
                },
            )

        args, _ = svc._commit_artifact_with_extraction.call_args
        artifact_payload = args[4]
        self.assertEqual(captured["calendar_policy_ref"], "calendar_policy.weekday_yoy")
        self.assertEqual(
            result["resolved_policy_summary"],
            artifact_payload["resolved_policy_summary"],
        )
        self.assertEqual(
            result["resolved_policy_summary"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )


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

    def test_compare_reuses_frozen_calendar_alignment_summary(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary()
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            svc,
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

        self.assertEqual(result["comparability"]["status"], "comparable")
        self.assertEqual(result["comparability"]["issues"], [])
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["reuse_source"],
            "observation_resolved_policy_summary",
        )
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["policy_ref"],
            "calendar_policy.weekday_yoy",
        )
        self.assertTrue(result["resolved_input_summary"]["calendar_alignment"]["rollup_safe"])
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["effective_coverage_summary"],
            {
                "aligned_bucket_count": 7,
                "unpaired_bucket_count": 0,
                "aligned_ratio": 1.0,
            },
        )

    def test_compare_requires_alignment_metadata_on_both_sides(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "calendar alignment metadata is missing on one observation",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_rejects_calendar_version_mismatch(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary()
        right["resolved_policy_summary"] = _resolved_policy_summary(
            resolved_calendar_version="calendar_data_cn_2026q2_v2"
        )
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "left and right observations freeze different calendar versions",
        ):
            run_compare_intent(
                svc,
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

    def test_calendar_version_mismatch_issue_keeps_structured_details(self) -> None:
        from app.intents.calendar_alignment_metadata import (
            resolve_calendar_alignment_reuse_for_intent,
        )

        summary = resolve_calendar_alignment_reuse_for_intent(
            intent_name="compare",
            left_resolved_policy_summary=_resolved_policy_summary(),
            right_resolved_policy_summary=_resolved_policy_summary(
                resolved_calendar_version="calendar_data_cn_2026q2_v2"
            ),
        )

        self.assertIsNone(summary["reuse_summary"])
        self.assertEqual(summary["fatal_message"], summary["issues"][0]["message"])
        self.assertEqual(summary["issues"][0]["code"], "calendar_version_mismatch")
        self.assertEqual(
            summary["issues"][0]["details"],
            {
                "field_name": "resolved_calendar_version",
                "left_value": "calendar_data_cn_2026q2_v1",
                "right_value": "calendar_data_cn_2026q2_v2",
            },
        )

    def test_compare_marks_needs_attention_for_upstream_calendar_warnings(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary(
            comparability_warnings=["fallback_applied"]
        )
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            svc,
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

        self.assertEqual(result["comparability"]["status"], "needs_attention")
        self.assertEqual(result["comparability"]["issues"][0]["code"], "fallback_applied")
        self.assertEqual(result["comparability"]["issues"][0]["severity"], "warning")
        self.assertEqual(
            result["comparability"]["issues"][0]["gate_family"],
            "comparability_gate",
        )
        self.assertFalse(result["comparability"]["issues"][0]["blocking"])
        self.assertIn(
            "calendar alignment required a fallback matcher",
            result["comparability"]["issues"][0]["message"],
        )
        self.assertIn(
            "Review whether the fallback alignment is acceptable",
            result["comparability"]["issues"][0]["message"],
        )

    def test_compare_marks_needs_attention_for_incomplete_calendar_coverage(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary(
            aligned_bucket_count=6,
            unpaired_bucket_count=1,
            aligned_ratio=6 / 7,
        )
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            svc,
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

        self.assertEqual(result["comparability"]["status"], "needs_attention")
        self.assertEqual(
            result["comparability"]["issues"][-1]["code"],
            "alignment_coverage_insufficient",
        )
        self.assertEqual(
            result["comparability"]["issues"][-1]["gate_family"],
            "comparability_gate",
        )
        self.assertFalse(result["comparability"]["issues"][-1]["blocking"])
        self.assertIn(
            "calendar bucket pairing coverage is incomplete",
            result["comparability"]["issues"][-1]["message"],
        )
        self.assertIn(
            "shrink the comparison window",
            result["comparability"]["issues"][-1]["message"],
        )
        self.assertEqual(
            result["comparability"]["issues"][-1]["details"]["effective_coverage_summary"],
            {
                "aligned_bucket_count": 6,
                "unpaired_bucket_count": 1,
                "aligned_ratio": 6 / 7,
            },
        )
        self.assertEqual(
            result["comparability"]["issues"][-1]["details"]["next_action_hint"],
            "shrink_window_or_complete_mapping",
        )
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"]["effective_coverage_summary"],
            {
                "aligned_bucket_count": 6,
                "unpaired_bucket_count": 1,
                "aligned_ratio": 6 / 7,
            },
        )

    def test_compare_warns_on_metric_data_coverage_without_relabeling_alignment_coverage(
        self,
    ) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 10.0},
                {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": None},
            ],
        )
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2024-01-01", "end": "2024-01-02"}, "value": 8.0},
                {"window": {"start": "2024-01-02", "end": "2024-01-03"}, "value": 9.0},
            ],
        )
        left["resolved_policy_summary"] = _resolved_policy_summary(
            expected_bucket_count=2,
            present_bucket_count=1,
            missing_bucket_count=1,
            coverage_ratio=0.5,
        )
        right["resolved_policy_summary"] = _resolved_policy_summary(
            expected_bucket_count=2,
            present_bucket_count=2,
            missing_bucket_count=0,
            coverage_ratio=1.0,
        )
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            svc,
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
                "mode": "time_series",
            },
        )

        self.assertEqual(result["comparability"]["status"], "needs_attention")
        self.assertIn(
            "metric_data_coverage_incomplete",
            [issue["code"] for issue in result["comparability"]["issues"]],
        )
        self.assertNotIn(
            "alignment_coverage_insufficient",
            [issue["code"] for issue in result["comparability"]["issues"]],
        )
        self.assertEqual(
            result["resolved_input_summary"]["calendar_alignment"][
                "effective_data_coverage_summary"
            ],
            {
                "expected_bucket_count": 2,
                "present_bucket_count": 1,
                "missing_bucket_count": 1,
                "coverage_ratio": 0.5,
                "aligned_expected_bucket_count": 2,
                "aligned_present_current_bucket_count": 1,
                "aligned_present_baseline_bucket_count": 1,
                "aligned_present_both_bucket_count": 1,
            },
        )

    def test_compare_rejects_unresolved_weekday_pairing_tie(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary(
            comparability_warnings=["weekday_pairing_tie"]
        )
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "compare: NOT_COMPARABLE - weekday alignment produced an unresolved tie",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_rejects_non_dict_resolved_policy_summary(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = "not-an-object"
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "compare: INVALID_ARGUMENT - malformed resolved calendar alignment metadata",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_rejects_missing_coverage_summary(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        malformed = _resolved_policy_summary()
        del malformed["coverage_summary"]
        left["resolved_policy_summary"] = malformed
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "compare: INVALID_ARGUMENT - malformed resolved calendar alignment metadata",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_rejects_non_string_warning_entry(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        malformed = _resolved_policy_summary()
        malformed["comparability_warnings"] = ["fallback_applied", 1]
        left["resolved_policy_summary"] = malformed
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "compare: INVALID_ARGUMENT - malformed resolved calendar alignment metadata",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_rejects_negative_bucket_counts(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary(
            aligned_bucket_count=-1,
            unpaired_bucket_count=0,
            aligned_ratio=0.0,
        )
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "compare: INVALID_ARGUMENT - malformed resolved calendar alignment metadata",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_rejects_inconsistent_coverage_summary(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _scalar_observation("m1")
        right = _scalar_observation("m1")
        left["resolved_policy_summary"] = _resolved_policy_summary(
            aligned_bucket_count=7,
            unpaired_bucket_count=1,
            aligned_ratio=1.0,
        )
        right["resolved_policy_summary"] = _resolved_policy_summary()
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError,
            "compare: INVALID_ARGUMENT - malformed resolved calendar alignment metadata",
        ):
            run_compare_intent(
                svc,
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

    def test_compare_time_series_commits_time_series_delta(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        svc._resolve_artifact_for_ref.side_effect = [
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
            svc,
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
                "mode": "time_series",
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

    def test_compare_time_series_reuses_calendar_aligned_pairing_for_summary_basis(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2026-02-14", "end": "2026-02-15"}, "value": 10.0},
                {"window": {"start": "2026-02-15", "end": "2026-02-16"}, "value": 12.0},
            ],
        )
        right = _time_series_observation(
            "m1",
            series=[
                {"window": {"start": "2025-02-14", "end": "2025-02-15"}, "value": 9.0},
                {"window": {"start": "2025-02-15", "end": "2025-02-16"}, "value": 11.0},
            ],
        )
        left["resolved_policy_summary"] = _resolved_policy_summary(
            current_window_start="2026-02-14",
            current_window_end="2026-02-21",
            baseline_window_start="2025-02-14",
            baseline_window_end="2025-02-21",
            current_bucket_start="2026-02-14",
            baseline_bucket_start="2025-02-14",
        )
        right["resolved_policy_summary"] = _resolved_policy_summary(
            current_window_start="2026-02-14",
            current_window_end="2026-02-21",
            baseline_window_start="2025-02-14",
            baseline_window_end="2025-02-21",
            current_bucket_start="2026-02-14",
            baseline_bucket_start="2025-02-14",
        )
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        result = run_compare_intent(
            svc,
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
                "mode": "time_series",
            },
        )

        self.assertEqual(
            result["analytical_metadata"]["pairing_basis"], "calendar_aligned_observation_windows"
        )
        self.assertEqual(
            result["analytical_metadata"]["pairing_rule"], "calendar_aligned_bucket_pairing"
        )
        self.assertEqual(result["summary_left_value"], 10.0)
        self.assertEqual(result["summary_right_value"], 9.0)
        self.assertEqual(result["summary_absolute_delta"], 1.0)
        self.assertEqual(
            result["analytical_metadata"]["matched_left_time_scope"],
            {"kind": "range", "start": "2026-02-14", "end": "2026-02-15"},
        )
        self.assertEqual(
            result["analytical_metadata"]["matched_right_time_scope"],
            {"kind": "range", "start": "2025-02-14", "end": "2025-02-15"},
        )
        self.assertEqual(result["rows"][0]["left_value"], 10.0)
        self.assertEqual(result["rows"][0]["right_value"], 9.0)

    def test_compare_time_series_missing_granularity_fails(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        left = _time_series_observation("m1")
        right = _time_series_observation("m1")
        left["granularity"] = None
        svc._resolve_artifact_for_ref.side_effect = [left, right]

        with self.assertRaisesRegex(
            ValueError, "compare: NOT_COMPARABLE - time_series observations must include"
        ):
            run_compare_intent(
                svc,
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
                    "mode": "time_series",
                },
            )

    def test_compare_time_series_empty_series_fails_before_commit(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        svc._resolve_artifact_for_ref.side_effect = [
            _time_series_observation("m1", series=[]),
            _time_series_observation("m1", series=[]),
        ]

        with self.assertRaisesRegex(
            ValueError, "compare: NOT_COMPARABLE - no time-series buckets found"
        ):
            run_compare_intent(
                svc,
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
                    "mode": "time_series",
                },
            )
        svc._commit_artifact_with_extraction.assert_not_called()

    def test_compare_time_series_mode_rejects_scalar_artifacts(self) -> None:
        from app.intents.compare import run_compare_intent

        svc = _make_svc()
        svc._resolve_artifact_for_ref.side_effect = [
            _scalar_observation("m1"),
            _scalar_observation("m1"),
        ]

        with self.assertRaisesRegex(ValueError, "mode='time_series'"):
            run_compare_intent(
                svc,
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
                    "mode": "time_series",
                },
            )


# ── decompose ─────────────────────────────────────────────────────────────────


class TestDecomposeRunnerCommitPath(unittest.TestCase):
    """run_decompose_intent must call _commit_artifact_with_extraction(step_type='decompose')."""

    def _run_decompose(
        self, svc: MagicMock, compare_artifact: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        from app.intents.decompose import run_decompose_intent

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
        svc._resolve_artifact_for_ref.return_value = compare_artifact
        svc._resolve_artifact_id_for_step.return_value = "art_fake_ref001"

        # Configure resolved_metric with real values so validation passes
        resolved_metric = MagicMock()
        resolved_metric.additivity_constraints = {
            "dimension_policy": "all",
            "time_axis_policy": "additive",
        }  # fully additive supports decompose
        resolved_metric.primary_time_ref = "time.default"
        resolved_metric.sample_kind = "rate"
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

    def test_decompose_time_series_delta_commits_summary_delta_decomposition(self) -> None:
        svc = _make_svc()
        result = self._run_decompose(
            svc,
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
            "time_scope": {"kind": "range", "start": "2024-01-01", "end": "2024-01-31"},
            "granularity": "day",
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

    def _make_core_and_ports(self) -> tuple[MagicMock, MagicMock]:
        core = MagicMock()
        ports = MagicMock()
        core.resolve_artifact_for_ref.side_effect = [
            self._make_ts_artifact("m1"),
            self._make_ts_artifact("m2"),
        ]
        core.new_step_id.return_value = "step_4c2_001"
        core.resolve_artifact_id_for_step.return_value = "art_left_001"
        core.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        core.insert_step.return_value = None
        return core, ports

    def _run_correlate(self, core: MagicMock, ports: MagicMock) -> dict[str, Any]:
        from app.intents.correlate import run_correlate_intent

        params = {
            "left_ref": {"step_id": "step_left", "session_id": _SESSION},
            "right_ref": {"step_id": "step_right", "session_id": _SESSION},
        }
        return run_correlate_intent(core, ports, _SESSION, params)

    def test_correlate_calls_commit_artifact_with_extraction(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_correlate(core, ports)
        core.commit_artifact_with_extraction.assert_called_once()

    def test_correlate_passes_step_type_correlate(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_correlate(core, ports)
        _, kwargs = core.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "correlate")

    def test_correlate_artifact_type_is_pairwise_ts_association(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_correlate(core, ports)
        args, _ = core.commit_artifact_with_extraction.call_args
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

    def _make_core_and_ports(self) -> tuple[MagicMock, MagicMock]:
        core = MagicMock()
        ports = MagicMock()
        left_id, right_id = "art_left001", "art_right001"
        core.resolve_artifact_with_id.side_effect = [
            (left_id, self._make_rate_artifact("m1")),
            (right_id, self._make_rate_artifact("m1")),
        ]
        core.new_step_id.return_value = "step_4c2_001"
        core.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        core.insert_step.return_value = None
        return core, ports

    def _run_test(self, core: MagicMock, ports: MagicMock) -> dict[str, Any]:
        from app.intents.test import run_test_intent

        left_id, right_id = "art_left001", "art_right001"
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
        return run_test_intent(core, ports, _SESSION, params)

    def test_test_calls_commit_artifact_with_extraction(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_test(core, ports)
        core.commit_artifact_with_extraction.assert_called_once()

    def test_test_passes_step_type_test(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_test(core, ports)
        _, kwargs = core.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "test")

    def test_test_artifact_type_is_hypothesis_test(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_test(core, ports)
        args, _ = core.commit_artifact_with_extraction.call_args
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

    def _make_core_and_ports(self) -> tuple[MagicMock, MagicMock]:
        core = MagicMock()
        ports = MagicMock()
        artifact_id = "art_ts001"
        core.resolve_artifact_with_id.return_value = (artifact_id, self._make_ts_artifact())
        core.new_step_id.return_value = "step_4c2_001"
        core.commit_artifact_with_extraction.return_value = _FAKE_ARTIFACT_ID
        core.insert_step.return_value = None
        return core, ports

    def _run_forecast(self, core: MagicMock, ports: MagicMock) -> dict[str, Any]:
        from app.intents.forecast import run_forecast_intent

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
        return run_forecast_intent(core, ports, _SESSION, params)

    def test_forecast_calls_commit_artifact_with_extraction(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_forecast(core, ports)
        core.commit_artifact_with_extraction.assert_called_once()

    def test_forecast_passes_step_type_forecast(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_forecast(core, ports)
        _, kwargs = core.commit_artifact_with_extraction.call_args
        self.assertEqual(kwargs.get("step_type"), "forecast")

    def test_forecast_artifact_type_is_forecast_series(self) -> None:
        core, ports = self._make_core_and_ports()
        self._run_forecast(core, ports)
        args, _ = core.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "forecast_series")
