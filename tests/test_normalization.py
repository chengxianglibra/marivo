from __future__ import annotations

import unittest

from marivo.runtime.intents.normalization import (
    normalize_dimensions,
    normalize_metric_ref,
    validate_granularity,
    validate_hour_boundaries,
)


class TestNormalizeMetricRef(unittest.TestCase):
    def test_strips_whitespace(self) -> None:
        self.assertEqual(normalize_metric_ref("  metric.revenue  "), "metric.revenue")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            normalize_metric_ref("")
        self.assertIn("requires 'metric'", str(ctx.exception))

    def test_none_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            normalize_metric_ref(None)
        self.assertIn("requires 'metric'", str(ctx.exception))


class TestNormalizeDimensions(unittest.TestCase):
    def test_empty_list_becomes_none(self) -> None:
        self.assertIsNone(normalize_dimensions([]))

    def test_none_stays_none(self) -> None:
        self.assertIsNone(normalize_dimensions(None))

    def test_deduplicates_preserving_order(self) -> None:
        self.assertEqual(normalize_dimensions(["a", "b", "a", "c"]), ["a", "b", "c"])

    def test_strips_whitespace(self) -> None:
        self.assertEqual(normalize_dimensions(["  region  ", "country"]), ["region", "country"])

    def test_removes_empty_strings(self) -> None:
        self.assertEqual(normalize_dimensions(["a", "", "  ", "b"]), ["a", "b"])

    def test_all_empty_becomes_none(self) -> None:
        self.assertIsNone(normalize_dimensions(["", "  "]))


class TestValidateGranularity(unittest.TestCase):
    def test_valid_values(self) -> None:
        for g in ("hour", "day", "week", "month"):
            self.assertEqual(validate_granularity(g), g)

    def test_none_passes(self) -> None:
        self.assertIsNone(validate_granularity(None))

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_granularity("year")
        self.assertIn("not valid", str(ctx.exception))


class TestValidateHourBoundaries(unittest.TestCase):
    def test_non_hour_granularity_skips(self) -> None:
        validate_hour_boundaries("day", "2024-01-01", "2024-01-02")

    def test_hour_granularity_with_datetime_passes(self) -> None:
        validate_hour_boundaries("hour", "2024-01-01 00:00:00", "2024-01-02 00:00:00")

    def test_hour_granularity_with_date_only_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_hour_boundaries("hour", "2024-01-01", "2024-01-02")
