"""Tests for the approval workflow module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.approvals import ApprovalService
from app.main import create_app
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class ApprovalServiceTests(unittest.TestCase):
    """Unit tests for ApprovalService."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "approval_test.duckdb"
        get_seeded_duckdb_path(db_path)
        meta_path = db_path.with_suffix(".meta.sqlite")
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()
        cls.analytics = DuckDBAnalyticsEngine(db_path)
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.approval = ApprovalService(cls.metadata)
        # Run a workflow to create recommendations
        session = cls.service.create_session("Approval test", {}, {}, {})
        cls.session_id = session["session_id"]
        cls.service.run_watch_time_drop_workflow(cls.session_id)
        # Get recommendation IDs
        recs = cls.metadata.query_rows(
            "SELECT rec_id, risk FROM recommendations WHERE session_id = ?",
            [cls.session_id],
        )
        cls.rec_ids = [r["rec_id"] for r in recs]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_request_approval(self) -> None:
        if not self.rec_ids:
            self.skipTest("No recommendations generated")
        result = self.approval.request_approval(self.session_id, self.rec_ids[0])
        self.assertTrue(result["request_id"].startswith("apr_"))
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["rec_id"], self.rec_ids[0])

    def test_request_approval_duplicate(self) -> None:
        if not self.rec_ids:
            self.skipTest("No recommendations generated")
        # Second call should return existing request
        r1 = self.approval.request_approval(self.session_id, self.rec_ids[0])
        r2 = self.approval.request_approval(self.session_id, self.rec_ids[0])
        self.assertEqual(r1["request_id"], r2["request_id"])

    def test_request_approval_unknown_rec(self) -> None:
        with self.assertRaises(KeyError):
            self.approval.request_approval(self.session_id, "rec_nonexistent")

    def test_list_requests(self) -> None:
        requests = self.approval.list_requests(session_id=self.session_id)
        self.assertIsInstance(requests, list)

    def test_approve_request(self) -> None:
        if len(self.rec_ids) < 2:
            self.skipTest("Not enough recommendations")
        req = self.approval.request_approval(self.session_id, self.rec_ids[1])
        result = self.approval.approve(req["request_id"], reviewer="admin", reason="Looks good")
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["reviewer"], "admin")

    def test_reject_request(self) -> None:
        if len(self.rec_ids) < 3:
            self.skipTest("Not enough recommendations")
        req = self.approval.request_approval(self.session_id, self.rec_ids[2])
        result = self.approval.reject(req["request_id"], reviewer="admin", reason="Too risky")
        self.assertEqual(result["status"], "rejected")

    def test_approve_non_pending_fails(self) -> None:
        if len(self.rec_ids) < 2:
            self.skipTest("Not enough recommendations")
        req = self.approval.list_requests(session_id=self.session_id, status="approved")
        if req:
            with self.assertRaises(ValueError):
                self.approval.approve(req[0]["request_id"], reviewer="admin")

    def test_auto_flag_recommendations(self) -> None:
        # Create a fresh session with workflow
        session = self.service.create_session("Auto-flag test", {}, {}, {})
        self.service.run_watch_time_drop_workflow(session["session_id"])
        flagged = self.approval.auto_flag_recommendations(session["session_id"], risk_threshold="P1")
        self.assertIsInstance(flagged, list)
        # All flagged should be pending
        for f in flagged:
            self.assertEqual(f["status"], "pending")

    def test_get_request(self) -> None:
        requests = self.approval.list_requests(session_id=self.session_id)
        if not requests:
            self.skipTest("No requests")
        fetched = self.approval.get_request(requests[0]["request_id"])
        self.assertEqual(fetched["request_id"], requests[0]["request_id"])

    def test_get_request_not_found(self) -> None:
        with self.assertRaises(KeyError):
            self.approval.get_request("apr_nonexistent")


class ApprovalAPITests(unittest.TestCase):
    """Integration tests for approval endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "approval_api.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))
        # Create a session and run workflow to get recommendations
        resp = cls.client.post("/sessions", json={"goal": "Approval API test"})
        cls.session_id = resp.json()["session_id"]
        cls.client.post(f"/sessions/{cls.session_id}/workflow/watch-time-drop")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _get_rec_ids(self) -> list[str]:
        resp = self.client.get(f"/sessions/{self.session_id}/evidence")
        recs = resp.json().get("recommendations", [])
        return [r["rec_id"] for r in recs]

    def test_create_and_approve_via_api(self) -> None:
        rec_ids = self._get_rec_ids()
        if not rec_ids:
            self.skipTest("No recommendations")
        resp = self.client.post("/approvals", json={
            "session_id": self.session_id,
            "rec_id": rec_ids[0],
        })
        self.assertEqual(resp.status_code, 200)
        request_id = resp.json()["request_id"]

        resp = self.client.post(f"/approvals/{request_id}/approve", json={
            "reviewer": "test_admin",
            "reason": "LGTM",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "approved")

    def test_list_approvals_via_api(self) -> None:
        resp = self.client.get("/approvals")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_auto_flag_via_api(self) -> None:
        resp = self.client.post(
            f"/sessions/{self.session_id}/approvals/auto-flag",
            json={"risk_threshold": "P1"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_reject_via_api(self) -> None:
        rec_ids = self._get_rec_ids()
        if len(rec_ids) < 2:
            self.skipTest("Not enough recommendations")
        resp = self.client.post("/approvals", json={
            "session_id": self.session_id,
            "rec_id": rec_ids[-1],
        })
        request_id = resp.json()["request_id"]
        # Only reject if still pending
        if resp.json()["status"] == "pending":
            resp = self.client.post(f"/approvals/{request_id}/reject", json={
                "reviewer": "test_admin",
                "reason": "Not now",
            })
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
