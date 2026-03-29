from __future__ import annotations

import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from app.analysis_core.ir import ExecutionPlanIR
from app.bindings import BindingService
from app.engines import EngineService
from app.planning import PlanningService
from app.routing import QueryRouter
from app.service import SemanticLayerService
from app.sources import SourceService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


def _seed_watch_time_metric(metadata: SQLiteMetadataStore) -> None:
    """Seed a published 'watch_time' metric so metric_query steps validate."""
    from app.semantic import SemanticService

    semantic = SemanticService(metadata)
    entity = semantic.create_entity("session", "Session", ["session_id"])
    semantic.publish_entity(entity["entity_id"])
    metric = semantic.create_metric(
        "watch_time",
        "Watch Time",
        "avg(play_duration_seconds)",
        ["platform", "app_version", "network_type", "content_type"],
        entity_id=entity["entity_id"],
    )
    semantic.publish_metric(metric["metric_id"])


def _typed_metric_query_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "table": "analytics.watch_events",
        "metric": "watch_time",
        "order": "DESC",
        "time_scope": {
            "mode": "compare",
            "grain": "day",
            "current": {"start": "2026-03-01", "end": "2026-03-08"},
            "baseline": {"start": "2026-02-22", "end": "2026-03-01"},
        },
    }
    params.update(overrides)
    return params


def _typed_aggregate_query_params(**overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "table": "analytics.watch_events",
        "group_by": ["platform"],
        "measures": [{"expr": "COUNT(*)", "as": "cnt"}],
        "time_scope": {
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2026-03-01", "end": "2026-03-08"},
        },
    }
    params.update(overrides)
    return params


