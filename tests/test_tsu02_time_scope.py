from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app.service as service_module
from app.analysis_core.compiler import CompiledQuery
from app.main import create_app
from app.time_scope import AdHocAggregateValueSpec
from app.time_scope import SemanticMetricValueSpec
from app.time_scope import normalize_aggregate_query_request
from app.time_scope import normalize_compare_metric_request
from tests.shared_fixtures import get_seeded_duckdb_path


class TimeScopeNormalizationTests(unittest.TestCase):
    def test_compare_metric_normalizes_to_shared_request(self) -> None:
        resolved = normalize_compare_metric_request({
            "table": "analytics.watch_events",
            "metric": "watch_time",
            "dimensions": ["platform"],
            "time_scope": {
                "mode": "compare",
                "grain": "day",
                "current": {"start": "2026-03-10", "end": "2026-03-17"},
                "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
            },
        })
        self.assertEqual(resolved.table, "analytics.watch_events")
        self.assertEqual(resolved.compare_kind, "semantic_metric")
        self.assertEqual(resolved.grouping, ["platform"])
        self.assertIsInstance(resolved.value_spec, SemanticMetricValueSpec)
        self.assertEqual(resolved.value_spec.metric, "watch_time")
        self.assertEqual(resolved.resolved_time_axis.observation_grain, "day")

    def test_aggregate_query_normalizes_measures_and_time_axis_override(self) -> None:
        resolved = normalize_aggregate_query_request({
            "table": "analytics.watch_events",
            "group_by": ["platform"],
            "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2026-03-10", "end": "2026-03-17"},
            },
            "time_axis": {
                "analysis_time": {"column": "event_date"},
                "partition_pruning": {"date_column": "log_date", "hour_column": "log_hour"},
            },
        })
        self.assertEqual(resolved.compare_kind, "ad_hoc_aggregate")
        self.assertIsInstance(resolved.value_spec, AdHocAggregateValueSpec)
        self.assertEqual(resolved.value_spec.measures[0].alias, "query_count")
        self.assertEqual(resolved.resolved_time_axis.override_analysis_time_column, "event_date")
        self.assertEqual(resolved.resolved_time_axis.override_partition_date_column, "log_date")
        self.assertEqual(resolved.resolved_time_axis.override_partition_hour_column, "log_hour")

    def test_missing_optional_scope_and_time_axis_get_empty_defaults(self) -> None:
        resolved = normalize_aggregate_query_request({
            "table": "analytics.watch_events",
            "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
            "time_scope": {
                "mode": "single_window",
                "grain": "day",
                "current": {"start": "2026-03-10", "end": "2026-03-17"},
            },
        })
        self.assertEqual(resolved.scope.constraints, {})
        self.assertIsNone(resolved.scope.predicate)
        self.assertIsNone(resolved.resolved_time_axis.override_analysis_time_column)
        self.assertEqual(resolved.resolved_time_axis.observation_grain, "day")

    def test_normalizers_reject_legacy_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy fields"):
            normalize_compare_metric_request({
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
                "period_end": "2026-03-16",
            })

    def test_normalizers_reject_time_predicates_in_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope.predicate must not contain time-axis predicates"):
            normalize_compare_metric_request({
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
                "scope": {"predicate": "event_time >= TIMESTAMP '2026-03-01 00:00:00'"},
            })

    def test_normalizers_allow_non_axis_suffix_predicates_in_scope(self) -> None:
        resolved = normalize_compare_metric_request({
            "table": "analytics.watch_events",
            "metric": "watch_time",
            "time_scope": {
                "mode": "compare",
                "grain": "day",
                "current": {"start": "2026-03-10", "end": "2026-03-17"},
                "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
            },
            "scope": {"predicate": "business_hour = 9 AND state_date = '2026-03-01'"},
        })
        self.assertEqual(resolved.scope.predicate, "business_hour = 9 AND state_date = '2026-03-01'")


class _FakeEngine:
    def query_rows(self, sql: str) -> list[dict[str, str]]:
        if "MAX(" in sql:
            return [{"max_date": "20260331"}]
        return []


class TimeScopeServiceBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmp.name) / "tsu02.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path)
        cls.service = cls.app.state.service

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        client = self._client()
        try:
            entity_resp = client.post("/semantic/entities", json={
                "name": f"session_tsu02_{id(self)}",
                "display_name": "Session",
                "keys": ["session_id"],
            })
            entity_id = entity_resp.json()["entity_id"]
            client.post(f"/semantic/entities/{entity_id}/publish")

            metric_resp = client.post("/semantic/metrics", json={
                "name": f"watch_time_tsu02_{id(self)}",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "event_date"],
                "entity_id": entity_id,
            })
            metric_id = metric_resp.json()["metric_id"]
            client.post(f"/semantic/metrics/{metric_id}/publish")

            self.metric_name = f"watch_time_tsu02_{id(self)}"
            self.session_id = client.post("/sessions", json={
                "goal": "TSU-02",
                "constraints": {"platform": "android"},
                "raw_filter": "country = 'US'",
            }).json()["session_id"]
        finally:
            client.close()

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(self.app)

    def test_compare_metric_service_bridges_normalized_request(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        original_extract = self.service.evidence_pipeline.extract_observations
        self.service._resolve_engine = lambda table_names: (_FakeEngine(), "duckdb", {table_names[0]: f"analytics.{table_names[0]}"})
        self.service.evidence_pipeline.extract_observations = lambda *args, **kwargs: []

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            captured["semantic_context"] = semantic_context or {}
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [{"platform": "android", "current_value": 10.0, "baseline_value": 5.0, "delta_pct": 100.0, "current_sessions": 10, "baseline_sessions": 8}]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            result = self.service._run_compare_metric(self.session_id, {
                "table": "analytics.watch_events",
                "metric": self.metric_name,
                "dimensions": ["platform"],
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
                "scope": {
                    "constraints": {"region": "us-east"},
                    "predicate": "device_type = 'phone'",
                },
            })
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine
            self.service.evidence_pipeline.extract_observations = original_extract

        self.assertEqual(result["step_type"], "compare_metric")
        self.assertEqual(captured["params"]["metric_name"], self.metric_name)
        self.assertEqual(captured["params"]["table_name"], "analytics.watch_events")
        self.assertEqual(captured["params"]["period_start"], "2026-03-10")
        self.assertEqual(captured["params"]["period_end"], "2026-03-16")
        self.assertEqual(captured["params"]["baseline_start"], "2026-03-03")
        self.assertEqual(captured["params"]["baseline_end"], "2026-03-09")
        self.assertIn("platform = 'android'", captured["params"]["filter"])
        self.assertIn("country = 'US'", captured["params"]["filter"])
        self.assertIn("region = 'us-east'", captured["params"]["filter"])
        self.assertIn("device_type = 'phone'", captured["params"]["filter"])

    def test_aggregate_query_service_bridges_normalized_request(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        original_extract = self.service.evidence_pipeline.extract_observations
        self.service._resolve_engine = lambda table_names: (_FakeEngine(), "duckdb", {table_names[0]: f"analytics.{table_names[0]}"})
        self.service.evidence_pipeline.extract_observations = lambda *args, **kwargs: []

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            captured["semantic_context"] = semantic_context or {}
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [{"platform": "android", "query_count_current": 10, "query_count_baseline": 5, "query_count_delta_pct": 100.0}]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            result = self.service._run_aggregate_query(self.session_id, {
                "table": "analytics.watch_events",
                "group_by": ["platform"],
                "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
                "scope": {
                    "constraints": {"region": "us-east"},
                    "predicate": "device_type = 'phone'",
                },
                "time_axis": {"analysis_time": {"column": "event_date"}},
                "order": "query_count_delta_pct DESC",
            })
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine
            self.service.evidence_pipeline.extract_observations = original_extract

        self.assertEqual(result["step_type"], "aggregate_query")
        self.assertEqual(captured["params"]["table_name"], "analytics.watch_events")
        self.assertEqual(captured["params"]["select"], ["platform", "COUNT(*) AS query_count"])
        self.assertEqual(captured["params"]["group_by"], ["platform"])
        self.assertEqual(captured["params"]["date_column"], "event_date")
        self.assertTrue(captured["params"]["compare_period"])
        self.assertEqual(captured["params"]["order_by"], "query_count_delta_pct DESC")
        self.assertIn("platform = 'android'", captured["params"]["where"])
        self.assertIn("country = 'US'", captured["params"]["where"])
        self.assertIn("region = 'us-east'", captured["params"]["where"])
        self.assertIn("device_type = 'phone'", captured["params"]["where"])
