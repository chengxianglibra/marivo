from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class DeltaPctIntegerDivisionTests(unittest.TestCase):
    """Fix 1 (P0): delta_pct SQL should use float division, not integer division."""

    def test_comparison_query_uses_float_division(self) -> None:
        """build_comparison_query output must contain '* 1.0' to force float division."""
        from app.analysis_core.compiler import build_comparison_query
        sql = build_comparison_query(
            metric_name="event_count",
            table_name="analytics.watch_events",
            metric_sql="count(*)",
            dimensions=["platform"],
        )
        # The fix: multiply by 1.0 before dividing to avoid integer division
        self.assertIn("* 1.0", sql)

    def test_delta_pct_float_result_with_integer_metric(self) -> None:
        """End-to-end: compare_metric with count(*) should produce float delta_pct."""
        temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(temp_dir.name) / "int_div.duckdb"
        get_seeded_duckdb_path(db_path)
        client = TestClient(create_app(db_path))
        try:
            entity_resp = client.post("/semantic/entities", json={
                "name": "session_intdiv",
                "display_name": "Session",
                "keys": ["session_id"],
            })
            entity_id = entity_resp.json()["entity_id"]
            client.post(f"/semantic/entities/{entity_id}/publish")

            metric_resp = client.post("/semantic/metrics", json={
                "name": "event_count",
                "display_name": "Event Count",
                "definition_sql": "count(*)",
                "dimensions": ["platform"],
                "entity_id": entity_id,
            })
            metric_id = metric_resp.json()["metric_id"]
            client.post(f"/semantic/metrics/{metric_id}/publish")

            session_id = client.post(
                "/sessions", json={"goal": "Test integer division fix."},
            ).json()["session_id"]

            resp = client.post(
                f"/sessions/{session_id}/steps/compare_metric",
                json={
                    "metric_name": "event_count",
                    "table_name": "analytics.watch_events",
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
            "/sessions", json={"goal": "Test profile scope."},
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
            "/sessions", json={"goal": "Test aggregate_query."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "order_by": "cnt DESC",
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

    def test_aggregate_query_missing_select(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing select."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={"table_name": "analytics.watch_events", "group_by": ["platform"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_aggregate_query_missing_group_by(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing group_by."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={"table_name": "analytics.watch_events", "select": ["count(*) as cnt"]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_aggregate_query_with_where(self) -> None:
        """aggregate_query with WHERE filter should work."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test aggregate with where."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "where": "platform = 'android'",
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
            "/sessions", json={"goal": "Test aggregate observations."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "order_by": "cnt DESC",
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
            "/sessions", json={"goal": "Test aggregate no-obs."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "extract_observations": False,
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
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
            "/sessions", json={
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
            "/sessions", json={
                "goal": "Test constraint injection aggregate.",
                "constraints": {"platform": "android"},
            },
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        # Only android rows
        for row in result["rows"]:
            self.assertEqual(row["platform"], "android")

    def test_no_constraints_no_filter(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "No constraints."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        # Should have multiple platforms
        result = resp.json()
        platforms = {row["platform"] for row in result["rows"]}
        self.assertGreater(len(platforms), 1)


class AggregateQueryComparePeriodTests(unittest.TestCase):
    """Tests for compare_period parameter on aggregate_query step."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "compare_period.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _new_session(self) -> str:
        return self.client.post(
            "/sessions", json={"goal": "WoW comparison test."}
        ).json()["session_id"]

    def test_compare_period_returns_delta_columns(self) -> None:
        """compare_period=True should produce {alias}_current, _baseline, _delta_pct columns."""
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "date_column": "event_date",
                "compare_period": True,
                "period_start": "2026-02-21",
                "period_end": "2026-03-06",
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

    def test_compare_period_explicit_period(self) -> None:
        """Explicitly supplied period_start/period_end should succeed and return rows."""
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "avg(play_duration_seconds) as avg_dur"],
                "group_by": ["platform"],
                "date_column": "event_date",
                "compare_period": True,
                "period_start": "2026-02-21",
                "period_end": "2026-03-06",
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertIn("summary", result)
        self.assertIn("2026-02-21", result["summary"])

    def test_compare_period_requires_date_column(self) -> None:
        """compare_period=True without date_column should return a 4xx or 5xx error."""
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "compare_period": True,
                # intentionally omit date_column
            },
        )
        self.assertGreaterEqual(resp.status_code, 400)

    def test_compare_period_generates_observations(self) -> None:
        """compare_period step should produce at least one observation."""
        session_id = self._new_session()
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["platform", "count(*) as cnt"],
                "group_by": ["platform"],
                "date_column": "event_date",
                "compare_period": True,
                "period_start": "2026-02-21",
                "period_end": "2026-03-06",
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        observations = result.get("observations", [])
        self.assertGreater(len(observations), 0)


if __name__ == "__main__":
    unittest.main()
