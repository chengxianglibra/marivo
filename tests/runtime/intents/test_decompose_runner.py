from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from marivo.contracts.generated import aoi
from marivo.runtime.intents.decompose import (
    _attribution_series_from_rows,
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


def _comparison_subject() -> dict[str, Any]:
    return {
        "kind": "comparison",
        "metric_ref": "metric.m1",
        "current": {
            "time_scope": {"field": "time", "start": "2024-01-01", "end": "2024-01-08"},
            "scope": {},
        },
        "baseline": {
            "time_scope": {"field": "time", "start": "2023-12-25", "end": "2024-01-01"},
            "scope": {},
        },
    }


def _scalar_delta_artifact(
    *,
    current_value: float = 100.0,
    baseline_value: float = 90.0,
    delta_abs: float = 10.0,
    delta_pct: float | None = None,
) -> dict[str, Any]:
    relative_delta = delta_pct if delta_pct is not None else delta_abs / baseline_value
    return {
        "artifact_family": "delta_frame",
        "shape": "scalar_delta",
        "capabilities": ["filterable", "decomposable"],
        "schema_version": "2.0",
        "metric": "m1",
        "metric_ref": "metric.m1",
        "axes": [],
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "current_value": current_value,
                            "baseline_value": baseline_value,
                            "delta_abs": delta_abs,
                            "delta_pct": relative_delta,
                            "direction": "increase",
                        }
                    ],
                }
            ],
            "scope": {
                "current_value": current_value,
                "baseline_value": baseline_value,
                "delta_abs": delta_abs,
                "delta_pct": relative_delta,
                "direction": "increase",
            },
        },
        "subject": _comparison_subject(),
    }


def _time_series_delta_artifact(
    *,
    points: list[dict[str, Any]],
    analytical_metadata: dict[str, Any] | None = None,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if scope is None:
        current_value = sum(float(point["current_value"]) for point in points)
        baseline_value = sum(float(point["baseline_value"]) for point in points)
        delta_abs = current_value - baseline_value
        scope = {
            "current_value": current_value,
            "baseline_value": baseline_value,
            "delta_abs": delta_abs,
            "delta_pct": delta_abs / baseline_value if baseline_value else None,
            "direction": "increase" if delta_abs > 0 else "decrease" if delta_abs < 0 else "flat",
        }
    return {
        "artifact_family": "delta_frame",
        "shape": "time_series_delta",
        "capabilities": ["sliceable", "filterable", "decomposable"],
        "schema_version": "2.0",
        "metric": "m1",
        "metric_ref": "metric.m1",
        "axes": [{"kind": "time", "grain": "day"}],
        "payload": {"series": [{"keys": {}, "points": points}], "scope": scope},
        "subject": {
            "kind": "comparison",
            "metric_ref": "metric.m1",
            "current": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                },
                "scope": {},
            },
            "baseline": {
                "time_scope": {
                    "field": "time",
                    "start": "2023-01-01",
                    "end": "2023-01-08",
                },
                "scope": {},
            },
        },
        "analytical_metadata": analytical_metadata or {},
    }


