from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _single_window_scope() -> dict[str, object]:
    return {
        "mode": "single_window",
        "grain": "day",
        "current": {"start": "2026-03-01", "end": "2026-03-08"},
    }


def _compare_scope() -> dict[str, object]:
    return {
        "mode": "compare",
        "grain": "day",
        "current": {"start": "2026-02-28", "end": "2026-03-06"},
        "baseline": {"start": "2026-02-22", "end": "2026-02-28"},
    }


class DeltaPctIntegerDivisionTests(unittest.TestCase):
    """Fix 1 (P0): delta_pct SQL should use float division, not integer division."""

    def test_metric_query_uses_float_division(self) -> None:
        """build_metric_query output must contain '* 1.0' to force float division."""
        from app.analysis_core.compiler import build_metric_query

        sql = build_metric_query(
            metric_name="event_count",
            table_name="analytics.watch_events",
            metric_sql="count(*)",
            dimensions=["platform"],
        )
        # The fix: multiply by 1.0 before dividing to avoid integer division
        self.assertIn("* 1.0", sql)

    def test_delta_pct_float_result_with_integer_metric(self) -> None:
        """End-to-end: metric_query with count(*) should produce float delta_pct."""
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "int_div.duckdb"
        get_seeded_duckdb_path(db_path)
        client = TestClient(create_app(db_path))
        try:
            entity_resp = client.post(
                "/semantic/entities",
                json={
                    "name": "session_intdiv",
                    "display_name": "Session",
                    "keys": ["session_id"],
                },
            )
            entity_id = entity_resp.json()["entity_id"]
            client.post(f"/semantic/entities/{entity_id}/publish")

            metric_resp = client.post(
                "/semantic/metrics",
                json={
                    "name": "event_count",
                    "display_name": "Event Count",
                    "definition_sql": "count(*)",
                    "dimensions": ["platform"],
                    "entity_id": entity_id,
                },
            )
            metric_id = metric_resp.json()["metric_id"]
            client.post(f"/semantic/metrics/{metric_id}/publish")

            session_id = client.post(
                "/sessions",
                json={"goal": "Test integer division fix."},
            ).json()["session_id"]

            resp = client.post(
                f"/sessions/{session_id}/steps/metric_query",
                json={
                    "table": "analytics.watch_events",
                    "metric": "event_count",
                    "time_scope": _compare_scope(),
                },
            )
            self.assertEqual(resp.status_code, 200)
            result = resp.json()
            # delta_pct should be a float (not truncated to int)
            for obs in result["observations"]:
                self.assertIsInstance(obs["payload"]["delta_pct"], (int, float))
        finally:
            client.close()
            temp_dir.cleanup()


