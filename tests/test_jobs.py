"""Tests for the async job orchestration module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.jobs import JobService
from app.main import create_app
from app.planning import PlanningService
from app.service import SemanticLayerService
from app.storage.duckdb_analytics import DuckDBAnalyticsEngine
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


class JobServiceTests(unittest.TestCase):
    """Unit tests for JobService."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "job_test.duckdb"
        get_seeded_duckdb_path(db_path)
        meta_path = db_path.with_suffix(".meta.sqlite")
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()
        cls.analytics = DuckDBAnalyticsEngine(db_path)
        cls.analytics.initialize()
        cls.service = SemanticLayerService(cls.metadata, cls.analytics)
        cls.planning = PlanningService(cls.metadata)
        cls.job_service = JobService(cls.metadata, cls.service, planning_service=cls.planning)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def _create_session(self) -> str:
        session = self.service.create_session("Test job", {}, {}, {})
        return session["session_id"]

    def test_submit_step_job(self) -> None:
        session_id = self._create_session()
        job = self.job_service.submit_job(
            session_id,
            "step",
            {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
        )
        self.assertTrue(job["job_id"].startswith("job_"))
        self.assertEqual(job["job_type"], "step")
        # In sync mode (no event loop), job completes immediately
        self.assertEqual(job["status"], "completed")

    def test_submit_invalid_type(self) -> None:
        with self.assertRaises(ValueError):
            self.job_service.submit_job("sess_fake", "invalid", {})

    def test_get_job(self) -> None:
        session_id = self._create_session()
        submitted = self.job_service.submit_job(
            session_id,
            "step",
            {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
        )
        fetched = self.job_service.get_job(submitted["job_id"])
        self.assertEqual(fetched["job_id"], submitted["job_id"])

    def test_get_job_not_found(self) -> None:
        with self.assertRaises(KeyError):
            self.job_service.get_job("job_nonexistent")

    def test_list_jobs(self) -> None:
        jobs = self.job_service.list_jobs()
        self.assertIsInstance(jobs, list)

    def test_list_jobs_by_session(self) -> None:
        session_id = self._create_session()
        self.job_service.submit_job(
            session_id,
            "step",
            {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
        )
        jobs = self.job_service.list_jobs(session_id=session_id)
        self.assertTrue(all(j["session_id"] == session_id for j in jobs))

    def test_cancel_completed_job_fails(self) -> None:
        session_id = self._create_session()
        job = self.job_service.submit_job(
            session_id,
            "step",
            {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
        )
        with self.assertRaises(ValueError):
            self.job_service.cancel_job(job["job_id"])

    def test_failed_job_records_error(self) -> None:
        session_id = self._create_session()
        job = self.job_service.submit_job(
            session_id,
            "step",
            {"step_type": "nonexistent_step"},
        )
        self.assertEqual(job["status"], "failed")
        self.assertIn("error_message", job)

    def test_plan_job(self) -> None:
        session_id = self._create_session()
        plan = self.planning.draft_plan(
            session_id,
            [
                {"step_type": "profile_table", "params": {"table_name": "analytics.watch_events"}},
            ],
        )
        self.planning.validate_plan(plan["plan_id"])
        self.planning.approve_plan(plan["plan_id"])
        job = self.job_service.submit_job(
            session_id,
            "plan",
            {"plan_id": plan["plan_id"]},
        )
        self.assertEqual(job["status"], "completed")


class JobAPITests(unittest.TestCase):
    """Integration tests for job endpoints."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "job_api.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_session(self) -> str:
        resp = self.client.post("/sessions", json={"goal": "Job API test"})
        return resp.json()["session_id"]

    def test_submit_and_get_job_via_api(self) -> None:
        session_id = self._create_session()
        resp = self.client.post(
            "/jobs",
            json={
                "session_id": session_id,
                "job_type": "step",
                "payload": {
                    "step_type": "profile_table",
                    "params": {"table_name": "analytics.watch_events"},
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()["job_id"]

        resp = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_id"], job_id)

    def test_list_jobs_via_api(self) -> None:
        resp = self.client.get("/jobs")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)


if __name__ == "__main__":
    unittest.main()