def _attribution_frame_canonical_subset(result: dict[str, Any]) -> dict[str, Any]:
    canonical_keys = {
        "artifact_id",
        "artifact_family",
        "shape",
        "subject",
        "axes",
        "measures",
        "capabilities",
        "lineage",
        "payload",
    }
    return {key: result[key] for key in canonical_keys}


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
        normalized = _normalize_decompose_compare_input(_scalar_delta_artifact())

        self.assertEqual(normalized["shape"], "scalar_delta")
        self.assertNotIn("comparison_type", normalized)
        self.assertEqual(normalized["scope_current_value"], 100.0)
        self.assertEqual(normalized["scope_baseline_value"], 90.0)
        self.assertEqual(normalized["scope_absolute_delta"], 10.0)
        self.assertEqual(normalized["source_observation_type"], "scalar")

    def test_scalar_delta_reads_payload_scope(self) -> None:
        normalized = _normalize_decompose_compare_input(
            _scalar_delta_artifact(
                current_value=50.0,
                baseline_value=40.0,
                delta_abs=10.0,
                delta_pct=0.25,
            )
        )

        self.assertEqual(normalized["scope_current_value"], 50.0)
        self.assertEqual(normalized["scope_baseline_value"], 40.0)
        self.assertEqual(normalized["scope_absolute_delta"], 10.0)

    def test_axes_determine_scalar_observation_type(self) -> None:
        normalized = _normalize_decompose_compare_input(_scalar_delta_artifact())

        self.assertEqual(normalized["source_observation_type"], "scalar")

    def test_axes_determine_time_series_observation_type(self) -> None:
        normalized = _normalize_decompose_compare_input(
            _time_series_delta_artifact(
                points=[
                    {
                        "window": {"start": "2024-01-01", "end": "2024-01-02"},
                        "current_value": 30.0,
                        "baseline_value": 23.0,
                        "delta_abs": 7.0,
                        "delta_pct": 7.0 / 23.0,
                        "direction": "increase",
                        "presence": "both",
                    }
                ],
                analytical_metadata={
                    "matched_bucket_count": 1,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                },
            )
        )

        self.assertEqual(normalized["source_observation_type"], "time_series")

    def test_time_series_compare_input_aggregates_from_series_points(self) -> None:
        normalized = _normalize_decompose_compare_input(
            _time_series_delta_artifact(
                points=[
                    {
                        "window": {"start": "2024-01-01", "end": "2024-01-02"},
                        "current_value": 20.0,
                        "baseline_value": 15.0,
                        "delta_abs": 5.0,
                        "delta_pct": 5.0 / 15.0,
                        "direction": "increase",
                        "presence": "both",
                    },
                    {
                        "window": {"start": "2024-01-02", "end": "2024-01-03"},
                        "current_value": 10.0,
                        "baseline_value": 8.0,
                        "delta_abs": 2.0,
                        "delta_pct": 2.0 / 8.0,
                        "direction": "increase",
                        "presence": "both",
                    },
                ],
                analytical_metadata={
                    "matched_bucket_count": 2,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                },
            )
        )

        self.assertNotIn("comparison_type", normalized)
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
            _time_series_delta_artifact(
                points=[
                    {
                        "window": {"start": "2024-01-02", "end": "2024-01-04"},
                        "current_value": 30.0,
                        "baseline_value": 23.0,
                        "delta_abs": 7.0,
                        "delta_pct": 7.0 / 23.0,
                        "direction": "increase",
                        "presence": "both",
                    }
                ],
                analytical_metadata={
                    "matched_bucket_count": 2,
                    "matched_time_scope": {
                        "field": "time",
                        "start": "2024-01-02",
                        "end": "2024-01-04",
                    },
                },
            )
        )

        self.assertNotIn("comparison_type", normalized)
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
            _time_series_delta_artifact(
                points=[
                    {
                        "window": {"start": "2024-01-02", "end": "2024-01-04"},
                        "current_value": 30.0,
                        "baseline_value": 23.0,
                        "delta_abs": 7.0,
                        "delta_pct": 7.0 / 23.0,
                        "direction": "increase",
                        "presence": "both",
                    }
                ],
                analytical_metadata={
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
            )
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

    def test_segmented_delta_same_dimension_normalizes_fast_path_rows(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "segmented_delta",
                "capabilities": ["sliceable", "filterable", "decomposable"],
                "metric_ref": "metric.revenue",
                "axes": [{"kind": "dimension", "name": "channel"}],
                "payload": {
                    "series": [
                        {
                            "keys": {"channel": "paid"},
                            "points": [
                                {
                                    "current_value": 70.0,
                                    "baseline_value": 58.0,
                                    "delta_abs": 12.0,
                                    "delta_pct": 12.0 / 58.0,
                                    "presence": "both",
                                }
                            ],
                        },
                        {
                            "keys": {"channel": "organic"},
                            "points": [
                                {
                                    "current_value": 50.0,
                                    "baseline_value": 42.0,
                                    "delta_abs": 8.0,
                                    "delta_pct": 8.0 / 42.0,
                                    "presence": "both",
                                }
                            ],
                        },
                    ],
                    "scope": {
                        "current_value": 120.0,
                        "baseline_value": 100.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.2,
                        "direction": "increase",
                    },
                },
                "subject": {
                    "kind": "comparison",
                    "metric_ref": "metric.revenue",
                    "current": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-08",
                            "end": "2024-01-15",
                        },
                        "scope": {},
                    },
                    "baseline": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-01",
                            "end": "2024-01-08",
                        },
                        "scope": {},
                    },
                },
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
                "analytical_metadata": {"series_complete": True},
            },
            requested_dimension="channel",
        )

        self.assertEqual(normalized["shape"], "segmented_delta")
        self.assertEqual(normalized["fast_path_dimension"], "channel")
        self.assertEqual(
            normalized["fast_path_rows"],
            [
                {
                    "key": "paid",
                    "current_value": 70.0,
                    "baseline_value": 58.0,
                    "absolute_contribution": 12.0,
                    "presence": "both",
                },
                {
                    "key": "organic",
                    "current_value": 50.0,
                    "baseline_value": 42.0,
                    "absolute_contribution": 8.0,
                    "presence": "both",
                },
            ],
        )

    def test_panel_delta_same_dimension_aggregates_fast_path_rows(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "panel_delta",
                "capabilities": ["sliceable", "filterable", "decomposable"],
                "metric_ref": "metric.revenue",
                "axes": [
                    {"kind": "time", "grain": "day"},
                    {"kind": "dimension", "name": "channel"},
                ],
                "payload": {
                    "series": [
                        {
                            "keys": {"channel": "paid"},
                            "points": [
                                {
                                    "window": {"start": "2024-01-08", "end": "2024-01-09"},
                                    "current_value": 30.0,
                                    "baseline_value": 20.0,
                                    "delta_abs": 10.0,
                                    "presence": "both",
                                },
                                {
                                    "window": {"start": "2024-01-09", "end": "2024-01-10"},
                                    "current_value": 40.0,
                                    "baseline_value": 38.0,
                                    "delta_abs": 2.0,
                                    "presence": "both",
                                },
                            ],
                        },
                        {
                            "keys": {"channel": "organic"},
                            "points": [
                                {
                                    "window": {"start": "2024-01-08", "end": "2024-01-09"},
                                    "current_value": 25.0,
                                    "baseline_value": 20.0,
                                    "delta_abs": 5.0,
                                    "presence": "both",
                                },
                                {
                                    "window": {"start": "2024-01-09", "end": "2024-01-10"},
                                    "current_value": 25.0,
                                    "baseline_value": 22.0,
                                    "delta_abs": 3.0,
                                    "presence": "both",
                                },
                            ],
                        },
                    ],
                    "scope": {
                        "current_value": 120.0,
                        "baseline_value": 100.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.2,
                        "direction": "increase",
                    },
                },
                "subject": {
                    "kind": "comparison",
                    "metric_ref": "metric.revenue",
                    "current": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-08",
                            "end": "2024-01-10",
                        },
                        "scope": {},
                    },
                    "baseline": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-01",
                            "end": "2024-01-03",
                        },
                        "scope": {},
                    },
                },
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
                "analytical_metadata": {"series_complete": True, "matched_bucket_count": 2},
            },
            requested_dimension="channel",
        )

        self.assertEqual(normalized["shape"], "panel_delta")
        self.assertEqual(normalized["fast_path_dimension"], "channel")
        self.assertEqual(
            normalized["fast_path_rows"],
            [
                {
                    "key": "paid",
                    "current_value": 70.0,
                    "baseline_value": 58.0,
                    "absolute_contribution": 12.0,
                    "presence": "both",
                },
                {
                    "key": "organic",
                    "current_value": 50.0,
                    "baseline_value": 42.0,
                    "absolute_contribution": 8.0,
                    "presence": "both",
                },
            ],
        )

    def test_segmented_delta_without_completeness_has_no_fast_path(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "segmented_delta",
                "capabilities": ["sliceable", "filterable", "decomposable"],
                "metric_ref": "metric.revenue",
                "axes": [{"kind": "dimension", "name": "channel"}],
                "payload": {
                    "series": [
                        {
                            "keys": {"channel": "paid"},
                            "points": [
                                {
                                    "current_value": 70.0,
                                    "baseline_value": 58.0,
                                    "delta_abs": 12.0,
                                }
                            ],
                        }
                    ],
                    "scope": {
                        "current_value": 120.0,
                        "baseline_value": 100.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.2,
                        "direction": "increase",
                    },
                },
                "subject": {
                    "kind": "comparison",
                    "metric_ref": "metric.revenue",
                    "current": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-08",
                            "end": "2024-01-15",
                        },
                        "scope": {},
                    },
                    "baseline": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-01",
                            "end": "2024-01-08",
                        },
                        "scope": {},
                    },
                },
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
                "analytical_metadata": {"series_complete": False},
            },
            requested_dimension="channel",
        )

        self.assertEqual(normalized["fast_path_dimension"], "channel")
        self.assertIsNone(normalized["fast_path_rows"])

    def test_segmented_delta_fast_path_infers_current_only_presence(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "segmented_delta",
                "capabilities": ["sliceable", "filterable", "decomposable"],
                "metric_ref": "metric.revenue",
                "axes": [{"kind": "dimension", "name": "channel"}],
                "payload": {
                    "series": [
                        {
                            "keys": {"channel": "paid"},
                            "points": [
                                {
                                    "current_value": 70.0,
                                    "baseline_value": None,
                                    "delta_abs": 70.0,
                                }
                            ],
                        }
                    ],
                    "scope": {
                        "current_value": 70.0,
                        "baseline_value": None,
                        "delta_abs": 70.0,
                        "delta_pct": None,
                        "direction": "increase",
                    },
                },
                "subject": _comparison_subject(),
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
                "analytical_metadata": {"series_complete": True},
            },
            requested_dimension="channel",
        )

        self.assertEqual(normalized["fast_path_rows"][0]["presence"], "current_only")

    def test_panel_delta_fast_path_computes_missing_delta_abs_from_values(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "panel_delta",
                "capabilities": ["sliceable", "filterable", "decomposable"],
                "metric_ref": "metric.revenue",
                "axes": [
                    {"kind": "time", "grain": "day"},
                    {"kind": "dimension", "name": "channel"},
                ],
                "payload": {
                    "series": [
                        {
                            "keys": {"channel": "paid"},
                            "points": [
                                {
                                    "current_value": 30.0,
                                    "baseline_value": 20.0,
                                }
                            ],
                        }
                    ],
                    "scope": {
                        "current_value": 30.0,
                        "baseline_value": 20.0,
                        "delta_abs": 10.0,
                        "delta_pct": 0.5,
                        "direction": "increase",
                    },
                },
                "subject": _comparison_subject(),
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
                "analytical_metadata": {"series_complete": True},
            },
            requested_dimension="channel",
        )

        self.assertEqual(normalized["fast_path_rows"][0]["absolute_contribution"], 10.0)

    def test_panel_delta_fast_path_infers_side_only_presence(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "panel_delta",
                "capabilities": ["sliceable", "filterable", "decomposable"],
                "metric_ref": "metric.revenue",
                "axes": [
                    {"kind": "time", "grain": "day"},
                    {"kind": "dimension", "name": "channel"},
                ],
                "payload": {
                    "series": [
                        {
                            "keys": {"channel": "paid"},
                            "points": [{"current_value": 30.0, "baseline_value": None}],
                        },
                        {
                            "keys": {"channel": "organic"},
                            "points": [{"current_value": None, "baseline_value": 22.0}],
                        },
                    ],
                    "scope": {
                        "current_value": 30.0,
                        "baseline_value": 22.0,
                        "delta_abs": 8.0,
                        "delta_pct": 8.0 / 22.0,
                        "direction": "increase",
                    },
                },
                "subject": _comparison_subject(),
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
                "analytical_metadata": {"series_complete": True},
            },
            requested_dimension="channel",
        )

        self.assertEqual(
            normalized["fast_path_rows"],
            [
                {
                    "key": "paid",
                    "current_value": 30.0,
                    "baseline_value": None,
                    "absolute_contribution": None,
                    "presence": "current_only",
                },
                {
                    "key": "organic",
                    "current_value": None,
                    "baseline_value": 22.0,
                    "absolute_contribution": None,
                    "presence": "baseline_only",
                },
            ],
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


class DecomposeDeltaFrameGuardTests(unittest.TestCase):
    def test_decompose_rejects_non_delta_frame_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "source artifact must be delta_frame"):
            _normalize_decompose_compare_input(
                {
                    "artifact_family": "metric_frame",
                    "shape": "scalar",
                    "capabilities": ["comparable"],
                    "axes": [],
                    "payload": {"series": []},
                }
            )

    def test_decompose_rejects_delta_frame_without_decomposable_capability(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires decomposable capability"):
            _normalize_decompose_compare_input(
                {
                    "artifact_family": "delta_frame",
                    "shape": "scalar_delta",
                    "capabilities": ["filterable"],
                    "axes": [],
                    "payload": {"series": []},
                }
            )

    def test_decompose_rejects_delta_frame_without_payload_scope(self) -> None:
        artifact = _scalar_delta_artifact()
        artifact["payload"].pop("scope")

        with self.assertRaisesRegex(ValueError, "payload.scope"):
            _normalize_decompose_compare_input(artifact)

    def test_decompose_rejects_delta_frame_scope_missing_required_field(self) -> None:
        artifact = _scalar_delta_artifact()
        artifact["payload"]["scope"].pop("delta_abs")

        with self.assertRaisesRegex(ValueError, "payload.scope missing field"):
            _normalize_decompose_compare_input(artifact)

    def test_decompose_rejects_malformed_axes_as_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "axes must be a list"):
            _normalize_decompose_compare_input(
                {
                    **_scalar_delta_artifact(),
                    "axes": {"kind": "time"},
                }
            )

    def test_decompose_rejects_time_series_delta_with_two_time_axes(self) -> None:
        with self.assertRaisesRegex(ValueError, "time_series_delta requires exactly one time axis"):
            _normalize_decompose_compare_input(
                {
                    **_time_series_delta_artifact(
                        points=[
                            {
                                "window": {"start": "2024-01-01", "end": "2024-01-02"},
                                "current_value": 30.0,
                                "baseline_value": 23.0,
                                "delta_abs": 7.0,
                                "delta_pct": 7.0 / 23.0,
                                "direction": "increase",
                                "presence": "both",
                            }
                        ]
                    ),
                    "axes": [
                        {"kind": "time", "grain": "day"},
                        {"kind": "time", "grain": "week"},
                    ],
                }
            )

    def test_decompose_rejects_delta_frame_with_only_top_level_series(self) -> None:
        artifact = _scalar_delta_artifact()
        top_level_series = artifact["payload"]["series"]
        artifact.pop("payload")
        artifact["series"] = top_level_series

        with self.assertRaisesRegex(ValueError, "payload.series"):
            _normalize_decompose_compare_input(artifact)

    def test_decompose_rejects_payload_series_entry_without_points(self) -> None:
        with self.assertRaisesRegex(ValueError, "series entry points must be a non-empty list"):
            _normalize_decompose_compare_input(
                {
                    **_scalar_delta_artifact(),
                    "payload": {"series": [{"keys": {}}]},
                }
            )

    def test_decompose_accepts_scalar_delta_frame_family(self) -> None:
        normalized = _normalize_decompose_compare_input(
            {
                "artifact_family": "delta_frame",
                "shape": "scalar_delta",
                "capabilities": ["filterable", "decomposable"],
                "metric": "m1",
                "metric_ref": "metric.m1",
                "axes": [],
                "payload": {
                    "series": [
                        {
                            "keys": {},
                            "points": [
                                {
                                    "current_value": 100.0,
                                    "baseline_value": 80.0,
                                    "delta_abs": 20.0,
                                    "delta_pct": 0.25,
                                    "direction": "increase",
                                }
                            ],
                        }
                    ],
                    "scope": {
                        "current_value": 100.0,
                        "baseline_value": 80.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.25,
                        "direction": "increase",
                    },
                },
                "subject": {
                    "kind": "comparison",
                    "metric_ref": "metric.m1",
                    "current": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-08",
                            "end": "2024-01-15",
                        },
                        "scope": {},
                    },
                    "baseline": {
                        "time_scope": {
                            "field": "time",
                            "start": "2024-01-01",
                            "end": "2024-01-08",
                        },
                        "scope": {},
                    },
                },
                "lineage": {
                    "current_source_ref": {"step_id": "step_current"},
                    "baseline_source_ref": {"step_id": "step_baseline"},
                },
            }
        )

        self.assertEqual(normalized["shape"], "scalar_delta")
        self.assertEqual(normalized["scope_absolute_delta"], 20.0)
        self.assertEqual(normalized["current_time_scope"]["start"], "2024-01-08")


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
        self,
        runtime: MagicMock,
        compare_artifact: dict[str, Any] | None = None,
        *,
        dimension: str = "dim1",
    ) -> dict[str, Any]:

        if compare_artifact is None:
            compare_artifact = {
                **_scalar_delta_artifact(),
                "unit": None,
                "lineage": {
                    "current_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                    "baseline_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
                },
            }
        runtime.resolve_artifact_by_id.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake_ref001"

        # Configure resolved_metric with real values so validation passes
        resolved_metric = MagicMock()
        resolved_metric.semantic_object = {
            "header": {
                "decomposition_semantics": "ratio",
            },
            "payload": {
                "dimensions": [dimension],
            },
        }
        resolved_metric.decomposition_semantics = "ratio"
        resolved_metric.dimensions = [dimension]
        resolved_metric.grain = "day"
        runtime.resolve_metric.return_value = resolved_metric
        runtime.resolve_metric_dimensions.return_value = [dimension]
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

        params = {"compare_artifact_id": "art_compare", "dimension": dimension}
        with patch("marivo.runtime.intents.decompose.execute_compiled") as mock_exec:
            # Return 1 row for both left and right segmented queries.
            # Configure metadata.get() to return None so the query_hash branch skips.
            mock_result = MagicMock()
            mock_result.rows = [{dimension: "segment_a", "current_value": 50.0}]
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

    def test_decompose_artifact_type_is_attribution_frame(self) -> None:
        runtime = self._make_runtime()
        self._run_decompose(runtime)
        args, _ = runtime.commit_artifact_with_extraction.call_args
        self.assertEqual(args[2], "attribution_frame")

    def test_decompose_output_has_schema_version_2(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertEqual(result["schema_version"], "2.0")

    def test_decompose_output_is_attribution_frame(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime, dimension="channel")

        self.assertEqual(result["artifact_family"], "attribution_frame")
        self.assertEqual(result["shape"], "ranked_contributions")
        self.assertEqual(result["axes"], [{"kind": "dimension", "name": "channel"}])
        self.assertEqual(
            result["measures"],
            [
                {"id": "contribution_abs", "value_type": "number", "nullable": False},
                {"id": "contribution_pct", "value_type": "number", "nullable": True},
            ],
        )
        self.assertEqual(result["payload"]["scope"]["delta_abs"], 10.0)
        self.assertEqual(
            result["payload"]["series"][0]["points"][0]["contribution_abs"],
            0.0,
        )
        self.assertEqual(
            result["payload"]["series"][0]["points"][0]["contribution_pct"],
            0.0,
        )
        self.assertTrue(result["artifact_id"])

    def test_decompose_output_canonical_subset_validates_as_attribution_frame(self) -> None:
        runtime = self._make_runtime()
        compare_artifact = {
            **_scalar_delta_artifact(),
            "subject": {
                "kind": "comparison",
                "metric_ref": "metric.m1",
                "current": {
                    "time_scope": {
                        "field": "time",
                        "start": "2024-01-01T00:00:00Z",
                        "end": "2024-01-08T00:00:00Z",
                    },
                    "scope": {},
                },
                "baseline": {
                    "time_scope": {
                        "field": "time",
                        "start": "2023-12-25T00:00:00Z",
                        "end": "2024-01-01T00:00:00Z",
                    },
                    "scope": {},
                },
            },
            "unit": None,
            "lineage": {
                "current_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                "baseline_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
            },
        }

        result = self._run_decompose(runtime, compare_artifact, dimension="channel")

        canonical_subset = _attribution_frame_canonical_subset(result)
        self.assertNotIn("metric_ref", result)
        self.assertEqual(
            canonical_subset["lineage"],
            {"operation": "decompose", "source_artifact_ids": ["art_compare"]},
        )
        self.assertIn("delta_frame", result["source_lineage"])
        self.assertIn("current_artifact", result["source_lineage"])
        self.assertIn("baseline_artifact", result["source_lineage"])
        aoi.AttributionFrameArtifact.model_validate(canonical_subset)

    def test_decompose_output_has_no_rows_backward_compat_alias(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertNotIn("rows", result)

    def test_decompose_output_has_no_dimension_backward_compat_alias(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertNotIn("dimension", result)

    def test_decompose_output_has_no_scope_values_at_top_level(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(runtime)
        self.assertNotIn("scope_current_value", result)
        self.assertNotIn("scope_baseline_value", result)
        self.assertNotIn("scope_absolute_delta", result)
        self.assertNotIn("scope_relative_delta", result)
        self.assertNotIn("scope_direction", result)

    def test_decompose_time_series_delta_commits_summary_attribution_frame(self) -> None:
        runtime = self._make_runtime()
        result = self._run_decompose(
            runtime,
            {
                **_time_series_delta_artifact(
                    points=[
                        {
                            "window": {"start": "2024-01-01", "end": "2024-01-02"},
                            "current_value": 60.0,
                            "baseline_value": 45.0,
                            "delta_abs": 15.0,
                            "delta_pct": 15.0 / 45.0,
                            "direction": "increase",
                            "presence": "both",
                        },
                        {
                            "window": {"start": "2024-01-02", "end": "2024-01-03"},
                            "current_value": 60.0,
                            "baseline_value": 45.0,
                            "delta_abs": 15.0,
                            "delta_pct": 15.0 / 45.0,
                            "direction": "increase",
                            "presence": "both",
                        },
                    ],
                    scope={
                        "current_value": 120.0,
                        "baseline_value": 90.0,
                        "delta_abs": 30.0,
                        "delta_pct": 0.333,
                        "direction": "increase",
                    },
                    analytical_metadata={
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
                ),
                "unit": None,
                "lineage": {
                    "current_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                    "baseline_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
                },
            },
        )

        self.assertEqual(result["compare_ref"]["shape"], "time_series_delta")
        self.assertNotIn("comparison_type", result["compare_ref"])
        self.assertEqual(result["current_ref"]["observation_type"], "time_series")
        self.assertEqual(result["baseline_ref"]["observation_type"], "time_series")
        self.assertEqual(result["payload"]["scope"]["delta_abs"], 30.0)
        self.assertEqual(result["schema_version"], "2.0")
        self.assertIsInstance(result["axes"], list)
        self.assertIsInstance(result["payload"]["series"], list)
        self.assertEqual(
            result["analytical_metadata"]["decomposition_source"],
            "time_series_summary_delta",
        )
        self.assertEqual(result["analytical_metadata"]["source_granularity"], "day")
        self.assertEqual(
            result["analytical_metadata"]["source_pairing_basis"],
            "calendar_aligned_observation_windows",
        )

    def test_decompose_segmented_delta_fast_path_skips_segmented_queries(self) -> None:
        runtime = self._make_runtime()
        compare_artifact = {
            "artifact_family": "delta_frame",
            "shape": "segmented_delta",
            "capabilities": ["sliceable", "filterable", "decomposable"],
            "schema_version": "2.0",
            "metric": "m1",
            "metric_ref": "metric.m1",
            "axes": [{"kind": "dimension", "name": "dim1"}],
            "payload": {
                "series": [
                    {
                        "keys": {"dim1": "paid"},
                        "points": [
                            {
                                "current_value": 70.0,
                                "baseline_value": 58.0,
                                "delta_abs": 12.0,
                                "presence": "both",
                            }
                        ],
                    },
                    {
                        "keys": {"dim1": "organic"},
                        "points": [
                            {
                                "current_value": 50.0,
                                "baseline_value": 42.0,
                                "delta_abs": 8.0,
                                "presence": "both",
                            }
                        ],
                    },
                ],
                "scope": {
                    "current_value": 120.0,
                    "baseline_value": 100.0,
                    "delta_abs": 20.0,
                    "delta_pct": 0.2,
                    "direction": "increase",
                },
            },
            "subject": _comparison_subject(),
            "lineage": {
                "current_source_ref": {"step_id": "step_obs_left", "session_id": _SESSION},
                "baseline_source_ref": {"step_id": "step_obs_right", "session_id": _SESSION},
            },
            "analytical_metadata": {"series_complete": True},
        }
        runtime.resolve_artifact_by_id.return_value = compare_artifact
        runtime.resolve_artifact_id_for_step.return_value = "art_fake_ref001"

        resolved_metric = MagicMock()
        resolved_metric.semantic_object = {"header": {"decomposition_semantics": "sum"}}
        resolved_metric.allowed_dimensions = ["dim1"]
        resolved_metric.dimensions = ["dim1"]
        runtime.resolve_metric.return_value = resolved_metric
        runtime.resolve_metric_dimensions.return_value = ["dim1"]
        runtime.resolve_metric_table.return_value = "src.metrics"
        runtime.resolve_engine_for_session.return_value = (
            MagicMock(),
            "duckdb",
            {"src.metrics": "src.metrics"},
        )
        runtime.resolve_metric_sql_for_execution.return_value = "SUM(val)"

        with patch("marivo.runtime.intents.decompose._run_segmented_query") as mock_query:
            result = run_decompose_intent(
                runtime,
                _SESSION,
                {"compare_artifact_id": "art_compare", "dimension": "dim1"},
            )

        mock_query.assert_not_called()
        self.assertEqual(
            result["payload"]["series"],
            [
                {
                    "keys": {"dim1": "paid"},
                    "points": [
                        {
                            "contribution_abs": 12.0,
                            "contribution_pct": 0.6,
                            "current_value": 70.0,
                            "baseline_value": 58.0,
                            "rank": 1,
                            "presence": "both",
                        }
                    ],
                },
                {
                    "keys": {"dim1": "organic"},
                    "points": [
                        {
                            "contribution_abs": 8.0,
                            "contribution_pct": 0.4,
                            "current_value": 50.0,
                            "baseline_value": 42.0,
                            "rank": 2,
                            "presence": "both",
                        }
                    ],
                },
            ],
        )


class DecomposeAttributionSeriesTests(unittest.TestCase):
    def test_attribution_series_requires_absolute_contribution(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "absolute_contribution",
        ):
            _attribution_series_from_rows(
                [{"key": "paid", "absolute_contribution": None}],
                dimension="channel",
            )

    def test_attribution_series_omits_invalid_presence(self) -> None:
        series = _attribution_series_from_rows(
            [
                {
                    "key": "paid",
                    "absolute_contribution": 12.0,
                    "contribution_share": 0.6,
                    "presence": "undefined",
                }
            ],
            dimension="channel",
        )

        point = series[0]["points"][0]
        self.assertNotIn("presence", point)


# -- detect --------------------------------------------------------------------
