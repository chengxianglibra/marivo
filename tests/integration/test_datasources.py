from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from marivo.adapters.local.sqlite_metadata import SQLiteMetadataStore
from marivo.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _build_duckdb_datasource_payload(path: str, display_name: str) -> dict:
    return {
        "datasource_type": "duckdb",
        "display_name": display_name,
        "connection": {"path": path},
    }


def _build_trino_datasource_payload(
    display_name: str,
    *,
    host: str = "trino.example.com",
    catalog: str = "iceberg",
    user: str | None = "marivo",
) -> dict:
    connection: dict = {"host": host, "catalog": catalog}
    if user is not None:
        connection["user"] = user
    return {
        "datasource_type": "trino",
        "display_name": display_name,
        "connection": connection,
    }


# ---------------------------------------------------------------------------
# Unit tests for DatasourceService
# ---------------------------------------------------------------------------


class DatasourceServiceUnitTests(unittest.TestCase):
    """Unit tests for DatasourceService using SQLiteMetadataStore directly."""

    @classmethod
    def setUpClass(cls) -> None:
        from marivo.datasources import DatasourceService

        cls.temp_dir = tempfile.TemporaryDirectory()
        meta_path = Path(cls.temp_dir.name) / "test_datasources.meta.sqlite"
        cls.metadata = SQLiteMetadataStore(meta_path)
        cls.metadata.initialize()
        cls.service = DatasourceService(cls.metadata)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    # -- Register DuckDB --------------------------------------------------

    def test_register_duckdb_datasource_returned_shape(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Test DuckDB",
            connection={"path": "/tmp/test.duckdb"},
        )
        self.assertTrue(ds["datasource_id"].startswith("ds_"))
        self.assertEqual(ds["datasource_type"], "duckdb")
        self.assertEqual(ds["display_name"], "Test DuckDB")
        self.assertEqual(ds["connection"], {"path": "/tmp/test.duckdb"})
        self.assertEqual(ds["status"], "active")
        self.assertEqual(ds["readiness_status"], "ready")
        self.assertIsNone(ds["failure_code"])
        self.assertIn("created_at", ds)
        self.assertIn("updated_at", ds)

    # -- Register Trino ---------------------------------------------------

    def test_register_trino_datasource_returned_shape(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Test Trino",
            connection={"host": "trino.example.com", "catalog": "iceberg"},
        )
        self.assertTrue(ds["datasource_id"].startswith("ds_"))
        self.assertEqual(ds["datasource_type"], "trino")
        self.assertEqual(ds["display_name"], "Test Trino")
        self.assertEqual(ds["connection"]["host"], "trino.example.com")
        self.assertEqual(ds["readiness_status"], "ready")
        self.assertIsNone(ds["failure_code"])

    # -- Get by ID --------------------------------------------------------

    def test_get_datasource_by_id(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Get Test DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        fetched = self.service.get_datasource(ds["datasource_id"])
        self.assertEqual(fetched["datasource_id"], ds["datasource_id"])
        self.assertEqual(fetched["display_name"], "Get Test DS")

    def test_get_datasource_404(self) -> None:
        with self.assertRaises(KeyError):
            self.service.get_datasource("ds_nonexistent")

    # -- List -------------------------------------------------------------

    def test_list_datasources(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="List Test DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        listed = self.service.list_datasources()
        self.assertTrue(any(d["datasource_id"] == ds["datasource_id"] for d in listed))

    # -- Update display_name ----------------------------------------------

    def test_update_datasource_display_name(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Before Update",
            connection={"path": "/tmp/test.duckdb"},
        )
        updated = self.service.update_datasource(
            ds["datasource_id"],
            display_name="After Update",
        )
        self.assertEqual(updated["display_name"], "After Update")

    # -- Delete -----------------------------------------------------------

    def test_delete_datasource(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Delete Test DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        self.service.delete_datasource(ds["datasource_id"])
        with self.assertRaises(KeyError):
            self.service.get_datasource(ds["datasource_id"])

    # -- Readiness: active -> ready --------------------------------------

    def test_readiness_active_datasource_is_ready(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Ready DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        self.assertEqual(ds["readiness_status"], "ready")
        self.assertIsNone(ds["failure_code"])

    # -- Readiness: inactive -> not_ready --------------------------------

    def test_readiness_inactive_datasource_is_not_ready(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Inactive DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        self.metadata.execute(
            "UPDATE datasources SET status = 'inactive' WHERE datasource_id = ?",
            [ds["datasource_id"]],
        )
        fetched = self.service.get_datasource(ds["datasource_id"])
        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "datasource_inactive")

    # -- Trino: no user in connection -> session_user_missing -----------

    def test_trino_no_session_user_raises(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Trino No User DS",
            connection={"host": "trino.example.com", "catalog": "iceberg"},
        )
        with self.assertRaises(ValueError) as ctx:
            self.service.build_analytics_engine(ds["datasource_id"])
        self.assertIn("session_user_missing", str(ctx.exception))

    # -- Trino: user in connection -> uses connection.user ---------------

    def test_trino_session_user_provided(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Trino Session User DS",
            connection={"host": "trino.example.com", "catalog": "iceberg", "user": "svc_marivo"},
        )
        engine = self.service.build_analytics_engine(ds["datasource_id"])
        self.assertEqual(engine.user, "svc_marivo")

    # -- Datasource type validation ----------------------------------------

    def test_datasource_type_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.service.register_datasource(
                datasource_type="spark",
                display_name="Invalid Type DS",
                connection={},
            )

    # -- Get datasource readiness ------------------------------------------

    def test_get_datasource_readiness(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Readiness DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        readiness = self.service.get_datasource_readiness(ds["datasource_id"])
        self.assertEqual(readiness["readiness_status"], "ready")
        self.assertIsNone(readiness["failure_code"])

    # -- Datasource invalid connection returns not_ready ------------------

    def test_datasource_invalid_connection_not_ready(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Invalid Conn DS",
            connection={},
        )
        fetched = self.service.get_datasource(ds["datasource_id"])
        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "datasource_invalid_connection")

    # -- Datasource degrades malformed stored connection_json -------------

    def test_datasource_degrades_malformed_stored_connection_json(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Bad Connection JSON DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        self.metadata.execute(
            "UPDATE datasources SET connection_json = ? WHERE datasource_id = ?",
            ["{bad-json", ds["datasource_id"]],
        )
        fetched = self.service.get_datasource(ds["datasource_id"])
        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "datasource_invalid_connection")


# ---------------------------------------------------------------------------
# HTTP API tests for /datasources endpoints
# ---------------------------------------------------------------------------


class DatasourceAPITests(unittest.TestCase):
    """Integration tests for /datasources endpoints via TestClient."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_datasources_api.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "test_datasources_api.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.metadata_store = SQLiteMetadataStore(cls.meta_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=cls.metadata_store),
            headers={"X-Marivo-User": "test_user"},
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    # -- Register DuckDB --------------------------------------------------

    def test_register_duckdb_datasource_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Demo Local"),
        )
        self.assertEqual(resp.status_code, 200)
        ds = resp.json()
        self.assertEqual(ds["datasource_type"], "duckdb")
        self.assertEqual(ds["display_name"], "Demo Local")
        self.assertEqual(ds["readiness_status"], "ready")
        self.assertIsNone(ds["failure_code"])

    # -- Register Trino ---------------------------------------------------

    def test_register_trino_datasource_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_trino_datasource_payload("Trino API DS"),
        )
        self.assertEqual(resp.status_code, 200)
        ds = resp.json()
        self.assertEqual(ds["datasource_type"], "trino")
        self.assertEqual(ds["display_name"], "Trino API DS")
        self.assertEqual(ds["readiness_status"], "ready")
        self.assertIsNone(ds["failure_code"])

    # -- Get datasource ---------------------------------------------------

    def test_get_datasource_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Detail Test DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        resp = self.client.get(f"/datasources/{datasource_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["datasource_id"], datasource_id)
        self.assertEqual(resp.json()["datasource_type"], "duckdb")
        self.assertEqual(resp.json()["readiness_status"], "ready")
        self.assertIsNone(resp.json()["failure_code"])

    # -- List datasources -------------------------------------------------

    def test_list_datasources_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "List Test DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        resp = self.client.get("/datasources")
        self.assertEqual(resp.status_code, 200)
        datasources = resp.json()
        self.assertTrue(any(d["datasource_id"] == datasource_id for d in datasources))

    # -- Update display_name ----------------------------------------------

    def test_update_datasource_display_name_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Update Test DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        resp = self.client.put(
            f"/datasources/{datasource_id}",
            json={"display_name": "Updated Name"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["display_name"], "Updated Name")

    # -- Partial update (only display_name) preserves other fields --------

    def test_update_datasource_partial_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Partial Update DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        original_connection = resp.json()["connection"]
        resp = self.client.put(
            f"/datasources/{datasource_id}",
            json={"display_name": "New Name Only"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["display_name"], "New Name Only")
        self.assertEqual(resp.json()["connection"], original_connection)

    # -- Delete datasource ------------------------------------------------

    def test_delete_datasource_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Delete Test DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        resp = self.client.delete(f"/datasources/{datasource_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["deleted"], True)
        resp = self.client.get(f"/datasources/{datasource_id}")
        self.assertEqual(resp.status_code, 404)

    # -- Delete not found -------------------------------------------------

    def test_delete_datasource_not_found(self) -> None:
        resp = self.client.delete("/datasources/ds_nonexistent")
        self.assertEqual(resp.status_code, 404)

    # -- Get not found ----------------------------------------------------

    def test_get_datasource_404(self) -> None:
        resp = self.client.get("/datasources/ds_nonexistent")
        self.assertEqual(resp.status_code, 404)

    # -- Update not found -------------------------------------------------

    def test_update_datasource_not_found(self) -> None:
        resp = self.client.put("/datasources/ds_nonexistent", json={"display_name": "x"})
        self.assertEqual(resp.status_code, 404)

    # -- OpenAPI uses explicit response model -----------------------------

    def test_datasource_openapi_uses_explicit_response_model(self) -> None:
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        schemas = payload["components"]["schemas"]
        self.assertIn("DatasourceResponse", schemas)

        ds_get = payload["paths"]["/datasources"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(ds_get["items"]["$ref"], "#/components/schemas/DatasourceResponse")

        ds_post = payload["paths"]["/datasources"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(ds_post["$ref"], "#/components/schemas/DatasourceResponse")


# ---------------------------------------------------------------------------
# Table preview tests via /datasources API
# ---------------------------------------------------------------------------


class DatasourcePreviewTests(unittest.TestCase):
    """Tests for the table preview endpoint via /datasources."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "preview_test.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "preview_test.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path)),
            headers={"X-Marivo-User": "test_user"},
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_datasource(self, name: str) -> dict:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), name),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_browse_catalog_schemas(self) -> None:
        ds = self._create_datasource("Browse Schema DS")

        resp = self.client.get(f"/datasources/{ds['datasource_id']}/browse/schemas")

        self.assertEqual(resp.status_code, 200)
        schemas = resp.json()
        self.assertGreater(len(schemas), 0)
        self.assertEqual(schemas[0]["schema_name"], "analytics")

    def test_browse_catalog_tables(self) -> None:
        ds = self._create_datasource("Browse Table DS")

        resp = self.client.get(
            f"/datasources/{ds['datasource_id']}/browse/tables",
            params={"schema_name": "analytics"},
        )

        self.assertEqual(resp.status_code, 200)
        names = {item["table_name"] for item in resp.json()}
        self.assertIn("watch_events", names)

    def test_browse_columns_live_without_sync(self) -> None:
        ds = self._create_datasource("Browse Columns DS")

        resp = self.client.get(
            f"/datasources/{ds['datasource_id']}/browse/columns",
            params={"schema_name": "analytics", "table_name": "watch_events"},
        )

        self.assertEqual(resp.status_code, 200)
        names = {item["name"] for item in resp.json()}
        self.assertIn("user_id", names)

    def test_preview_table_basic(self) -> None:
        ds = self._create_datasource("Preview Basic DS")
        resp = self.client.get(
            f"/datasources/{ds['datasource_id']}/catalog/preview",
            params={"schema": "analytics", "table": "watch_events", "limit": 10},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["schema_name"], "analytics")
        self.assertEqual(result["table_name"], "watch_events")
        self.assertLessEqual(result["row_count"], 10)
        self.assertTrue(len(result["columns"]) > 0)
        self.assertTrue(len(result["rows"]) > 0)

    def test_preview_source_not_found(self) -> None:
        resp = self.client.get(
            "/datasources/ds_nonexistent/catalog/preview",
            params={"schema": "analytics", "table": "watch_events"},
        )
        self.assertEqual(resp.status_code, 404)
