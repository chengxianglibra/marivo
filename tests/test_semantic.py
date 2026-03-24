from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticEntityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_semantic.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_create_and_get_entity(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "user", "display_name": "User", "keys": ["user_id"]},
        )
        self.assertEqual(resp.status_code, 200)
        entity = resp.json()
        self.assertEqual(entity["name"], "user")
        self.assertEqual(entity["status"], "draft")
        self.assertEqual(entity["revision"], 1)

        resp = self.client.get(f"/semantic/entities/{entity['entity_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "user")

    def test_update_entity(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "video_session", "display_name": "Video Session", "keys": ["session_id"]},
        )
        entity_id = resp.json()["entity_id"]

        resp = self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"description": "A video playback session"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["description"], "A video playback session")

    def test_entity_execution_semantics_round_trip(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "name": "playback_session",
                "display_name": "Playback Session",
                "keys": ["session_id"],
                "level": "session",
                "join_constraints": {"requires": ["user_id"]},
                "upstream_dependencies": ["user"],
                "lineage": ["analytics.watch_events"],
                "quality_expectations": {"freshness_hours": 24},
            },
        )
        self.assertEqual(resp.status_code, 200)
        entity = resp.json()
        self.assertEqual(entity["level"], "session")
        self.assertEqual(entity["join_constraints"], {"requires": ["user_id"]})
        self.assertEqual(entity["upstream_dependencies"], ["user"])
        self.assertEqual(entity["lineage"], ["analytics.watch_events"])
        self.assertEqual(entity["quality_expectations"], {"freshness_hours": 24})
        self.assertEqual(entity["properties"], {})

    def test_publish_entity(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "publish_test", "display_name": "Publish Test", "keys": ["id"]},
        )
        entity_id = resp.json()["entity_id"]

        resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["revision"], 2)

    def test_list_entities_filter_by_status(self) -> None:
        self.client.post(
            "/semantic/entities",
            json={"name": "draft_ent", "display_name": "Draft", "keys": ["id"]},
        )
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "pub_ent", "display_name": "Pub", "keys": ["id"]},
        )
        self.client.post(f"/semantic/entities/{resp.json()['entity_id']}/publish")

        resp = self.client.get("/semantic/entities?status=published")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(e["status"] == "published" for e in resp.json()))

    def test_entity_404(self) -> None:
        resp = self.client.get("/semantic/entities/nonexistent")
        self.assertEqual(resp.status_code, 404)


class SemanticMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_metrics.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_create_and_publish_metric(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version"],
            },
        )
        self.assertEqual(resp.status_code, 200)
        metric = resp.json()
        self.assertEqual(metric["name"], "watch_time")
        self.assertEqual(metric["status"], "draft")

        resp = self.client.post(f"/semantic/metrics/{metric['metric_id']}/publish")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["revision"], 2)

    def test_update_metric(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "name": "first_frame",
                "display_name": "First Frame Time",
                "definition_sql": "avg(first_frame_time_ms)",
                "dimensions": ["platform"],
            },
        )
        metric_id = resp.json()["metric_id"]

        resp = self.client.put(
            f"/semantic/metrics/{metric_id}",
            json={"dimensions": ["platform", "network_type"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["dimensions"], ["platform", "network_type"])

    def test_metric_execution_semantics_round_trip(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "name": "engaged_watch_time",
                "display_name": "Engaged Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform", "app_version", "network_type"],
                "grain": "session",
                "measure_type": "average",
                "allowed_dimensions": ["platform", "network_type"],
                "lineage": ["analytics.watch_events.play_duration_seconds"],
                "quality_expectations": {"min_group_size": 100},
            },
        )
        self.assertEqual(resp.status_code, 200)
        metric = resp.json()
        self.assertEqual(metric["grain"], "session")
        self.assertEqual(metric["measure_type"], "average")
        self.assertEqual(metric["allowed_dimensions"], ["platform", "network_type"])
        self.assertEqual(metric["lineage"], ["analytics.watch_events.play_duration_seconds"])
        self.assertEqual(metric["quality_expectations"], {"min_group_size": 100})
        self.assertEqual(metric["properties"], {})

    def test_list_metrics(self) -> None:
        resp = self.client.get("/semantic/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_metric_404(self) -> None:
        resp = self.client.get("/semantic/metrics/nonexistent")
        self.assertEqual(resp.status_code, 404)


class SemanticMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_mappings.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        # Register and sync a source to get object_ids
        resp = cls.client.post(
            "/sources",
            json={"source_type": "duckdb", "display_name": "Mapping Test Source", "connection": {"path": str(cls.db_path)}},
        )
        cls.source_id = resp.json()["source_id"]
        cls.client.post(f"/sources/{cls.source_id}/sync")
        resp = cls.client.get(f"/sources/{cls.source_id}/objects?type=table")
        cls.table_objects = resp.json()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_create_and_list_mappings(self) -> None:
        # Create an entity first
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "map_user", "display_name": "User", "keys": ["user_id"]},
        )
        entity_id = resp.json()["entity_id"]
        object_id = self.table_objects[0]["object_id"]

        resp = self.client.post(
            "/semantic/mappings",
            json={
                "semantic_type": "entity",
                "semantic_id": entity_id,
                "object_id": object_id,
                "mapping_type": "primary_source",
            },
        )
        self.assertEqual(resp.status_code, 200)
        mapping = resp.json()
        self.assertEqual(mapping["semantic_type"], "entity")

        resp = self.client.get(f"/semantic/mappings?semantic_id={entity_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

    def test_delete_mapping(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "del_map_user", "display_name": "Del Map User", "keys": ["user_id"]},
        )
        entity_id = resp.json()["entity_id"]
        object_id = self.table_objects[0]["object_id"]

        resp = self.client.post(
            "/semantic/mappings",
            json={
                "semantic_type": "entity",
                "semantic_id": entity_id,
                "object_id": object_id,
                "mapping_type": "primary_source",
            },
        )
        mapping_id = resp.json()["mapping_id"]

        resp = self.client.delete(f"/semantic/mappings/{mapping_id}")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/semantic/mappings?semantic_id={entity_id}")
        self.assertEqual(len(resp.json()), 0)


