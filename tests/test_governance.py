"""Tests for the governance enforcement module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.governance import GovernanceService
from app.main import create_app
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class GovernanceServiceTests(unittest.TestCase):
    """Unit tests for GovernanceService."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "gov_test.duckdb"
        get_seeded_duckdb_path(db_path)
        meta_path = db_path.with_suffix(".meta.sqlite")
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()
        cls.analytics = DuckDBAnalyticsEngine(db_path)
        cls.analytics.initialize()
        cls.gov = GovernanceService(cls.metadata, cls.analytics)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_create_policy(self) -> None:
        result = self.gov.create_policy("test_agg_only", "aggregate_only")
        self.assertTrue(result["policy_id"].startswith("pol_"))
        self.assertEqual(result["name"], "test_agg_only")
        self.assertEqual(result["policy_type"], "aggregate_only")
        self.assertTrue(result["enabled"])

    def test_create_policy_invalid_type(self) -> None:
        with self.assertRaises(ValueError):
            self.gov.create_policy("bad_pol", "invalid_type")

    def test_list_policies(self) -> None:
        policies = self.gov.list_policies()
        self.assertIsInstance(policies, list)

    def test_get_policy(self) -> None:
        pol = self.gov.create_policy("get_test_pol", "field_mask", definition={"fields": ["email"]})
        fetched = self.gov.get_policy(pol["policy_id"])
        self.assertEqual(fetched["name"], "get_test_pol")
        self.assertEqual(fetched["definition"]["fields"], ["email"])

    def test_update_policy(self) -> None:
        pol = self.gov.create_policy("upd_test_pol", "max_rows", definition={"max_rows_scanned": 1000})
        updated = self.gov.update_policy(pol["policy_id"], enabled=False)
        self.assertFalse(updated["enabled"])

    def test_delete_policy(self) -> None:
        pol = self.gov.create_policy("del_test_pol", "row_filter")
        result = self.gov.delete_policy(pol["policy_id"])
        self.assertEqual(result["status"], "deleted")
        with self.assertRaises(KeyError):
            self.gov.get_policy(pol["policy_id"])

    def test_create_quality_rule(self) -> None:
        rule = self.gov.create_quality_rule(
            "freshness_rule", "freshness", "analytics.watch_events",
            {"max_age_hours": 48}, severity="warn",
        )
        self.assertTrue(rule["rule_id"].startswith("qr_"))
        self.assertEqual(rule["rule_type"], "freshness")

    def test_create_quality_rule_invalid(self) -> None:
        with self.assertRaises(ValueError):
            self.gov.create_quality_rule("bad_rule", "nonexistent", "t", {})

    def test_check_policies_aggregate_only(self) -> None:
        self.gov.create_policy("agg_check_pol", "aggregate_only")
        result = self.gov.check_policies("sess_fake", "sample_rows")
        self.assertFalse(result["passed"])
        self.assertTrue(any("aggregate-only" in v["message"] for v in result["violations"]))

    def test_check_policies_allows_compare(self) -> None:
        result = self.gov.check_policies("sess_fake", "compare_watch_time")
        self.assertTrue(result["passed"])

    def test_check_quality_row_count_min(self) -> None:
        self.gov.create_quality_rule(
            "row_count_check", "row_count_min", "analytics.watch_events",
            {"min_rows": 1}, severity="block",
        )
        result = self.gov.check_quality("analytics.watch_events")
        self.assertTrue(result["passed"])

    def test_check_step_combined(self) -> None:
        result = self.gov.check_step("sess_fake", "compare_watch_time")
        self.assertIn("passed", result)
        self.assertIn("violations", result)


class GovernanceAPITests(unittest.TestCase):
    """Integration tests for governance endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "gov_api.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_policy_crud_via_api(self) -> None:
        resp = self.client.post("/policies", json={
            "name": "api_test_pol",
            "policy_type": "aggregate_only",
        })
        self.assertEqual(resp.status_code, 200)
        policy_id = resp.json()["policy_id"]

        resp = self.client.get(f"/policies/{policy_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "api_test_pol")

        resp = self.client.get("/policies")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

        resp = self.client.put(f"/policies/{policy_id}", json={"enabled": False})
        self.assertEqual(resp.status_code, 200)

        resp = self.client.delete(f"/policies/{policy_id}")
        self.assertEqual(resp.status_code, 200)

    def test_quality_rules_via_api(self) -> None:
        resp = self.client.post("/quality-rules", json={
            "name": "api_qr_test",
            "rule_type": "freshness",
            "table_name": "analytics.watch_events",
            "threshold": {"max_age_hours": 24},
        })
        self.assertEqual(resp.status_code, 200)
        rule_id = resp.json()["rule_id"]

        resp = self.client.get("/quality-rules")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.delete(f"/quality-rules/{rule_id}")
        self.assertEqual(resp.status_code, 200)

    def test_governance_check_endpoint(self) -> None:
        resp = self.client.post("/governance/check", json={
            "session_id": "sess_fake123456",
            "step_type": "compare_watch_time",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("passed", resp.json())


if __name__ == "__main__":
    unittest.main()
