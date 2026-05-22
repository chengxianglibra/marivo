from __future__ import annotations

import unittest

import pytest

from marivo.runtime.intents.metric_frame import (
    build_attribution_frame_artifact,
    build_axes,
    build_delta_frame_artifact,
    build_scalar_series,
    build_segmented_series,
    determine_observation_type,
    is_attribution_frame_artifact,
    is_delta_frame_artifact,
    read_attribution_rows_from_series,
    read_compare_scalar_point,
    read_delta_frame_shape,
)


class TestBuildAxes(unittest.TestCase):
    def test_scalar_no_axes(self):
        self.assertEqual(build_axes(None, None), [])

    def test_time_series_single_time_axis(self):
        axes = build_axes("day", None)
        self.assertEqual(axes, [{"kind": "time", "grain": "day"}])

    def test_segmented_single_dimension_axis(self):
        axes = build_axes(None, ["region"])
        self.assertEqual(axes, [{"kind": "dimension", "name": "region"}])

    def test_panel_two_axes(self):
        axes = build_axes("day", ["region"])
        self.assertEqual(
            axes,
            [
                {"kind": "time", "grain": "day"},
                {"kind": "dimension", "name": "region"},
            ],
        )

    def test_panel_multiple_dimensions(self):
        axes = build_axes("day", ["region", "platform"])
        self.assertEqual(
            axes,
            [
                {"kind": "time", "grain": "day"},
                {"kind": "dimension", "name": "region"},
                {"kind": "dimension", "name": "platform"},
            ],
        )


class TestDetermineObservationType(unittest.TestCase):
    def test_scalar(self):
        self.assertEqual(determine_observation_type(None, None), "scalar")

    def test_time_series(self):
        self.assertEqual(determine_observation_type("day", None), "time_series")

    def test_segmented(self):
        self.assertEqual(determine_observation_type(None, ["region"]), "segmented")

    def test_panel(self):
        self.assertEqual(determine_observation_type("day", ["region"]), "panel")


class TestBuildScalarSeries(unittest.TestCase):
    def test_with_value(self):
        series = build_scalar_series(value=42.5)
        self.assertEqual(series, [{"keys": {}, "points": [{"value": 42.5}]}])

    def test_with_none_value(self):
        series = build_scalar_series(value=None)
        self.assertEqual(series, [{"keys": {}, "points": [{"value": None}]}])


class TestBuildSegmentedSeries(unittest.TestCase):
    def test_single_dimension(self):
        rows = [
            {"region": "US", "current_value": "120"},
            {"region": "EU", "current_value": "95"},
        ]
        series = build_segmented_series(rows, dimensions=["region"])
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0]["keys"], {"region": "US"})
        self.assertEqual(series[0]["points"][0]["value"], 120.0)
        self.assertEqual(series[1]["keys"], {"region": "EU"})
        self.assertEqual(series[1]["points"][0]["value"], 95.0)

    def test_sorted_by_value_desc(self):
        rows = [
            {"region": "EU", "current_value": "95"},
            {"region": "US", "current_value": "120"},
        ]
        series = build_segmented_series(rows, dimensions=["region"])
        self.assertEqual(series[0]["keys"]["region"], "US")
        self.assertEqual(series[1]["keys"]["region"], "EU")


def test_build_metric_frame_artifact_scalar_contract() -> None:
    from marivo.runtime.intents.metric_frame import build_metric_frame_artifact

    artifact = build_metric_frame_artifact(
        artifact_id="art_1",
        shape="scalar",
        metric_ref="metric.view_time",
        time_scope={"field": "event_time", "start": "2026-01-01", "end": "2026-01-02"},
        scope={},
        axes=[],
        series=[{"keys": {}, "points": [{"value": 42.0}]}],
        unit=None,
    )

    assert artifact == {
        "artifact_id": "art_1",
        "artifact_family": "metric_frame",
        "shape": "scalar",
        "subject": {
            "kind": "metric",
            "metric_ref": "metric.view_time",
            "time_scope": {
                "field": "event_time",
                "start": "2026-01-01",
                "end": "2026-01-02",
            },
            "scope": {},
        },
        "axes": [],
        "measures": [{"id": "value", "value_type": "number", "nullable": True, "unit": None}],
        "payload": {"series": [{"keys": {}, "points": [{"value": 42.0}]}]},
    }


def test_read_metric_frame_shape_and_series() -> None:
    from marivo.runtime.intents.metric_frame import (
        build_metric_frame_artifact,
        read_metric_frame_points,
        read_metric_frame_series,
        read_metric_frame_shape,
        read_metric_frame_unit,
    )

    artifact = build_metric_frame_artifact(
        artifact_id="art_view_time_time_series",
        shape="time_series",
        metric_ref="metric.view_time",
        time_scope={"field": "time", "start": "2024-01-01", "end": "2024-01-02"},
        scope={},
        axes=[{"kind": "time", "grain": "day"}],
        series=[{"keys": {}, "points": [{"value": 1.0}]}],
        unit="usd",
    )

    assert read_metric_frame_shape(artifact) == "time_series"
    assert read_metric_frame_series(artifact) == [{"keys": {}, "points": [{"value": 1.0}]}]
    assert read_metric_frame_points(artifact) == [{"value": 1.0}]
    assert read_metric_frame_unit(artifact) == "usd"


