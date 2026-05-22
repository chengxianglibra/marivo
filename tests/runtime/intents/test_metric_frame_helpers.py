from __future__ import annotations

import pytest

from marivo.runtime.intents.metric_frame import (
    build_delta_frame_artifact,
    build_metric_frame_artifact,
    is_delta_frame_artifact,
    iter_frame_points,
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


def test_iter_frame_points_yields_metric_frame_refs() -> None:
    artifact = build_metric_frame_artifact(
        artifact_id="artifact_metric",
        shape="panel",
        metric_ref="metric.revenue",
        time_scope={
            "field": "event_time",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-04T00:00:00Z",
        },
        scope={},
        axes=[
            {"kind": "time", "grain": "day"},
            {"kind": "dimension", "name": "region"},
        ],
        series=[
            {
                "keys": {"region": "US"},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-02T00:00:00Z",
                        },
                        "value": 100.0,
                    }
                ],
            }
        ],
        unit="usd",
    )

    points = list(iter_frame_points("artifact_metric", artifact))

    assert len(points) == 1
    assert points[0].series_keys == {"region": "US"}
    assert points[0].value("value") == 100.0
    assert points[0].window == {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-02T00:00:00Z",
    }
    assert points[0].ref == {
        "artifact_id": "artifact_metric",
        "series_index": 0,
        "point_index": 0,
        "series_keys": {"region": "US"},
        "point_key": "2026-01-01T00:00:00Z",
    }


def test_iter_frame_points_yields_delta_frame_refs() -> None:
    artifact = build_delta_frame_artifact(
        artifact_id="artifact_delta",
        shape="time_series_delta",
        metric_ref="metric.revenue",
        axes=[{"kind": "time", "grain": "day"}],
        series=[
            {
                "keys": {},
                "points": [
                    {
                        "window": {
                            "start": "2026-01-01T00:00:00Z",
                            "end": "2026-01-02T00:00:00Z",
                        },
                        "current_value": 120.0,
                        "baseline_value": 100.0,
                        "delta_abs": 20.0,
                        "delta_pct": 0.2,
                        "direction": "increase",
                    }
                ],
            }
        ],
        unit="usd",
    )

    points = list(iter_frame_points("artifact_delta", artifact))

    assert len(points) == 1
    assert points[0].value("current_value") == 120.0
    assert points[0].value("delta_pct") == 0.2
    assert points[0].ref["point_key"] == "2026-01-01T00:00:00Z"


def test_iter_frame_points_does_not_share_mutable_refs_between_points() -> None:
    shared_tags = ["initial"]
    artifact = {
        "payload": {
            "series": [
                {
                    "keys": {"region": "US"},
                    "points": [
                        {
                            "item_id": "row_1",
                            "value": 100.0,
                            "metadata": {"tags": shared_tags},
                        },
                        {
                            "item_id": "row_2",
                            "value": 200.0,
                            "metadata": {"tags": shared_tags},
                        },
                    ],
                }
            ]
        }
    }

    first, second = iter_frame_points("artifact_metric", artifact)
    first.series_keys["region"] = "CA"
    first.ref["series_keys"]["region"] = "MX"
    first.point["metadata"]["tags"].append("mutated")

    assert first.series_keys == {"region": "CA"}
    assert first.ref["series_keys"] == {"region": "MX"}
    assert second.series_keys == {"region": "US"}
    assert second.ref["series_keys"] == {"region": "US"}
    assert second.point["metadata"]["tags"] == ["initial"]
    assert artifact["payload"]["series"][0]["points"][0]["metadata"]["tags"] == ["initial"]


def test_iter_frame_points_uses_item_id_fallback_for_point_key() -> None:
    artifact = {
        "payload": {
            "series": [
                {
                    "keys": {},
                    "points": [{"item_id": " item_123 ", "value": 10.0}],
                }
            ]
        }
    }

    points = iter_frame_points("artifact_metric", artifact)

    assert points[0].ref["point_key"] == "item_123"


def test_iter_frame_points_skips_malformed_series_and_points() -> None:
    artifact = {
        "payload": {
            "series": [
                "not-a-series",
                {
                    "keys": {"region": "US"},
                    "points": [
                        None,
                        ["not-a-point"],
                        {"start": "2026-01-01T00:00:00Z", "value": 10.0},
                    ],
                },
            ]
        }
    }

    points = iter_frame_points("artifact_metric", artifact)

    assert len(points) == 1
    assert points[0].series_index == 1
    assert points[0].point_index == 2
    assert points[0].ref["point_key"] == "2026-01-01T00:00:00Z"
