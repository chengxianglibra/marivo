from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.models import AggregateQueryStep
from app.api.models import CompareMetricStep
from app.api.models import TimeScope
from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class TimeScopeModelTests(unittest.TestCase):
    def test_compare_mode_requires_baseline(self) -> None:
        with self.assertRaises(ValueError):
            TimeScope.model_validate({
                "mode": "compare",
                "grain": "day",
                "current": {"start": "2026-03-01", "end": "2026-03-02"},
            })

    def test_single_window_rejects_baseline(self) -> None:
        with self.assertRaises(ValueError):
            TimeScope.model_validate({
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2026-03-01", "end": "2026-03-02"},
                "baseline": {"start": "2026-02-28", "end": "2026-03-01"},
            })

    def test_hour_grain_requires_datetime_boundaries(self) -> None:
        with self.assertRaises(ValueError):
            TimeScope.model_validate({
                "mode": "single_window",
                "grain": "hour",
                "current": {"start": "2026-03-01", "end": "2026-03-02"},
            })

    def test_day_grain_accepts_date_only_boundaries(self) -> None:
        scope = TimeScope.model_validate({
            "mode": "single_window",
            "grain": "day",
            "current": {"start": "2026-03-01", "end": "2026-03-02"},
        })
        self.assertEqual(scope.grain, "day")


class StepModelTests(unittest.TestCase):
    def test_compare_metric_accepts_rfc_shape(self) -> None:
        payload = CompareMetricStep.model_validate({
            "table": "iceberg.analytics.watch_events",
            "metric": "avg_watch_time_minutes",
            "dimensions": ["device_type"],
            "time_scope": {
                "mode": "compare",
                "grain": "hour",
                "current": {
                    "start": "2026-03-25T10:00:00",
                    "end": "2026-03-25T14:00:00",
                },
                "baseline": {
                    "start": "2026-03-25T06:00:00",
                    "end": "2026-03-25T10:00:00",
                },
            },
            "scope": {
                "constraints": {"cluster": "prod"},
                "predicate": "query_state = 'FAILED'",
            },
            "time_axis": {
                "analysis_time": {"column": "event_time"},
                "partition_pruning": {"date_column": "log_date", "hour_column": "log_hour"},
            },
            "order": "delta_pct DESC",
            "limit": 50,
        })
        self.assertEqual(payload.metric, "avg_watch_time_minutes")

    def test_aggregate_query_requires_aliased_aggregate_measures(self) -> None:
        with self.assertRaises(ValueError):
            AggregateQueryStep.model_validate({
                "table": "iceberg.analytics.watch_events",
                "group_by": ["device_type"],
                "measures": [{"expr": "watch_duration_sec", "as": "raw_watch_duration"}],
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-01", "end": "2026-03-02"},
                },
            })

    def test_aggregate_query_serializes_measure_alias_as_keyword(self) -> None:
        payload = AggregateQueryStep.model_validate({
            "table": "iceberg.analytics.watch_events",
            "group_by": ["device_type"],
            "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2026-03-01", "end": "2026-03-02"},
            },
        })
        self.assertEqual(payload.model_dump(by_alias=True)["measures"][0]["as"], "query_count")


class TypedStepRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmp.name) / "tsu01.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.session_id = self.client.post("/sessions", json={"goal": "TSU-01 contract test"}).json()["session_id"]

    def test_compare_metric_route_uses_typed_body_schema(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_step(session_id: str, step_type: str, params: dict[str, object] | None = None) -> dict[str, object]:
            captured["session_id"] = session_id
            captured["step_type"] = step_type
            captured["params"] = params or {}
            return {"step_type": step_type, "status": "accepted", "summary": "stub"}

        original = self.app.state.service.run_step
        self.app.state.service.run_step = fake_run_step
        try:
            response = self.client.post(
                f"/sessions/{self.session_id}/steps/compare_metric",
                json={
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-01", "end": "2026-03-08"},
                    },
                    "limit": 20,
                },
            )
        finally:
            self.app.state.service.run_step = original

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["step_type"], "compare_metric")
        self.assertEqual(captured["params"], {
            "table": "analytics.watch_events",
            "metric": "watch_time",
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2026-03-01", "end": "2026-03-08"},
            },
            "limit": 20,
        })

    def test_compare_metric_route_rejects_invalid_time_scope_before_service(self) -> None:
        response = self.client.post(
            f"/sessions/{self.session_id}/steps/compare_metric",
            json={
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-01", "end": "2026-03-08"},
                },
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_openapi_exposes_specialized_request_bodies(self) -> None:
        schema = self.client.get("/openapi.json").json()
        compare_path = schema["paths"]["/sessions/{session_id}/steps/compare_metric"]["post"]
        aggregate_path = schema["paths"]["/sessions/{session_id}/steps/aggregate_query"]["post"]
        self.assertIn("CompareMetricStep", compare_path["requestBody"]["content"]["application/json"]["schema"]["$ref"])
        self.assertIn("AggregateQueryStep", aggregate_path["requestBody"]["content"]["application/json"]["schema"]["$ref"])
