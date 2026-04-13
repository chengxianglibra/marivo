from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import app.service as service_module
from app.analysis_core.compiler import CompiledQuery
from app.main import create_app
from app.time_axis_metadata import TimeAxisMetadataContext
from app.time_scope import (
    AdHocAggregateValueSpec,
    SemanticMetricValueSpec,
    TimeAxisResolver,
    TimeScopeResolver,
    normalize_aggregate_query_request,
    normalize_metric_query_request,
)
from tests.semantic_test_helpers import (
    create_typed_entity,
    create_typed_metric,
    ensure_published_typed_dimension,
    patch_typed_entity_properties,
    publish_typed_entity,
    publish_typed_metric,
)
from tests.shared_fixtures import get_seeded_duckdb_path


class TimeScopeNormalizationTests(unittest.TestCase):
    def test_metric_query_normalizes_to_shared_request(self) -> None:
        resolved = normalize_metric_query_request(
            {
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "dimensions": ["platform"],
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
            }
        )
        self.assertEqual(resolved.table, "analytics.watch_events")
        self.assertEqual(resolved.compare_kind, "semantic_metric")
        self.assertEqual(resolved.grouping, ["platform"])
        self.assertIsInstance(resolved.value_spec, SemanticMetricValueSpec)
        self.assertEqual(resolved.value_spec.metric, "watch_time")
        self.assertEqual(resolved.resolved_time_axis.observation_grain, "day")
        self.assertEqual(resolved.time_scope.current.start, "2026-03-10")
        self.assertEqual(resolved.time_scope.current.end, "2026-03-17")
        self.assertEqual(resolved.time_scope.warnings, [])

    def test_aggregate_query_normalizes_measures_and_time_axis_override(self) -> None:
        resolved = normalize_aggregate_query_request(
            {
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
            }
        )
        self.assertEqual(resolved.compare_kind, "ad_hoc_aggregate")
        self.assertIsInstance(resolved.value_spec, AdHocAggregateValueSpec)
        self.assertEqual(resolved.value_spec.measures[0].alias, "query_count")
        self.assertEqual(resolved.resolved_time_axis.override_analysis_time_column, "event_date")
        self.assertEqual(resolved.resolved_time_axis.override_partition_date_column, "log_date")
        self.assertEqual(resolved.resolved_time_axis.override_partition_hour_column, "log_hour")

    def test_missing_optional_scope_and_time_axis_get_empty_defaults(self) -> None:
        resolved = normalize_aggregate_query_request(
            {
                "table": "analytics.watch_events",
                "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                },
            }
        )
        self.assertEqual(resolved.scope.constraints, {})
        self.assertIsNone(resolved.scope.predicate)
        self.assertIsNone(resolved.resolved_time_axis.override_analysis_time_column)
        self.assertEqual(resolved.resolved_time_axis.observation_grain, "day")

    def test_normalizers_reject_legacy_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "legacy fields"):
            normalize_metric_query_request(
                {
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                    "period_end": "2026-03-16",
                }
            )

    def test_normalizers_reject_time_predicates_in_scope(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "scope.predicate must not contain time-axis predicates"
        ):
            normalize_metric_query_request(
                {
                    "table": "analytics.watch_events",
                    "metric": "watch_time",
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                    "scope": {"predicate": "event_time >= TIMESTAMP '2026-03-01 00:00:00'"},
                }
            )

    def test_normalizers_allow_non_axis_suffix_predicates_in_scope(self) -> None:
        resolved = normalize_metric_query_request(
            {
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                },
                "scope": {"predicate": "business_hour = 9 AND state_date = '2026-03-01'"},
            }
        )
        self.assertEqual(
            resolved.scope.predicate, "business_hour = 9 AND state_date = '2026-03-01'"
        )

    def test_day_grain_normalizes_datetime_boundaries_to_dates(self) -> None:
        resolved = normalize_metric_query_request(
            {
                "table": "analytics.watch_events",
                "metric": "watch_time",
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {
                        "start": "2026-03-10T08:15:00",
                        "end": "2026-03-17 00:00:00",
                    },
                },
            }
        )
        self.assertEqual(resolved.time_scope.current.start, "2026-03-10")
        self.assertEqual(resolved.time_scope.current.end, "2026-03-17")

    def test_hour_grain_normalizes_to_second_precision(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "single_window",
                "grain": "hour",
                "current": {
                    "start": "2026-03-25 10:00:00.999999",
                    "end": "2026-03-25T14:00:00",
                },
            }
        )
        self.assertEqual(resolved.current.start, "2026-03-25T10:00:00")
        self.assertEqual(resolved.current.end, "2026-03-25T14:00:00")

    def test_hour_grain_rejects_date_only_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "datetime string for hour grain"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "hour",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                }
            )

    def test_hour_grain_rejects_timezone_aware_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "naive datetime"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "hour",
                    "current": {
                        "start": "2026-03-25T10:00:00+08:00",
                        "end": "2026-03-25T14:00:00+08:00",
                    },
                }
            )

    def test_compare_mode_requires_baseline_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "time_scope.baseline is required"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "compare",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                }
            )

    def test_single_window_rejects_baseline_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "only allowed when mode='compare'"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-26"},
                    "baseline": {"start": "2026-03-24", "end": "2026-03-25"},
                }
            )

    def test_time_scope_rejects_non_increasing_windows(self) -> None:
        with self.assertRaisesRegex(ValueError, "start < end"):
            TimeScopeResolver(step_type="metric_query").resolve(
                {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-25"},
                }
            )

    def test_compare_mode_keeps_unequal_windows_and_adds_warning(self) -> None:
        resolved = TimeScopeResolver(step_type="metric_query").resolve(
            {
                "mode": "compare",
                "grain": "day",
                "current": {"start": "2026-03-10", "end": "2026-03-17"},
                "baseline": {"start": "2026-03-01", "end": "2026-03-03"},
            }
        )
        self.assertEqual(len(resolved.warnings), 1)
        self.assertEqual(resolved.warnings[0]["code"], "window_length_mismatch")
        self.assertEqual(resolved.warnings[0]["current_duration"], 7)
        self.assertEqual(resolved.warnings[0]["baseline_duration"], 2)


