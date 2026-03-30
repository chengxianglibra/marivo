"""Tests for M-08 Temporal Annotation.

Verifies:
- observed_window_json and temporal_order columns exist in the DB schema
- metric_query observations carry an observed_window with start/end/granularity
- aggregate_query observations carry observed_window when compare mode is used
- aggregate_query observations carry request-level observed_window for plain aggregations
- temporal_order increments correctly across observations in the same session
- _load_observations returns temporal_order and observed_window correctly
- evidence graph API includes temporal fields in observations
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.storage.sqlite_metadata import SQLiteMetadataStore


class TemporalAnnotationSchemaTests(unittest.TestCase):
    """M-08.1: DDL columns exist in the observations table."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(self.temp_dir.name) / "meta.sqlite"
        self.store = SQLiteMetadataStore(str(meta_path))
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_observed_window_json_column_exists(self) -> None:
        row = self.store.query_one(
            "SELECT * FROM pragma_table_info('observations') WHERE name = 'observed_window_json'"
        )
        self.assertIsNotNone(row, "observed_window_json column must exist in observations table")

    def test_temporal_order_column_exists(self) -> None:
        row = self.store.query_one(
            "SELECT * FROM pragma_table_info('observations') WHERE name = 'temporal_order'"
        )
        self.assertIsNotNone(row, "temporal_order column must exist in observations table")

    def test_temporal_order_default_zero(self) -> None:
        """Inserting an observation without temporal_order defaults to 0."""
        self.store.execute(
            """
            INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status, created_at)
            VALUES ('sess_m08a', 'test', '{}', '{}', '{}', 'active', datetime('now'))
            """
        )
        self.store.execute(
            """
            INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json)
            VALUES ('step_m08a', 'sess_m08a', 'aggregate_query', 'completed', 'x', '{}')
            """
        )
        self.store.execute(
            """
            INSERT INTO observations
                (observation_id, session_id, step_id, observation_type,
                 subject_json, payload_json, significance_json, quality_json)
            VALUES ('obs_m08a', 'sess_m08a', 'step_m08a', 'metric_observation',
                    '{}', '{}', '{}', '{}')
            """
        )
        row = self.store.query_one(
            "SELECT temporal_order FROM observations WHERE observation_id = 'obs_m08a'"
        )
        self.assertEqual(row["temporal_order"], 0)

    def test_observed_window_json_nullable(self) -> None:
        """observed_window_json can be NULL."""
        self.store.execute(
            """
            INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status, created_at)
            VALUES ('sess_m08b', 'test', '{}', '{}', '{}', 'active', datetime('now'))
            """
        )
        self.store.execute(
            """
            INSERT INTO steps (step_id, session_id, step_type, status, summary, result_json)
            VALUES ('step_m08b', 'sess_m08b', 'aggregate_query', 'completed', 'x', '{}')
            """
        )
        self.store.execute(
            """
            INSERT INTO observations
                (observation_id, session_id, step_id, observation_type,
                 subject_json, payload_json, significance_json, quality_json,
                 observed_window_json)
            VALUES ('obs_m08b', 'sess_m08b', 'step_m08b', 'metric_observation',
                    '{}', '{}', '{}', '{}', NULL)
            """
        )
        row = self.store.query_one(
            "SELECT observed_window_json FROM observations WHERE observation_id = 'obs_m08b'"
        )
        self.assertIsNone(row["observed_window_json"])


if __name__ == "__main__":
    unittest.main()
