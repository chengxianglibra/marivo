"""Tests for the observability module."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.observability import (
    JSONFormatter,
    MetricsCollector,
    correlation_execution_stage,
    correlation_planner_id,
    correlation_session_id,
    setup_logging,
)
from tests.shared_fixtures import get_seeded_duckdb_path


class MetricsCollectorTests(unittest.TestCase):
    """Unit tests for MetricsCollector."""

    def test_record_request(self) -> None:
        mc = MetricsCollector()
        mc.record_request("GET", "/health", 200, 5.0)
        mc.record_request("GET", "/health", 200, 3.0)
        snap = mc.snapshot()
        self.assertEqual(snap["request_count"]["GET:/health"], 2)
        self.assertAlmostEqual(snap["request_duration_sum_ms"]["GET:/health"], 8.0)

    def test_record_error(self) -> None:
        mc = MetricsCollector()
        mc.record_request("GET", "/bad", 404, 1.0)
        snap = mc.snapshot()
        self.assertEqual(snap["error_count"][404], 1)

    def test_record_step(self) -> None:
        mc = MetricsCollector()
        mc.record_step("metric_query", 150.0, engine="duckdb", stage="executor")
        mc.record_step("metric_query", 200.0)
        snap = mc.snapshot()
        self.assertEqual(snap["step_count"]["metric_query"], 2)
        self.assertEqual(len(snap["step_duration_ms"]["metric_query"]), 2)
        self.assertIn("step_dimension_count", snap)
        self.assertTrue(any("engine=duckdb" in key for key in snap["step_dimension_count"]))

    def test_prometheus_output(self) -> None:
        mc = MetricsCollector()
        mc.record_request("POST", "/sessions", 200, 10.0)
        mc.record_step("profile_table", 50.0)
        text = mc.prometheus()
        self.assertIn("factum_requests_total", text)
        self.assertIn("factum_step_executions_total", text)
        self.assertIn('method="POST"', text)

    def test_snapshot_structure(self) -> None:
        mc = MetricsCollector()
        snap = mc.snapshot()
        self.assertIn("request_count", snap)
        self.assertIn("error_count", snap)
        self.assertIn("step_count", snap)
        self.assertIn("execution_stage_count", snap)
        self.assertIn("active_sessions", snap)
        self.assertIn("active_jobs", snap)


class JSONFormatterTests(unittest.TestCase):
    """Tests for the JSON log formatter."""

    def test_format_includes_fields(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Hello %s", args=("world",), exc_info=None,
        )
        output = formatter.format(record)
        self.assertIn('"message": "Hello world"', output)
        self.assertIn('"level": "INFO"', output)

    def test_format_includes_correlation_id(self) -> None:
        formatter = JSONFormatter()
        token = correlation_session_id.set("sess_test123")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="test.py",
                lineno=1, msg="test msg", args=(), exc_info=None,
            )
            output = formatter.format(record)
            self.assertIn("sess_test123", output)
        finally:
            correlation_session_id.reset(token)

    def test_format_includes_execution_dimensions(self) -> None:
        formatter = JSONFormatter()
        planner_token = correlation_planner_id.set("draft_plan")
        stage_token = correlation_execution_stage.set("planner")
        try:
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="test.py",
                lineno=1, msg="planner msg", args=(), exc_info=None,
            )
            output = formatter.format(record)
            self.assertIn("draft_plan", output)
            self.assertIn("planner", output)
        finally:
            correlation_planner_id.reset(planner_token)
            correlation_execution_stage.reset(stage_token)


class ObservabilityAPITests(unittest.TestCase):
    """Integration tests for /metrics endpoint and timing middleware."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "obs_test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_metrics_endpoint_json(self) -> None:
        # Make a request first so there are metrics to report
        self.client.get("/health")
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("request_count", data)

    def test_metrics_endpoint_prometheus(self) -> None:
        self.client.get("/health")
        resp = self.client.get("/metrics?format=prometheus")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("factum_requests_total", resp.text)

    def test_timing_middleware_records_requests(self) -> None:
        self.client.get("/health")
        resp = self.client.get("/metrics")
        data = resp.json()
        self.assertTrue(any("/health" in k for k in data["request_count"]))


if __name__ == "__main__":
    unittest.main()
