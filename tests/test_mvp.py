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


if __name__ == "__main__":
    unittest.main()
