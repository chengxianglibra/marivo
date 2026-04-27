from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.time_axis_metadata import (
    PHASE1_TIMEZONE_NOTE,
    PHASE1_TIMEZONE_STRATEGY,
    normalize_time_capabilities,
)
from tests.semantic_test_helpers import (
    create_typed_entity,
    create_typed_metric,
    ensure_published_typed_time,
    publish_typed_entity,
    publish_typed_metric,
)
from tests.shared_fixtures import get_seeded_duckdb_path


class TimeCapabilitiesSchemaTests(unittest.TestCase):
    def test_normalize_time_capabilities_accepts_minimal_schema(self) -> None:
        normalized = normalize_time_capabilities(
            {
                "analysis_time": {
                    "timestamp_column": "event_time",
                    "timestamp_format": "native",
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
                "default_compare_grain": "day",
            }
        )
        self.assertEqual(
            normalized,
            {
                "analysis_time": {
                    "timestamp_column": "event_time",
                    "timestamp_format": "native",
                    "fallback_date_column": "log_date",
                    "fallback_hour_column": "log_hour",
                },
                "partition_time": {
                    "date_column": "log_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "log_hour",
                    "hour_format": "hh",
                },
                "default_compare_grain": "day",
            },
        )

    def test_normalize_time_capabilities_rejects_partition_hour_without_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "hour_column requires date_column"):
            normalize_time_capabilities(
                {
                    "partition_time": {
                        "hour_column": "log_hour",
                    },
                }
            )

    def test_normalize_time_capabilities_rejects_analysis_hour_without_date(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "fallback_hour_column requires fallback_date_column"
        ):
            normalize_time_capabilities(
                {
                    "analysis_time": {
                        "fallback_hour_column": "log_hour",
                    },
                }
            )

    def test_normalize_time_capabilities_accepts_custom_timestamp_format(self) -> None:
        """Custom strftime format strings are accepted."""
        normalized = normalize_time_capabilities(
            {
                "analysis_time": {
                    "timestamp_column": "create_time",
                    "timestamp_format": "%Y%m%d %H:%M:%S",
                }
            }
        )
        self.assertEqual(
            normalized,
            {
                "analysis_time": {
                    "timestamp_column": "create_time",
                    "timestamp_format": "%Y%m%d %H:%M:%S",
                }
            },
        )


class TimeAxisMetadataProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmp.name) / "tsu11.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path)
        cls.client = TestClient(cls.app)
        cls.service = cls.app.state.service

        source_id = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "TSU-11 Source",
                "authority": {
                    "catalog_system": "duckdb",
                    "connection": {"path": str(db_path)},
                    "synthetic_catalog": "main",
                },
            },
        ).json()["source_id"]
        cls.client.post(
            f"/sources/{source_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        cls.client.post(f"/sources/{source_id}/sync")

        entity = create_typed_entity(
            cls.client,
            name="session_tsu11",
            display_name="Session",
            keys=["session_id"],
        )
        cls.entity_id = entity["entity_contract_id"]
        publish_typed_entity(cls.client, cls.entity_id)
        cls.entity_ref = entity["header"]["entity_ref"]

        metric = create_typed_metric(
            cls.client,
            name="watch_time_tsu11",
            display_name="Watch Time",
            definition_sql="avg(play_duration_seconds)",
            dimensions=["platform", "event_date"],
            entity_ref="entity.session_tsu11",
        )
        cls.metric_name = metric["header"]["metric_ref"].removeprefix("metric.")
        cls.metric_ref = metric["header"]["metric_ref"]
        cls.primary_time_ref = metric["header"]["primary_time_ref"]
        publish_typed_metric(cls.client, metric["metric_contract_id"])
        ensure_published_typed_time(cls.service.metadata, time_ref="time.partition_time")

        table_row = cls.service.metadata.query_one(
            """
            SELECT object_id, fqn
            FROM source_objects
            WHERE object_type = 'table' AND native_name = ?
            """,
            ["watch_events"],
        )
        assert table_row is not None
        cls.watch_events_object_id = str(table_row["object_id"])
        cls.watch_events_fqn = str(table_row["fqn"])

        entity_binding_resp = cls.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.session_tsu11_entity",
                    "display_name": "Session Entity Binding",
                    "binding_scope": "entity",
                    "bound_object_ref": cls.entity_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": cls.watch_events_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": cls.watch_events_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.session_id", "physical_name": "session_id"},
                                {"surface_ref": "field.event_date", "physical_name": "event_date"},
                                {"surface_ref": "field.log_date", "physical_name": "event_date"},
                            ],
                            "time_surfaces": [
                                {
                                    "surface_ref": "time_surface.event_date",
                                    "physical_name": "event_date",
                                }
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
                        }
                    ],
                    "time_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": cls.primary_time_ref,
                            },
                            "semantic_ref": cls.primary_time_ref,
                            "resolution_kind": "date_column",
                            "date_surface_ref": "time_surface.event_date",
                        }
                    ],
                },
            },
        )
        assert entity_binding_resp.status_code == 200, entity_binding_resp.text
        cls.client.post(f"/semantic/bindings/{entity_binding_resp.json()['binding_id']}/publish")

        metric_binding_resp = cls.client.post(
            "/semantic/bindings",
            json={
                "header": {
                    "binding_ref": "binding.watch_time_tsu11_metric",
                    "display_name": "Watch Time Metric Binding",
                    "binding_scope": "metric",
                    "bound_object_ref": cls.metric_ref,
                    "binding_contract_version": "binding.v1",
                },
                "interface_contract": {
                    "carrier_bindings": [
                        {
                            "binding_key": "primary",
                            "source_object_ref": cls.watch_events_object_id,
                            "carrier_kind": "table",
                            "carrier_locator": cls.watch_events_fqn,
                            "binding_role": "primary",
                            "field_surfaces": [
                                {"surface_ref": "field.event_date", "physical_name": "event_date"},
                                {"surface_ref": "field.log_date", "physical_name": "event_date"},
                                {"surface_ref": "field.log_hour", "physical_name": "event_hour"},
                                {
                                    "surface_ref": "field.value",
                                    "physical_name": "play_duration_seconds",
                                },
                            ],
                            "time_surfaces": [
                                {
                                    "surface_ref": "time_surface.log_date",
                                    "physical_name": "event_date",
                                },
                                {
                                    "surface_ref": "time_surface.log_hour",
                                    "physical_name": "event_hour",
                                },
                            ],
                        }
                    ],
                    "field_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {"target_kind": "metric_input", "target_key": "count_target"},
                            "semantic_ref": "metric_input.count_target",
                            "surface_ref": "field.value",
                        }
                    ],
                    "time_bindings": [
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": cls.primary_time_ref,
                            },
                            "semantic_ref": cls.primary_time_ref,
                            "resolution_kind": "date_hour_columns",
                            "date_surface_ref": "time_surface.log_date",
                            "date_format": "yyyymmdd",
                            "hour_surface_ref": "time_surface.log_hour",
                            "hour_format": "hh",
                            "timezone_strategy": "session_consistent_naive",
                        },
                        {
                            "carrier_binding_key": "primary",
                            "target": {
                                "target_kind": "primary_time",
                                "target_key": "time.partition_time",
                            },
                            "semantic_ref": "time.partition_time",
                            "resolution_kind": "date_hour_columns",
                            "date_surface_ref": "time_surface.log_date",
                            "date_format": "yyyymmdd",
                            "hour_surface_ref": "time_surface.log_hour",
                            "hour_format": "hh",
                        },
                    ],
                },
            },
        )
        assert metric_binding_resp.status_code == 200, metric_binding_resp.text
        cls.metric_binding_id = metric_binding_resp.json()["binding_id"]
        publish_resp = cls.client.post(f"/semantic/bindings/{cls.metric_binding_id}/publish")
        assert publish_resp.status_code == 200, publish_resp.text

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_provider_reads_time_caps_from_published_metric_binding(self) -> None:
        context = self.service.time_axis_metadata_provider.load_for_windowed_query(
            table_name="analytics.watch_events",
            metric_name=self.metric_name,
        )

        self.assertEqual(
            context.entity_time_capabilities,
            {
                "analysis_time": {
                    "fallback_date_column": "event_date",
                    "fallback_hour_column": "event_hour",
                },
                "partition_time": {
                    "date_column": "event_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "event_hour",
                    "hour_format": "hh",
                },
            },
        )
        self.assertEqual(
            context.source_time_capabilities,
            {
                "partition_time": {
                    "date_column": "event_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "event_hour",
                    "hour_format": "hh",
                },
            },
        )
        self.assertIn("event_date", context.available_columns)
        self.assertIn("platform", context.available_columns)
        self.assertEqual(context.timezone_strategy, PHASE1_TIMEZONE_STRATEGY)
        self.assertEqual(context.timezone_note, PHASE1_TIMEZONE_NOTE)
        self.assertTrue(context.has_time_binding)

    def test_provider_ignores_legacy_time_capabilities_when_binding_exists(self) -> None:
        table_row = self.service.metadata.query_one(
            "SELECT object_id, properties_json FROM source_objects WHERE object_type = 'table' AND native_name = ?",
            ["watch_events"],
        )
        self.assertIsNotNone(table_row)
        props = json.loads(table_row["properties_json"] or "{}")
        props["time_capabilities"] = {
            "analysis_time": {"fallback_date_column": "platform"},
            "partition_time": {"date_column": "platform"},
        }
        self.service.metadata.execute(
            "UPDATE source_objects SET properties_json = ? WHERE object_id = ?",
            [json.dumps(props), table_row["object_id"]],
        )

        context = self.service.time_axis_metadata_provider.load_for_windowed_query(
            table_name="analytics.watch_events",
            metric_name=self.metric_name,
        )
        self.assertEqual(
            context.entity_time_capabilities,
            {
                "analysis_time": {
                    "fallback_date_column": "event_date",
                    "fallback_hour_column": "event_hour",
                },
                "partition_time": {
                    "date_column": "event_date",
                    "date_format": "yyyymmdd",
                    "hour_column": "event_hour",
                    "hour_format": "hh",
                },
            },
        )

    def test_provider_requires_binding_for_windowed_query(self) -> None:
        self.service.metadata.execute(
            "UPDATE typed_bindings SET status = 'draft' WHERE binding_id = ?",
            [self.metric_binding_id],
        )
        with self.assertRaisesRegex(
            ValueError, "No published time binding matched analytics.watch_events"
        ):
            self.service.time_axis_metadata_provider.load_for_windowed_query(
                table_name="analytics.watch_events",
                metric_name=self.metric_name,
            )
        self.service.metadata.execute(
            "UPDATE typed_bindings SET status = 'published' WHERE binding_id = ?",
            [self.metric_binding_id],
        )
