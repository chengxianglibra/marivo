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

    def test_outlier_row_emits_additional_anomaly_observation(self) -> None:
        rows = [
            {"cluster": f"c{i}", "cnt": 10.0}
            for i in range(9)
        ] + [{"cluster": "outlier", "cnt": 1000.0}]
        observations = self.extractor.extract(rows, context={"group_by": ["cluster"]})
        metric_changes = [obs for obs in observations if obs["type"] == "metric_change"]
        anomalies = [obs for obs in observations if obs["type"] == "anomaly_detection"]
        self.assertEqual(len(metric_changes), 10)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["subject"]["slice"], {"cluster": "outlier"})
        self.assertIn("outlier_factor", anomalies[0]["payload"])

    def test_normal_rows_do_not_emit_anomaly_observations(self) -> None:
        rows = [{"cluster": f"c{i}", "cnt": 100.0 + i} for i in range(6)]
        observations = self.extractor.extract(rows, context={"group_by": ["cluster"]})
        anomalies = [obs for obs in observations if obs["type"] == "anomaly_detection"]
        self.assertEqual(anomalies, [])

    def test_small_population_skips_anomaly_pass(self) -> None:
        rows = [{"cluster": f"c{i}", "cnt": 10.0} for i in range(3)] + [{"cluster": "x", "cnt": 1000.0}]
        observations = self.extractor.extract(rows, context={"group_by": ["cluster"]})
        self.assertEqual(len(observations), 4)
        self.assertTrue(all(obs["type"] == "metric_change" for obs in observations))

    def test_anomaly_z_threshold_override_is_respected(self) -> None:
        rows = [
            {"cluster": "a", "cnt": 10.0},
            {"cluster": "b", "cnt": 10.0},
            {"cluster": "c", "cnt": 10.0},
            {"cluster": "d", "cnt": 10.0},
            {"cluster": "e", "cnt": 30.0},
        ]
        strict = self.extractor.extract(rows, context={"group_by": ["cluster"]})
        loose = self.extractor.extract(rows, context={
            "group_by": ["cluster"],
            "anomaly_z_threshold": 1.5,
        })
        self.assertEqual([obs for obs in strict if obs["type"] == "anomaly_detection"], [])
        self.assertEqual(len([obs for obs in loose if obs["type"] == "anomaly_detection"]), 1)

    def test_anomaly_observation_propagates_observed_window(self) -> None:
        rows = [
            {"log_hour": "2024-01-15 00:00:00", "cnt": 10.0},
            {"log_hour": "2024-01-15 01:00:00", "cnt": 10.0},
            {"log_hour": "2024-01-15 02:00:00", "cnt": 10.0},
            {"log_hour": "2024-01-15 03:00:00", "cnt": 10.0},
            {"log_hour": "2024-01-15 04:00:00", "cnt": 10.0},
            {"log_hour": "2024-01-15 05:00:00", "cnt": 10000.0},
        ]
        observations = self.extractor.extract(rows, context={
            "group_by": ["log_hour"],
            "anomaly_z_threshold": 1.5,
        })
        anomalies = [obs for obs in observations if obs["type"] == "anomaly_detection"]
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(
            anomalies[0]["observed_window"],
            {
                "start": "2024-01-15T05:00",
                "end": "2024-01-15T06:00",
                "granularity": "hour",
            },
        )

    def test_anomaly_detection_stratifies_multi_dimensional_group_by(self) -> None:
        rows = [
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u1", "cnt": 10.0},
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u2", "cnt": 10.0},
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u3", "cnt": 10.0},
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u4", "cnt": 10.0},
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u5", "cnt": 10.0},
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u6", "cnt": 1000.0},
            {"log_date": "20240323", "resource_group": "rg_b", "user": "v1", "cnt": 500.0},
            {"log_date": "20240323", "resource_group": "rg_b", "user": "v2", "cnt": 500.0},
            {"log_date": "20240323", "resource_group": "rg_b", "user": "v3", "cnt": 500.0},
            {"log_date": "20240323", "resource_group": "rg_b", "user": "v4", "cnt": 500.0},
            {"log_date": "20240323", "resource_group": "rg_b", "user": "v5", "cnt": 500.0},
            {"log_date": "20240323", "resource_group": "rg_b", "user": "v6", "cnt": 500.0},
        ]
        observations = self.extractor.extract(rows, context={
            "group_by": ["log_date", "resource_group", "user"],
            "anomaly_z_threshold": 1.5,
        })
        anomalies = [obs for obs in observations if obs["type"] == "anomaly_detection"]
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(
            anomalies[0]["subject"]["slice"],
            {"log_date": "20240323", "resource_group": "rg_a", "user": "u6"},
        )
        self.assertEqual(
            anomalies[0]["payload"]["stratum"],
            {"resource_group": "rg_a"},
        )