class EntityPropertiesPatchTests(unittest.TestCase):
    """G-5d: PATCH /semantic/entities/{id}/properties endpoint."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_patch.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_and_publish_entity(self, name: str = "video_event") -> str:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": name, "display_name": "Video Event", "keys": ["vid_id"]},
        )
        entity_id = resp.json()["entity_id"]
        self.client.post(f"/semantic/entities/{entity_id}/publish")
        return entity_id

    def test_patch_unit_on_published_entity(self) -> None:
        entity_id = self._create_and_publish_entity("video_event_patch_unit")
        resp = self.client.patch(
            f"/semantic/entities/{entity_id}/properties",
            json={"properties": {"unit": "milliseconds"}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["properties"].get("unit"), "milliseconds")
        self.assertEqual(data["status"], "published")

    def test_patch_bumps_revision(self) -> None:
        entity_id = self._create_and_publish_entity("video_event_bump_rev")
        before = self.client.get(f"/semantic/entities/{entity_id}").json()
        self.client.patch(
            f"/semantic/entities/{entity_id}/properties",
            json={"properties": {"unit": "seconds"}},
        )
        after = self.client.get(f"/semantic/entities/{entity_id}").json()
        self.assertGreater(after["revision"], before["revision"])

    def test_patch_merges_existing_properties(self) -> None:
        entity_id = self._create_and_publish_entity("video_event_merge")
        # Set initial properties via PUT
        self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"properties": {"unit": "seconds", "category": "streaming"}},
        )
        # Patch only unit
        resp = self.client.patch(
            f"/semantic/entities/{entity_id}/properties",
            json={"properties": {"unit": "milliseconds"}},
        )
        self.assertEqual(resp.status_code, 200)
        props = resp.json()["properties"]
        self.assertEqual(props["unit"], "milliseconds")
        self.assertEqual(props["category"], "streaming")  # preserved

    def test_patch_fields_deep_merge_preserves_other_columns(self) -> None:
        """Patching fields.col_a.unit must not wipe fields.col_b."""
        entity_id = self._create_and_publish_entity("video_event_fields_merge")
        # Set initial field-level properties
        self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"properties": {"fields": {"col_a": {"unit": "bytes"}, "col_b": {"unit": "seconds"}}}},
        )
        # Patch only col_a
        resp = self.client.patch(
            f"/semantic/entities/{entity_id}/properties",
            json={"properties": {"fields": {"col_a": {"unit": "megabytes"}}}},
        )
        self.assertEqual(resp.status_code, 200)
        props = resp.json()["properties"]
        self.assertEqual(props["fields"]["col_a"]["unit"], "megabytes")  # updated
        self.assertEqual(props["fields"]["col_b"]["unit"], "seconds")    # preserved

    def test_patch_draft_entity_returns_422(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "draft_entity_patch", "display_name": "Draft", "keys": ["id"]},
        )
        entity_id = resp.json()["entity_id"]
        resp = self.client.patch(
            f"/semantic/entities/{entity_id}/properties",
            json={"properties": {"unit": "bytes"}},
        )
        self.assertEqual(resp.status_code, 422)

    def test_patch_nonexistent_entity_returns_404(self) -> None:
        resp = self.client.patch(
            "/semantic/entities/ent_nonexistent/properties",
            json={"properties": {"unit": "bytes"}},
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_empty_properties_returns_422(self) -> None:
        entity_id = self._create_and_publish_entity("video_event_empty_patch")
        resp = self.client.patch(
            f"/semantic/entities/{entity_id}/properties",
            json={"properties": {}},
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
