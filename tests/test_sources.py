from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.semantic_test_helpers import create_typed_metric, create_typed_metric_binding
from tests.shared_fixtures import get_seeded_duckdb_path


def build_duckdb_source_payload(path: str, display_name: str, mode: str = "selected") -> dict:
    return {
        "source_type": "duckdb",
        "display_name": display_name,
        "authority": {
            "catalog_system": "duckdb",
            "connection": {"path": path},
            "synthetic_catalog": "main",
        },
        "sync": {"mode": mode},
    }


class SourceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_sources.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "test_sources.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.metadata_store = SQLiteMetadataStore(cls.meta_path)
        cls.client = TestClient(create_app(cls.db_path, metadata_store=cls.metadata_store))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _local_connection(self) -> dict:
        return {"path": str(self.db_path)}

    def _sync_all_tables(self, source_id: str) -> dict:
        """Helper to add sync selections for all tables and trigger sync."""
        self.client.post(
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
        resp = self.client.post(f"/sources/{source_id}/sync")
        return resp.json()

    def test_register_and_list_sources(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Demo Local"),
        )
        self.assertEqual(resp.status_code, 200)
        source = resp.json()
        self.assertEqual(source["source_type"], "duckdb")
        self.assertEqual(source["display_name"], "Demo Local")

        resp = self.client.get("/sources")
        self.assertEqual(resp.status_code, 200)
        sources = resp.json()
        self.assertTrue(any(s["source_id"] == source["source_id"] for s in sources))

    def test_register_source_rejects_unsupported_type(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "mysql",
                "display_name": "Unsupported Source",
                "authority": {"catalog_system": "mysql", "connection": {}},
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_get_source(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Detail Test"),
        )
        source_id = resp.json()["source_id"]
        resp = self.client.get(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["source_id"], source_id)
        self.assertEqual(resp.json()["authority"]["catalog_system"], "duckdb")
        self.assertEqual(resp.json()["sync"]["mode"], "selected")
        self.assertEqual(resp.json()["policy"]["allow_sync"], True)
        self.assertEqual(resp.json()["intrinsic_capabilities"], {"supports_partitions": False})
        self.assertEqual(resp.json()["readiness_status"], "ready")
        self.assertIsNone(resp.json()["failure_code"])
        self.assertEqual(resp.json()["mappings"], [])

    def test_get_source_includes_mapping_summaries(self) -> None:
        source_resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Mapped Source Detail"),
        )
        engine_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Mapped Engine Detail",
                "connection": {"path": str(self.db_path)},
            },
        )
        mapping_resp = self.client.post(
            "/mappings",
            json={
                "source_id": source_resp.json()["source_id"],
                "engine_id": engine_resp.json()["engine_id"],
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    }
                ],
            },
        )
        self.assertEqual(mapping_resp.status_code, 200)

        detail = self.client.get(f"/sources/{source_resp.json()['source_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(len(detail.json()["mappings"]), 1)
        self.assertEqual(
            detail.json()["mappings"][0]["mapping_id"], mapping_resp.json()["mapping_id"]
        )
        self.assertEqual(detail.json()["mappings"][0]["engine_id"], engine_resp.json()["engine_id"])
        self.assertEqual(
            detail.json()["mappings"][0]["catalog_mappings"],
            [
                {
                    "authority_catalog": "main",
                    "execution_catalog": "duckdb_runtime",
                    "default_schema": None,
                }
            ],
        )

    def test_source_openapi_uses_explicit_response_model(self) -> None:
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        schemas = payload["components"]["schemas"]
        self.assertIn("SourceResponse", schemas)
        self.assertIn("SourceAuthorityResponse", schemas)
        self.assertIn("SourceSyncResponse", schemas)
        self.assertIn("SourcePolicyResponse", schemas)
        self.assertIn("SourceIntrinsicCapabilitiesResponse", schemas)

        source_get = payload["paths"]["/sources"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(source_get["items"]["$ref"], "#/components/schemas/SourceResponse")

        source_post = payload["paths"]["/sources"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(source_post["$ref"], "#/components/schemas/SourceResponse")

    def test_get_source_reports_not_ready_when_legacy_row_is_missing_synthetic_catalog(
        self,
    ) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Legacy DuckDB Source"),
        )
        source_id = resp.json()["source_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            """
            UPDATE sources
            SET authority_json = ?
            WHERE source_id = ?
            """,
            [
                f'{{"catalog_system":"duckdb","connection":{{"path":"{self.db_path}"}}}}',
                source_id,
            ],
        )

        detail = self.client.get(f"/sources/{source_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["readiness_status"], "not_ready")
        self.assertEqual(detail.json()["failure_code"], "source_missing_synthetic_catalog")

    def test_source_api_normalizes_malformed_stored_authority_connection(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Malformed Stored Source"),
        )
        source_id = resp.json()["source_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            """
            UPDATE sources
            SET authority_json = ?
            WHERE source_id = ?
            """,
            [
                '{"catalog_system":"duckdb","connection":"oops","synthetic_catalog":"main"}',
                source_id,
            ],
        )

        detail = self.client.get(f"/sources/{source_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["authority"]["connection"], {})
        self.assertEqual(detail.json()["readiness_status"], "not_ready")
        self.assertEqual(detail.json()["failure_code"], "source_invalid_connection")

        listed = self.client.get("/sources")
        self.assertEqual(listed.status_code, 200)
        listed_source = next(item for item in listed.json() if item["source_id"] == source_id)
        self.assertEqual(listed_source["authority"]["connection"], {})
        self.assertEqual(listed_source["readiness_status"], "not_ready")
        self.assertEqual(listed_source["failure_code"], "source_invalid_connection")

    def test_source_api_degrades_malformed_stored_authority_json(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Bad Stored Source JSON"),
        )
        source_id = resp.json()["source_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            """
            UPDATE sources
            SET authority_json = ?
            WHERE source_id = ?
            """,
            ["{bad-json", source_id],
        )

        detail = self.client.get(f"/sources/{source_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["readiness_status"], "not_ready")
        self.assertEqual(detail.json()["failure_code"], "source_invalid_authority")

        listed = self.client.get("/sources")
        self.assertEqual(listed.status_code, 200)
        listed_source = next(item for item in listed.json() if item["source_id"] == source_id)
        self.assertEqual(listed_source["readiness_status"], "not_ready")
        self.assertEqual(listed_source["failure_code"], "source_invalid_authority")

    def test_source_api_degrades_malformed_stored_policy_json(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Bad Stored Source Policy"),
        )
        source_id = resp.json()["source_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            "UPDATE sources SET policy_json = ? WHERE source_id = ?",
            ["{bad-json", source_id],
        )

        detail = self.client.get(f"/sources/{source_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["readiness_status"], "not_ready")
        self.assertEqual(detail.json()["failure_code"], "source_invalid_policy")

        listed = self.client.get("/sources")
        self.assertEqual(listed.status_code, 200)
        listed_source = next(item for item in listed.json() if item["source_id"] == source_id)
        self.assertEqual(listed_source["readiness_status"], "not_ready")
        self.assertEqual(listed_source["failure_code"], "source_invalid_policy")

    def test_source_api_degrades_malformed_stored_capabilities_json(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Bad Stored Source Capabilities"),
        )
        source_id = resp.json()["source_id"]
        metadata = self.client.app.state.metadata_store
        metadata.execute(
            "UPDATE sources SET intrinsic_capabilities_json = ? WHERE source_id = ?",
            ["{bad-json", source_id],
        )

        detail = self.client.get(f"/sources/{source_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["readiness_status"], "not_ready")
        self.assertEqual(detail.json()["failure_code"], "source_invalid_capabilities")

        listed = self.client.get("/sources")
        self.assertEqual(listed.status_code, 200)
        listed_source = next(item for item in listed.json() if item["source_id"] == source_id)
        self.assertEqual(listed_source["readiness_status"], "not_ready")
        self.assertEqual(listed_source["failure_code"], "source_invalid_capabilities")

    def test_get_source_404(self) -> None:
        resp = self.client.get("/sources/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_sync_local_source_and_browse_objects(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Sync Test"),
        )
        source_id = resp.json()["source_id"]

        sync_result = self._sync_all_tables(source_id)
        self.assertEqual(sync_result["status"], "succeeded")

        # Check sync job status
        job_id = sync_result["job_id"]
        resp = self.client.get(f"/sources/{source_id}/sync/{job_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "succeeded")

        # Browse all objects
        resp = self.client.get(f"/sources/{source_id}/objects")
        self.assertEqual(resp.status_code, 200)
        objects = resp.json()
        self.assertGreater(len(objects), 0)

        # Filter by type=table
        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        self.assertEqual(resp.status_code, 200)
        tables = resp.json()
        self.assertEqual(
            len(tables), 4
        )  # watch_events, player_qoe, ad_events, recommendation_events
        table_names = {t["native_name"] for t in tables}
        self.assertIn("watch_events", table_names)
        watch_events = next(table for table in tables if table["native_name"] == "watch_events")
        self.assertEqual(
            watch_events["authority_locator"],
            {"catalog": "main", "schema": "analytics", "table": "watch_events"},
        )
        self.assertEqual(watch_events["fqn"], "main.analytics.watch_events")
        self.assertNotIn("mapping_id", watch_events)
        self.assertNotIn("execution_catalog", watch_events)
        self.assertNotIn("engine_id", watch_events)
        self.assertNotIn("execution_catalog", watch_events["authority_locator"])

        detail_resp = self.client.get(f"/sources/{source_id}/objects/{watch_events['object_id']}")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertEqual(detail_resp.json()["authority_locator"], watch_events["authority_locator"])
        self.assertNotIn("mapping_id", detail_resp.json())
        self.assertNotIn("execution_catalog", detail_resp.json())

        resp = self.client.get(f"/sources/{source_id}/objects?type=table&schema=analytics")
        self.assertEqual(resp.status_code, 200)
        filtered_tables = resp.json()
        self.assertEqual(len(filtered_tables), 4)
        self.assertTrue(
            all(table["authority_locator"]["schema"] == "analytics" for table in filtered_tables)
        )

    def test_sync_idempotent(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Idempotent Test"),
        )
        source_id = resp.json()["source_id"]

        # Sync twice
        self._sync_all_tables(source_id)
        self._sync_all_tables(source_id)

        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        tables = resp.json()
        self.assertEqual(len(tables), 4)

    def test_sync_reuses_existing_object_when_locator_json_key_order_differs(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Locator Order Test"),
        )
        source_id = resp.json()["source_id"]

        self._sync_all_tables(source_id)
        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        tables = resp.json()
        watch_events = next(table for table in tables if table["native_name"] == "watch_events")
        original_object_id = watch_events["object_id"]

        store = self.client.app.state.metadata_store
        store.execute(
            "UPDATE source_objects SET authority_locator_json = ? WHERE object_id = ?",
            [
                json.dumps({"table": "watch_events", "schema": "analytics", "catalog": "main"}),
                original_object_id,
            ],
        )

        self._sync_all_tables(source_id)
        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        resynced_tables = resp.json()
        resynced_watch_events = next(
            table for table in resynced_tables if table["native_name"] == "watch_events"
        )
        self.assertEqual(resynced_watch_events["object_id"], original_object_id)

    def test_get_source_object_detail(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Object Detail Test"),
        )
        source_id = resp.json()["source_id"]
        self._sync_all_tables(source_id)

        list_resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        self.assertEqual(list_resp.status_code, 200)
        listed_object = list_resp.json()[0]

        resp = self.client.get(f"/sources/{source_id}/objects/{listed_object['object_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), listed_object)

    def test_get_source_object_detail_for_column(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Column Detail Test"),
        )
        source_id = resp.json()["source_id"]
        self._sync_all_tables(source_id)

        list_resp = self.client.get(f"/sources/{source_id}/objects?type=column")
        self.assertEqual(list_resp.status_code, 200)
        listed_object = list_resp.json()[0]

        resp = self.client.get(f"/sources/{source_id}/objects/{listed_object['object_id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), listed_object)

    def test_get_source_object_detail_404_for_unknown_source(self) -> None:
        resp = self.client.get("/sources/src_missing/objects/obj_missing")
        self.assertEqual(resp.status_code, 404)

    def test_get_source_object_detail_404_for_unknown_object(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Unknown Object Test"),
        )
        source_id = resp.json()["source_id"]
        self._sync_all_tables(source_id)

        detail_resp = self.client.get(f"/sources/{source_id}/objects/obj_missing")
        self.assertEqual(detail_resp.status_code, 404)

    def test_get_source_object_detail_404_for_object_from_other_source(self) -> None:
        first_resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "First Source Detail Test"),
        )
        first_source_id = first_resp.json()["source_id"]
        self._sync_all_tables(first_source_id)
        first_object = self.client.get(f"/sources/{first_source_id}/objects?type=table").json()[0]

        second_resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Second Source Detail Test"),
        )
        second_source_id = second_resp.json()["source_id"]
        self._sync_all_tables(second_source_id)

        detail_resp = self.client.get(
            f"/sources/{second_source_id}/objects/{first_object['object_id']}"
        )
        self.assertEqual(detail_resp.status_code, 404)

    def test_update_source_api(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Update Test"),
        )
        source_id = resp.json()["source_id"]
        resp = self.client.put(
            f"/sources/{source_id}",
            json={
                "display_name": "Updated Name",
                "sync": {"mode": "selected"},
                "authority": {
                    "catalog_system": "duckdb",
                    "connection": {"path": "/tmp/new.duckdb"},
                    "synthetic_catalog": "main",
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()
        self.assertEqual(updated["display_name"], "Updated Name")
        self.assertEqual(updated["sync"]["mode"], "selected")
        self.assertEqual(updated["authority"]["connection"]["path"], "/tmp/new.duckdb")

    def test_update_source_partial(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Partial Update"),
        )
        source_id = resp.json()["source_id"]
        original_authority = resp.json()["authority"]
        resp = self.client.put(f"/sources/{source_id}", json={"display_name": "New Name Only"})
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()
        self.assertEqual(updated["display_name"], "New Name Only")
        self.assertEqual(updated["authority"], original_authority)

    def test_update_source_invalid_authority_returns_400(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Invalid Authority Update"),
        )
        source_id = resp.json()["source_id"]
        resp = self.client.put(
            f"/sources/{source_id}",
            json={
                "authority": {
                    "catalog_system": "trino",
                    "connection": {"host": "trino.local"},
                }
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("authority.catalog_system must match source_type", resp.text)

    def test_update_source_not_found(self) -> None:
        resp = self.client.put("/sources/nonexistent", json={"display_name": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_source_api(self) -> None:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Delete Test"),
        )
        source_id = resp.json()["source_id"]
        # Sync to create source_objects
        self.client.post(f"/sources/{source_id}/sync")
        # Add a sync selection
        self.client.post(
            f"/sources/{source_id}/sync/selections",
            json={"selections": [{"schema_name": "analytics", "table_name": "watch_events"}]},
        )
        # Delete source
        resp = self.client.delete(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "deleted")
        # Verify gone
        resp = self.client.get(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 404)

    def test_delete_source_not_found(self) -> None:
        resp = self.client.delete("/sources/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_delete_source_blocked_by_mapping(self) -> None:
        """DELETE returns 409 when mappings reference the source."""
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Bound Source"),
        )
        source_id = resp.json()["source_id"]
        # Register an engine and create a mapping
        eng_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Tmp Engine",
                "connection": self._local_connection(),
            },
        )
        engine_id = eng_resp.json()["engine_id"]
        self.client.post(
            "/mappings",
            json={
                "source_id": source_id,
                "engine_id": engine_id,
                "catalog_mappings": [
                    {
                        "authority_catalog": "main",
                        "execution_catalog": "duckdb_runtime",
                    }
                ],
            },
        )

        resp = self.client.delete(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertIn("mapping", detail["message"].lower())
        self.assertGreater(len(detail["dependencies"]), 0)

    def test_delete_source_blocked_by_typed_binding(self) -> None:
        """DELETE returns 409 when typed bindings reference source objects."""
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "Mapped Source"),
        )
        source_id = resp.json()["source_id"]
        # Sync to get source_objects
        self._sync_all_tables(source_id)
        objects = self.client.get(f"/sources/{source_id}/objects?type=table").json()
        object_id = objects[0]["object_id"]
        metric = create_typed_metric(
            self.client,
            name="tmp_metric",
            display_name="Tmp Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        create_typed_metric_binding(
            self.client,
            metric_ref="metric.tmp_metric",
            object_id=object_id,
            carrier_locator=str(objects[0]["fqn"]),
        )

        resp = self.client.delete(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertIn("binding", detail["message"].lower())
        self.assertIn("binding.tmp_metric_primary", detail["dependencies"][0])


class SyncModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_sync_mode.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "test_sync_mode.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path))
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_source(self, name: str) -> dict:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), name),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_sync_mode_none_returns_400(self) -> None:
        """Triggering sync on a mode=none source returns 400."""
        source = self._create_source("None Mode Source")
        source_id = source["source_id"]
        # Update sync_mode to none directly via metadata
        store = self.client.app.state.metadata_store
        store.execute("UPDATE sources SET sync_mode = 'none' WHERE source_id = ?", [source_id])

        resp = self.client.post(f"/sources/{source_id}/sync")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("disabled", resp.json()["detail"].lower())

    def test_sync_mode_selected_no_selections_returns_400(self) -> None:
        """Triggering sync with selected and no selections returns 400."""
        source = self._create_source("Selected No Sel")
        source_id = source["source_id"]
        store = self.client.app.state.metadata_store
        store.execute("UPDATE sources SET sync_mode = 'selected' WHERE source_id = ?", [source_id])

        resp = self.client.post(f"/sources/{source_id}/sync")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no sync selections", resp.json()["detail"].lower())

    def test_selection_crud(self) -> None:
        """Add, list, remove sync selections."""
        source = self._create_source("Selection CRUD")
        source_id = source["source_id"]

        # Initially empty
        resp = self.client.get(f"/sources/{source_id}/sync/selections")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

        # Add selections
        resp = self.client.post(
            f"/sources/{source_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                ]
            },
        )
        self.assertEqual(resp.status_code, 200)
        sels = resp.json()
        self.assertEqual(len(sels), 2)

        # List
        resp = self.client.get(f"/sources/{source_id}/sync/selections")
        self.assertEqual(len(resp.json()), 2)

        # Remove one
        sel_id = sels[0]["selection_id"]
        resp = self.client.delete(f"/sources/{source_id}/sync/selections/{sel_id}")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/sources/{source_id}/sync/selections")
        self.assertEqual(len(resp.json()), 1)

        # Clear all
        resp = self.client.delete(f"/sources/{source_id}/sync/selections")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(f"/sources/{source_id}/sync/selections")
        self.assertEqual(resp.json(), [])

    def test_selective_sync_only_syncs_selected_tables(self) -> None:
        """mode=selected with selections only syncs chosen tables."""
        source = self._create_source("Selective Sync")
        source_id = source["source_id"]
        store = self.client.app.state.metadata_store
        store.execute("UPDATE sources SET sync_mode = 'selected' WHERE source_id = ?", [source_id])

        # Add one table selection
        self.client.post(
            f"/sources/{source_id}/sync/selections",
            json={"selections": [{"schema_name": "analytics", "table_name": "watch_events"}]},
        )

        # Trigger sync
        resp = self.client.post(f"/sources/{source_id}/sync")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "succeeded")

        # Only the selected table should be synced
        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        tables = resp.json()
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["native_name"], "watch_events")
        self.assertEqual(tables[0]["fqn"], "main.analytics.watch_events")
        self.assertEqual(
            tables[0]["authority_locator"],
            {"catalog": "main", "schema": "analytics", "table": "watch_events"},
        )

    def test_browse_catalog_schemas(self) -> None:
        """Browse live catalog schemas without persisting."""
        source = self._create_source("Browse Schema")
        source_id = source["source_id"]

        resp = self.client.get(f"/sources/{source_id}/catalog/schemas")
        self.assertEqual(resp.status_code, 200)
        schemas = resp.json()
        self.assertGreater(len(schemas), 0)
        self.assertEqual(schemas[0]["name"], "analytics")

    def test_browse_catalog_tables(self) -> None:
        """Browse live catalog tables without persisting."""
        source = self._create_source("Browse Table")
        source_id = source["source_id"]

        resp = self.client.get(f"/sources/{source_id}/catalog/tables?schema=analytics")
        self.assertEqual(resp.status_code, 200)
        tables = resp.json()
        self.assertEqual(len(tables), 4)
        names = {t["name"] for t in tables}
        self.assertIn("watch_events", names)

        # Verify nothing was persisted
        resp = self.client.get(f"/sources/{source_id}/objects")
        self.assertEqual(resp.json(), [])

    def test_browse_catalog_schemas_uses_source_catalog_for_trino(self) -> None:
        from unittest.mock import MagicMock, patch

        resp = self.client.post(
            "/sources",
            json={
                "source_type": "trino",
                "display_name": "Browse Trino Schemas",
                "authority": {
                    "catalog_system": "trino",
                    "connection": {
                        "host": "trino.example.com",
                        "catalog": "iceberg",
                        "user": "marivo",
                    },
                },
            },
        )
        self.assertEqual(resp.status_code, 200)
        source_id = resp.json()["source_id"]

        mock_adapter = MagicMock()
        mock_adapter.list_schemas.return_value = []
        with patch("app.registry.source_registry.build_catalog_adapter", return_value=mock_adapter):
            resp = self.client.get(f"/sources/{source_id}/catalog/schemas")

        self.assertEqual(resp.status_code, 200)
        mock_adapter.list_schemas.assert_called_once_with("iceberg")

    def test_trino_sync_persists_source_authority_catalog_locator(self) -> None:
        from unittest.mock import MagicMock, patch

        from app.adapters.base import CatalogCapabilities, PhysicalObject

        resp = self.client.post(
            "/sources",
            json={
                "source_type": "trino",
                "display_name": "Trino Sync Authority Locator",
                "authority": {
                    "catalog_system": "trino",
                    "connection": {
                        "host": "trino.example.com",
                        "catalog": "iceberg_authority",
                        "schema": "analytics",
                        "user": "marivo",
                    },
                },
                "sync": {"mode": "selected"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        source_id = resp.json()["source_id"]
        self.client.post(
            f"/sources/{source_id}/sync/selections",
            json={"selections": [{"schema_name": "analytics", "table_name": "watch_events"}]},
        )

        mock_adapter = MagicMock()
        mock_adapter.get_table_detail.return_value = PhysicalObject(
            native_name="watch_events",
            native_id=None,
            object_type="table",
            parent_path="analytics",
            properties={"source": "mock_trino"},
        )
        mock_adapter.list_columns.return_value = []
        mock_adapter.capabilities.return_value = CatalogCapabilities(supports_partitions=False)
        with patch("app.registry.source_registry.build_catalog_adapter", return_value=mock_adapter):
            sync_resp = self.client.post(f"/sources/{source_id}/sync")

        self.assertEqual(sync_resp.status_code, 200)
        self.assertEqual(sync_resp.json()["status"], "succeeded")
        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        self.assertEqual(resp.status_code, 200)
        tables = resp.json()
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["fqn"], "iceberg_authority.analytics.watch_events")
        self.assertEqual(
            tables[0]["authority_locator"],
            {"catalog": "iceberg_authority", "schema": "analytics", "table": "watch_events"},
        )
        self.assertNotIn("execution_catalog", tables[0])
        self.assertNotIn("mapping_id", tables[0])
        self.assertNotIn("execution_catalog", tables[0]["authority_locator"])

    def test_selective_sync_keeps_columns_as_child_objects_not_table_properties(self) -> None:
        from unittest.mock import MagicMock, patch

        from app.adapters.base import CatalogCapabilities, PhysicalObject

        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), "No Table Columns Redundancy Test"),
        )
        self.assertEqual(resp.status_code, 200)
        source_id = resp.json()["source_id"]
        self.client.post(
            f"/sources/{source_id}/sync/selections",
            json={"selections": [{"schema_name": "analytics", "table_name": "watch_events"}]},
        )

        mock_adapter = MagicMock()
        mock_adapter.get_table_detail.return_value = PhysicalObject(
            native_name="watch_events",
            native_id=None,
            object_type="table",
            parent_path="analytics",
            properties={
                "columns": [{"name": "event_time", "type": "timestamp"}],
                "column_count": 2,
                "table_type": "BASE TABLE",
            },
        )
        mock_adapter.list_columns.return_value = [
            PhysicalObject(
                native_name="event_time",
                native_id=None,
                object_type="column",
                parent_path="analytics.watch_events",
                properties={"data_type": "timestamp", "nullable": True, "comment": ""},
            ),
            PhysicalObject(
                native_name="user_id",
                native_id=None,
                object_type="column",
                parent_path="analytics.watch_events",
                properties={"data_type": "varchar", "nullable": False, "comment": "user"},
            ),
        ]
        mock_adapter.capabilities.return_value = CatalogCapabilities(supports_partitions=False)
        with patch("app.registry.source_registry.build_catalog_adapter", return_value=mock_adapter):
            sync_resp = self.client.post(f"/sources/{source_id}/sync")

        self.assertEqual(sync_resp.status_code, 200)
        table_resp = self.client.get(f"/sources/{source_id}/objects", params={"type": "table"})
        table_obj = table_resp.json()[0]
        self.assertNotIn("columns", table_obj["properties"])
        self.assertEqual(table_obj["properties"]["column_count"], 2)
        self.assertEqual(table_obj["properties"]["table_type"], "BASE TABLE")

        column_resp = self.client.get(f"/sources/{source_id}/objects", params={"type": "column"})
        columns = {obj["native_name"]: obj["properties"] for obj in column_resp.json()}
        self.assertEqual(columns["event_time"]["data_type"], "timestamp")
        self.assertFalse(columns["user_id"]["nullable"])
        self.assertEqual(columns["user_id"]["comment"], "user")

        stale_properties = dict(table_obj["properties"])
        stale_properties["columns"] = [{"name": "stale", "type": "varchar"}]
        self.client.app.state.metadata_store.execute(
            "UPDATE source_objects SET properties_json = ? WHERE object_id = ?",
            [json.dumps(stale_properties), table_obj["object_id"]],
        )

        with patch("app.registry.source_registry.build_catalog_adapter", return_value=mock_adapter):
            resync_resp = self.client.post(f"/sources/{source_id}/sync")

        self.assertEqual(resync_resp.status_code, 200)
        table_resp = self.client.get(f"/sources/{source_id}/objects/{table_obj['object_id']}")
        self.assertNotIn("columns", table_resp.json()["properties"])


class TrinoCatalogAdapterTests(unittest.TestCase):
    """Unit tests for TrinoCatalogAdapter — mocks _connect() so no real Trino needed."""

    def _make_cursor(self, rows: list[tuple], columns: list[str]):
        """Build a mock cursor that returns the given rows."""
        from unittest.mock import MagicMock

        cur = MagicMock()
        cur.description = [(col,) for col in columns]
        cur.fetchall.return_value = rows
        return cur

    def _make_conn(self, cursor):
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn

    def test_source_type_and_capabilities(self) -> None:
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost")
        self.assertEqual(adapter.source_type(), "trino")
        caps = adapter.capabilities()
        self.assertTrue(caps.supports_schemas)
        self.assertTrue(caps.supports_column_stats)
        self.assertFalse(caps.supports_partitions)

    def test_connection_success(self) -> None:
        from unittest.mock import MagicMock, patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost")
        with patch.object(adapter, "_connect") as mock_connect:
            conn = MagicMock()
            cur = MagicMock()
            cur.fetchone.return_value = (1,)
            conn.cursor.return_value = cur
            mock_connect.return_value = conn
            self.assertTrue(adapter.test_connection())

    def test_connection_failure(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost")
        with patch.object(adapter, "_connect", side_effect=Exception("refused")):
            self.assertFalse(adapter.test_connection())

    def test_list_catalogs(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost")
        cursor = self._make_cursor([("hive",), ("tpch",)], ["Catalog"])
        with patch.object(adapter, "_connect", return_value=self._make_conn(cursor)):
            catalogs = adapter.list_catalogs()
        self.assertEqual(len(catalogs), 2)
        self.assertEqual(catalogs[0].native_name, "hive")
        self.assertEqual(catalogs[1].native_name, "tpch")
        self.assertEqual(catalogs[0].object_type, "catalog")

    def test_list_schemas_with_catalog(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")
        cursor = self._make_cursor(
            [("default",), ("sales",), ("information_schema",)],
            ["Schema"],
        )
        with patch.object(adapter, "_connect", return_value=self._make_conn(cursor)):
            schemas = adapter.list_schemas("hive")
        # information_schema should be filtered out
        self.assertEqual(len(schemas), 2)
        names = {s.native_name for s in schemas}
        self.assertIn("default", names)
        self.assertIn("sales", names)
        self.assertEqual(schemas[0].parent_path, "hive")

    def test_list_schemas_all_catalogs(self) -> None:
        """When catalog_name=None, list_schemas aggregates across all catalogs."""
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost")

        catalog_cursor = self._make_cursor([("hive",), ("tpch",)], ["Catalog"])
        hive_schema_cursor = self._make_cursor([("default",)], ["Schema"])
        tpch_schema_cursor = self._make_cursor([("sf1",)], ["Schema"])

        call_count = [0]
        cursors = [catalog_cursor, hive_schema_cursor, tpch_schema_cursor]

        def side_effect():
            c = cursors[call_count[0]]
            call_count[0] += 1
            return self._make_conn(c)

        with patch.object(adapter, "_connect", side_effect=side_effect):
            schemas = adapter.list_schemas(None)
        self.assertEqual(len(schemas), 2)
        names = {s.native_name for s in schemas}
        self.assertIn("default", names)
        self.assertIn("sf1", names)

    def test_list_tables_uses_show_tables_and_column_count_query(self) -> None:
        """list_tables() enumerates schema-local tables and hydrates column counts separately."""
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")
        show_tables_cursor = self._make_cursor([("orders",), ("lineitem",)], ["Table"])
        column_count_cursor = self._make_cursor(
            [("orders", 5), ("lineitem", 16)],
            ["table_name", "column_count"],
        )
        call_count = [0]
        cursors = [show_tables_cursor, column_count_cursor]

        def side_effect():
            c = cursors[call_count[0]]
            call_count[0] += 1
            return self._make_conn(c)

        with patch.object(adapter, "_connect", side_effect=side_effect) as mock_connect:
            tables = adapter.list_tables("sales")
        self.assertEqual(mock_connect.call_count, 2)
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0].native_name, "orders")
        self.assertEqual(tables[0].properties["column_count"], 5)
        self.assertEqual(tables[0].properties["table_type"], "BASE TABLE")
        self.assertEqual(tables[1].native_name, "lineitem")
        self.assertEqual(tables[1].properties["column_count"], 16)

    def test_list_tables_empty_schema(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")
        show_tables_cursor = self._make_cursor([], ["Table"])
        with patch.object(adapter, "_connect", return_value=self._make_conn(show_tables_cursor)):
            tables = adapter.list_tables("missing_schema")
        self.assertEqual(tables, [])

    def test_get_table_detail(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")

        table_cursor = self._make_cursor([("orders", "BASE TABLE")], ["table_name", "table_type"])
        properties_cursor = self._make_cursor([], ["key", "value"])
        show_create_cursor = self._make_cursor([], ["Create Table"])
        col_cursor = self._make_cursor(
            [("id", "integer", 1, "NO"), ("name", "varchar", 2, "YES")],
            ["column_name", "data_type", "ordinal_position", "is_nullable"],
        )
        comment_cursor = self._make_cursor(
            [("id", "integer", "", "primary key"), ("name", "varchar", "", "")],
            ["Column", "Type", "Extra", "Comment"],
        )
        call_count = [0]
        cursors = [
            table_cursor,
            properties_cursor,
            show_create_cursor,
            col_cursor,
            comment_cursor,
        ]

        def side_effect():
            c = cursors[call_count[0]]
            call_count[0] += 1
            return self._make_conn(c)

        with patch.object(adapter, "_connect", side_effect=side_effect):
            detail = adapter.get_table_detail("sales", "orders")

        self.assertEqual(detail.native_name, "orders")
        self.assertEqual(detail.properties["column_count"], 2)
        cols = detail.properties["columns"]
        self.assertEqual(cols[0]["name"], "id")
        self.assertFalse(cols[0]["nullable"])
        self.assertEqual(cols[0]["comment"], "primary key")
        self.assertTrue(cols[1]["nullable"])

    def test_list_columns(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")
        cursor = self._make_cursor(
            [("id", "integer", 1, "NO"), ("ts", "timestamp", 2, "YES")],
            ["column_name", "data_type", "ordinal_position", "is_nullable"],
        )
        with patch.object(adapter, "_connect", return_value=self._make_conn(cursor)):
            cols = adapter.list_columns("sales", "orders")
        self.assertEqual(len(cols), 2)
        self.assertEqual(cols[0].native_name, "id")
        self.assertEqual(cols[0].object_type, "column")
        self.assertEqual(cols[0].properties["data_type"], "integer")

    def test_get_table_stats(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")
        cursor = self._make_cursor(
            [
                ("id", 1000.0, None, 0.0, None, None, None),
                (None, None, None, None, 5000.0, None, None),
            ],
            [
                "column_name",
                "distinct_values_count",
                "data_size",
                "nulls_fraction",
                "row_count",
                "low_value",
                "high_value",
            ],
        )
        with patch.object(adapter, "_connect", return_value=self._make_conn(cursor)):
            stats = adapter.get_table_stats("sales", "orders")
        self.assertEqual(stats["row_count"], 5000.0)
        self.assertIn("id", stats["columns"])
        self.assertEqual(stats["columns"]["id"]["distinct_count"], 1000.0)

    def test_build_adapter_trino(self) -> None:
        """_build_adapter() creates TrinoCatalogAdapter for trino source type."""
        from app.sources import _build_adapter

        adapter = _build_adapter("trino", {"host": "trino.example.com", "port": 9090})
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        self.assertIsInstance(adapter, TrinoCatalogAdapter)
        self.assertEqual(adapter._host, "trino.example.com")
        self.assertEqual(adapter._port, 9090)
        self.assertIsNone(adapter._password)
        self.assertEqual(adapter._http_scheme, "http")

    def test_build_adapter_trino_with_auth(self) -> None:
        """_build_adapter() passes password and http_scheme when provided."""
        from app.sources import _build_adapter

        adapter = _build_adapter(
            "trino",
            {
                "host": "trino.example.com",
                "password": "secret",
                "http_scheme": "https",
            },
        )
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        self.assertIsInstance(adapter, TrinoCatalogAdapter)
        self.assertEqual(adapter._password, "secret")
        self.assertEqual(adapter._http_scheme, "https")


class ColumnPropertiesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "col_props.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "col_props.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path))
        )

        # Register and sync a DuckDB source
        resp = cls.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(cls.db_path), "ColProps Test"),
        )
        cls.source_id = resp.json()["source_id"]
        # Add sync selections for all tables and sync
        cls.client.post(
            f"/sources/{cls.source_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        cls.client.post(f"/sources/{cls.source_id}/sync")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _get_column_object_id(self) -> str:
        resp = self.client.get(f"/sources/{self.source_id}/objects", params={"type": "column"})
        objects = resp.json()
        self.assertGreater(len(objects), 0, "No column objects found after sync")
        return objects[0]["object_id"]

    def _get_table_object_id(self) -> str:
        resp = self.client.get(f"/sources/{self.source_id}/objects", params={"type": "table"})
        objects = resp.json()
        self.assertGreater(len(objects), 0, "No table objects found after sync")
        return objects[0]["object_id"]

    def test_patch_unit_on_column(self) -> None:
        object_id = self._get_column_object_id()
        resp = self.client.patch(
            f"/sources/{self.source_id}/objects/{object_id}/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["properties"]["unit"], "seconds")
        # data_type should still be present (synced by adapter)
        self.assertIn("data_type", result["properties"])

    def test_patch_unit_survives_resync(self) -> None:
        object_id = self._get_column_object_id()
        # Patch unit
        self.client.patch(
            f"/sources/{self.source_id}/objects/{object_id}/properties",
            json={"unit": "bytes"},
        )
        # Re-sync (selections already set in setUpClass)
        self.client.post(f"/sources/{self.source_id}/sync")
        # Check unit survives
        resp = self.client.get(f"/sources/{self.source_id}/objects", params={"type": "column"})
        objects = resp.json()
        obj = next((o for o in objects if o["object_id"] == object_id), None)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["properties"]["unit"], "bytes")

    def test_patch_unit_404_bad_object(self) -> None:
        resp = self.client.patch(
            f"/sources/{self.source_id}/objects/obj_nonexistent/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_unit_400_non_column(self) -> None:
        table_object_id = self._get_table_object_id()
        resp = self.client.patch(
            f"/sources/{self.source_id}/objects/{table_object_id}/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(resp.status_code, 400)


class TablePreviewTests(unittest.TestCase):
    """Tests for the table preview endpoint."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "preview_test.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "preview_test.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path))
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_source(self, name: str) -> dict:
        resp = self.client.post(
            "/sources",
            json=build_duckdb_source_payload(str(self.db_path), name),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_preview_table_basic(self) -> None:
        """Preview returns sample rows with columns metadata."""
        source = self._create_source("Preview Basic")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events", "limit": 10},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["schema_name"], "analytics")
        self.assertEqual(result["table_name"], "watch_events")
        self.assertLessEqual(result["row_count"], 10)
        self.assertTrue(len(result["columns"]) > 0)
        self.assertTrue(len(result["rows"]) > 0)
        # Check columns have name and type
        for col in result["columns"]:
            self.assertIn("name", col)
            self.assertIn("type", col)

    def test_preview_table_with_columns(self) -> None:
        """Preview with column selection returns only selected columns."""
        source = self._create_source("Preview Columns")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={
                "schema": "analytics",
                "table": "watch_events",
                "columns": "user_id,event_date",
            },
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(len(result["columns"]), 2)
        col_names = {c["name"] for c in result["columns"]}
        self.assertEqual(col_names, {"user_id", "event_date"})
        # Rows should only have those keys
        for row in result["rows"]:
            self.assertEqual(set(row.keys()), col_names)

    def test_preview_table_not_found(self) -> None:
        """Preview returns 404 for missing table."""
        source = self._create_source("Preview Missing")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "nonexistent_table"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_preview_invalid_column(self) -> None:
        """Preview returns 400 for invalid column names."""
        source = self._create_source("Preview Invalid Col")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={
                "schema": "analytics",
                "table": "watch_events",
                "columns": "invalid_column_name",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_preview_limit_clamped(self) -> None:
        """Preview clamps limit to max 1000."""
        source = self._create_source("Preview Limit")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events", "limit": 5000},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["limit_applied"], 1000)

    def test_preview_source_not_found(self) -> None:
        """Preview returns 404 for missing source."""
        resp = self.client.get(
            "/sources/src_nonexistent/catalog/preview",
            params={"schema": "analytics", "table": "watch_events"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_preview_truncated_flag(self) -> None:
        """Preview sets truncated=True when more rows exist beyond limit."""
        source = self._create_source("Preview Truncated")
        # watch_events has more than 1 row, so truncated should be True
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events", "limit": 1},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["row_count"], 1)
        self.assertTrue(result["truncated"])

    def test_preview_truncated_false_when_fewer_rows(self) -> None:
        """Preview sets truncated=False when table has fewer rows than limit."""
        source = self._create_source("Preview Fewer Rows")
        # Request a large limit (within bounds)
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events", "limit": 500},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        # If row_count < limit, truncated should be False
        if result["row_count"] < 500:
            self.assertFalse(result["truncated"])
        # If row_count == limit, we can't tell without knowing actual table size
        # But the logic ensures truncated=False when fewer rows exist

    def test_preview_empty_columns_treated_as_all(self) -> None:
        """Preview treats empty columns=, as all columns (not SELECT FROM error)."""
        source = self._create_source("Preview Empty Columns")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events", "columns": ","},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        # Should return all columns, not fail
        self.assertTrue(len(result["columns"]) > 0)

    def test_preview_default_limit(self) -> None:
        """Preview uses default limit of 100 when not specified."""
        source = self._create_source("Preview Default")
        resp = self.client.get(
            f"/sources/{source['source_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["limit_requested"], 100)
        self.assertEqual(result["limit_applied"], 100)


if __name__ == "__main__":
    unittest.main()
