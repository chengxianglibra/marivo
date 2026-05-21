from __future__ import annotations

import unittest

from marivo.runtime.intents.metric_frame import (
    build_axes,
    build_scalar_series,
    build_segmented_series,
    determine_observation_type,
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
