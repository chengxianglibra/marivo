from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marivo.runtime.intents.sample_summary import (
    compute_numeric_summary_from_metric_frame,
    extract_time_sample_axis,
    run_sample_summary_transform,
)


def _metric_frame_artifact(
    *,
    shape: str = "time_series",
    values: list[float | None] | None = None,
) -> dict[str, Any]:
    point_values = values if values is not None else [100.0, 110.0, None, 130.0]
    return {
        "artifact_id": "art_metric_frame_current",
        "artifact_family": "metric_frame",
        "shape": shape,
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.revenue",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-05T00:00:00Z",
            },
            "scope": {},
        },
        "axes": [{"kind": "time", "grain": "day"}],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "window": {
                                "start": f"2026-01-0{index + 1}T00:00:00Z",
                                "end": f"2026-01-0{index + 2}T00:00:00Z",
                            },
                            "value": value,
                        }
                        for index, value in enumerate(point_values)
                    ],
                }
            ]
        },
    }


def _runtime(source_artifact: dict[str, Any]) -> MagicMock:
    runtime = MagicMock()
    runtime.resolve_artifact_by_id.return_value = source_artifact
    runtime.commit_artifact_with_extraction.return_value = "art_sample_current"
    return runtime


def test_extract_time_sample_axis_from_time_series_metric_frame() -> None:
    axis = extract_time_sample_axis(_metric_frame_artifact())

    assert axis == {"kind": "sample", "source_axis": "time", "grain": "day"}


@pytest.mark.parametrize("shape", ["scalar", "segmented", "panel"])
def test_extract_time_sample_axis_rejects_unsupported_shapes(shape: str) -> None:
    artifact = _metric_frame_artifact(shape=shape)
    if shape == "scalar":
        artifact["axes"] = []
    if shape == "segmented":
        artifact["axes"] = [{"kind": "dimension", "name": "region"}]
    if shape == "panel":
        artifact["axes"] = [
            {"kind": "time", "grain": "day"},
            {"kind": "dimension", "name": "region"},
        ]

    with pytest.raises(ValueError, match="requires a time_series metric_frame"):
        extract_time_sample_axis(artifact)


def test_compute_numeric_summary_ignores_null_points() -> None:
    summary = compute_numeric_summary_from_metric_frame(_metric_frame_artifact())

    assert summary == {
        "n": 3,
        "mean": pytest.approx(113.33333333333333),
        "standard_deviation": pytest.approx(15.275252316519467),
    }


def test_compute_numeric_summary_ignores_bool_points() -> None:
    summary = compute_numeric_summary_from_metric_frame(
        _metric_frame_artifact(values=[True, 2.0, False, None, 4.0])
    )

    assert summary == {
        "n": 2,
        "mean": pytest.approx(3.0),
        "standard_deviation": pytest.approx(1.4142135623730951),
    }


def test_compute_numeric_summary_returns_null_stats_for_no_numeric_points() -> None:
    summary = compute_numeric_summary_from_metric_frame(_metric_frame_artifact(values=[None, None]))

    assert summary == {"n": 0, "mean": None, "standard_deviation": None}


def test_run_sample_summary_commits_sample_frame_from_metric_frame_payload() -> None:
    runtime = _runtime(_metric_frame_artifact())

    with patch("marivo.runtime.intents.sample_summary.new_step_id", return_value="step_sample"):
        result = run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_metric_frame_current",
                "sample_kind": "numeric",
            },
            reasoning="need test input",
        )

    runtime.resolve_artifact_by_id.assert_called_once_with("sess_1", "art_metric_frame_current")
    artifact = runtime.commit_artifact_with_extraction.call_args.args[4]
    assert artifact["artifact_family"] == "sample_frame"
    assert artifact["shape"] == "numeric_summary"
    assert artifact["subject"]["metric_ref"] == "metric.revenue"
    assert artifact["axes"] == [{"kind": "sample", "source_axis": "time", "grain": "day"}]
    assert artifact["payload"]["summary"]["n"] == 3
    assert artifact["lineage"]["source_artifact_ids"] == ["art_metric_frame_current"]
    assert result["step_type"] == "sample_summary"
    assert result["artifact_id"] == "art_sample_current"


def test_run_sample_summary_marks_single_numeric_point_insufficient_data() -> None:
    runtime = _runtime(_metric_frame_artifact(values=[42.0]))

    with patch("marivo.runtime.intents.sample_summary.new_step_id", return_value="step_sample"):
        run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_metric_frame_current",
                "sample_kind": "numeric",
            },
        )

    artifact = runtime.commit_artifact_with_extraction.call_args.args[4]
    assert artifact["payload"]["summary"] == {
        "n": 1,
        "mean": pytest.approx(42.0),
        "standard_deviation": None,
    }
    quality = artifact["payload"]["quality"]
    assert quality["status"] == "insufficient_data"
    assert quality["issues"] == [
        {
            "code": "insufficient_sample_size",
            "message": "At least two numeric points are required for sample standard deviation.",
        }
    ]


def test_run_sample_summary_rejects_request_grain() -> None:
    runtime = _runtime(_metric_frame_artifact())

    with pytest.raises(ValueError, match="unsupported field"):
        run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_metric_frame_current",
                "sample_kind": "numeric",
                "grain": "day",
            },
        )


def test_run_sample_summary_rejects_non_metric_frame_source() -> None:
    runtime = _runtime({"artifact_id": "art_delta", "artifact_family": "delta_frame"})

    with pytest.raises(ValueError, match="must point to a metric_frame"):
        run_sample_summary_transform(
            runtime,
            "sess_1",
            {
                "source_artifact_id": "art_delta",
                "sample_kind": "numeric",
            },
        )
