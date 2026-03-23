"""Tests for M-01 Extractor Registry and new extractor implementations."""

from __future__ import annotations

import unittest

from app.evidence_engine.contract import ExtractorContract
from app.evidence_engine.extractors.aggregate import AggregateRowExtractor
from app.evidence_engine.extractors.anomaly import AnomalyExtractor
from app.evidence_engine.extractors.base import ObservationExtractor
from app.evidence_engine.extractors.comparison import ComparisonRowExtractor
from app.evidence_engine.extractors.contribution_shift import ContributionShiftExtractor
from app.evidence_engine.extractors.funnel import FunnelExtractor
from app.evidence_engine.registry import ExtractorRegistry, _default_registry


class ExtractorRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ExtractorRegistry()
        self.registry.register(ComparisonRowExtractor())
        self.registry.register(AggregateRowExtractor())

    def test_register_and_get(self) -> None:
        extractor = self.registry.get("comparison_rows")
        self.assertIsInstance(extractor, ComparisonRowExtractor)

    def test_get_unknown_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.registry.get("nonexistent_extractor")

    def test_find_for_artifact_type(self) -> None:
        results = self.registry.find_for_artifact("comparison_rows")
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], ComparisonRowExtractor)

    def test_find_for_artifact_no_match(self) -> None:
        results = self.registry.find_for_artifact("unknown_type")
        self.assertEqual(results, [])

    def test_list_all_returns_metadata(self) -> None:
        items = self.registry.list_all()
        self.assertEqual(len(items), 2)
        names = {item["name"] for item in items}
        self.assertIn("comparison_rows", names)
        self.assertIn("aggregate_rows", names)

    def test_list_all_has_required_keys(self) -> None:
        items = self.registry.list_all()
        for item in items:
            self.assertIn("name", item)
            self.assertIn("artifact_type", item)
            self.assertIn("observation_types", item)
            self.assertIn("preconditions", item)

    def test_as_mapping_is_backward_compatible(self) -> None:
        mapping = self.registry.as_mapping()
        self.assertIsInstance(mapping, dict)
        for v in mapping.values():
            self.assertIsInstance(v, ObservationExtractor)

    def test_default_registry_has_six_extractors(self) -> None:
        items = _default_registry.list_all()
        self.assertEqual(len(items), 6)
        names = {item["name"] for item in items}
        self.assertIn("comparison_rows", names)
        self.assertIn("aggregate_rows", names)
        self.assertIn("funnel_rows", names)
        self.assertIn("anomaly_rows", names)
        self.assertIn("contribution_shift_rows", names)
        self.assertIn("correlation_observations", names)


class FunnelExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = FunnelExtractor()

    def test_classvars(self) -> None:
        self.assertEqual(FunnelExtractor.artifact_type, "funnel_rows")
        self.assertEqual(FunnelExtractor.observation_types, ["funnel_drop"])
        self.assertIsInstance(FunnelExtractor.preconditions, list)

    def test_basic_drop_above_threshold(self) -> None:
        rows = [
            {"stage_name": "view", "count": 1000},
            {"stage_name": "click", "count": 600},
            {"stage_name": "purchase", "count": 100},  # 83% drop
        ]
        obs = self.extractor.extract(rows, context={"funnel_name": "checkout"})
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["type"], "funnel_drop")
        self.assertEqual(obs[0]["subject"]["metric"], "checkout")

    def test_no_observation_below_threshold(self) -> None:
        rows = [
            {"stage_name": "view", "count": 1000},
            {"stage_name": "click", "count": 900},
            {"stage_name": "purchase", "count": 810},
        ]
        obs = self.extractor.extract(rows, context={"threshold": 0.30})
        self.assertEqual(obs, [])

    def test_empty_rows_returns_empty(self) -> None:
        obs = self.extractor.extract([], context={"funnel_name": "test"})
        self.assertEqual(obs, [])

    def test_custom_stage_and_count_columns(self) -> None:
        rows = [
            {"step": "A", "users": 500},
            {"step": "B", "users": 100},  # 80% drop
        ]
        obs = self.extractor.extract(rows, context={
            "stage_col": "step",
            "count_col": "users",
            "threshold": 0.30,
            "funnel_name": "custom",
        })
        self.assertEqual(len(obs), 1)

    def test_payload_has_stages_and_worst_stage(self) -> None:
        rows = [
            {"stage_name": "A", "count": 1000},
            {"stage_name": "B", "count": 300},  # 70% drop
        ]
        obs = self.extractor.extract(rows, context={"threshold": 0.30})
        self.assertIn("stages", obs[0]["payload"])
        self.assertIn("worst_stage", obs[0]["payload"])

    def test_custom_threshold(self) -> None:
        rows = [
            {"stage_name": "A", "count": 1000},
            {"stage_name": "B", "count": 800},  # 20% drop
        ]
        obs_strict = self.extractor.extract(rows, context={"threshold": 0.15})
        obs_loose = self.extractor.extract(rows, context={"threshold": 0.25})
        self.assertEqual(len(obs_strict), 1)
        self.assertEqual(len(obs_loose), 0)


class AnomalyExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = AnomalyExtractor()

    def test_classvars(self) -> None:
        self.assertEqual(AnomalyExtractor.artifact_type, "anomaly_rows")
        self.assertEqual(AnomalyExtractor.observation_types, ["anomaly_detection"])

    def test_detects_clear_anomaly(self) -> None:
        # 9 normal values + 1 extreme outlier ensures z > 2.0
        rows = [{"dim": str(i), "val": 1.0} for i in range(9)] + [{"dim": "outlier", "val": 1000.0}]
        obs = self.extractor.extract(rows, context={"value_col": "val", "dim_col": "dim"})
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["type"], "anomaly_detection")

    def test_uniform_distribution_no_anomaly(self) -> None:
        rows = [{"dim": str(i), "val": 10} for i in range(10)]
        obs = self.extractor.extract(rows, context={"value_col": "val", "dim_col": "dim"})
        self.assertEqual(obs, [])

    def test_insufficient_data_returns_empty(self) -> None:
        rows = [{"dim": "A", "val": 1}, {"dim": "B", "val": 100}]
        obs = self.extractor.extract(rows, context={"value_col": "val", "dim_col": "dim"})
        self.assertEqual(obs, [])

    def test_zero_std_returns_empty(self) -> None:
        rows = [{"dim": str(i), "val": 5} for i in range(5)]
        obs = self.extractor.extract(rows, context={"value_col": "val", "dim_col": "dim"})
        self.assertEqual(obs, [])

    def test_payload_has_z_score(self) -> None:
        # 9 normal values + 1 outlier ensures z > 2.0
        rows = [{"dim": str(i), "val": 1.0} for i in range(9)] + [{"dim": "outlier", "val": 1000.0}]
        obs = self.extractor.extract(rows, context={"value_col": "val", "dim_col": "dim"})
        self.assertTrue(len(obs) > 0)
        self.assertIn("z_score", obs[0]["payload"])

    def test_missing_required_context_raises(self) -> None:
        rows = [{"dim": "A", "val": 1}]
        with self.assertRaises(ValueError):
            self.extractor.extract(rows, context={"value_col": "val"})

    def test_custom_z_threshold(self) -> None:
        rows = [
            {"dim": "A", "val": 10},
            {"dim": "B", "val": 10},
            {"dim": "C", "val": 10},
            {"dim": "D", "val": 10},
            {"dim": "E", "val": 30},  # mild outlier (~z=2)
        ]
        obs_strict = self.extractor.extract(rows, context={
            "value_col": "val", "dim_col": "dim", "z_threshold": 1.0,
        })
        obs_loose = self.extractor.extract(rows, context={
            "value_col": "val", "dim_col": "dim", "z_threshold": 5.0,
        })
        # strict threshold should detect; loose should not
        self.assertGreaterEqual(len(obs_strict), 1)
        self.assertEqual(obs_loose, [])


class ContributionShiftExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.extractor = ContributionShiftExtractor()

    def test_classvars(self) -> None:
        self.assertEqual(ContributionShiftExtractor.artifact_type, "contribution_shift_rows")
        self.assertEqual(ContributionShiftExtractor.observation_types, ["contribution_shift"])

    def test_detects_large_shift(self) -> None:
        rows = [
            {"region": "US", "baseline": 500, "current": 100},  # dropped share
            {"region": "EU", "baseline": 500, "current": 900},  # gained share
        ]
        obs = self.extractor.extract(rows, context={
            "dim_col": "region",
            "baseline_col": "baseline",
            "current_col": "current",
        })
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["type"], "contribution_shift")

    def test_small_shift_below_threshold(self) -> None:
        rows = [
            {"region": "US", "baseline": 500, "current": 510},
            {"region": "EU", "baseline": 500, "current": 490},
        ]
        obs = self.extractor.extract(rows, context={
            "dim_col": "region",
            "baseline_col": "baseline",
            "current_col": "current",
        })
        self.assertEqual(obs, [])

    def test_total_zero_returns_empty(self) -> None:
        rows = [
            {"region": "US", "baseline": 0, "current": 0},
        ]
        obs = self.extractor.extract(rows, context={
            "dim_col": "region",
            "baseline_col": "baseline",
            "current_col": "current",
        })
        self.assertEqual(obs, [])

    def test_payload_structure(self) -> None:
        rows = [
            {"region": "US", "baseline": 100, "current": 10},
            {"region": "EU", "baseline": 100, "current": 190},
        ]
        obs = self.extractor.extract(rows, context={
            "dim_col": "region",
            "baseline_col": "baseline",
            "current_col": "current",
        })
        self.assertIn("contributions", obs[0]["payload"])
        self.assertIn("biggest_shift_segment", obs[0]["payload"])

    def test_missing_required_context_raises(self) -> None:
        rows = [{"region": "US", "baseline": 100, "current": 50}]
        with self.assertRaises(ValueError):
            self.extractor.extract(rows, context={"dim_col": "region"})

    def test_custom_share_threshold(self) -> None:
        rows = [
            {"region": "US", "baseline": 1000, "current": 850},
            {"region": "EU", "baseline": 1000, "current": 1150},
        ]
        obs_strict = self.extractor.extract(rows, context={
            "dim_col": "region",
            "baseline_col": "baseline",
            "current_col": "current",
            "share_threshold": 0.05,
        })
        obs_loose = self.extractor.extract(rows, context={
            "dim_col": "region",
            "baseline_col": "baseline",
            "current_col": "current",
            "share_threshold": 0.20,
        })
        self.assertGreaterEqual(len(obs_strict), 1)
        self.assertEqual(obs_loose, [])


class ExtractorContractInheritanceTests(unittest.TestCase):
    def test_all_new_extractors_are_extractor_contracts(self) -> None:
        for cls in (FunnelExtractor, AnomalyExtractor, ContributionShiftExtractor):
            self.assertTrue(issubclass(cls, ExtractorContract))
            self.assertTrue(issubclass(cls, ObservationExtractor))

    def test_existing_extractors_upgraded_to_contract(self) -> None:
        for cls in (ComparisonRowExtractor, AggregateRowExtractor):
            self.assertTrue(issubclass(cls, ExtractorContract))

    def test_pipeline_uses_default_registry(self) -> None:
        from app.evidence_engine.synthesizers import DefaultClaimSynthesizer
        from app.evidence_engine.pipeline import EvidencePipeline

        pipeline = EvidencePipeline(DefaultClaimSynthesizer())
        # All 5 extractors from default registry should be available
        for name in ("comparison_rows", "aggregate_rows", "funnel_rows", "anomaly_rows",
                     "contribution_shift_rows"):
            self.assertIn(name, pipeline._extractors)


if __name__ == "__main__":
    unittest.main()
