"""Tests for correlate_metrics step type."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.evidence_engine.causal_checkers import DoseResponseChecker, LevelUpgrade
from app.service import SemanticLayerService
from app.storage.sqlite_metadata import SQLiteMetadataStore
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine


class CorrelateMetricsStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "correlate.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "correlate.meta.sqlite"
        cls.analytics = DuckDBAnalyticsEngine(str(cls.db_path))
        cls.metadata = SQLiteMetadataStore(str(cls.meta_path))
        cls.metadata.initialize()
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        # Generate unique session_id per test to avoid collisions
        self._session_counter = getattr(self.__class__, '_session_counter', 0) + 1
        self.__class__._session_counter = self._session_counter

    def _create_session(self) -> str:
        result = self.service.create_session(f"test session {self._session_counter}", {}, {}, {})
        return result["session_id"]

    def _insert_artifact(self, session_id: str, rows: list[dict], name: str = "test") -> str:
        # Use session_id suffix to ensure uniqueness across tests
        short_session = session_id.split("_")[-1][:6]
        step_id = f"step_{short_session}_{name}"
        artifact_id = f"art_{short_session}_{name}"
        self.metadata.execute(
            "INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json) "
            "VALUES (?, ?, ?, 'aggregate', ?, ?)",
            [artifact_id, session_id, step_id, name, json.dumps(rows)],
        )
        return artifact_id

    def test_correlate_metrics_basic(self) -> None:
        """Basic Spearman correlation between two perfectly correlated series."""
        session_id = self._create_session()

        # Two perfectly correlated series (rho = 1.0)
        left_rows = [
            {"log_date": "20260301", "query_count": 100},
            {"log_date": "20260302", "query_count": 200},
            {"log_date": "20260303", "query_count": 300},
        ]
        right_rows = [
            {"log_date": "20260301", "failure_rate": 0.01},
            {"log_date": "20260302", "failure_rate": 0.02},
            {"log_date": "20260303", "failure_rate": 0.03},
        ]

        left_artifact_id = self._insert_artifact(session_id, left_rows, "left")
        right_artifact_id = self._insert_artifact(session_id, right_rows, "right")

        result = self.service.run_step(
            session_id,
            "correlate_metrics",
            params={
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "left_value_column": "query_count",
                "right_value_column": "failure_rate",
                "join_on": "log_date",
                "method": "spearman",
                "left_metric": "query_count",
                "right_metric": "failure_rate",
            },
        )

        self.assertEqual(result["step_type"], "correlate_metrics")
        self.assertIn("artifact_id", result)
        self.assertIn("correlation", result)
        corr = result["correlation"]
        self.assertEqual(corr["n"], 3)
        self.assertEqual(corr["method"], "spearman")
        self.assertAlmostEqual(corr["rho"], 1.0, places=3)
        self.assertLess(corr["p_value"], 0.05)

        # Check observed_window derived from log_date
        self.assertIn("observed_window", corr)
        self.assertEqual(corr["observed_window"]["start"], "2026-03-01")
        self.assertEqual(corr["observed_window"]["end"], "2026-03-03")

        # Check observation was created
        self.assertIn("observations", result)
        obs = result["observations"][0]
        self.assertEqual(obs["type"], "correlation_result")
        self.assertEqual(obs["subject"]["metric"], "failure_rate")
        self.assertEqual(obs["subject"]["related_metric"], "query_count")
        self.assertAlmostEqual(obs["payload"]["rho"], 1.0, places=3)

    def test_correlate_metrics_negative_correlation(self) -> None:
        """Negative correlation (rho = -1.0)."""
        session_id = self._create_session()

        left_rows = [
            {"log_date": "20260301", "query_count": 300},
            {"log_date": "20260302", "query_count": 200},
            {"log_date": "20260303", "query_count": 100},
        ]
        right_rows = [
            {"log_date": "20260301", "failure_rate": 0.01},
            {"log_date": "20260302", "failure_rate": 0.02},
            {"log_date": "20260303", "failure_rate": 0.03},
        ]

        left_artifact_id = self._insert_artifact(session_id, left_rows, "left")
        right_artifact_id = self._insert_artifact(session_id, right_rows, "right")

        result = self.service.run_step(
            session_id,
            "correlate_metrics",
            params={
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "left_value_column": "query_count",
                "right_value_column": "failure_rate",
                "join_on": "log_date",
                "left_metric": "query_count",
                "right_metric": "failure_rate",
            },
        )

        self.assertAlmostEqual(result["correlation"]["rho"], -1.0, places=3)

    def test_correlate_metrics_insufficient_pairs(self) -> None:
        """Raise ValueError when fewer than min_pairs matched rows."""
        session_id = self._create_session()

        left_rows = [{"log_date": "20260301", "query_count": 100}]
        right_rows = [{"log_date": "20260302", "failure_rate": 0.01}]  # No match

        left_artifact_id = self._insert_artifact(session_id, left_rows, "left")
        right_artifact_id = self._insert_artifact(session_id, right_rows, "right")

        with self.assertRaises(ValueError) as ctx:
            self.service.run_step(
                session_id,
                "correlate_metrics",
                params={
                    "left_artifact_id": left_artifact_id,
                    "right_artifact_id": right_artifact_id,
                    "left_value_column": "query_count",
                    "right_value_column": "failure_rate",
                    "join_on": "log_date",
                    "min_pairs": 3,
                    "left_metric": "query_count",
                    "right_metric": "failure_rate",
                },
            )
        self.assertIn("only 0 matched pairs", str(ctx.exception))

    def test_correlate_metrics_pearson_method(self) -> None:
        """Pearson correlation method."""
        session_id = self._create_session()

        left_rows = [
            {"log_date": "20260301", "x": 1.0},
            {"log_date": "20260302", "x": 2.0},
            {"log_date": "20260303", "x": 3.0},
        ]
        right_rows = [
            {"log_date": "20260301", "y": 2.0},
            {"log_date": "20260302", "y": 4.0},
            {"log_date": "20260303", "y": 6.0},
        ]

        left_artifact_id = self._insert_artifact(session_id, left_rows, "left")
        right_artifact_id = self._insert_artifact(session_id, right_rows, "right")

        result = self.service.run_step(
            session_id,
            "correlate_metrics",
            params={
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "left_value_column": "x",
                "right_value_column": "y",
                "join_on": "log_date",
                "method": "pearson",
                "left_metric": "x_metric",
                "right_metric": "y_metric",
            },
        )

        self.assertEqual(result["correlation"]["method"], "pearson")
        self.assertAlmostEqual(result["correlation"]["rho"], 1.0, places=3)

    def test_correlate_metrics_both_methods(self) -> None:
        """method='both' returns spearman_rho and pearson_rho."""
        session_id = self._create_session()

        left_rows = [
            {"log_date": "20260301", "x": 1.0},
            {"log_date": "20260302", "x": 2.0},
            {"log_date": "20260303", "x": 3.0},
        ]
        right_rows = [
            {"log_date": "20260301", "y": 2.0},
            {"log_date": "20260302", "y": 4.0},
            {"log_date": "20260303", "y": 6.0},
        ]

        left_artifact_id = self._insert_artifact(session_id, left_rows, "left")
        right_artifact_id = self._insert_artifact(session_id, right_rows, "right")

        result = self.service.run_step(
            session_id,
            "correlate_metrics",
            params={
                "left_artifact_id": left_artifact_id,
                "right_artifact_id": right_artifact_id,
                "left_value_column": "x",
                "right_value_column": "y",
                "join_on": "log_date",
                "method": "both",
                "left_metric": "x_metric",
                "right_metric": "y_metric",
            },
        )

        corr = result["correlation"]
        self.assertIn("spearman_rho", corr)
        self.assertIn("pearson_rho", corr)
        self.assertIn("rho", corr)  # Primary rho is spearman when method='both'

    def test_correlate_metrics_step_id_lookup(self) -> None:
        """Look up artifact by step_id instead of artifact_id."""
        session_id = self._create_session()

        # Insert artifact with a specific step_id
        step_id = "step_lookup_test"
        artifact_id = "art_lookup_test"
        rows = [{"log_date": "20260301", "val": 1.0}, {"log_date": "20260302", "val": 2.0}]
        self.metadata.execute(
            "INSERT INTO artifacts (artifact_id, session_id, step_id, artifact_type, name, content_json) "
            "VALUES (?, ?, ?, 'aggregate', 'test', ?)",
            [artifact_id, session_id, step_id, json.dumps(rows)],
        )

        # Use step_id to reference
        right_rows = [{"log_date": "20260301", "y": 2.0}, {"log_date": "20260302", "y": 4.0}]

        right_artifact_id = self._insert_artifact(session_id, right_rows, "right2")

        # This should work - step_id lookup
        result = self.service.run_step(
            session_id,
            "correlate_metrics",
            params={
                "left_step_id": step_id,  # Lookup by step_id
                "right_artifact_id": right_artifact_id,
                "left_value_column": "val",
                "right_value_column": "y",
                "join_on": "log_date",
                "min_pairs": 2,  # We only have 2 matched pairs
                "left_metric": "val_metric",
                "right_metric": "y_metric",
            },
        )

        self.assertEqual(result["correlation"]["n"], 2)


class DoseResponseCheckerPrecomputedTests(unittest.TestCase):
    """Tests for DoseResponseChecker consuming correlation_result observations."""

    def test_precomputed_correlation_in_supporting_observations(self) -> None:
        """Claim with correlation_result in supporting_observations gets bonus token."""
        checker = DoseResponseChecker()

        claim = {
            "claim_id": "claim_test",
            "inference_level": "L1",
            "supporting_observations": ["obs_corr"],
            "scope": {"metric": "failure_rate"},
        }

        observation = {
            "observation_id": "obs_corr",
            "type": "correlation_result",
            "payload": {
                "rho": 0.85,
                "p_value": 0.01,
                "n": 10,
                "method": "spearman",
                "left_metric": "query_count",
                "right_metric": "failure_rate",
            },
            "subject": {"metric": "failure_rate", "related_metric": "query_count"},
        }

        upgrades = checker.check([claim], [observation], [])
        self.assertEqual(len(upgrades), 1)
        self.assertEqual(upgrades[0].claim_id, "claim_test")
        self.assertIn("dose_response_precomputed", upgrades[0].justification_tokens[0])

    def test_precomputed_weak_correlation_no_bonus(self) -> None:
        """Correlation below threshold does not produce bonus."""
        checker = DoseResponseChecker()

        claim = {
            "claim_id": "claim_weak",
            "inference_level": "L1",
            "supporting_observations": ["obs_weak"],
            "scope": {"metric": "failure_rate"},
        }

        observation = {
            "observation_id": "obs_weak",
            "type": "correlation_result",
            "payload": {
                "rho": 0.5,  # Below 0.7 threshold
                "p_value": 0.1,
                "n": 10,
                "method": "spearman",
                "left_metric": "query_count",
                "right_metric": "failure_rate",
            },
        }

        upgrades = checker.check([claim], [observation], [])
        self.assertEqual(len(upgrades), 0)

    def test_session_wide_scan_finds_correlation(self) -> None:
        """DoseResponseChecker finds correlation_result not in supporting_observations."""
        checker = DoseResponseChecker()

        claim = {
            "claim_id": "claim_scan",
            "inference_level": "L1",
            "supporting_observations": [],  # Empty
            "scope": {"metric": "failure_rate"},
        }

        observation = {
            "observation_id": "obs_scan",
            "type": "correlation_result",
            "payload": {
                "rho": 0.9,
                "p_value": 0.001,
                "n": 15,
                "method": "spearman",
                "left_metric": "query_count",
                "right_metric": "failure_rate",
            },
        }

        upgrades = checker.check([claim], [observation], [])
        self.assertEqual(len(upgrades), 1)
        self.assertIn("dose_response_precomputed_session", upgrades[0].justification_tokens[0])


if __name__ == "__main__":
    unittest.main()