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
from tests.shared_fixtures import get_seeded_duckdb_path


class TimeCapabilitiesSchemaTests(unittest.TestCase):
    def test_normalize_time_capabilities_accepts_minimal_schema(self) -> None:
        normalized = normalize_time_capabilities(
            {
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
                "default_compare_grain": "day",
            }
        )
        self.assertEqual(
            normalized,
            {
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
                "connection": {"path": str(db_path)},
            },
        ).json()["source_id"]
        cls.client.post(f"/sources/{source_id}/sync")

        entity_resp = cls.client.post(
            "/semantic/entities",
            json={
                "name": "session_tsu11",
                "display_name": "Session",
                "keys": ["session_id"],
                "properties": {
                    "time_capabilities": {
                        "analysis_time": {"fallback_date_column": "event_date"},
                        "default_compare_grain": "day",
                    },
                },
            },
        )
        cls.entity_id = entity_resp.json()["entity_id"]
        cls.client.post(f"/semantic/entities/{cls.entity_id}/publish")

        metric_resp = cls.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time_tsu11",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "event_date"],
                "entity_id": cls.entity_id,
            },
        )
        cls.metric_name = metric_resp.json()["name"]
        cls.client.post(f"/semantic/metrics/{metric_resp.json()['metric_id']}/publish")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_provider_reads_entity_and_source_time_capabilities(self) -> None:
        table_row = self.service.metadata.query_one(
            "SELECT object_id, properties_json FROM source_objects WHERE object_type = 'table' AND native_name = ?",
            ["watch_events"],
        )
        self.assertIsNotNone(table_row)
        props = json.loads(table_row["properties_json"] or "{}")
        props["time_capabilities"] = {
            "analysis_time": {
                "fallback_date_column": "event_date",
            },
            "partition_time": {
                "date_column": "event_date",
            },
            "default_compare_grain": "day",
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
                "analysis_time": {"fallback_date_column": "event_date"},
                "default_compare_grain": "day",
            },
        )
        self.assertEqual(
            context.source_time_capabilities,
            {
                "analysis_time": {
                    "fallback_date_column": "event_date",
                },
                "partition_time": {
                    "date_column": "event_date",
                },
                "default_compare_grain": "day",
            },
        )
        self.assertIn("event_date", context.available_columns)
        self.assertIn("platform", context.available_columns)
        self.assertEqual(context.timezone_strategy, PHASE1_TIMEZONE_STRATEGY)
        self.assertEqual(context.timezone_note, PHASE1_TIMEZONE_NOTE)

    def test_provider_rejects_invalid_entity_time_capabilities(self) -> None:
        patch_resp = self.client.patch(
            f"/semantic/entities/{self.entity_id}/properties",
            json={
                "properties": {"time_capabilities": {"partition_time": {"hour_column": "log_hour"}}}
            },
        )
        self.assertEqual(patch_resp.status_code, 200)

        with self.assertRaisesRegex(
            ValueError,
            "semantic entity 'session_tsu11' time_capabilities.partition_time.hour_column requires date_column",
        ):
            self.service.time_axis_metadata_provider.load_for_windowed_query(
                table_name="analytics.watch_events",
                metric_name=self.metric_name,
            )

        repair_resp = self.client.patch(
            f"/semantic/entities/{self.entity_id}/properties",
            json={
                "properties": {
                    "time_capabilities": {
                        "analysis_time": {"fallback_date_column": "event_date"},
                        "default_compare_grain": "day",
                    }
                }
            },
        )
        self.assertEqual(repair_resp.status_code, 200)