class UnitInferenceTests(unittest.TestCase):
    """G-5a: column_unit_hint inference in AggregateRowExtractor."""

    def test_duration_column_name_hint(self) -> None:
        rows = [{"cluster": "web", "elapsed_time": 1500}]
        obs = AggregateRowExtractor().extract(rows, context={"group_by": ["cluster"]})
        hint = obs[0]["payload"].get("column_unit_hint")
        self.assertIsNotNone(hint, "Expected column_unit_hint in payload")
        self.assertEqual(hint["family"], "duration")
        self.assertIn("unit", hint)
        self.assertGreaterEqual(hint["confidence"], 0.5)

    def test_bytes_column_name_hint(self) -> None:
        rows = [{"cluster": "web", "bytes_sent": 1_000_000}]
        obs = AggregateRowExtractor().extract(rows, context={"group_by": ["cluster"]})
        hint = obs[0]["payload"].get("column_unit_hint")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["family"], "bytes")

    def test_no_hint_for_non_unit_column(self) -> None:
        rows = [{"cluster": "web", "cnt": 42}]
        obs = AggregateRowExtractor().extract(rows, context={"group_by": ["cluster"]})
        hint = obs[0]["payload"].get("column_unit_hint")
        self.assertIsNone(hint, "cnt should not produce a unit hint")

    def test_metadata_unit_overrides_heuristic(self) -> None:
        hint = AggregateRowExtractor.infer_column_unit(
            "elapsed_time",
            [1500.0, 2000.0],
            metadata_unit="milliseconds",
        )
        self.assertIsNotNone(hint)
        self.assertEqual(hint["source"], "metadata")
        self.assertEqual(hint["unit"], "milliseconds")
        self.assertEqual(hint["confidence"], 1.0)

    def test_entity_unit_used_when_no_metadata(self) -> None:
        hint = AggregateRowExtractor.infer_column_unit(
            "latency",
            [200.0, 350.0],
            entity_unit="microseconds",
        )
        self.assertIsNotNone(hint)
        self.assertEqual(hint["source"], "entity")
        self.assertEqual(hint["unit"], "microseconds")
        self.assertGreater(hint["confidence"], 0.9)

    def test_confidence_threshold_met(self) -> None:
        hint = AggregateRowExtractor.infer_column_unit(
            "latency_ms",
            [100.0, 200.0, 300.0],
        )
        self.assertIsNotNone(hint)
        self.assertGreaterEqual(hint["confidence"], 0.4)

    def test_hint_embedded_in_all_rows(self) -> None:
        """Hint should appear in every observation row (same column, same hint)."""
        rows = [{"cluster": c, "elapsed_time": v} for c, v in [("web", 1500), ("api", 2000)]]
        obs = AggregateRowExtractor().extract(rows, context={"group_by": ["cluster"]})
        for ob in obs:
            self.assertIn("column_unit_hint", ob["payload"])

    def test_column_metadata_context_respected(self) -> None:
        """column_metadata passed in context overrides heuristic."""
        rows = [{"cluster": "web", "elapsed_time": 5000}]
        obs = AggregateRowExtractor().extract(rows, context={
            "group_by": ["cluster"],
            "column_metadata": {"elapsed_time": {"unit": "microseconds"}},
        })
        hint = obs[0]["payload"]["column_unit_hint"]
        self.assertEqual(hint["source"], "metadata")
        self.assertEqual(hint["unit"], "microseconds")


class StaticUnitInferenceTests(unittest.TestCase):
    """G-5a: unit inference helper tests."""

    def test_infer_returns_none_for_unknown_column(self) -> None:
        hint = AggregateRowExtractor.infer_column_unit("user_id", [1, 2, 3])
        self.assertIsNone(hint)

    def test_infer_bytes_magnitude_band(self) -> None:
        hint = AggregateRowExtractor.infer_column_unit(
            "file_size", [500_000.0, 1_000_000.0]
        )
        self.assertIsNotNone(hint)
        self.assertEqual(hint["family"], "bytes")
        self.assertIn(hint["unit"], ("kilobytes", "megabytes"))

    def test_infer_hint_shape(self) -> None:
        hint = AggregateRowExtractor.infer_column_unit("latency", [100.0])
        self.assertIsNotNone(hint)
        for key in ("source", "family", "unit", "confidence", "candidates", "signals"):
            self.assertIn(key, hint, f"Missing key: {key}")
        self.assertIsInstance(hint["candidates"], list)
        self.assertIsInstance(hint["signals"], list)


if __name__ == "__main__":
    unittest.main()