def test_build_delta_frame_artifact_sets_family_shape_payload_and_capabilities() -> None:
    artifact = build_delta_frame_artifact(
        artifact_id="art_cmp",
        shape="scalar_delta",
        metric_ref="metric.revenue",
        subject={
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-08",
                    "end": "2024-01-15",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                }
            },
        },
        axes=[],
        series=[
            {
                "keys": {},
                "points": [
                    {
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
        lineage={"current_source_ref": {"artifact_id": "art_current"}},
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": 20.0,
            "delta_pct": 0.2,
            "direction": "increase",
        },
    )

    assert is_delta_frame_artifact(artifact)
    assert artifact["artifact_family"] == "delta_frame"
    assert artifact["shape"] == "scalar_delta"
    assert "decomposable" in artifact["capabilities"]
    assert artifact["payload"]["series"][0]["points"][0]["delta_abs"] == 20.0
    assert artifact["payload"]["scope"]["delta_abs"] == 20.0
    assert read_delta_frame_shape(artifact) == "scalar_delta"


def test_build_attribution_frame_artifact_reads_flat_rows_from_payload_series() -> None:
    artifact = build_attribution_frame_artifact(
        artifact_id="art_attr",
        metric_ref="metric.revenue",
        dimension="channel",
        subject={
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-08",
                    "end": "2024-01-15",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                }
            },
        },
        series=[
            {
                "keys": {"channel": "paid"},
                "points": [
                    {
                        "contribution_abs": 12.0,
                        "contribution_pct": 0.6,
                        "current_value": 70.0,
                        "baseline_value": 58.0,
                        "presence": "both",
                        "rank": 1,
                    }
                ],
            }
        ],
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": 20.0,
            "delta_pct": 0.2,
            "direction": "increase",
        },
        quality={
            "reconciliation_status": "within_tolerance",
            "unexplained_delta_abs": 0.0,
            "unexplained_pct": 0.0,
        },
        lineage={"operation": "decompose", "source_artifact_ids": ["art_cmp"]},
    )

    assert is_attribution_frame_artifact(artifact)
    assert artifact["artifact_family"] == "attribution_frame"
    assert artifact["shape"] == "ranked_contributions"
    assert artifact["measures"] == [
        {"id": "contribution_abs", "value_type": "number", "nullable": False},
        {"id": "contribution_pct", "value_type": "number", "nullable": True},
    ]
    assert read_attribution_rows_from_series(artifact) == [
        {
            "key": "paid",
            "channel": "paid",
            "contribution_abs": 12.0,
            "contribution_pct": 0.6,
            "current_value": 70.0,
            "baseline_value": 58.0,
            "presence": "both",
            "rank": 1,
        }
    ]


def test_read_delta_frame_shape_rejects_non_delta_artifact() -> None:
    with pytest.raises(ValueError, match="delta_frame artifact"):
        read_delta_frame_shape({"artifact_family": "metric_frame", "shape": "scalar"})


def test_read_compare_scalar_point_reads_delta_frame_payload_series() -> None:
    artifact = build_delta_frame_artifact(
        artifact_id="art_cmp",
        shape="scalar_delta",
        metric_ref="metric.revenue",
        subject={
            "kind": "comparison",
            "metric_ref": "metric.revenue",
            "current": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-08",
                    "end": "2024-01-15",
                }
            },
            "baseline": {
                "time_scope": {
                    "field": "time",
                    "start": "2024-01-01",
                    "end": "2024-01-08",
                }
            },
        },
        axes=[],
        series=[
            {
                "keys": {},
                "points": [
                    {
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
        lineage={"operation": "compare"},
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": 20.0,
            "delta_pct": 0.2,
            "direction": "increase",
        },
    )

    assert read_compare_scalar_point(artifact) == {
        "current_value": 120.0,
        "baseline_value": 100.0,
        "delta_abs": 20.0,
        "delta": 20.0,
        "delta_pct": 0.2,
        "direction": "increase",
    }


def test_read_attribution_rows_from_series_reads_canonical_attribution_frame() -> None:
    artifact = build_attribution_frame_artifact(
        artifact_id="art_attr",
        metric_ref="metric.revenue",
        dimension="channel",
        subject={"kind": "comparison", "metric_ref": "metric.revenue"},
        series=[
            {
                "keys": {"channel": "paid"},
                "points": [
                    {
                        "contribution_abs": 12.0,
                        "contribution_pct": 0.6,
                        "rank": 1,
                    }
                ],
            }
        ],
        scope={
            "current_value": 120.0,
            "baseline_value": 100.0,
            "delta_abs": 20.0,
            "delta_pct": 0.2,
            "direction": "increase",
        },
        quality={"reconciliation_status": "within_tolerance"},
        lineage={"operation": "decompose"},
    )

    assert read_attribution_rows_from_series(artifact) == [
        {
            "key": "paid",
            "channel": "paid",
            "contribution_abs": 12.0,
            "contribution_pct": 0.6,
            "rank": 1,
        }
    ]