class PlanningServiceTests(unittest.TestCase):
    """Unit tests for PlanningService."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "plan.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "plan.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_watch_time_metric(cls.metadata)
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Planning test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_draft_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {
                    "step_type": "profile_table",
                    "params": {"table_name": "analytics.watch_events"},
                    "dependencies": [0],
                },
                {"step_type": "synthesize_findings", "dependencies": [0, 1]},
            ],
        )
        self.assertTrue(plan["plan_id"].startswith("plan_"))
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(len(plan["steps"]), 3)
        self.assertEqual(plan["steps"][0]["step_type"], "metric_query")
        self.assertEqual(plan["steps"][2]["dependencies"], [0, 1])

    def test_get_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        fetched = self.planning.get_plan(plan["plan_id"])
        self.assertEqual(fetched["plan_id"], plan["plan_id"])

    def test_get_plan_not_found(self) -> None:
        with self.assertRaises(KeyError):
            self.planning.get_plan("plan_nonexistent")

    def test_list_plans(self) -> None:
        plans = self.planning.list_plans(self.session["session_id"])
        self.assertIsInstance(plans, list)
        self.assertGreaterEqual(len(plans), 1)

    def test_patch_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        patched = self.planning.patch_plan(
            plan["plan_id"],
            steps=[
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        self.assertEqual(len(patched["steps"]), 2)

    def test_get_execution_plan_ir(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {
                    "step_type": "sample_rows",
                    "params": {"table_name": "analytics.watch_events"},
                    "dependencies": [0],
                },
            ],
        )

        plan_ir = self.planning.get_execution_plan_ir(plan["plan_id"])

        self.assertIsInstance(plan_ir, ExecutionPlanIR)
        self.assertEqual(plan_ir.plan_id, plan["plan_id"])
        self.assertEqual(plan_ir.session_id, self.session["session_id"])
        self.assertEqual(plan_ir.request.goal, "Planning test")
        self.assertEqual(plan_ir.request.requested_metrics, ["watch_time"])
        self.assertEqual(plan_ir.request.requested_tables, ["analytics.watch_events"])
        self.assertEqual(
            [step.step_type for step in plan_ir.steps], ["metric_query", "sample_rows"]
        )
        self.assertEqual(plan_ir.steps[1].params["table_name"], "analytics.watch_events")
        self.assertEqual(plan_ir.steps[1].dependencies, [0])
        semantic_resolution = plan_ir.semantic_resolution_for_step(0)
        assert semantic_resolution is not None
        self.assertEqual(semantic_resolution.requested_metrics, ["watch_time"])
        self.assertEqual(semantic_resolution.source_table, "analytics.watch_events")
        execution_target = plan_ir.execution_target_for_step(1)
        assert execution_target is not None
        self.assertEqual(execution_target.table_names, ["analytics.watch_events"])
        self.assertEqual(execution_target.routing_table_names, ["watch_events"])
        self.assertEqual(execution_target.engine_type, "duckdb")
        self.assertEqual(execution_target.routing_strategy, "no_router")
        self.assertEqual(plan_ir.policy_transforms, [])

    def test_get_execution_plan_ir_includes_request_policy_transforms(self) -> None:
        session = self.service.create_session(
            "Policy-aware planning test",
            {"region": "us"},
            {"max_rows_scanned": 5000},
            {"aggregate_only": True},
        )
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )

        plan_ir = self.planning.get_execution_plan_ir(plan["plan_id"])

        self.assertEqual(
            [transform.transform_type for transform in plan_ir.policy_transforms],
            ["session_constraints", "budget_guard", "session_policy"],
        )
        self.assertEqual(plan_ir.request.budget, {"max_rows_scanned": 5000})

    def test_get_execution_plan_ir_accepts_semantic_repository(self) -> None:
        planning = PlanningService(
            self.metadata,
            semantic_repository=self.service.semantic_repository,
        )
        plan = planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )

        plan_ir = planning.get_execution_plan_ir(plan["plan_id"])

        self.assertIs(planning.semantic_repository, self.service.semantic_repository)
        self.assertIs(planning.semantic_resolver, self.service.semantic_repository.resolver)
        self.assertEqual(plan_ir.request.requested_metrics, ["watch_time"])

    def test_get_execution_plan_ir_records_semantic_routing_detail(self) -> None:
        from datetime import datetime
        from uuid import uuid4

        source_service = SourceService(self.metadata)
        engine_service = EngineService(self.metadata)
        binding_service = BindingService(self.metadata)

        src = source_service.register_source("duckdb", "Planning Semantic Route Src", {})
        duck = engine_service.register_engine(
            "duckdb",
            "Planning Semantic Route Duck",
            {"path": "/tmp/planning_semantic_route.duckdb"},
            capabilities={
                "supported_step_types": ("sample_rows", "profile_table"),
                "policy_support": (),
            },
        )
        trino = engine_service.register_engine(
            "trino",
            "Planning Semantic Route Trino",
            {
                "host": "localhost",
                "port": 8080,
                "user": "test",
                "catalog": "hive",
                "schema": "default",
            },
        )
        binding_service.create_binding(src["source_id"], duck["engine_id"], priority=9)
        binding_service.create_binding(src["source_id"], trino["engine_id"], priority=7)

        now = datetime.now(UTC).isoformat()
        schema_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'schema', 'planning_semantic_schema', 'demo.planning_semantic_schema', '{}', ?, ?)
            """,
            [schema_id, src["source_id"], now, now],
        )
        table_id = f"obj_{uuid4().hex[:12]}"
        self.metadata.execute(
            """
            INSERT INTO source_objects
                (object_id, source_id, object_type, parent_id, native_name, fqn, properties_json, created_at, updated_at)
            VALUES (?, ?, 'table', ?, 'planning_semantic_table', 'demo.planning_semantic_schema.planning_semantic_table', '{}', ?, ?)
            """,
            [table_id, src["source_id"], schema_id, now, now],
        )

        session = self.service.create_session(
            "Semantic routing planning test",
            {},
            {},
            {"aggregate_only": True},
        )
        planning = PlanningService(
            self.metadata,
            query_router=QueryRouter(self.metadata, engine_service),
            semantic_repository=self.service.semantic_repository,
        )
        plan = planning.draft_plan(
            session["session_id"],
            [
                {
                    "step_type": "metric_query",
                    "params": {
                        **_typed_metric_query_params(),
                        "table": "analytics.planning_semantic_table",
                        "dimensions": ["platform", "app_version", "network_type"],
                    },
                },
            ],
        )

        plan_ir = planning.get_execution_plan_ir(plan["plan_id"])

        execution_target = plan_ir.execution_target_for_step(0)
        assert execution_target is not None
        self.assertEqual(execution_target.engine_id, trino["engine_id"])
        self.assertEqual(execution_target.routing_strategy, "semantic_bound_route")
        self.assertTrue(execution_target.routing_reason)
        self.assertEqual(
            execution_target.routing_detail["strategy"],
            "semantic_intent_and_capability",
        )
        self.assertEqual(execution_target.capability_profile["engine_type"], "trino")

    def test_patch_non_draft_fails(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        with self.assertRaises(ValueError):
            self.planning.patch_plan(plan["plan_id"], steps=[])

    def test_delete_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        result = self.planning.delete_plan(plan["plan_id"])
        self.assertEqual(result["status"], "deleted")
        with self.assertRaises(KeyError):
            self.planning.get_plan(plan["plan_id"])


class PlanValidationTests(unittest.TestCase):
    """Tests for plan validation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "val.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "val.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_watch_time_metric(cls.metadata)
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Validation test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_validate_valid_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
                {"step_type": "synthesize_findings", "dependencies": [0, 1]},
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        # Clean plan is auto-approved
        plan = self.planning.get_plan(plan["plan_id"])
        self.assertEqual(plan["status"], "approved")

    def test_validate_unknown_step_type(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "unknown_step"},
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])
        self.assertIn("unknown step_type", result["errors"][0])

    def test_validate_returns_structured_issues(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query"},
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["issues"][0]["code"], "missing_required_param")
        self.assertEqual(result["issues"][0]["category"], "params")
        self.assertEqual(result["issues"][0]["step_index"], 0)
        self.assertEqual(
            result["issues"][0]["detail"]["missing_params"],
            ["table", "metric", "time_scope"],
        )

    def test_validate_forward_dependency(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {
                    "step_type": "metric_query",
                    "params": _typed_metric_query_params(),
                    "dependencies": [1],
                },
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])

    def test_validate_missing_params(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query"},
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])
        self.assertTrue(any("time_scope" in e for e in result["errors"]))

    def test_validate_profile_table_missing_params(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "profile_table"},  # missing table_name
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])

    def test_validate_auto_approves_clean_plan(self) -> None:
        """Plans with no governance/budget warnings are auto-approved."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertTrue(result["auto_approved"])
        refreshed = self.planning.get_plan(plan["plan_id"])
        self.assertEqual(refreshed["status"], "approved")

    def test_approve_already_approved_is_noop(self) -> None:
        """approve_plan on an already-approved plan is a no-op."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        approved = self.planning.approve_plan(plan["plan_id"])
        self.assertEqual(approved["status"], "approved")

    def test_approve_draft_fails(self) -> None:
        """approve_plan on a draft plan should fail."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        with self.assertRaises(ValueError):
            self.planning.approve_plan(plan["plan_id"])


class PlanExecutionTests(unittest.TestCase):
    """Tests for plan execution."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "exec.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "exec.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_watch_time_metric(cls.metadata)
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Execution test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_execute_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {
                    "step_type": "profile_table",
                    "params": {"table_name": "analytics.watch_events"},
                    "dependencies": [0],
                },
                {"step_type": "synthesize_findings", "dependencies": [0, 1]},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        result = self.planning.execute_plan(plan["plan_id"], self.service)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["step_results"]), 3)

        # Verify plan status
        final = self.planning.get_plan(plan["plan_id"])
        self.assertEqual(final["status"], "completed")
        for step in final["steps"]:
            self.assertEqual(step["status"], "completed")
            self.assertIn("actual_cost_feedback", step)

    def test_direct_service_step_writes_live_claims_by_default(self) -> None:
        result = self.service.run_step(
            self.session["session_id"],
            "metric_query",
            _typed_metric_query_params(),
        )

        self.assertIn("readiness", result)
        self.assertIn("live_claims", result)
        self.assertGreater(len(result["live_claims"]), 0)

        observations = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM observations WHERE session_id = ?",
            [self.session["session_id"]],
        )
        claims = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM claims WHERE session_id = ? AND status = 'tentative'",
            [self.session["session_id"]],
        )
        self.assertGreater(observations["cnt"], 0)
        self.assertGreater(claims["cnt"], 0)

    def test_execute_plan_populates_claims_for_session(self) -> None:
        session = self.service.create_session("Execution claims test", {}, {}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {
                    "step_type": "aggregate_query",
                    "params": {
                        **_typed_aggregate_query_params(),
                    },
                },
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        result = self.planning.execute_plan(plan["plan_id"], self.service)

        self.assertEqual(result["status"], "completed")
        live_claims = self.metadata.query_rows(
            "SELECT claim_id, session_id, status FROM claims WHERE session_id = ? ORDER BY created_at",
            [session["session_id"]],
        )
        self.assertGreater(len(live_claims), 0)
        self.assertEqual({row["session_id"] for row in live_claims}, {session["session_id"]})
        self.assertTrue(all(row["status"] == "tentative" for row in live_claims))

    def test_execute_plan_then_synthesize_consumes_tentative_claims(self) -> None:
        session = self.service.create_session("Execution synthesize test", {}, {}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {
                    "step_type": "aggregate_query",
                    "params": {
                        **_typed_aggregate_query_params(),
                    },
                },
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])
        self.planning.execute_plan(plan["plan_id"], self.service)

        tentative_before = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM claims WHERE session_id = ? AND status = 'tentative'",
            [session["session_id"]],
        )
        self.assertGreater(tentative_before["cnt"], 0)

        synth = self.service.run_step(session["session_id"], "synthesize_findings")

        self.assertIn("claims", synth)
        self.assertGreater(len(synth["claims"]), 0)
        promoted = self.metadata.query_one(
            "SELECT COUNT(*) AS cnt FROM claims WHERE session_id = ? AND status IN ('confirmed', 'insufficient')",
            [session["session_id"]],
        )
        self.assertGreater(promoted["cnt"], 0)

    def test_execute_plan_persists_step_result_snapshot(self) -> None:
        session = self.service.create_session("Execution snapshot test", {}, {}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {"step_type": "synthesize_findings", "dependencies": [0]},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        self.planning.execute_plan(plan["plan_id"], self.service)

        final = self.planning.get_plan(plan["plan_id"])
        compare_step = final["steps"][0]
        synth_step = final["steps"][1]

        self.assertIn("result", compare_step)
        self.assertEqual(compare_step["result"]["step_type"], "metric_query")
        self.assertIn("summary", compare_step["result"])
        self.assertIn("artifact_id", compare_step["result"])
        self.assertIn("observations", compare_step["result"])
        self.assertLessEqual(len(compare_step["result"]["observations"]), 10)

        self.assertIn("result", synth_step)
        self.assertEqual(synth_step["result"]["step_type"], "synthesize_findings")
        self.assertIn("claims", synth_step["result"])
        self.assertIn("recommendations", synth_step["result"])

    def test_execute_plan_truncates_rows_result_snapshot(self) -> None:
        session = self.service.create_session("Execution rows snapshot test", {}, {}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {
                    "step_type": "sample_rows",
                    "params": {"table_name": "analytics.watch_events", "limit": 20},
                },
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        self.planning.execute_plan(plan["plan_id"], self.service)

        final = self.planning.get_plan(plan["plan_id"])
        step_result = final["steps"][0]["result"]
        self.assertIn("rows", step_result)
        self.assertEqual(len(step_result["rows"]), 10)
        self.assertTrue(step_result["rows_truncated"])
        self.assertIn("artifact_id", step_result)

    def test_execute_plan_truncates_profile_columns_in_snapshot(self) -> None:
        session = self.service.create_session("Execution profile snapshot test", {}, {}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        self.planning.execute_plan(plan["plan_id"], self.service)

        final = self.planning.get_plan(plan["plan_id"])
        profile = final["steps"][0]["result"]["profile"]
        self.assertIn("columns", profile)
        self.assertLessEqual(len(profile["columns"]), 10)
        if "columns_truncated" in profile:
            self.assertTrue(profile["columns_truncated"])

    def test_execute_non_approved_fails(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        with self.assertRaises(ValueError):
            self.planning.execute_plan(plan["plan_id"], self.service)

    def test_explain_plan(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {"step_type": "synthesize_findings", "dependencies": [0]},
            ],
        )
        explanation = self.planning.explain_plan(plan["plan_id"])
        self.assertIn("explanation", explanation)
        self.assertIn("metric_query", explanation["explanation"])
        self.assertIn("synthesize_findings", explanation["explanation"])


class PlanFaultToleranceTests(unittest.TestCase):
    """Fix 4: Tests for continue_on_failure in plan execution."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "fault.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "fault.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_watch_time_metric(cls.metadata)
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Fault tolerance test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_continue_on_failure_partial(self) -> None:
        """Plan with a bad step + good step should produce 'partial' status."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                # Step 0: will fail (nonexistent table)
                {
                    "step_type": "profile_table",
                    "params": {"table_name": "analytics.nonexistent_table_xyz"},
                },
                # Step 1: should succeed (independent)
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        result = self.planning.execute_plan(
            plan["plan_id"],
            self.service,
            continue_on_failure=True,
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["step_results"]), 2)
        # First step should be failed
        self.assertEqual(result["step_results"][0]["status"], "failed")
        # Second step should have succeeded
        self.assertIn("summary", result["step_results"][1])

    def test_continue_on_failure_skips_dependents(self) -> None:
        """Steps depending on a failed step should be skipped."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                # Step 0: will fail
                {
                    "step_type": "profile_table",
                    "params": {"table_name": "analytics.nonexistent_table_xyz"},
                },
                # Step 1: depends on step 0 → should be skipped
                {
                    "step_type": "sample_rows",
                    "params": {"table_name": "analytics.watch_events"},
                    "dependencies": [0],
                },
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        result = self.planning.execute_plan(
            plan["plan_id"],
            self.service,
            continue_on_failure=True,
        )
        self.assertEqual(result["status"], "failed")  # all failed/skipped → failed
        self.assertEqual(result["step_results"][0]["status"], "failed")
        self.assertEqual(result["step_results"][1]["status"], "skipped")

    def test_continue_on_failure_false_raises(self) -> None:
        """Default continue_on_failure=False should raise on first failure."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {
                    "step_type": "profile_table",
                    "params": {"table_name": "analytics.nonexistent_table_xyz"},
                },
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        with self.assertRaises(Exception):
            self.planning.execute_plan(plan["plan_id"], self.service)

    def test_continue_on_failure_all_succeed(self) -> None:
        """All steps succeed → status should be 'completed', not 'partial'."""
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
                {
                    "step_type": "sample_rows",
                    "params": {"table_name": "analytics.watch_events"},
                    "dependencies": [0],
                },
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])

        result = self.planning.execute_plan(
            plan["plan_id"],
            self.service,
            continue_on_failure=True,
        )
        self.assertEqual(result["status"], "completed")


class CostEstimationTests(unittest.TestCase):
    """Tests for cost estimation and budget checks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "cost.meta.sqlite"
        duck_path = Path(cls.temp_dir.name) / "cost.duckdb"
        cls.metadata = SQLiteMetadataStore(meta_path)
        get_seeded_duckdb_path(duck_path)
        cls.analytics = DuckDBAnalyticsEngine(duck_path)
        cls.metadata.initialize()
        cls.analytics.initialize()
        _seed_watch_time_metric(cls.metadata)
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Cost test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_estimate_costs(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
                {"step_type": "synthesize_findings"},
            ],
        )
        result = self.planning.estimate_costs(plan["plan_id"], self.analytics)
        self.assertIn("total_estimated_cost", result)
        self.assertIn("cost_estimates", result)
        self.assertGreater(result["total_estimated_cost"], 0)
        # metric_query should have a cost, synthesize should be 0
        self.assertIsNotNone(result["steps"][0]["estimated_cost"])
        self.assertIn("estimated_cost_detail", result["steps"][0])
        self.assertEqual(result["steps"][1]["estimated_cost"], 0)

    def test_estimate_costs_parameterized(self) -> None:
        plan = self.planning.draft_plan(
            self.session["session_id"],
            [
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        result = self.planning.estimate_costs(plan["plan_id"], self.analytics)
        self.assertGreater(result["total_estimated_cost"], 0)

    def test_budget_check_within(self) -> None:
        session = self.service.create_session(
            "Budget test", {}, {"max_rows_scanned": 999999999}, {}
        )
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        self.planning.estimate_costs(plan["plan_id"], self.analytics)
        result = self.planning.check_budget(plan["plan_id"], session["session_id"])
        self.assertTrue(result["within_budget"])
        self.assertIn("confidence", result)

    def test_budget_check_exceeded(self) -> None:
        session = self.service.create_session("Budget tight", {}, {"max_rows_scanned": 1}, {})
        plan = self.planning.draft_plan(
            session["session_id"],
            [
                {"step_type": "metric_query", "params": _typed_metric_query_params()},
            ],
        )
        self.planning.estimate_costs(plan["plan_id"], self.analytics)
        result = self.planning.check_budget(plan["plan_id"], session["session_id"])
        self.assertFalse(result["within_budget"])
        self.assertEqual(result["risk_level"], "high")


class PlanningAPITests(unittest.TestCase):
    """Integration tests for planning API endpoints via TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "plan_api.duckdb"
        from fastapi.testclient import TestClient

        from app.main import create_app

        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        # Seed a published metric for metric_query steps
        entity_resp = cls.client.post(
            "/semantic/entities",
            json={
                "name": "session",
                "display_name": "Session",
                "keys": ["session_id"],
            },
        )
        entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{entity_id}/publish")
        metric_resp = cls.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type", "content_type"],
                "entity_id": entity_id,
            },
        )
        metric_id = metric_resp.json()["metric_id"]
        cls.client.post(f"/semantic/metrics/{metric_id}/publish")
        cls.session_id = cls.client.post(
            "/sessions",
            json={"goal": "Plan API test."},
        ).json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_full_plan_lifecycle_via_api(self) -> None:
        # Draft
        resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={
                "steps": [
                    {"step_type": "metric_query", "params": _typed_metric_query_params()},
                    {
                        "step_type": "profile_table",
                        "params": {"table_name": "analytics.watch_events"},
                        "dependencies": [0],
                    },
                    {"step_type": "synthesize_findings", "dependencies": [0, 1]},
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        plan = resp.json()
        plan_id = plan["plan_id"]
        self.assertEqual(plan["status"], "draft")

        # List
        resp = self.client.get(f"/sessions/{self.session_id}/plans")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 1)

        # Get
        resp = self.client.get(f"/sessions/{self.session_id}/plans/{plan_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["plan_id"], plan_id)

        # Validate (auto-approves clean plans)
        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/validate")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["valid"])
        self.assertTrue(resp.json()["auto_approved"])

        # Approve is a no-op on already-approved plan
        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/approve")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "approved")

        # Explain
        resp = self.client.get(f"/sessions/{self.session_id}/plans/{plan_id}/explain")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("explanation", resp.json())

        # Execute
        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/execute")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "completed")
        self.assertEqual(len(resp.json()["step_results"]), 3)

    def test_estimate_costs_via_api(self) -> None:
        resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={
                "steps": [{"step_type": "metric_query", "params": _typed_metric_query_params()}],
            },
        )
        plan_id = resp.json()["plan_id"]

        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/estimate-costs")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("total_estimated_cost", resp.json())

    def test_budget_check_via_api(self) -> None:
        session = self.client.post(
            "/sessions",
            json={
                "goal": "Budget check API test.",
                "budget": {"max_rows_scanned": 999999999},
            },
        ).json()
        resp = self.client.post(
            f"/sessions/{session['session_id']}/plans",
            json={
                "steps": [{"step_type": "metric_query", "params": _typed_metric_query_params()}],
            },
        )
        plan_id = resp.json()["plan_id"]

        # Estimate costs first
        self.client.post(f"/sessions/{session['session_id']}/plans/{plan_id}/estimate-costs")

        resp = self.client.get(f"/sessions/{session['session_id']}/plans/{plan_id}/budget-check")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["within_budget"])

    def test_patch_plan_via_api(self) -> None:
        resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={
                "steps": [{"step_type": "metric_query", "params": _typed_metric_query_params()}],
            },
        )
        plan_id = resp.json()["plan_id"]

        resp = self.client.patch(
            f"/sessions/{self.session_id}/plans/{plan_id}",
            json={
                "steps": [
                    {"step_type": "metric_query", "params": _typed_metric_query_params()},
                    {
                        "step_type": "profile_table",
                        "params": {"table_name": "analytics.watch_events"},
                    },
                ],
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["steps"]), 2)

    def test_validate_invalid_plan(self) -> None:
        resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={
                "steps": [{"step_type": "nonexistent_step"}],
            },
        )
        plan_id = resp.json()["plan_id"]

        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/validate")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["valid"])

    def test_execute_plan_continue_on_failure_via_api(self) -> None:
        """continue_on_failure should be accepted by the API and produce partial status."""
        resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "profile_table",
                        "params": {"table_name": "analytics.nonexistent_xyz"},
                    },
                    {
                        "step_type": "profile_table",
                        "params": {"table_name": "analytics.watch_events"},
                    },
                ],
            },
        )
        plan_id = resp.json()["plan_id"]

        self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/validate")
        self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/approve")

        resp = self.client.post(
            f"/sessions/{self.session_id}/plans/{plan_id}/execute",
            json={"continue_on_failure": True},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "partial")

    def test_get_plan_returns_step_result_after_execution(self) -> None:
        resp = self.client.post(
            f"/sessions/{self.session_id}/plans",
            json={
                "steps": [
                    {
                        "step_type": "sample_rows",
                        "params": {"table_name": "analytics.watch_events", "limit": 20},
                    },
                ],
            },
        )
        plan_id = resp.json()["plan_id"]

        self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/validate")
        self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/approve")

        execute_resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/execute")
        self.assertEqual(execute_resp.status_code, 200)

        detail_resp = self.client.get(f"/sessions/{self.session_id}/plans/{plan_id}")
        self.assertEqual(detail_resp.status_code, 200)
        step = detail_resp.json()["steps"][0]
        self.assertIn("result", step)
        self.assertEqual(step["result"]["step_type"], "sample_rows")
        self.assertEqual(len(step["result"]["rows"]), 10)
        self.assertTrue(step["result"]["rows_truncated"])


if __name__ == "__main__":
    unittest.main()
