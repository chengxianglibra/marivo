from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.semantic_test_helpers import (
    create_legacy_entity,
    publish_legacy_entity,
)
from tests.shared_fixtures import get_seeded_duckdb_path


class SemanticEntityRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_semantic_routes.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_entity_routes_use_typed_contract(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.user",
                    "display_name": "User",
                    "description": "A registered user",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.user_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        entity = resp.json()
        entity_id = entity["entity_contract_id"]
        self.assertEqual(entity["header"]["entity_ref"], "entity.user")
        self.assertEqual(entity["status"], "draft")
        self.assertEqual(entity["revision"], 1)

        resp = self.client.get(f"/semantic/entities/{entity_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["entity_ref"], "entity.user")

        resp = self.client.put(
            f"/semantic/entities/{entity_id}",
            json={"description": "Updated description"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["description"], "Updated description")

        resp = self.client.post(f"/semantic/entities/{entity_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")
        self.assertEqual(resp.json()["revision"], 2)

        resp = self.client.get("/semantic/entities?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

    def test_entity_routes_reject_legacy_contract_and_missing_object(self) -> None:
        resp = self.client.post(
            "/semantic/entities",
            json={"name": "user", "display_name": "User", "keys": ["user_id"]},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

        resp = self.client.get("/semantic/entities/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_entity_properties_patch_route_is_removed(self) -> None:
        entity = self.client.post(
            "/semantic/entities",
            json={
                "header": {
                    "entity_ref": "entity.account",
                    "display_name": "Account",
                    "entity_contract_version": "entity.v4",
                },
                "interface_contract": {
                    "identity": {
                        "key_refs": ["key.account_id"],
                        "uniqueness_scope": "global",
                        "id_stability": "stable",
                    }
                },
            },
        ).json()
        resp = self.client.patch(
            f"/semantic/entities/{entity['entity_contract_id']}/properties",
            json={"properties": {"unit": "seconds"}},
        )
        self.assertEqual(resp.status_code, 404)


class SemanticMetricRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(cls.temp_dir.name) / "test_metric_routes.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.client = TestClient(create_app(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def test_metric_routes_use_typed_contract(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "header": {
                    "metric_ref": "metric.dau",
                    "display_name": "DAU",
                    "description": "Daily active users",
                    "metric_family": "count_metric",
                    "observed_entity_ref": "entity.user",
                    "observation_grain_ref": "grain.user",
                    "sample_kind": "numeric",
                    "value_semantics": "count",
                    "additivity": "additive",
                    "metric_contract_version": "metric.v1",
                },
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "users",
                        "semantics": "distinct users",
                        "aggregation": "count_distinct",
                    },
                },
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        metric = resp.json()
        metric_id = metric["metric_contract_id"]
        self.assertEqual(metric["header"]["metric_ref"], "metric.dau")
        self.assertEqual(metric["status"], "draft")

        resp = self.client.get(f"/semantic/metrics/{metric_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["header"]["metric_ref"], "metric.dau")

        resp = self.client.put(
            f"/semantic/metrics/{metric_id}",
            json={
                "payload": {
                    "metric_family": "count_metric",
                    "count_target": {
                        "name": "active_users",
                        "semantics": "active users",
                        "aggregation": "count_distinct",
                    },
                }
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["payload"]["count_target"]["name"], "active_users")

        resp = self.client.post(f"/semantic/metrics/{metric_id}/publish")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "published")

        resp = self.client.get("/semantic/metrics?status=published")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(all(item["status"] == "published" for item in resp.json()["items"]))

    def test_metric_routes_reject_legacy_contract_and_missing_object(self) -> None:
        resp = self.client.post(
            "/semantic/metrics",
            json={
                "name": "watch_time",
                "display_name": "Watch Time",
                "definition_sql": "avg(play_duration_seconds)",
                "dimensions": ["platform"],
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIsInstance(resp.json()["detail"], list)

        resp = self.client.get("/semantic/metrics/nonexistent")
        self.assertEqual(resp.status_code, 404)


class SemanticMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_mappings.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))
        resp = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Mapping Test Source",
                "connection": {"path": str(cls.db_path)},
            },
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
        entity = create_legacy_entity(
            self.client,
            name="map_user",
            display_name="User",
            keys=["user_id"],
        )
        entity_id = entity["entity_id"]
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
        entity = create_legacy_entity(
            self.client,
            name="del_map_user",
            display_name="Del Map User",
            keys=["user_id"],
        )
        entity_id = entity["entity_id"]
        publish_legacy_entity(self.client, entity_id)
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
