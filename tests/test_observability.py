"""Tests for the observability module."""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi.testclient import TestClient

from marivo.main import create_app
from marivo.observability import (
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
        self.assertIn("marivo_requests_total", text)
        self.assertIn("marivo_step_executions_total", text)
        self.assertIn('method="POST"', text)

    def test_snapshot_structure(self) -> None:
        mc = MetricsCollector()
        snap = mc.snapshot()
        self.assertIn("request_count", snap)
        self.assertIn("error_count", snap)
        self.assertIn("step_count", snap)
        self.assertIn("execution_stage_count", snap)
        self.assertIn("active_sessions", snap)


class JSONFormatterTests(unittest.TestCase):
    """Tests for the JSON log formatter."""

    def test_format_includes_fields(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        self.assertIn('"message": "Hello world"', output)
        self.assertIn('"level": "INFO"', output)

    def test_format_includes_correlation_id(self) -> None:
        formatter = JSONFormatter()
        token = correlation_session_id.set("sess_test123")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="test msg",
                args=(),
                exc_info=None,
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
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="planner msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            self.assertIn("draft_plan", output)
            self.assertIn("planner", output)
        finally:
            correlation_planner_id.reset(planner_token)
            correlation_execution_stage.reset(stage_token)

    def test_format_includes_extra_fields(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="marivo.runtime.execution",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="SQL execution",
            args=(),
            exc_info=None,
        )
        record.sql = "SELECT 1"
        record.param_count = 0
        output = formatter.format(record)
        self.assertIn('"sql": "SELECT 1"', output)
        self.assertIn('"param_count": 0', output)


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

    def test_health_reports_status_only(self) -> None:
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_metrics_endpoint_prometheus(self) -> None:
        self.client.get("/health")
        resp = self.client.get("/metrics?format=prometheus")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("marivo_requests_total", resp.text)

    def test_timing_middleware_records_requests(self) -> None:
        self.client.get("/health")
        resp = self.client.get("/metrics")
        data = resp.json()
        self.assertTrue(any("/health" in k for k in data["request_count"]))


class SetupLoggingFileHandlerTests(unittest.TestCase):
    """Tests for setup_logging() file handler support."""

    def tearDown(self) -> None:
        logging.getLogger().handlers.clear()

    def test_file_handler_created_when_log_file_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "logs" / "runtime.jsonl"
            setup_logging(log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
            self.assertEqual(len(file_handlers), 1)
            self.assertTrue(log_path.parent.exists())

    def test_no_file_handler_when_log_file_is_none(self) -> None:
        os.environ.pop("MARIVO_LOG_DIR", None)
        setup_logging()
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(file_handlers), 0)

    def test_marivo_log_dir_env_var_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["MARIVO_LOG_DIR"] = tmp
            try:
                setup_logging()
                root = logging.getLogger()
                file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
                self.assertEqual(len(file_handlers), 1)
                expected_path = Path(tmp) / "runtime.jsonl"
                self.assertEqual(file_handlers[0].baseFilename, str(expected_path))
            finally:
                os.environ.pop("MARIVO_LOG_DIR", None)

    def test_explicit_log_file_takes_precedence_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            os.environ["MARIVO_LOG_DIR"] = tmp1
            explicit_path = Path(tmp2) / "logs" / "runtime.jsonl"
            try:
                setup_logging(log_file=explicit_path)
                root = logging.getLogger()
                file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
                self.assertEqual(len(file_handlers), 1)
                self.assertEqual(file_handlers[0].baseFilename, str(explicit_path))
            finally:
                os.environ.pop("MARIVO_LOG_DIR", None)

    def test_graceful_on_unwritable_path(self) -> None:
        setup_logging(log_file=Path("/nonexistent_root/logs/runtime.jsonl"))
        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        self.assertTrue(len(stream_handlers) >= 1)

    def test_file_handler_uses_json_formatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "runtime.jsonl"
            setup_logging(log_file=log_path)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
            self.assertTrue(len(file_handlers) >= 1)
            self.assertIsInstance(file_handlers[0].formatter, JSONFormatter)

    def test_file_handler_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "runtime.jsonl"
            old_level = os.environ.get("LOG_LEVEL")
            os.environ["LOG_LEVEL"] = "DEBUG"
            try:
                setup_logging(log_file=log_path)
                logging.getLogger().info("test log entry")
                for h in logging.getLogger().handlers:
                    if isinstance(h, RotatingFileHandler):
                        h.flush()
                content = log_path.read_text().strip()
                self.assertIn('"message": "test log entry"', content)
            finally:
                if old_level is not None:
                    os.environ["LOG_LEVEL"] = old_level
                else:
                    os.environ.pop("LOG_LEVEL", None)


if __name__ == "__main__":
    unittest.main()
