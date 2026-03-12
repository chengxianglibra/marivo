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
            json={"source_type": "local", "display_name": "Mapping Test Source", "connection": {"path": str(cls.db_path)}},
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


if __name__ == "__main__":
    unittest.main()
