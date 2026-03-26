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

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


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


class CompareMetricTemporalTests(unittest.TestCase):
    """M-08.2: metric_query step fills observed_window and temporal_order."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Register a metric for testing
        r = cls.client.post("/semantic/entities", json={
            "name": "session_m08", "display_name": "Session M08",
            "keys": ["session_id"],
        })
        ent_id = r.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{ent_id}/publish")
        r = cls.client.post("/semantic/metrics", json={
            "name": "avg_duration_m08", "display_name": "Avg Duration M08",
            "definition_sql": "AVG(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type", "event_date"],
            "entity_id": ent_id,
        })
        met_id = r.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{met_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        r = self.client.post("/sessions", json={"goal": "temporal test"})
        return r.json()["session_id"]

    def _run_compare(self, session_id: str, dims: list[str]) -> list[dict]:
        resp = self.client.post(
            f"/sessions/{session_id}/steps/metric_query",
            json={
                "table": "analytics.watch_events",
                "metric": "avg_duration_m08",
                "dimensions": dims,
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-02-28", "end": "2026-03-06"},
                    "baseline": {"start": "2026-02-22", "end": "2026-02-28"},
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json().get("observations", [])

    def test_metric_query_observations_have_observed_window(self) -> None:
        sess = self._new_session()
        obs = self._run_compare(sess, ["platform"])
        if not obs:
            self.skipTest("No observations generated — check demo data date range")
        window = obs[0].get("observed_window")
        self.assertIsNotNone(window)
        self.assertIn("start", window)
        self.assertIn("end", window)
        self.assertIn("granularity", window)
        self.assertEqual(window["granularity"], "day")
        self.assertEqual(window["start"], "2026-02-28")
        self.assertEqual(window["end"], "2026-03-06")

    def test_metric_query_observed_window_has_string_dates(self) -> None:
        sess = self._new_session()
        obs = self._run_compare(sess, ["platform"])
        if not obs:
            self.skipTest("No observations generated")
        window = obs[0]["observed_window"]
        self.assertIsInstance(window["start"], str)
        self.assertIsInstance(window["end"], str)
        self.assertTrue(len(window["start"]) >= 8, "start should be a date string")

    def test_temporal_order_starts_at_zero_for_first_step(self) -> None:
        sess = self._new_session()
        obs = self._run_compare(sess, ["platform"])
        if not obs:
            self.skipTest("No observations generated")
        orders = sorted(o["temporal_order"] for o in obs)
        self.assertEqual(orders[0], 0, "First step observations must start at temporal_order=0")

    def test_temporal_order_increments_across_steps(self) -> None:
        sess = self._new_session()
        obs1 = self._run_compare(sess, ["platform"])
        obs2 = self._run_compare(sess, ["app_version"])
        if not obs1 or not obs2:
            self.skipTest("No observations generated")
        max_order_1 = max(o["temporal_order"] for o in obs1)
        min_order_2 = min(o["temporal_order"] for o in obs2)
        self.assertGreater(min_order_2, max_order_1,
                           "Second step observations must start after first step's temporal_order")

    def test_evidence_graph_includes_temporal_order(self) -> None:
        sess = self._new_session()
        obs = self._run_compare(sess, ["platform"])
        if not obs:
            self.skipTest("No observations generated")
        resp = self.client.get(f"/sessions/{sess}/evidence")
        self.assertEqual(resp.status_code, 200)
        graph_obs = resp.json().get("observations", [])
        self.assertTrue(len(graph_obs) > 0, "Evidence graph must contain observations")
        for o in graph_obs:
            self.assertIn("temporal_order", o)

    def test_evidence_graph_includes_observed_window(self) -> None:
        sess = self._new_session()
        obs = self._run_compare(sess, ["platform"])
        if not obs:
            self.skipTest("No observations generated")
        resp = self.client.get(f"/sessions/{sess}/evidence")
        graph_obs = resp.json().get("observations", [])
        self.assertTrue(len(graph_obs) > 0)
        for o in graph_obs:
            if "observed_window" in o:
                window = o["observed_window"]
                self.assertIn("start", window)
                self.assertIn("end", window)
                self.assertIn("granularity", window)
                return
        self.fail("No observation had an observed_window in the evidence graph")


class AggregateQueryTemporalTests(unittest.TestCase):
    """M-08.2: aggregate_query step temporal annotation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        r = self.client.post("/sessions", json={"goal": "agg temporal test"})
        return r.json()["session_id"]

    def _run_agg(self, session_id: str, **kwargs) -> list[dict]:
        body = {
            "table": "analytics.watch_events",
            "group_by": ["platform"],
            "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2026-03-01", "end": "2026-03-08"},
            },
            **kwargs,
        }
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query", json=body
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json().get("observations", [])

    def test_plain_aggregate_uses_request_level_observed_window(self) -> None:
        sess = self._new_session()
        obs = self._run_agg(sess)
        if not obs:
            self.skipTest("No observations generated")
        for o in obs:
            self.assertIn("observed_window", o)
            self.assertEqual(
                o["observed_window"],
                {
                    "start": "2026-03-01",
                    "end": "2026-03-08",
                    "granularity": "day",
                },
            )

    def test_compare_aggregate_uses_current_time_scope_window(self) -> None:
        sess = self._new_session()
        obs = self._run_agg(
            sess,
            time_scope={
                "mode": "compare",
                "grain": "day",
                "current": {"start": "2026-02-28", "end": "2026-03-06"},
                "baseline": {"start": "2026-02-22", "end": "2026-02-28"},
            },
        )
        if not obs:
            self.skipTest("No observations generated")
        for o in obs:
            self.assertIn("observed_window", o)
            self.assertEqual(
                o["observed_window"],
                {
                    "start": "2026-02-28",
                    "end": "2026-03-06",
                    "granularity": "day",
                },
            )

    def test_temporal_order_assigned_for_plain_aggregate(self) -> None:
        sess = self._new_session()
        obs = self._run_agg(sess)
        if not obs:
            self.skipTest("No observations generated")
        orders = sorted(o["temporal_order"] for o in obs)
        self.assertEqual(orders[0], 0, "First step temporal_order must start at 0")
        self.assertEqual(orders, list(range(len(orders))),
                         "temporal_order must be consecutive")

    def test_temporal_order_persists_in_evidence_graph(self) -> None:
        sess = self._new_session()
        obs = self._run_agg(sess)
        if not obs:
            self.skipTest("No observations generated")
        resp = self.client.get(f"/sessions/{sess}/evidence")
        graph_obs = resp.json().get("observations", [])
        self.assertTrue(len(graph_obs) > 0)
        for o in graph_obs:
            self.assertIn("temporal_order", o)
            self.assertIsInstance(o["temporal_order"], int)


if __name__ == "__main__":
    unittest.main()