class TimeAxisResolverTests(unittest.TestCase):
    def _compare_request(self, *, grain: str = "hour", time_axis: dict[str, object] | None = None):
        payload: dict[str, object] = {
            "table": "iceberg.analytics.query_events",
            "metric": "queued_time",
            "time_scope": {
                "mode": "compare",
                "grain": grain,
                "current": {
                    "start": "2026-03-25T10:00:00" if grain == "hour" else "2026-03-25",
                    "end": "2026-03-25T14:00:00" if grain == "hour" else "2026-03-26",
                },
                "baseline": {
                    "start": "2026-03-25T06:00:00" if grain == "hour" else "2026-03-24",
                    "end": "2026-03-25T10:00:00" if grain == "hour" else "2026-03-25",
                },
            },
        }
        if time_axis is not None:
            payload["time_axis"] = time_axis
        return normalize_metric_query_request(payload)

    def test_resolver_prefers_timestamp_analysis_with_partition_pruning_for_mixed_layout(
        self,
    ) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "log_date", "log_hour"],
            entity_time_capabilities={
                "analysis_time": {
                    "timestamp_column": "event_time",
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "event_time")
        self.assertIn("log_date = '20260325'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour >= '06'", resolved.partition_pruning_predicate)
        self.assertIn("log_hour < '14'", resolved.partition_pruning_predicate)

    def test_resolver_builds_partition_only_hour_expression(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertIn("CAST(CONCAT(", resolved.analysis_time_expr)
        self.assertIn("SUBSTR(CAST(log_date AS VARCHAR), 1, 4)", resolved.analysis_time_expr)
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date = '20260325' AND log_hour >= '06' AND log_hour < '14'",
        )

    def test_resolver_reuses_metadata_date_format_for_partition_only_hour_expression(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["ds", "hh"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "ds",
                    "fallback_hour_column": "hh",
                },
                "partition_time": {
                    "date_column": "ds",
                    "date_format": "yyyymmdd",
                    "hour_column": "hh",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "partition_fields")
        self.assertEqual(resolved.analysis_time_format, "yyyymmdd")
        self.assertIn("SUBSTR(CAST(ds AS VARCHAR), 1, 4)", resolved.analysis_time_expr)
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "ds = '20260325' AND hh >= '06' AND hh < '14'",
        )

    def test_resolver_falls_back_to_date_field_for_day_partition_layout(self) -> None:
        request = self._compare_request(grain="day")
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["log_date", "resource_group"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        # log_date defaults to yyyymmdd format, so analysis_time_expr should be a CAST expression
        self.assertEqual(
            resolved.analysis_time_expr,
            "CAST(CONCAT(SUBSTR(CAST(log_date AS VARCHAR), 1, 4), '-', SUBSTR(CAST(log_date AS VARCHAR), 5, 2), '-', SUBSTR(CAST(log_date AS VARCHAR), 7, 2)) AS DATE)",
        )
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date >= '20260324' AND log_date < '20260326'",
        )

    def test_resolver_reuses_metadata_date_format_for_day_field_analysis(self) -> None:
        request = self._compare_request(grain="day")
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["ds", "resource_group"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "ds",
                },
                "partition_time": {
                    "date_column": "ds",
                    "date_format": "yyyymmdd",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        # yyyymmdd format requires CAST expression for DATE_TRUNC compatibility
        self.assertEqual(
            resolved.analysis_time_expr,
            "CAST(CONCAT(SUBSTR(CAST(ds AS VARCHAR), 1, 4), '-', SUBSTR(CAST(ds AS VARCHAR), 5, 2), '-', SUBSTR(CAST(ds AS VARCHAR), 7, 2)) AS DATE)",
        )
        self.assertEqual(resolved.analysis_time_format, "yyyymmdd")
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "ds >= '20260324' AND ds < '20260326'",
        )

    def test_resolver_day_only_pruning_uses_current_window_for_single_window_mode(self) -> None:
        request = normalize_metric_query_request(
            {
                "table": "iceberg.analytics.query_events",
                "metric": "queued_time",
                "time_scope": {
                    "mode": "single_window",
                    "grain": "day",
                    "current": {"start": "2026-03-25", "end": "2026-03-28"},
                },
            }
        )
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "resource_group"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "date_field")
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "log_date >= '20260325' AND log_date < '20260328'",
        )

    def test_resolver_builds_cross_day_hour_partition_pruning(self) -> None:
        request = normalize_metric_query_request(
            {
                "table": "iceberg.analytics.query_events",
                "metric": "queued_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "hour",
                    "current": {
                        "start": "2026-03-25T22:00:00",
                        "end": "2026-03-26T02:00:00",
                    },
                    "baseline": {
                        "start": "2026-03-24T22:00:00",
                        "end": "2026-03-25T02:00:00",
                    },
                },
            }
        )
        resolved = TimeAxisResolver(
            request=request,
            engine_type="trino",
            available_columns=["log_date", "log_hour"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "(log_date = '20260324' AND log_hour >= '22') OR "
            "(log_date > '20260324' AND log_date < '20260326') OR "
            "(log_date = '20260326' AND log_hour < '02')",
        )

    def test_resolver_builds_midnight_terminated_cross_day_hour_pruning(self) -> None:
        request = normalize_metric_query_request(
            {
                "table": "iceberg.analytics.query_events",
                "metric": "queued_time",
                "time_scope": {
                    "mode": "compare",
                    "grain": "hour",
                    "current": {
                        "start": "2026-03-25T22:00:00",
                        "end": "2026-03-26T00:00:00",
                    },
                    "baseline": {
                        "start": "2026-03-24T22:00:00",
                        "end": "2026-03-25T00:00:00",
                    },
                },
            }
        )
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["log_date", "log_hour"],
            source_time_capabilities={
                "analysis_time": {
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
            },
        ).resolve()
        self.assertEqual(
            resolved.partition_pruning_predicate,
            "(log_date = '20260324' AND log_hour >= '22') OR (log_date = '20260325')",
        )

    def test_resolver_heuristics_prefer_timestamp_when_mixed_columns_exist(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "log_date", "log_hour"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "event_time")
        self.assertIsNotNone(resolved.partition_pruning_predicate)

    def test_resolver_keeps_timestamp_only_axis_without_partition_pruning(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "platform"],
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "event_time")
        self.assertIsNone(resolved.partition_pruning_predicate)

    def test_resolver_prefers_metadata_over_timestamp_heuristic_candidates(self) -> None:
        request = self._compare_request()
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "created_at"],
            source_time_capabilities={
                "analysis_time": {
                    "timestamp_column": "created_at",
                },
            },
        ).resolve()
        self.assertEqual(resolved.analysis_time_kind, "timestamp")
        self.assertEqual(resolved.analysis_time_expr, "created_at")

    def test_resolver_request_override_beats_metadata(self) -> None:
        request = self._compare_request(time_axis={"analysis_time": {"column": "created_at"}})
        resolved = TimeAxisResolver(
            request=request,
            engine_type="duckdb",
            available_columns=["event_time", "created_at"],
            entity_time_capabilities={"analysis_time": {"timestamp_column": "event_time"}},
        ).resolve()
        self.assertEqual(resolved.analysis_time_expr, "created_at")

    def test_resolver_rejects_metadata_columns_not_present_in_known_schema(self) -> None:
        request = self._compare_request(grain="day")
        with self.assertRaisesRegex(ValueError, "unknown column 'event_time'"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["event_date"],
                source_time_capabilities={"analysis_time": {"timestamp_column": "event_time"}},
            ).resolve()

    def test_resolver_rejects_hour_grain_without_hour_capable_axis(self) -> None:
        request = self._compare_request()
        with self.assertRaisesRegex(ValueError, "hour-compatible time axis"):
            TimeAxisResolver(
                request=request,
                engine_type="duckdb",
                available_columns=["log_date"],
            ).resolve()


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
        from fastapi.testclient import TestClient

        client = TestClient(cls.app)
        try:
            source_id = client.post(
                "/sources",
                json={
                    "source_type": "duckdb",
                    "display_name": "TSU-05 Source",
                    "connection": {"path": str(db_path)},
                },
            ).json()["source_id"]
            client.post(f"/sources/{source_id}/sync")
            table_objects = {
                table["native_name"]: table
                for table in client.get(f"/sources/{source_id}/objects?type=table").json()
            }
            cls.watch_events_object_id = table_objects["watch_events"]["object_id"]
            cls.watch_events_fqn = str(table_objects["watch_events"]["fqn"])
        finally:
            client.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def setUp(self) -> None:
        client = self._client()
        try:
            suffix = str(id(self))
            entity = create_typed_entity(
                client,
                name=f"session_tsu02_{suffix}",
                display_name="Session",
                keys=["session_id"],
            )
            self.entity_id = entity["entity_contract_id"]
            publish_typed_entity(client, self.entity_id)
            entity_ref = f"entity.session_tsu02_{suffix}"

            metric = create_typed_metric(
                client,
                name=f"watch_time_tsu02_{suffix}",
                display_name="Watch Time",
                definition_sql="avg(play_duration_seconds)",
                dimensions=["platform", "event_date"],
                entity_ref=entity_ref,
            )
            metric_id = metric["metric_contract_id"]
            publish_typed_metric(client, metric_id)
            ensure_published_typed_dimension(self.service.metadata, dimension_name="cluster")

            entity_binding_resp = client.post(
                "/semantic/bindings",
                json={
                    "header": {
                        "binding_ref": f"binding.session_tsu02_{suffix}_entity",
                        "display_name": "TSU-02 Entity Binding",
                        "binding_scope": "entity",
                        "bound_object_ref": entity_ref,
                        "binding_contract_version": "binding.v1",
                    },
                    "interface_contract": {
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "source_object_ref": self.watch_events_object_id,
                                "carrier_kind": "table",
                                "carrier_locator": self.watch_events_fqn,
                                "binding_role": "primary",
                                "field_surfaces": [
                                    {
                                        "surface_ref": "field.event_date",
                                        "physical_name": "event_date",
                                    },
                                    {
                                        "surface_ref": "field.session_id",
                                        "physical_name": "session_id",
                                    },
                                    {
                                        "surface_ref": "field.cluster",
                                        "physical_name": "cluster",
                                    },
                                ],
                            }
                        ],
                        "field_bindings": [
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "identity_key",
                                    "target_key": "key.session_id",
                                },
                                "semantic_ref": "key.session_id",
                                "surface_ref": "field.session_id",
                            },
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "primary_time",
                                    "target_key": "time.event_date",
                                },
                                "semantic_ref": "time.event_date",
                                "surface_ref": "field.event_date",
                            },
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "stable_descriptor",
                                    "target_key": "dimension.cluster",
                                },
                                "semantic_ref": "dimension.cluster",
                                "surface_ref": "field.cluster",
                            },
                        ],
                    },
                },
            )
            self.assertEqual(entity_binding_resp.status_code, 200, entity_binding_resp.text)
            entity_binding_id = entity_binding_resp.json()["binding_id"]
            publish_entity_binding_resp = client.post(
                f"/semantic/bindings/{entity_binding_id}/publish"
            )
            self.assertEqual(
                publish_entity_binding_resp.status_code,
                200,
                publish_entity_binding_resp.text,
            )

            metric_binding_resp = client.post(
                "/semantic/bindings",
                json={
                    "header": {
                        "binding_ref": f"binding.watch_time_tsu02_{suffix}_primary",
                        "display_name": "TSU-02 Metric Binding",
                        "binding_scope": "metric",
                        "bound_object_ref": f"metric.watch_time_tsu02_{suffix}",
                        "binding_contract_version": "binding.v1",
                    },
                    "interface_contract": {
                        "imports": [
                            {
                                "import_key": "entity_bridge",
                                "binding_ref": f"binding.session_tsu02_{suffix}_entity",
                                "required_ref_prefixes": ["dimension."],
                            }
                        ],
                        "carrier_bindings": [
                            {
                                "binding_key": "primary",
                                "source_object_ref": self.watch_events_object_id,
                                "carrier_kind": "table",
                                "carrier_locator": self.watch_events_fqn,
                                "binding_role": "primary",
                                "field_surfaces": [
                                    {
                                        "surface_ref": "field.event_date",
                                        "physical_name": "event_date",
                                    },
                                    {
                                        "surface_ref": "field.value",
                                        "physical_name": "play_duration_seconds",
                                    },
                                    {
                                        "surface_ref": "field.platform",
                                        "physical_name": "platform",
                                    },
                                ],
                            }
                        ],
                        "field_bindings": [
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "primary_time",
                                    "target_key": "time.event_date",
                                },
                                "semantic_ref": "time.event_date",
                                "surface_ref": "field.event_date",
                            },
                            {
                                "carrier_binding_key": "primary",
                                "target": {
                                    "target_kind": "metric_input",
                                    "target_key": "count_target",
                                },
                                "semantic_ref": "metric_input.count_target",
                                "surface_ref": "field.value",
                            },
                        ],
                    },
                },
            )
            self.assertEqual(metric_binding_resp.status_code, 200, metric_binding_resp.text)
            metric_binding_id = metric_binding_resp.json()["binding_id"]
            publish_metric_binding_resp = client.post(
                f"/semantic/bindings/{metric_binding_id}/publish"
            )
            self.assertEqual(
                publish_metric_binding_resp.status_code,
                200,
                publish_metric_binding_resp.text,
            )

            self.metric_name = f"watch_time_tsu02_{suffix}"
            self.session_id = client.post("/sessions", json={"goal": "TSU-02"}).json()["session_id"]
        finally:
            client.close()

    def _client(self):
        from fastapi.testclient import TestClient

        return TestClient(self.app)

    def test_metric_query_service_uses_typed_execution_request(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: table_names[0]},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            captured["semantic_context"] = semantic_context or {}
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [
                {
                    "platform": "android",
                    "current_value": 10.0,
                    "baseline_value": 5.0,
                    "delta_pct": 100.0,
                    "current_sessions": 10,
                    "baseline_sessions": 8,
                }
            ]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            result = self.service._run_metric_query(
                self.session_id,
                {
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
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        self.assertEqual(result["step_type"], "metric_query")
        self.assertEqual(captured["params"]["metric"], self.metric_name)
        self.assertEqual(captured["params"]["table"], "analytics.watch_events")
        scoped_query = captured["params"]["scoped_query"]
        self.assertEqual(scoped_query["analysis_time_kind"], "date_field")
        # For date_field with unknown format, CAST is applied for DATE_TRUNC compatibility
        self.assertEqual(scoped_query["analysis_time_expr"], "CAST(event_date AS DATE)")
        self.assertIsNone(scoped_query["analysis_time_format"])
        self.assertEqual(scoped_query["current"]["start"], "2026-03-10")
        self.assertEqual(scoped_query["current"]["end"], "2026-03-17")
        self.assertEqual(scoped_query["baseline"]["start"], "2026-03-03")
        self.assertEqual(scoped_query["baseline"]["end"], "2026-03-10")
        self.assertIsNone(scoped_query["session_constraints_filter"])
        self.assertIsNone(scoped_query["session_raw_filter"])
        self.assertEqual(scoped_query["scope_constraints_filter"], "region = 'us-east'")
        self.assertEqual(scoped_query["scope_predicate_filter"], "device_type = 'phone'")

    def test_metric_query_scope_constraints_map_semantic_dimension_ref(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: table_names[0]},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [
                {
                    "platform": "android",
                    "current_value": 10.0,
                    "baseline_value": 5.0,
                    "delta_pct": 100.0,
                    "current_sessions": 10,
                    "baseline_sessions": 8,
                }
            ]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            self.service._run_metric_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                    "scope": {"constraints": {"dimension.cluster": "k8sbi-bi1"}},
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        scoped_query = captured["params"]["scoped_query"]
        self.assertEqual(scoped_query["scope_constraints_filter"], "cluster = 'k8sbi-bi1'")

    def test_metric_query_scope_constraints_reject_unmapped_semantic_dimension_ref(self) -> None:
        with self.assertRaisesRegex(ValueError, "not available in metric semantic scope"):
            self.service._run_metric_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                    "scope": {"constraints": {"dimension.region": "us-east"}},
                },
            )

    def test_metric_query_single_window_row_contract_accepts_current_only_fields(self) -> None:
        normalized = self.service._normalize_metric_rows(
            [
                {
                    "platform": "android",
                    "current_value": 10.0,
                    "current_sessions": 10,
                }
            ],
            mode="single_window",
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["current_value"], 10.0)
        self.assertEqual(normalized[0]["current_sessions"], 10)

    def test_metric_query_single_window_extractor_context_uses_current_only_contract(self) -> None:
        context = self.service._build_metric_query_extractor_context(
            mode="single_window",
            metric_name=self.metric_name,
            observation_type="metric_observation",
            dimensions=["platform"],
            quality_builder=self.service._metric_query_quality_builder("single_window"),
        )

        self.assertEqual(
            context["required_payload_keys"],
            ("current_value", "current_sessions"),
        )
        self.assertEqual(
            context["payload_fields"],
            {
                "current_value": "current_value",
                "current_sessions": "current_sessions",
            },
        )
        self.assertTrue(context["quality_builder"]({"current_sessions": 200})["sample_size_ok"])
        self.assertFalse(context["quality_builder"]({"current_sessions": 100})["sample_size_ok"])

    def test_metric_query_single_window_executes_without_delta_fields(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: table_names[0]},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [{"platform": "android", "current_value": 10.0, "current_sessions": 10}]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            result = self.service._run_metric_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        self.assertEqual(result["step_type"], "metric_query")
        self.assertNotIn("debug", result)
        self.assertIn("current window observation", result["summary"])
        self.assertNotIn("baseline", result["summary"].lower())
        self.assertEqual(captured["params"]["order"], "CURRENT_VALUE DESC")

    def test_metric_query_single_window_order_allows_current_sessions(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: table_names[0]},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["order"] = step.params["order"]
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [{"platform": "android", "current_value": 10.0, "current_sessions": 10}]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            self.service._run_metric_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "order": "current_sessions ASC",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        self.assertEqual(captured["order"], "CURRENT_SESSIONS ASC")

    def test_metric_query_single_window_rejects_delta_pct_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "single_window mode supports only current_value"):
            self.service._run_metric_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "order": "delta_pct DESC",
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                },
            )

    def test_aggregate_query_service_uses_typed_execution_request(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: table_names[0]},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            captured["semantic_context"] = semantic_context or {}
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [
                {
                    "platform": "android",
                    "query_count_current": 10,
                    "query_count_baseline": 5,
                    "query_count_delta_pct": 100.0,
                }
            ]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            result = self.service._run_aggregate_query(
                self.session_id,
                {
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
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        self.assertEqual(result["step_type"], "aggregate_query")
        self.assertEqual(captured["params"]["table"], "analytics.watch_events")
        self.assertEqual(
            captured["params"]["measures"], [{"expr": "COUNT(*)", "as": "query_count"}]
        )
        self.assertEqual(captured["params"]["group_by"], ["platform"])
        self.assertEqual(captured["params"]["order"], "query_count_delta_pct DESC")
        scoped_query = captured["params"]["scoped_query"]
        self.assertEqual(scoped_query["analysis_time_kind"], "date_field")
        # For date_field with unknown format, CAST is applied for DATE_TRUNC compatibility
        self.assertEqual(scoped_query["analysis_time_expr"], "CAST(event_date AS DATE)")
        self.assertIsNone(scoped_query["analysis_time_format"])
        self.assertEqual(scoped_query["current"]["start"], "2026-03-10")
        self.assertEqual(scoped_query["current"]["end"], "2026-03-17")
        self.assertEqual(scoped_query["baseline"]["start"], "2026-03-03")
        self.assertEqual(scoped_query["baseline"]["end"], "2026-03-10")
        self.assertIsNone(scoped_query["session_constraints_filter"])
        self.assertIsNone(scoped_query["session_raw_filter"])
        self.assertEqual(scoped_query["scope_constraints_filter"], "region = 'us-east'")
        self.assertEqual(scoped_query["scope_predicate_filter"], "device_type = 'phone'")

    def test_aggregate_query_scope_constraints_reject_semantic_dimension_ref(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a semantic metric scope"):
            self.service._run_aggregate_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "group_by": ["platform"],
                    "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                    "scope": {"constraints": {"dimension.cluster": "k8sbi-bi1"}},
                    "time_axis": {"analysis_time": {"column": "event_date"}},
                },
            )

    def test_aggregate_query_service_passes_full_fqn_to_routing(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine

        def fake_resolve_engine(table_names):
            captured["table_names"] = list(table_names)
            return (_FakeEngine(), "duckdb", {table_names[0]: "analytics.watch_events"})

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [{"platform": "android", "query_count": 10}]

        self.service._resolve_engine = fake_resolve_engine
        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            self.service._run_aggregate_query(
                self.session_id,
                {
                    "table": "duckdb.analytics.watch_events",
                    "group_by": ["platform"],
                    "measures": [{"expr": "COUNT(*)", "as": "query_count"}],
                    "time_scope": {
                        "mode": "single_window",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                    },
                    "time_axis": {"analysis_time": {"column": "event_date"}},
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        self.assertEqual(captured["table_names"], ["duckdb.analytics.watch_events"])
        self.assertEqual(captured["params"]["table"], "analytics.watch_events")

    def test_metric_query_service_passes_mixed_layout_pruning_to_trino_scoped_query(self) -> None:
        captured: dict[str, object] = {}
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        original_metadata_load = self.service.time_axis_metadata_provider.load_for_windowed_query
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "trino",
            {table_names[0]: f"iceberg.analytics.{table_names[0]}"},
        )
        self.service.time_axis_metadata_provider.load_for_windowed_query = lambda **kwargs: (
            TimeAxisMetadataContext(
                available_columns=["event_time", "log_date", "log_hour"],
                source_time_capabilities={
                    "analysis_time": {
                        "timestamp_column": "event_time",
                        "fallback_date_column": "log_date",
                        "fallback_hour_column": "log_hour",
                    },
                    "partition_time": {
                        "date_column": "log_date",
                        "date_format": "yyyymmdd",
                        "hour_column": "log_hour",
                        "hour_format": "hh",
                    },
                },
            )
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            captured["params"] = dict(step.params)
            captured["engine_type"] = engine_type
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [
                {
                    "platform": "android",
                    "current_value": 10.0,
                    "baseline_value": 5.0,
                    "delta_pct": 100.0,
                    "current_sessions": 10,
                    "baseline_sessions": 8,
                }
            ]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            self.service._run_metric_query(
                self.session_id,
                {
                    "table": "iceberg.analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "time_scope": {
                        "mode": "compare",
                        "grain": "hour",
                        "current": {"start": "2026-03-25T10:00:00", "end": "2026-03-25T14:00:00"},
                        "baseline": {"start": "2026-03-25T06:00:00", "end": "2026-03-25T10:00:00"},
                    },
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine
            self.service.time_axis_metadata_provider.load_for_windowed_query = (
                original_metadata_load
            )

        self.assertEqual(captured["engine_type"], "trino")
        scoped_query = captured["params"]["scoped_query"]
        self.assertEqual(scoped_query["analysis_time_kind"], "timestamp")
        self.assertEqual(scoped_query["analysis_time_expr"], "event_time")
        self.assertEqual(
            scoped_query["partition_pruning_predicate"],
            "log_date = '20260325' AND log_hour >= '06' AND log_hour < '14'",
        )

    def test_metric_query_rejects_rows_missing_required_comparison_columns(self) -> None:
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: f"analytics.{table_names[0]}"},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = [
                {
                    "platform": "android",
                    "current_value": 10.0,
                    "baseline_value": 5.0,
                    "delta_pct": 100.0,
                }
            ]

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                self.service._run_metric_query(
                    self.session_id,
                    {
                        "table": "analytics.watch_events",
                        "metric": self.metric_name,
                        "dimensions": ["platform"],
                        "time_scope": {
                            "mode": "compare",
                            "grain": "day",
                            "current": {"start": "2026-03-10", "end": "2026-03-17"},
                            "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                        },
                    },
                )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

    def test_metric_query_summary_uses_window_wording_not_period_wording(self) -> None:
        original_compile = self.service._compile_step_with_feedback
        original_execute = service_module.execute_compiled
        original_resolve_engine = self.service._resolve_engine
        self.service._resolve_engine = lambda table_names: (
            _FakeEngine(),
            "duckdb",
            {table_names[0]: f"analytics.{table_names[0]}"},
        )

        def fake_compile(step, *, engine_type, semantic_context=None):
            return CompiledQuery(sql="SELECT 1", params=[])

        class _Result:
            rows = []

        self.service._compile_step_with_feedback = fake_compile
        service_module.execute_compiled = lambda engine, compiled: _Result()
        try:
            result = self.service._run_metric_query(
                self.session_id,
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "dimensions": ["platform"],
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                },
            )
        finally:
            self.service._compile_step_with_feedback = original_compile
            service_module.execute_compiled = original_execute
            self.service._resolve_engine = original_resolve_engine

        self.assertIn("current_window", result["summary"])
        self.assertIn("baseline_window", result["summary"])
        self.assertNotIn("period", result["summary"].lower())
        self.assertIn("current_window", result["debug"])
        self.assertIn("baseline_window", result["debug"])

    def test_service_resolver_prefers_entity_time_capabilities_over_source_time_capabilities(
        self,
    ) -> None:
        client = self._client()
        try:
            table_row = self.service.metadata.query_one(
                "SELECT object_id, properties_json FROM source_objects WHERE object_type = 'table' AND native_name = ?",
                ["watch_events"],
            )
            self.assertIsNotNone(table_row)
            table_props = json.loads(table_row["properties_json"] or "{}")
            table_props["time_capabilities"] = {
                "analysis_time": {"fallback_date_column": "platform"},
            }
            self.service.metadata.execute(
                "UPDATE source_objects SET properties_json = ? WHERE object_id = ?",
                [json.dumps(table_props), table_row["object_id"]],
            )

            patch_typed_entity_properties(
                client,
                self.entity_id,
                {"time_capabilities": {"analysis_time": {"fallback_date_column": "event_date"}}},
            )

            request = normalize_metric_query_request(
                {
                    "table": "analytics.watch_events",
                    "metric": self.metric_name,
                    "time_scope": {
                        "mode": "compare",
                        "grain": "day",
                        "current": {"start": "2026-03-10", "end": "2026-03-17"},
                        "baseline": {"start": "2026-03-03", "end": "2026-03-10"},
                    },
                }
            )
            self.service._resolve_windowed_query_time_axis(
                request,
                engine_type="duckdb",
                metric_name=self.metric_name,
                fallback_columns=["event_date", "platform"],
            )
        finally:
            client.close()

        self.assertEqual(request.resolved_time_axis.analysis_time_kind, "date_field")
        # For date_field with unknown format, CAST is applied for DATE_TRUNC compatibility
        self.assertEqual(request.resolved_time_axis.analysis_time_expr, "CAST(event_date AS DATE)")
        self.assertIsNone(request.resolved_time_axis.analysis_time_format)
        self.assertEqual(
            request.resolved_time_axis.partition_pruning_predicate,
            "event_date >= '2026-03-03' AND event_date < '2026-03-17'",
        )
