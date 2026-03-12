from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.analysis_core.ir import ExecutionPlanIR
from app.planning import PlanningService
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


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
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Planning test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_draft_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
            {"step_type": "analyze_qoe", "dependencies": [0]},
            {"step_type": "synthesize_findings", "dependencies": [0, 1]},
        ])
        self.assertTrue(plan["plan_id"].startswith("plan_"))
        self.assertEqual(plan["status"], "draft")
        self.assertEqual(len(plan["steps"]), 3)
        self.assertEqual(plan["steps"][0]["step_type"], "compare_watch_time")
        self.assertEqual(plan["steps"][2]["dependencies"], [0, 1])

    def test_get_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
        ])
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
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
        patched = self.planning.patch_plan(plan["plan_id"], steps=[
            {"step_type": "compare_watch_time"},
            {"step_type": "analyze_qoe"},
        ])
        self.assertEqual(len(patched["steps"]), 2)

    def test_get_execution_plan_ir(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
            {"step_type": "sample_rows", "params": {"table_name": "analytics.watch_events"}, "dependencies": [0]},
        ])

        plan_ir = self.planning.get_execution_plan_ir(plan["plan_id"])

        self.assertIsInstance(plan_ir, ExecutionPlanIR)
        self.assertEqual([step.step_type for step in plan_ir.steps], ["compare_watch_time", "sample_rows"])
        self.assertEqual(plan_ir.steps[1].params["table_name"], "analytics.watch_events")
        self.assertEqual(plan_ir.steps[1].dependencies, [0])

    def test_patch_non_draft_fails(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
        self.planning.validate_plan(plan["plan_id"])
        with self.assertRaises(ValueError):
            self.planning.patch_plan(plan["plan_id"], steps=[])

    def test_delete_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
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
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Validation test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_validate_valid_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
            {"step_type": "analyze_qoe"},
            {"step_type": "synthesize_findings", "dependencies": [0, 1]},
        ])
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        # Check status transitioned
        plan = self.planning.get_plan(plan["plan_id"])
        self.assertEqual(plan["status"], "validated")

    def test_validate_unknown_step_type(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "unknown_step"},
        ])
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])
        self.assertIn("unknown step_type", result["errors"][0])

    def test_validate_returns_structured_issues(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_metric"},
        ])
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["issues"][0]["code"], "missing_required_param")
        self.assertEqual(result["issues"][0]["category"], "params")
        self.assertEqual(result["issues"][0]["step_index"], 0)
        self.assertEqual(
            result["issues"][0]["detail"]["missing_params"],
            ["metric_name", "table_name"],
        )

    def test_validate_forward_dependency(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time", "dependencies": [1]},
            {"step_type": "analyze_qoe"},
        ])
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])

    def test_validate_missing_params(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_metric"},  # missing metric_name and table_name
        ])
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])
        self.assertTrue(any("metric_name" in e for e in result["errors"]))

    def test_validate_profile_table_missing_params(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "profile_table"},  # missing table_name
        ])
        result = self.planning.validate_plan(plan["plan_id"])
        self.assertFalse(result["valid"])

    def test_approve_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
        self.planning.validate_plan(plan["plan_id"])
        approved = self.planning.approve_plan(plan["plan_id"])
        self.assertEqual(approved["status"], "approved")

    def test_approve_non_validated_fails(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
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
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Execution test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_execute_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
            {"step_type": "analyze_qoe", "dependencies": [0]},
            {"step_type": "synthesize_findings", "dependencies": [0, 1]},
        ])
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

    def test_execute_non_approved_fails(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
        with self.assertRaises(ValueError):
            self.planning.execute_plan(plan["plan_id"], self.service)

    def test_explain_plan(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
            {"step_type": "synthesize_findings", "dependencies": [0]},
        ])
        explanation = self.planning.explain_plan(plan["plan_id"])
        self.assertIn("explanation", explanation)
        self.assertIn("compare_watch_time", explanation["explanation"])
        self.assertIn("synthesize_findings", explanation["explanation"])


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
        cls.planning = PlanningService(cls.metadata)
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.session = cls.service.create_session("Cost test", {}, {}, {})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_estimate_costs(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "compare_watch_time"},
            {"step_type": "synthesize_findings"},
        ])
        result = self.planning.estimate_costs(plan["plan_id"], self.analytics)
        self.assertIn("total_estimated_cost", result)
        self.assertIn("cost_estimates", result)
        self.assertGreater(result["total_estimated_cost"], 0)
        # compare_watch_time should have a cost, synthesize should be 0
        self.assertIsNotNone(result["steps"][0]["estimated_cost"])
        self.assertIn("estimated_cost_detail", result["steps"][0])
        self.assertEqual(result["steps"][1]["estimated_cost"], 0)

    def test_estimate_costs_parameterized(self) -> None:
        plan = self.planning.draft_plan(self.session["session_id"], [
            {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
        ])
        result = self.planning.estimate_costs(plan["plan_id"], self.analytics)
        self.assertGreater(result["total_estimated_cost"], 0)

    def test_budget_check_within(self) -> None:
        session = self.service.create_session("Budget test", {}, {"max_rows_scanned": 999999999}, {})
        plan = self.planning.draft_plan(session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
        self.planning.estimate_costs(plan["plan_id"], self.analytics)
        result = self.planning.check_budget(plan["plan_id"], session["session_id"])
        self.assertTrue(result["within_budget"])
        self.assertIn("confidence", result)

    def test_budget_check_exceeded(self) -> None:
        session = self.service.create_session("Budget tight", {}, {"max_rows_scanned": 1}, {})
        plan = self.planning.draft_plan(session["session_id"], [
            {"step_type": "compare_watch_time"},
        ])
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
        from app.main import create_app
        from fastapi.testclient import TestClient
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        cls.session_id = cls.client.post(
            "/sessions", json={"goal": "Plan API test."},
        ).json()["session_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_full_plan_lifecycle_via_api(self) -> None:
        # Draft
        resp = self.client.post(f"/sessions/{self.session_id}/plans", json={
            "steps": [
                {"step_type": "compare_watch_time"},
                {"step_type": "analyze_qoe", "dependencies": [0]},
                {"step_type": "synthesize_findings", "dependencies": [0, 1]},
            ],
        })
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

        # Validate
        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/validate")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["valid"])

        # Approve
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
        resp = self.client.post(f"/sessions/{self.session_id}/plans", json={
            "steps": [{"step_type": "compare_watch_time"}],
        })
        plan_id = resp.json()["plan_id"]

        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/estimate-costs")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("total_estimated_cost", resp.json())

    def test_budget_check_via_api(self) -> None:
        session = self.client.post("/sessions", json={
            "goal": "Budget check API test.",
            "budget": {"max_rows_scanned": 999999999},
        }).json()
        resp = self.client.post(f"/sessions/{session['session_id']}/plans", json={
            "steps": [{"step_type": "compare_watch_time"}],
        })
        plan_id = resp.json()["plan_id"]

        # Estimate costs first
        self.client.post(f"/sessions/{session['session_id']}/plans/{plan_id}/estimate-costs")

        resp = self.client.get(f"/sessions/{session['session_id']}/plans/{plan_id}/budget-check")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["within_budget"])

    def test_patch_plan_via_api(self) -> None:
        resp = self.client.post(f"/sessions/{self.session_id}/plans", json={
            "steps": [{"step_type": "compare_watch_time"}],
        })
        plan_id = resp.json()["plan_id"]

        resp = self.client.patch(f"/sessions/{self.session_id}/plans/{plan_id}", json={
            "steps": [
                {"step_type": "compare_watch_time"},
                {"step_type": "analyze_qoe"},
            ],
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["steps"]), 2)

    def test_validate_invalid_plan(self) -> None:
        resp = self.client.post(f"/sessions/{self.session_id}/plans", json={
            "steps": [{"step_type": "nonexistent_step"}],
        })
        plan_id = resp.json()["plan_id"]

        resp = self.client.post(f"/sessions/{self.session_id}/plans/{plan_id}/validate")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["valid"])


if __name__ == "__main__":
    unittest.main()