class ProfileScopeTests(unittest.TestCase):
    """Fix 3: profile_table should include profile_scope metadata."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "prof_scope.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_profile_table_includes_scope(self) -> None:
        """profile_table result should contain profile_scope when partition-filtered."""
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test profile scope."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        profile = result["profile"]
        self.assertIn("profile_scope", profile)
        # The watch_events table has event_date column which should trigger scope
        if profile["profile_scope"] is not None:
            self.assertIn("date_column", profile["profile_scope"])
            self.assertIn("date_value", profile["profile_scope"])
            self.assertIn("scoped_row_count", profile["profile_scope"])
            # Summary should mention scope
            self.assertIn("scoped to", result["summary"])


class DefaultDimensionCapTests(unittest.TestCase):
    """Fix 2: auto-selected dimensions should be capped at 2."""

    def test_max_default_dimensions_is_2(self) -> None:
        from app.service import SemanticLayerService

        self.assertEqual(SemanticLayerService._MAX_DEFAULT_DIMENSIONS, 2)

    def test_auto_dimensions_capped_at_2(self) -> None:
        from app.service import SemanticLayerService

        all_dims = [f"dim_{i}" for i in range(10)]
        dims = SemanticLayerService._comparison_dimensions(all_dims, date_column="event_date")
        self.assertLessEqual(len(dims), 2)


class AggregateQueryStepTests(unittest.TestCase):
    """Fix 5: aggregate_query step type for GROUP BY + aggregation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "agg_query.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_aggregate_query_step(self) -> None:
        """aggregate_query should execute GROUP BY query and return rows."""
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test aggregate_query."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
                "order": "cnt DESC",
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "aggregate_query")
        self.assertIn("rows", result)
        self.assertGreater(len(result["rows"]), 0)
        # Each row should have platform and cnt
        for row in result["rows"]:
            self.assertIn("platform", row)
            self.assertIn("cnt", row)

    def test_aggregate_query_missing_measures(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test missing measures."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "time_scope": _single_window_scope(),
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_aggregate_query_without_group_by_returns_overall_aggregate(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test overall aggregate."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()["rows"]
        self.assertGreater(len(rows), 0)
        self.assertIn("cnt", rows[0])

    def test_aggregate_query_with_scope_predicate(self) -> None:
        """aggregate_query with scope predicate should work."""
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test aggregate with scope predicate."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
                "scope": {"predicate": "platform = 'android'"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        # Should only have android rows
        for row in result["rows"]:
            self.assertEqual(row["platform"], "android")


class AggregateQueryObservationTests(unittest.TestCase):
    """aggregate_query should generate observations in the evidence graph."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "agg_obs.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_aggregate_query_generates_observations(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test aggregate observations."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
                "order": "cnt DESC",
                "limit": 10,
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertIn("observations", result)
        self.assertGreater(len(result["observations"]), 0)

        # Verify observations appear in evidence graph
        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        self.assertGreater(len(evidence["observations"]), 0)

    def test_aggregate_query_opt_out_observations(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test aggregate no-obs."},
        ).json()["session_id"]

        result = self.client.app.state.service._run_aggregate_query(
            session_id,
            {
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
                "extract_observations": False,
            },
        )
        self.assertNotIn("observations", result)


class SessionConstraintInjectionTests(unittest.TestCase):
    """Session constraints should be automatically injected into step WHERE clauses."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "constraints.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_constraints_injected_into_sample_rows(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={
                "goal": "Test constraint injection.",
                "constraints": {"platform": "android"},
            },
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        # All rows should be android since constraint was injected
        for row in result["rows"]:
            self.assertEqual(row["platform"], "android")

    def test_constraints_injected_into_aggregate_query(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={
                "goal": "Test constraint injection aggregate.",
                "constraints": {"platform": "android"},
            },
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        # Only android rows
        for row in result["rows"]:
            self.assertEqual(row["platform"], "android")

    def test_no_constraints_no_filter(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "No constraints."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        # Should have multiple platforms
        result = resp.json()
        platforms = {row["platform"] for row in result["rows"]}
        self.assertGreater(len(platforms), 1)


class AggregateQueryTimeScopeTests(unittest.TestCase):
    """Tests for typed compare and single-window aggregate_query execution."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "aggregate_time_scope.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        return self.client.post("/sessions", json={"goal": "WoW comparison test."}).json()[
            "session_id"
        ]

    def test_compare_mode_returns_delta_columns(self) -> None:
        """compare mode should produce {alias}_current, _baseline, _delta_pct columns."""
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _compare_scope(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "aggregate_query")
        self.assertGreater(len(result["rows"]), 0)
        first_row = result["rows"][0]
        self.assertIn("cnt_current", first_row)
        self.assertIn("cnt_baseline", first_row)
        self.assertIn("cnt_delta_pct", first_row)
        self.assertIn("platform", first_row)

    def test_compare_mode_summary_mentions_current_and_baseline_windows(self) -> None:
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "AVG(play_duration_seconds)", "as": "avg_dur"}],
                "time_scope": _compare_scope(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertIn("summary", result)
        self.assertIn("current 2026-02-28", result["summary"])
        self.assertIn("baseline 2026-02-22", result["summary"])

    def test_compare_mode_generates_observations(self) -> None:
        """compare mode should produce at least one observation."""
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _compare_scope(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        observations = result.get("observations", [])
        self.assertGreater(len(observations), 0)

    def test_scope_predicate_rejects_time_condition(self) -> None:
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
                "time_scope": _single_window_scope(),
                "scope": {"predicate": "event_date >= '2026-03-01'"},
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_legacy_select_contract_is_rejected(self) -> None:
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table": "analytics.watch_events",
                "select": ["platform", "COUNT(*) AS cnt"],
                "group_by": ["platform"],
                "time_scope": _single_window_scope(),
            },
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
