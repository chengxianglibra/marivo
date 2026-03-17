from __future__ import annotations

import unittest

from app.evidence_engine.extractors.aggregate import AggregateRowExtractor


class AggregateRowExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = AggregateRowExtractor()

    def test_basic_extraction(self) -> None:
        rows = [
            {"cluster": "web", "cnt": 100},
            {"cluster": "api", "cnt": 50},
        ]
        observations = self.extractor.extract(rows, context={"group_by": ["cluster"]})
        self.assertEqual(len(observations), 2)
        self.assertTrue(observations[0]["observation_id"].startswith("obs_"))
        self.assertEqual(observations[0]["type"], "metric_change")
        self.assertEqual(observations[0]["subject"]["slice"], {"cluster": "web"})
        self.assertEqual(observations[0]["payload"]["current_value"], 100)

    def test_custom_observation_type(self) -> None:
        rows = [{"cluster": "web", "error_rate": 0.15}]
        observations = self.extractor.extract(rows, context={
            "group_by": ["cluster"],
            "observation_type": "anomaly_detection",
        })
        self.assertEqual(observations[0]["type"], "anomaly_detection")

    def test_custom_value_column(self) -> None:
        rows = [{"cluster": "web", "cnt": 100, "error_rate": 0.15}]
        observations = self.extractor.extract(rows, context={
            "group_by": ["cluster"],
            "value_column": "error_rate",
        })
        self.assertEqual(observations[0]["payload"]["current_value"], 0.15)

    def test_auto_detect_value_column(self) -> None:
        rows = [{"cluster": "web", "total": 200}]
        observations = self.extractor.extract(rows, context={"group_by": ["cluster"]})
        self.assertEqual(observations[0]["payload"]["current_value"], 200)

    def test_empty_rows(self) -> None:
        observations = self.extractor.extract([], context={"group_by": ["cluster"]})
        self.assertEqual(observations, [])

    def test_multiple_group_by(self) -> None:
        rows = [{"cluster": "web", "host": "h1", "cnt": 42}]
        observations = self.extractor.extract(rows, context={
            "group_by": ["cluster", "host"],
        })
        self.assertEqual(observations[0]["subject"]["slice"], {"cluster": "web", "host": "h1"})

    def test_no_context(self) -> None:
        rows = [{"x": 1, "y": 2}]
        observations = self.extractor.extract(rows)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["subject"]["metric"], "aggregate")

    def test_metric_label(self) -> None:
        rows = [{"x": 1}]
        observations = self.extractor.extract(rows, context={"metric": "failure_rate"})
        self.assertEqual(observations[0]["subject"]["metric"], "failure_rate")

    def test_name_property(self) -> None:
        self.assertEqual(self.extractor.name, "aggregate_rows")


if __name__ == "__main__":
    unittest.main()
