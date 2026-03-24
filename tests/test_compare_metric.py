from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class MetricResolutionTests(unittest.TestCase):
    """Tests for resolving metrics from semantic layer and compare_metric step."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "metric_res.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Seed a published metric via the semantic API
        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "session",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "watch_time",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        cls.metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{cls.metric_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_resolve_metric_sql(self) -> None:
        service = self.client.app.state.service
        sql = service.resolve_metric_sql("watch_time")
        self.assertEqual(sql, "avg(play_duration_seconds)")

    def test_resolve_metric_dimensions(self) -> None:
        service = self.client.app.state.service
        dims = service.resolve_metric_dimensions("watch_time")
        self.assertEqual(dims, ["platform", "app_version", "network_type", "content_type"])

    def test_resolve_metric_not_found(self) -> None:
        service = self.client.app.state.service
        self.assertIsNone(service.resolve_metric_sql("nonexistent_metric"))
        self.assertIsNone(service.resolve_metric_dimensions("nonexistent_metric"))

    def test_compare_metric_step(self) -> None:
        session = self.client.post(
            "/sessions", json={"goal": "Test compare_metric step."},
        ).json()
        session_id = session["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time",
                "table_name": "analytics.watch_events",
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "compare_metric")
        self.assertEqual(result["metric_name"], "watch_time")
        self.assertIn("summary", result)
        self.assertGreaterEqual(len(result["observations"]), 1)

    def test_compare_metric_missing_params(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing params."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_compare_metric_rejects_step_level_filter(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test filter rejection."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time",
                "table_name": "analytics.watch_events",
                "filter": "platform = 'android'",
            },
        )
        self.assertEqual(resp.status_code, 400)
        detail = resp.json().get("detail", "")
        self.assertIn("filter", detail)
        self.assertIn("raw_filter", detail)

    def test_compare_metric_rejects_step_level_where(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test where rejection."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time",
                "table_name": "analytics.watch_events",
                "where": "platform = 'android'",
            },
        )
        self.assertEqual(resp.status_code, 400)
        detail = resp.json().get("detail", "")
        self.assertIn("where", detail)
        self.assertIn("raw_filter", detail)

    def test_compare_metric_unpublished_metric(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test unpublished metric."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={"metric_name": "nonexistent", "table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_build_comparison_query(self) -> None:
        service = self.client.app.state.service
        query = service.build_comparison_query(
            metric_name="watch_time",
            table_name="analytics.watch_events",
            metric_sql="avg(play_duration_seconds)",
            dimensions=["platform", "app_version"],
        )
        self.assertIn("current_value", query)
        self.assertIn("baseline_value", query)
        self.assertIn("delta_pct", query)
        self.assertIn("analytics.watch_events", query)


class CustomPeriodTests(unittest.TestCase):
    """Fix 5: compare_metric with user-supplied period_start/period_end."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "custom_period.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "session_period",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "watch_time_period",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{metric_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_custom_period_bounds(self) -> None:
        """compare_metric with period_start/period_end should succeed."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test custom period."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time_period",
                "table_name": "analytics.watch_events",
                "period_start": "2025-01-01",
                "period_end": "2025-01-14",
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "compare_metric")
        self.assertIn("summary", result)


class ComparisonDimensionsTests(unittest.TestCase):
    """Tests for _comparison_dimensions static method."""

    def test_excludes_date_column(self) -> None:
        from app.service import SemanticLayerService
        dims = SemanticLayerService._comparison_dimensions(
            ["platform", "log_date", "app_version"],
            date_column="log_date",
        )
        self.assertNotIn("log_date", dims)
        self.assertEqual(dims, ["platform", "app_version"])

    def test_excludes_temporal_dimensions_when_no_requested(self) -> None:
        from app.service import SemanticLayerService
        dims = SemanticLayerService._comparison_dimensions(
            ["platform", "log_date", "log_hour", "app_version", "network_type"],
            date_column="log_date",
        )
        self.assertNotIn("log_date", dims)
        self.assertNotIn("log_hour", dims)
        # After temporal exclusion, non-temporal dims are ["platform", "app_version", "network_type"]
        # but capped at _MAX_DEFAULT_DIMENSIONS (2)
        self.assertEqual(dims, ["platform", "app_version"])

    def test_caps_at_max_default_dimensions(self) -> None:
        from app.service import SemanticLayerService
        all_dims = [f"dim_{i}" for i in range(10)]
        dims = SemanticLayerService._comparison_dimensions(
            all_dims, date_column="event_date",
        )
        self.assertEqual(len(dims), SemanticLayerService._MAX_DEFAULT_DIMENSIONS)

    def test_explicit_requested_only_excludes_date_column(self) -> None:
        """When caller specifies dimensions, only the date_column is stripped."""
        from app.service import SemanticLayerService
        dims = SemanticLayerService._comparison_dimensions(
            ["platform", "log_date", "log_hour"],
            date_column="log_date",
            requested=["log_hour", "platform"],
        )
        # log_hour is kept because caller explicitly asked for it
        self.assertIn("log_hour", dims)
        self.assertIn("platform", dims)
        self.assertNotIn("log_date", dims)

    def test_empty_after_temporal_exclusion(self) -> None:
        """All dimensions are temporal → returns empty list."""
        from app.service import SemanticLayerService
        dims = SemanticLayerService._comparison_dimensions(
            ["log_date", "log_hour"],
            date_column="log_date",
        )
        self.assertEqual(dims, [])


class MultipleStepRunTests(unittest.TestCase):
    """Fix 1: multiple runs of the same step type should accumulate observations."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "multi_step.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Seed a metric with multiple dimensions
        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "session_multi",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "watch_time_multi",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{metric_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_multiple_compare_metric_preserves_all_observations(self) -> None:
        """Running compare_metric twice in the same session should keep both sets of observations."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test multiple compare_metric runs."},
        ).json()["session_id"]

        # First run: group by platform
        resp1 = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time_multi",
                "table_name": "analytics.watch_events",
                "dimensions": ["platform"],
            },
        )
        self.assertEqual(resp1.status_code, 200)
        obs_count_1 = len(resp1.json()["observations"])
        self.assertGreaterEqual(obs_count_1, 1)

        # Second run: group by network_type
        resp2 = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time_multi",
                "table_name": "analytics.watch_events",
                "dimensions": ["network_type"],
            },
        )
        self.assertEqual(resp2.status_code, 200)
        obs_count_2 = len(resp2.json()["observations"])
        self.assertGreaterEqual(obs_count_2, 1)

        # Evidence graph should contain observations from BOTH runs
        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        total_obs = len(evidence["observations"])
        self.assertEqual(total_obs, obs_count_1 + obs_count_2)

        # Should have 2 steps in the evidence
        compare_steps = [s for s in evidence["steps"] if s["step_type"] == "compare_metric"]
        self.assertEqual(len(compare_steps), 2)


class DimensionDateColumnErrorTests(unittest.TestCase):
    """Fix 3: requesting date_column as dimension should raise clear error."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "dim_error.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "session_dim_err",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "watch_time_dim_err",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["event_date", "platform", "app_version"],
            "entity_id": entity_id,
        })
        metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{metric_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_dimension_equals_date_column_returns_error(self) -> None:
        """Requesting dimensions=['event_date'] when event_date is the date column should fail clearly."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test dim=date_column error."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time_dim_err",
                "table_name": "analytics.watch_events",
                "dimensions": ["event_date"],
            },
        )
        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("period-splitting column", detail)
        self.assertIn("event_date", detail)


class TemporalDimensionIntegrationTests(unittest.TestCase):
    """Integration test: compare_metric with temporal dimensions still returns results."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "temporal_test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Seed a metric whose dimensions include the date column
        entity_resp = cls.client.post("/semantic/entities", json={
            "name": "session_temporal",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")

        metric_resp = cls.client.post("/semantic/metrics", json={
            "name": "watch_time_temporal",
            "display_name": "Watch Time (temporal dims)",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["event_date", "platform", "app_version"],
            "entity_id": entity_id,
        })
        metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{metric_id}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_compare_metric_with_temporal_dims_returns_results(self) -> None:
        """compare_metric should auto-exclude temporal dims and return rows."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test temporal dim exclusion."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={
                "metric_name": "watch_time_temporal",
                "table_name": "analytics.watch_events",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.json())
        result = resp.json()
        self.assertEqual(result["step_type"], "compare_metric")
        # Should NOT say "no results" — temporal dimensions were excluded
        self.assertNotIn("no results", result["summary"])
        self.assertGreaterEqual(len(result["observations"]), 1)


if __name__ == "__main__":
    unittest.main()
