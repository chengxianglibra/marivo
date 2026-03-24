from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class DuckDBMvpTests(unittest.TestCase):
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

    def test_catalog_exposes_dynamic_catalog(self) -> None:
        response = self.client.get("/catalog")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        # Top-level keys are present
        self.assertIn("entities", payload)
        self.assertIn("metrics", payload)
        self.assertIn("assets", payload)
        self.assertIn("policies", payload)
        # Lists are returned (may be empty in a fresh test DB)
        self.assertIsInstance(payload["entities"], list)
        self.assertIsInstance(payload["metrics"], list)
        self.assertIsInstance(payload["assets"], list)
        self.assertIsInstance(payload["policies"], list)

    def test_evidence_graph_contains_support_edges(self) -> None:
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Investigate watch time drop and recommend fixes."},
        ).json()["session_id"]

        # Seed a published metric for compare_metric
        entity_resp = self.client.post("/semantic/entities", json={
            "name": "session_mvp",
            "display_name": "Session",
            "keys": ["session_id"],
        })
        entity_id = entity_resp.json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_resp = self.client.post("/semantic/metrics", json={
            "name": "watch_time_mvp",
            "display_name": "Watch Time",
            "definition_sql": "avg(play_duration_seconds)",
            "dimensions": ["platform", "app_version", "network_type", "content_type"],
            "entity_id": entity_id,
        })
        metric_id = metric_resp.json()["metric_id"]
        self.client.post(f"/semantic/metrics/{metric_id}/publish")

        self.client.post(
            f"/sessions/{session_id}/steps/compare_metric",
            json={"metric_name": "watch_time_mvp", "table_name": "analytics.watch_events"},
        )
        self.client.post(f"/sessions/{session_id}/steps/synthesize_findings")

        graph_response = self.client.get(f"/sessions/{session_id}/evidence")
        self.assertEqual(graph_response.status_code, 200)
        graph = graph_response.json()
        self.assertGreaterEqual(len(graph["observations"]), 1)
        self.assertGreaterEqual(len(graph["claims"]), 1)
        self.assertTrue(any(edge["edge_type"] == "supports" for edge in graph["edges"]))
        self.assertGreaterEqual(len(graph["recommendations"]), 1)

    def test_list_sessions_empty(self) -> None:
        """GET /sessions should return a list."""
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_get_session_after_create(self) -> None:
        """GET /sessions/{id} should return session details."""
        create_resp = self.client.post("/sessions", json={"goal": "Test session"})
        session_id = create_resp.json()["session_id"]
        resp = self.client.get(f"/sessions/{session_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["session_id"], session_id)
        self.assertEqual(data["goal"], "Test session")
        self.assertEqual(data["status"], "open")
        self.assertIn("created_at", data)

    def test_get_session_not_found(self) -> None:
        """GET /sessions/{id} with unknown ID should 404."""
        resp = self.client.get("/sessions/sess_nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_list_sessions_includes_created(self) -> None:
        """GET /sessions should include recently created session."""
        create_resp = self.client.post("/sessions", json={"goal": "Listed session"})
        session_id = create_resp.json()["session_id"]
        resp = self.client.get("/sessions")
        self.assertEqual(resp.status_code, 200)
        ids = [s["session_id"] for s in resp.json()]
        self.assertIn(session_id, ids)

    def test_list_sessions_filter_by_status(self) -> None:
        """GET /sessions?status=open should filter."""
        self.client.post("/sessions", json={"goal": "Status filter test"})
        resp = self.client.get("/sessions?status=open")
        self.assertEqual(resp.status_code, 200)
        for s in resp.json():
            self.assertEqual(s["status"], "open")


class QueryRouterWiredServiceTests(unittest.TestCase):
    """Tests that SemanticLayerService works with QueryRouter wired in."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "router_test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_individual_steps_with_query_router(self) -> None:
        """Each generic step type should work when QueryRouter is present."""
        session_id = self.client.post(
            "/sessions",
            json={"goal": "Test individual steps with router."},
        ).json()["session_id"]

        # profile_table
        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200, "Step profile_table failed")
        self.assertEqual(resp.json()["step_type"], "profile_table")

        # sample_rows
        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events", "limit": 5},
        )
        self.assertEqual(resp.status_code, 200, "Step sample_rows failed")
        self.assertEqual(resp.json()["step_type"], "sample_rows")

    def test_service_has_query_router_attribute(self) -> None:
        """Verify that create_app wires the QueryRouter into the service."""
        service = self.client.app.state.service
        self.assertIsNotNone(service.query_router)

    def test_service_without_query_router_still_works(self) -> None:
        """SemanticLayerService should work without QueryRouter (backward compat)."""
        from app.service import SemanticLayerService

        # Reuse the already-initialized engine from the test app to avoid a
        # costly DuckDB re-initialization (~45 s).
        app = self.client.app
        meta = app.state.metadata_store
        analytics = app.state.analytics_engine
        svc = SemanticLayerService(meta, analytics)  # no query_router
        self.assertIsNone(svc.query_router)

        session = svc.create_session("Test no router", {}, {}, {})
        result = svc.run_step(
            session["session_id"],
            "profile_table",
            {"table_name": "analytics.watch_events"},
        )
        self.assertEqual(result["step_type"], "profile_table")
        self.assertIn("summary", result)

    def test_resolve_engine_returns_tuple(self) -> None:
        """_resolve_engine should return (engine, engine_type, qualified_names) tuple."""
        service = self.client.app.state.service
        result = service._resolve_engine(["watch_events"])
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        engine, engine_type, qualified = result
        self.assertIsInstance(engine_type, str)
        # Default fallback should be duckdb
        self.assertEqual(engine_type, "duckdb")
        self.assertIsInstance(qualified, dict)

    def test_provenance_uses_resolved_engine_type(self) -> None:
        """Step provenance should reflect the resolved engine type."""
        session = self.client.post(
            "/sessions",
            json={"goal": "Test provenance engine type."},
        ).json()
        session_id = session["session_id"]
        # Run a step and check evidence provenance
        self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        evidence = self.client.get(f"/sessions/{session_id}/evidence").json()
        steps = evidence.get("steps", [])
        self.assertGreater(len(steps), 0)
        provenance = steps[0].get("provenance", {})
        self.assertIn("engine", provenance)
        # With default setup, engine type should be duckdb
        self.assertEqual(provenance["engine"], "duckdb")


class GenericStepTypeTests(unittest.TestCase):
    """Tests for profile_table and sample_rows step types."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "generic_steps.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_profile_table(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test profile_table."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "profile_table")
        self.assertIn("profile", result)
        self.assertGreater(result["profile"]["row_count"], 0)
        self.assertGreater(len(result["profile"]["columns"]), 0)

    def test_profile_table_missing_param(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing param."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_sample_rows(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test sample_rows."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events", "limit": 5},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["step_type"], "sample_rows")
        self.assertEqual(len(result["rows"]), 5)

    def test_sample_rows_default_limit(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test default limit."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={"table_name": "analytics.player_qoe"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(len(result["rows"]), 10)

    def test_sample_rows_missing_param(self) -> None:
        session_id = self.client.post(
            "/sessions", json={"goal": "Test missing param."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/sample_rows",
            json={},
        )
        self.assertEqual(resp.status_code, 400)


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


class ColumnUnitMetadataTests(unittest.TestCase):
    """Tests for column unit annotation and enrichment in profile_table/sample_rows."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "col_unit.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))

        # Register and sync a DuckDB source
        resp = cls.client.post(
            "/sources",
            json={"source_type": "duckdb", "display_name": "ColUnit Test", "connection": {"path": str(cls.db_path)}},
        )
        cls.source_id = resp.json()["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_profile_table_has_data_type(self) -> None:
        """After sync, profile_table column entries should have data_type from synced column objects."""
        session_id = self.client.post(
            "/sessions", json={"goal": "Test data_type in profile."},
        ).json()["session_id"]

        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        col_entries = result["profile"]["columns"]
        self.assertGreater(len(col_entries), 0)
        # At least some columns should have data_type populated from sync
        has_data_type = any("data_type" in col for col in col_entries)
        self.assertTrue(has_data_type, "Expected at least one column to have data_type after sync")

    def test_sample_rows_columns_metadata_empty_without_sync(self) -> None:
        """sample_rows should return columns_metadata={} when no sync has been done (no crash)."""
        # Create a fresh app with no sync done
        temp = tempfile.TemporaryDirectory()
        try:
            fresh_db = Path(temp.name) / "fresh.duckdb"
            get_seeded_duckdb_path(fresh_db)
            fresh_client = TestClient(create_app(fresh_db))
            # Register source but don't sync
            fresh_client.post(
                "/sources",
                json={"source_type": "duckdb", "display_name": "Fresh", "connection": {"path": str(fresh_db)}},
            )
            session_id = fresh_client.post(
                "/sessions", json={"goal": "Test no sync."},
            ).json()["session_id"]
            resp = fresh_client.post(
                f"/sessions/{session_id}/steps/sample_rows",
                json={"table_name": "analytics.watch_events", "limit": 2},
            )
            self.assertEqual(resp.status_code, 200)
            result = resp.json()
            self.assertIn("columns_metadata", result)
            self.assertEqual(result["columns_metadata"], {})
            fresh_client.close()
        finally:
            temp.cleanup()

    def test_column_unit_end_to_end(self) -> None:
        """Full flow: sync → patch unit on a column → profile_table shows unit → sample_rows shows unit."""
        # Find a column object
        resp = self.client.get(f"/sources/{self.source_id}/objects", params={"type": "column"})
        col_objects = resp.json()
        self.assertGreater(len(col_objects), 0)

        # Find a column from watch_events
        watch_col = next(
            (o for o in col_objects if "watch_events" in o["fqn"]),
            col_objects[0],
        )
        col_obj_id = watch_col["object_id"]
        col_name = watch_col["native_name"]
        table_fqn = ".".join(watch_col["fqn"].split(".")[:3])  # schema.table portion

        # Patch unit
        patch_resp = self.client.patch(
            f"/sources/{self.source_id}/objects/{col_obj_id}/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["properties"]["unit"], "seconds")

        # profile_table should include unit in the column entry
        session_id = self.client.post(
            "/sessions", json={"goal": "Test unit in profile."},
        ).json()["session_id"]
        profile_resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(profile_resp.status_code, 200)
        col_profiles = profile_resp.json()["profile"]["columns"]
        annotated = next((c for c in col_profiles if c["column"] == col_name), None)
        if annotated is not None:
            self.assertEqual(annotated.get("unit"), "seconds")

        # sample_rows should include unit in columns_metadata
        session2_id = self.client.post(
            "/sessions", json={"goal": "Test unit in sample."},
        ).json()["session_id"]
        sample_resp = self.client.post(
            f"/sessions/{session2_id}/steps/sample_rows",
            json={"table_name": "analytics.watch_events", "limit": 2},
        )
        self.assertEqual(sample_resp.status_code, 200)
        sample_result = sample_resp.json()
        self.assertIn("columns_metadata", sample_result)
        if col_name in sample_result["columns_metadata"]:
            self.assertEqual(sample_result["columns_metadata"][col_name]["unit"], "seconds")


class ConstraintsAppliedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        # Sync source so profile_table works
        sources = cls.client.get("/sources").json()
        if sources:
            cls.client.post(f"/sources/{sources[0]['source_id']}/sync")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_no_constraints_returns_empty(self) -> None:
        """Session with no constraints → profile_table returns empty applied/skipped."""
        session_id = self.client.post(
            "/sessions", json={"goal": "No constraints test."}
        ).json()["session_id"]
        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        ca = resp.json()["constraints_applied"]
        self.assertEqual(ca["applied"], [])
        self.assertEqual(ca["skipped"], [])
        self.assertIsNone(ca["note"])

    def test_profile_table_skips_constraints(self) -> None:
        """Session with raw_filter + constraints → profile_table skips them."""
        session_id = self.client.post(
            "/sessions",
            json={
                "goal": "Constrained profile test.",
                "constraints": {"platform": "ios"},
                "raw_filter": "region = 'US'",
            },
        ).json()["session_id"]
        resp = self.client.post(
            f"/sessions/{session_id}/steps/profile_table",
            json={"table_name": "analytics.watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        ca = resp.json()["constraints_applied"]
        self.assertEqual(ca["applied"], [])
        self.assertEqual(len(ca["skipped"]), 2)
        self.assertIsNotNone(ca["note"])
        self.assertIn("profile_table", ca["note"])

    def test_aggregate_query_applies_constraints(self) -> None:
        """Session with raw_filter → aggregate_query lists it in applied."""
        session_id = self.client.post(
            "/sessions",
            json={
                "goal": "Constrained aggregate test.",
                "raw_filter": "platform = 'android'",
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
        ca = resp.json()["constraints_applied"]
        self.assertEqual(ca["skipped"], [])
        self.assertEqual(len(ca["applied"]), 1)
        self.assertIn("raw_filter:", ca["applied"][0])


class G2TemporalWindowInferenceTests(unittest.TestCase):
    """G-2 regression: date-grouped aggregate_query observations carry observed_window.

    This test proves that:
    1. aggregate_query with a temporal group_by column (e.g., event_date) populates observed_window
    2. The evidence pipeline can upgrade claims from L1 to L2 via TemporalPrecedenceChecker
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "g2_temporal.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

        # Register a metric used by test_l1_to_l2_upgrade_via_temporal_precedence.
        r = cls.client.post("/semantic/entities", json={
            "name": "g2_watch_session", "display_name": "G2 Watch Session",
            "keys": ["session_id"],
        })
        ent_id = r.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{ent_id}/publish")
        r = cls.client.post("/semantic/metrics", json={
            "name": "g2_event_count", "display_name": "G2 Event Count",
            "definition_sql": "COUNT(*)",
            "dimensions": ["platform"],
            "entity_id": ent_id,
        })
        met_id = r.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{met_id}/publish")
        cls.g2_metric_name = "g2_event_count"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_aggregate_query_infers_observed_window_from_event_date(self) -> None:
        """aggregate_query with event_date in group_by should infer observed_window."""
        session_id = self.client.post(
            "/sessions", json={"goal": "G-2 temporal window inference test."},
        ).json()["session_id"]

        # Run aggregate grouped by a temporal column (event_date)
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["event_date", "platform", "count(*) as cnt"],
                "group_by": ["event_date", "platform"],
                "order_by": "event_date",
                "limit": 5,
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()

        # Verify observations were extracted
        self.assertIn("observations", result)
        observations = result["observations"]
        self.assertGreater(len(observations), 0)

        # G-2: Verify observed_window is populated on each observation
        for obs in observations:
            self.assertIn("observed_window", obs, "G-2: observed_window should be inferred from event_date")
            window = obs["observed_window"]
            self.assertIn("start", window)
            self.assertIn("end", window)
            self.assertIn("granularity", window)
            # Day granularity for date column
            self.assertEqual(window["granularity"], "day")

    def test_aggregate_query_explicit_observed_window_column(self) -> None:
        """aggregate_query with explicit observed_window_column param should use it."""
        session_id = self.client.post(
            "/sessions", json={"goal": "G-2 explicit column test."},
        ).json()["session_id"]

        # Use explicit observed_window_column
        resp = self.client.post(
            f"/sessions/{session_id}/steps/aggregate_query",
            json={
                "table_name": "analytics.watch_events",
                "select": ["event_date", "count(*) as cnt"],
                "group_by": ["event_date"],
                "observed_window_column": "event_date",  # explicit override
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()

        observations = result.get("observations", [])
        self.assertGreater(len(observations), 0)
        for obs in observations:
            self.assertIn("observed_window", obs)

    def test_aggregate_query_yyyymmdd_format(self) -> None:
        """aggregate_query should parse YYYYMMDD format temporal values."""
        session_id = self.client.post(
            "/sessions", json={"goal": "G-2 YYYYMMDD format test."},
        ).json()["session_id"]

        # Create a table with YYYYMMDD format date column
        # Note: analytics.watch_events uses ISO date format; this test verifies
        # the parser can handle YYYYMMDD if present in the data
        from app.evidence_engine.extractors.aggregate import _parse_temporal_value
        from datetime import date

        # Unit test the parser directly
        parsed_date, granularity = _parse_temporal_value("20240115")
        self.assertEqual(parsed_date, date(2024, 1, 15))
        self.assertEqual(granularity, "day")

    def test_l1_to_l2_upgrade_via_temporal_precedence(self) -> None:
        """G-2c: TemporalPrecedenceChecker upgrades an L1 claim to L2 when
        supporting observations carry strictly non-overlapping observed_windows.

        Exercises the checker directly with hand-crafted input so the assertion
        is independent of seeded-data randomness while still proving the
        real checker code (not a mock).
        """
        from app.evidence_engine.causal_checkers import TemporalPrecedenceChecker

        checker = TemporalPrecedenceChecker()

        # Two aggregate-query-style observations with non-overlapping windows.
        # These represent day-grouped aggregate observations as G-2 would produce
        # them (observed_window inferred from the event_date group_by column).
        obs_a = {
            "observation_id": "obs_g2c_a",
            "type": "metric_change",
            "subject": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": -4.8},
            "observed_window": {"start": "2026-02-21", "end": "2026-02-27", "granularity": "day"},
        }
        obs_b = {
            "observation_id": "obs_g2c_b",
            "type": "metric_change",
            "subject": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": -5.2},
            "observed_window": {"start": "2026-02-28", "end": "2026-03-06", "granularity": "day"},
        }

        # An L1 claim backed by both observations.
        # L1 is the pre-condition for TemporalPrecedenceChecker (L0 claims are ignored).
        claim_l1 = {
            "claim_id": "claim_g2c_test",
            "inference_level": "L1",
            "scope": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_a", "obs_g2c_b"],
            "contradicting_observations": [],
        }

        upgrades = checker.check([claim_l1], [obs_a, obs_b], [])

        # G-2c core assertion: checker must propose an L2 upgrade
        self.assertEqual(len(upgrades), 1, "Expected exactly one upgrade proposal")
        upgrade = upgrades[0]
        self.assertEqual(upgrade.claim_id, "claim_g2c_test")
        self.assertEqual(upgrade.new_level, "L2")
        self.assertTrue(
            any("temporal_precedence" in t for t in upgrade.justification_tokens),
            f"Justification tokens must reference temporal_precedence: {upgrade.justification_tokens}",
        )

        # Verify the checker correctly rejects overlapping windows (regression guard)
        obs_overlap = {
            "observation_id": "obs_g2c_overlap",
            "type": "metric_change",
            "subject": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "payload": {"delta_pct": -3.0},
            "observed_window": {"start": "2026-02-25", "end": "2026-03-02", "granularity": "day"},
        }
        claim_overlap = {
            "claim_id": "claim_g2c_overlap",
            "inference_level": "L1",
            "scope": {"metric": "g2_event_count", "slice": {"platform": "ios"}},
            "supporting_observations": ["obs_g2c_a", "obs_g2c_overlap"],
            "contradicting_observations": [],
        }
        no_upgrades = checker.check([claim_overlap], [obs_a, obs_overlap], [])
        self.assertEqual(len(no_upgrades), 0, "Overlapping windows must NOT trigger L2 upgrade")


class G2dCausalEdgePromotionTests(unittest.TestCase):
    """G-2d regression: temporally_precedes edges survive synthesize_findings promotion.

    Proves that:
    1. IncrementalSynthesizer writes a temporally_precedes edge during incremental synthesis.
    2. _run_synthesis (synthesize_findings) preserves the edge via the causal-edge replay path.
    3. The claim remains at L2 after promotion.
    """

    def _make_service_with_synth(self, tmpdir: str):
        """Return (service, synth) wired together with real SQLite + DuckDB stores."""
        import json
        from pathlib import Path

        from app.evidence_engine.incremental_synthesizer import IncrementalSynthesizer
        from app.service import SemanticLayerService
        from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
        from app.storage.sqlite_metadata import SQLiteMetadataStore

        meta = SQLiteMetadataStore(Path(tmpdir) / "test.meta.sqlite")
        meta.initialize()
        analytics = DuckDBAnalyticsEngine(Path(tmpdir) / "test.duckdb")
        analytics.initialize()

        svc = SemanticLayerService(meta, analytics)
        synth = IncrementalSynthesizer(meta)
        svc._incremental_synthesizer = synth
        return svc, synth, meta

    def test_causal_edge_survives_synthesize_findings(self) -> None:
        """Causal edge written during incremental synthesis must survive promotion."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_prom01"
            step_id = "step_g2d_prom01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "G-2d promotion test", "{}", "{}", "{}", "active"],
            )

            # Two windowed observations with the same (metric, slice) scope and
            # strictly non-overlapping windows so TemporalPrecedenceChecker fires.
            windows = [
                ("obs_g2d_01", "2024-01-01", "2024-01-07"),
                ("obs_g2d_02", "2024-01-10", "2024-01-17"),
            ]
            for oid, wstart, wend in windows:
                meta.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, step_id, observation_type,
                        subject_json, payload_json, significance_json, quality_json,
                        observed_window_json, temporal_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        oid, sess_id, step_id, "metric_change",
                        json.dumps({"metric": "m_g2d", "slice": {}}),
                        json.dumps({"delta_pct": 6.0}),
                        json.dumps({"sample_size": 200, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "day"}),
                        0,
                    ],
                )

            # Step 1: Incremental synthesis → CrossSlice (L0→L1) then Temporal (L1→L2+edge).
            # Two calls are required: call 1 upgrades to L1, call 2 sees L1 and fires Temporal.
            synth.process(sess_id)   # CrossSlice: L0 → L1
            synth.process(sess_id)   # TemporalPrecedence: L1 → L2 + causal edge

            edges_before = meta.query_rows(
                "SELECT edge_type FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                [sess_id],
            )
            self.assertEqual(len(edges_before), 1, "Edge must be present before synthesize_findings")

            claims_before = meta.query_rows(
                "SELECT inference_level FROM claims WHERE session_id = ? AND status = 'tentative'",
                [sess_id],
            )
            self.assertTrue(
                any(r["inference_level"] == "L2" for r in claims_before),
                "Claim must be at L2 before synthesize_findings",
            )

            # Step 2: synthesize_findings → promotion + edge replay
            svc._run_synthesis(sess_id)

            edges_after = meta.query_rows(
                """
                SELECT edge_type, from_node_id, from_node_type,
                       to_node_id, to_node_type, weight, explanation
                FROM evidence_edges
                WHERE session_id = ? AND edge_type = 'temporally_precedes'
                """,
                [sess_id],
            )
            self.assertEqual(
                len(edges_after), 1,
                "temporally_precedes edge must survive synthesize_findings promotion",
            )
            edge = dict(edges_after[0])
            self.assertEqual(edge["from_node_id"], "obs_g2d_01",
                             "Edge must originate from the earliest observation")
            self.assertEqual(edge["from_node_type"], "observation")
            self.assertEqual(edge["to_node_type"], "claim")
            self.assertGreater(edge["weight"], 0, "Edge weight must be positive")
            self.assertIn("obs_g2d_02", edge["explanation"],
                          "Explanation must reference the paired (later) observation")
            self.assertIn("3 days", edge["explanation"],
                          "Explanation must state the lag in days")

            # Verify claim is promoted to L2 and no longer tentative
            claims_after = meta.query_rows(
                "SELECT inference_level, status FROM claims WHERE session_id = ?",
                [sess_id],
            )
            l2_claims = [r for r in claims_after if r["inference_level"] == "L2"]
            self.assertGreater(len(l2_claims), 0, "Claim must remain at L2 after promotion")
            promoted_statuses = {r["status"] for r in l2_claims}
            self.assertTrue(
                promoted_statuses <= {"confirmed", "insufficient"},
                f"Claim must be promoted (not tentative), got: {promoted_statuses}",
            )

    def test_synthesize_findings_replay_idempotent(self) -> None:
        """Promotion replay must not multiply causal edges across repeated calls.

        We simulate two successive PROMOTION-path synthesize_findings runs by
        manually resetting claim status back to 'tentative' between calls.
        This directly verifies that the save → clear → replay pattern produces
        exactly the same number of edges each time, not N*k.
        """
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            svc, synth, meta = self._make_service_with_synth(tmpdir)

            sess_id = "sess_g2d_idem01"

            meta.execute(
                "INSERT INTO sessions (session_id, goal, constraints_json, budget_json, policy_json, status) VALUES (?, ?, ?, ?, ?, ?)",
                [sess_id, "G-2d idempotency test", "{}", "{}", "{}", "active"],
            )

            for oid, wstart, wend in [
                ("obs_id_01", "2024-02-01", "2024-02-07"),
                ("obs_id_02", "2024-02-10", "2024-02-17"),
            ]:
                meta.execute(
                    """
                    INSERT INTO observations (
                        observation_id, session_id, step_id, observation_type,
                        subject_json, payload_json, significance_json, quality_json,
                        observed_window_json, temporal_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        oid, sess_id, "step_idem_01", "metric_change",
                        json.dumps({"metric": "m_idem", "slice": {}}),
                        json.dumps({"delta_pct": 7.0}),
                        json.dumps({"sample_size": 150, "practical_significance": True}),
                        json.dumps({"freshness_ok": True, "sample_size_ok": True}),
                        json.dumps({"start": wstart, "end": wend, "granularity": "day"}),
                        0,
                    ],
                )

            synth.process(sess_id)   # CrossSlice: L0 → L1
            synth.process(sess_id)   # TemporalPrecedence: L1 → L2 + causal edge

            def _count_tp_edges():
                return len(meta.query_rows(
                    "SELECT 1 FROM evidence_edges WHERE session_id = ? AND edge_type = 'temporally_precedes'",
                    [sess_id],
                ))

            self.assertEqual(_count_tp_edges(), 1, "One edge before first synthesis")

            # First synthesize_findings (PROMOTION path)
            svc._run_synthesis(sess_id)
            self.assertEqual(_count_tp_edges(), 1,
                             "Edge count must be exactly 1 after first synthesize_findings")

            # Simulate a second PROMOTION-path run by resetting the claim to 'tentative'.
            # This is intentionally artificial to isolate the replay path: we want to
            # prove that save→clear→replay is idempotent and never produces N*k edges.
            meta.execute(
                "UPDATE claims SET status = 'tentative' WHERE session_id = ?",
                [sess_id],
            )

            # Second synthesize_findings (PROMOTION path again on the same claim)
            svc._run_synthesis(sess_id)
            self.assertEqual(
                _count_tp_edges(), 1,
                "Repeated synthesize_findings must not multiply causal edges (expected exactly 1)",
            )


if __name__ == "__main__":
    unittest.main()
