from __future__ import annotations

import pytest

from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    is_delta_frame_artifact,
    read_delta_frame_series,
    read_delta_frame_shape,
    read_delta_scalar_point,
)


def test_is_delta_frame_artifact_returns_true():
    artifact = {"artifact_family": "delta_frame"}
    assert is_delta_frame_artifact(artifact) is True


def test_is_delta_frame_artifact_returns_false_for_metric_frame():
    artifact = {"artifact_family": "metric_frame"}
    assert is_delta_frame_artifact(artifact) is False


def test_read_delta_frame_shape_reads_shape_field():
    artifact = {"shape": "panel_delta"}
    assert read_delta_frame_shape(artifact) == "panel_delta"


def test_read_delta_frame_shape_raises_on_missing():
    with pytest.raises(ValueError, match="delta_frame artifact missing shape"):
        read_delta_frame_shape({})


def test_read_delta_frame_series_reads_payload_series():
    series = [{"keys": {}, "points": [{"delta_abs": 5.0}]}]
    artifact = {"payload": {"series": series}}
    assert read_delta_frame_series(artifact) == series


def test_build_delta_frame_artifact_scalar():
    result = build_delta_frame_artifact(
        artifact_id="art_test",
        shape="scalar_delta",
        metric_ref="metric.test",
        current_scope={
            "time_scope": {"field": "log_date", "start": "2026-05-15", "end": "2026-05-16"},
            "scope": {},
        },
        baseline_scope={
            "time_scope": {"field": "log_date", "start": "2026-05-08", "end": "2026-05-09"},
            "scope": {},
        },
        axes=[{"kind": "comparison_side"}],
        series=[
            {
                "keys": {},
                "points": [
                    {
                        "current_value": 10.0,
                        "baseline_value": 5.0,
                        "delta_abs": 5.0,
                        "delta_pct": 1.0,
                        "direction": "increase",
                    }
                ],
            }
        ],
        unit=None,
    )
    assert result["artifact_family"] == "delta_frame"
    assert result["shape"] == "scalar_delta"
    assert result["subject"]["kind"] == "comparison"
    assert result["subject"]["metric_ref"] == "metric.test"
    assert result["measures"][0]["id"] == "delta_abs"


def test_build_delta_frame_artifact_panel():
    result = build_delta_frame_artifact(
        artifact_id="art_test",
        shape="panel_delta",
        metric_ref="metric.test",
        current_scope={
            "time_scope": {"field": "log_date", "start": "2026-05-15", "end": "2026-05-16"},
            "scope": {},
        },
        baseline_scope={
            "time_scope": {"field": "log_date", "start": "2026-05-08", "end": "2026-05-09"},
            "scope": {},
        },
        axes=[
            {"kind": "time", "grain": "day"},
            {"kind": "dimension", "name": "country"},
            {"kind": "comparison_side"},
        ],
        series=[
            {
                "keys": {"country": "US"},
                "points": [
                    {
                        "window": {"start": "2026-05-15T00:00:00Z", "end": "2026-05-16T00:00:00Z"},
                        "current_value": 150,
                        "baseline_value": 100,
                        "delta_abs": 50,
                        "delta_pct": 0.5,
                        "direction": "increase",
                        "presence": "both",
                    }
                ],
            }
        ],
        unit=None,
    )
    assert result["axes"] == [
        {"kind": "time", "grain": "day"},
        {"kind": "dimension", "name": "country"},
        {"kind": "comparison_side"},
    ]
    assert result["payload"]["series"][0]["keys"]["country"] == "US"


def test_read_delta_scalar_point_reads_from_series():
    artifact = {
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [
                        {
                            "current_value": 10.0,
                            "baseline_value": 5.0,
                            "delta_abs": 5.0,
                            "delta_pct": 1.0,
                            "direction": "increase",
                        }
                    ],
                }
            ]
        },
    }
    point = read_delta_scalar_point(artifact)
    assert point["delta_abs"] == 5.0
    assert point["current_value"] == 10.0
