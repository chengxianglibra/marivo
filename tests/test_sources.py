from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.semantic_test_helpers import create_legacy_entity
from tests.shared_fixtures import get_seeded_duckdb_path


class SourceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_sources.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _local_connection(self) -> dict:
        return {"path": str(self.db_path)}

    def test_register_and_list_sources(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Demo Local",
                "connection": self._local_connection(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        source = resp.json()
        self.assertEqual(source["source_type"], "duckdb")
        self.assertEqual(source["display_name"], "Demo Local")

        resp = self.client.get("/sources")
        self.assertEqual(resp.status_code, 200)
        sources = resp.json()
        self.assertTrue(any(s["source_id"] == source["source_id"] for s in sources))

    def test_get_source(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Detail Test",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]
        resp = self.client.get(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["source_id"], source_id)

    def test_get_source_404(self) -> None:
        resp = self.client.get("/sources/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_sync_local_source_and_browse_objects(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Sync Test",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]

        resp = self.client.post(f"/sources/{source_id}/sync")
        self.assertEqual(resp.status_code, 200)
        sync_result = resp.json()
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

    def test_sync_idempotent(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Idempotent Test",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]

        # Sync twice
        self.client.post(f"/sources/{source_id}/sync")
        self.client.post(f"/sources/{source_id}/sync")

        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        tables = resp.json()
        self.assertEqual(len(tables), 4)

    def test_update_source_api(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Update Test",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]
        resp = self.client.put(
            f"/sources/{source_id}",
            json={
                "display_name": "Updated Name",
                "sync_mode": "by_select",
                "connection": {"path": "/tmp/new.duckdb"},
            },
        )
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()
        self.assertEqual(updated["display_name"], "Updated Name")
        self.assertEqual(updated["sync_mode"], "by_select")
        self.assertEqual(updated["connection"]["path"], "/tmp/new.duckdb")

    def test_update_source_partial(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Partial Update",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]
        original_conn = resp.json()["connection"]
        resp = self.client.put(f"/sources/{source_id}", json={"display_name": "New Name Only"})
        self.assertEqual(resp.status_code, 200)
        updated = resp.json()
        self.assertEqual(updated["display_name"], "New Name Only")
        self.assertEqual(updated["connection"], original_conn)

    def test_update_source_not_found(self) -> None:
        resp = self.client.put("/sources/nonexistent", json={"display_name": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_source_api(self) -> None:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Delete Test",
                "connection": self._local_connection(),
            },
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

    def test_delete_source_blocked_by_binding(self) -> None:
        """DELETE returns 409 when bindings reference the source."""
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Bound Source",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]
        # Register an engine and create a binding
        eng_resp = self.client.post(
            "/engines",
            json={
                "engine_type": "duckdb",
                "display_name": "Tmp Engine",
                "connection": self._local_connection(),
            },
        )
        engine_id = eng_resp.json()["engine_id"]
        self.client.post("/bindings", json={"source_id": source_id, "engine_id": engine_id})

        resp = self.client.delete(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertIn("binding", detail["message"].lower())
        self.assertGreater(len(detail["dependencies"]), 0)

    def test_delete_source_blocked_by_mapping(self) -> None:
        """DELETE returns 409 when semantic mappings reference source objects."""
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "Mapped Source",
                "connection": self._local_connection(),
            },
        )
        source_id = resp.json()["source_id"]
        # Sync to get source_objects
        self.client.post(f"/sources/{source_id}/sync")
        objects = self.client.get(f"/sources/{source_id}/objects?type=table").json()
        object_id = objects[0]["object_id"]
        # Create an entity + mapping
        entity = create_legacy_entity(
            self.client,
            name="tmp_ent",
            display_name="Tmp",
            keys=["id"],
        )
        entity_id = entity["entity_id"]
        self.client.post(
            "/semantic/mappings",
            json={
                "semantic_type": "entity",
                "semantic_id": entity_id,
                "object_id": object_id,
                "mapping_type": "primary",
            },
        )

        resp = self.client.delete(f"/sources/{source_id}")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertIn("mapping", detail["message"].lower())
        self.assertIn(entity_id, detail["dependencies"][0])


class SyncModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_sync_mode.duckdb"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_source(self, name: str) -> dict:
        resp = self.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": name,
                "connection": {"path": str(self.db_path)},
            },
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

    def test_sync_mode_by_select_no_selections_returns_400(self) -> None:
        """Triggering sync with by_select and no selections returns 400."""
        source = self._create_source("BySelect No Sel")
        source_id = source["source_id"]
        store = self.client.app.state.metadata_store
        store.execute("UPDATE sources SET sync_mode = 'by_select' WHERE source_id = ?", [source_id])

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
        """mode=by_select with selections only syncs chosen tables."""
        source = self._create_source("Selective Sync")
        source_id = source["source_id"]
        store = self.client.app.state.metadata_store
        store.execute("UPDATE sources SET sync_mode = 'by_select' WHERE source_id = ?", [source_id])

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

    def test_full_sync_still_works(self) -> None:
        """mode=all (default) still syncs everything."""
        source = self._create_source("Full Sync Check")
        source_id = source["source_id"]

        resp = self.client.post(f"/sources/{source_id}/sync")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/sources/{source_id}/objects?type=table")
        tables = resp.json()
        self.assertEqual(len(tables), 4)

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

    def test_list_tables_uses_single_query(self) -> None:
        """list_tables() uses a single JOIN query; column_count comes from grouped result."""
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")
        cursor = self._make_cursor(
            [("orders", "BASE TABLE", 5), ("lineitem", "BASE TABLE", 16)],
            ["table_name", "table_type", "column_count"],
        )
        with patch.object(
            adapter, "_connect", return_value=self._make_conn(cursor)
        ) as mock_connect:
            tables = adapter.list_tables("sales")
        # Only one _connect() call — no per-table sub-queries
        self.assertEqual(mock_connect.call_count, 1)
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0].native_name, "orders")
        self.assertEqual(tables[0].properties["column_count"], 5)
        self.assertEqual(tables[1].native_name, "lineitem")
        self.assertEqual(tables[1].properties["column_count"], 16)

    def test_get_table_detail(self) -> None:
        from unittest.mock import patch

        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost", catalog="hive")

        table_cursor = self._make_cursor([("orders", "BASE TABLE")], ["table_name", "table_type"])
        col_cursor = self._make_cursor(
            [("id", "integer", 1, "NO"), ("name", "varchar", 2, "YES")],
            ["column_name", "data_type", "ordinal_position", "is_nullable"],
        )
        call_count = [0]
        cursors = [table_cursor, col_cursor]

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
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(create_app(cls.db_path))

        # Register and sync a DuckDB source
        resp = cls.client.post(
            "/sources",
            json={
                "source_type": "duckdb",
                "display_name": "ColProps Test",
                "connection": {"path": str(cls.db_path)},
            },
        )
        cls.source_id = resp.json()["source_id"]
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
        # Re-sync
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


if __name__ == "__main__":
    unittest.main()
