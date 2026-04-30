from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.datasources import DatasourceService
from app.main import create_app
from app.storage.sqlite_metadata import SQLiteMetadataStore
from tests.shared_fixtures import get_seeded_duckdb_path


def _build_duckdb_datasource_payload(path: str, display_name: str, mode: str = "selected") -> dict:
    return {
        "datasource_type": "duckdb",
        "display_name": display_name,
        "connection": {"path": path, "catalog": "main"},
        "sync_mode": mode,
    }


def _build_trino_datasource_payload(
    display_name: str,
    *,
    host: str = "trino.example.com",
    catalog: str = "iceberg",
    user: str | None = "marivo",
    allow_identity_reuse: bool = False,
) -> dict:
    connection: dict = {"host": host, "catalog": catalog}
    if user is not None:
        connection["user"] = user
    return {
        "datasource_type": "trino",
        "display_name": display_name,
        "connection": connection,
        "sync_mode": "selected",
        "policy": {"allow_identity_reuse": allow_identity_reuse},
    }


# ---------------------------------------------------------------------------
# Unit tests for DatasourceService
# ---------------------------------------------------------------------------


class DatasourceServiceUnitTests(unittest.TestCase):
    """Unit tests for DatasourceService using SQLiteMetadataStore directly."""

    @classmethod
    def setUpClass(cls) -> None:
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
        self.assertEqual(ds["sync_mode"], "selected")
        self.assertEqual(ds["policy"]["allow_live_browse"], True)
        self.assertEqual(ds["policy"]["allow_sync"], True)
        self.assertNotIn("allow_identity_reuse", ds["policy"])
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
        self.assertEqual(ds["policy"]["allow_identity_reuse"], False)
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

    # -- Update policy (including allow_identity_reuse) -------------------

    def test_update_datasource_policy(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Policy Update DS",
            connection={"host": "trino.example.com"},
        )
        updated = self.service.update_datasource(
            ds["datasource_id"],
            policy={"allow_identity_reuse": True, "allow_live_browse": False},
        )
        self.assertEqual(updated["policy"]["allow_identity_reuse"], True)
        self.assertEqual(updated["policy"]["allow_live_browse"], False)

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

    # -- DuckDB: allow_identity_reuse silently ignored --------------------

    def test_duckdb_allow_identity_reuse_silently_ignored(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="DuckDB Identity DS",
            connection={"path": "/tmp/test.duckdb"},
            policy={"allow_identity_reuse": True},
        )
        self.assertNotIn("allow_identity_reuse", ds["policy"])

    # -- Trino: allow_identity_reuse=false, no session_user -> session_user_missing

    def test_trino_no_identity_reuse_no_session_user_raises(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Trino No User DS",
            connection={"host": "trino.example.com", "catalog": "iceberg"},
            policy={"allow_identity_reuse": False},
        )
        with self.assertRaises(ValueError) as ctx:
            self.service.build_analytics_engine(ds["datasource_id"])
        self.assertIn("session_user_missing", str(ctx.exception))

    # -- Trino: allow_identity_reuse=true, no session_user -> uses connection.user

    def test_trino_identity_reuse_uses_connection_user(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Trino Identity Reuse DS",
            connection={"host": "trino.example.com", "catalog": "iceberg", "user": "svc_marivo"},
            policy={"allow_identity_reuse": True},
        )
        engine = self.service.build_analytics_engine(ds["datasource_id"])
        self.assertEqual(engine.user, "svc_marivo")

    # -- Trino: session_user provided ------------------------------------

    def test_trino_session_user_provided(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="trino",
            display_name="Trino Session User DS",
            connection={"host": "trino.example.com", "catalog": "iceberg", "user": "svc_marivo"},
        )
        # When connection.user is set, it is used directly
        engine = self.service.build_analytics_engine(ds["datasource_id"])
        self.assertEqual(engine.user, "svc_marivo")

    # -- Sync mode validation ---------------------------------------------

    def test_sync_mode_validation(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Sync Mode DS",
            connection={"path": "/tmp/test.duckdb"},
            sync_mode="all",
        )
        self.assertEqual(ds["sync_mode"], "all")

    # -- Datasource type validation ----------------------------------------

    def test_datasource_type_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.service.register_datasource(
                datasource_type="spark",
                display_name="Invalid Type DS",
                connection={},
            )

    # -- Ensure idempotent ------------------------------------------------

    def test_ensure_datasource_idempotent(self) -> None:
        ds1 = self.service.ensure_datasource(
            datasource_type="duckdb",
            display_name="Idempotent DS",
            connection={"path": "/tmp/idem.duckdb"},
        )
        ds2 = self.service.ensure_datasource(
            datasource_type="duckdb",
            display_name="Idempotent DS",
            connection={"path": "/tmp/idem.duckdb"},
        )
        self.assertEqual(ds1["datasource_id"], ds2["datasource_id"])

    # -- Validate datasource ----------------------------------------------

    def test_validate_datasource_reports_invalid_connection(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Bad Connection DS",
            connection={},
        )
        validation = self.service.validate_datasource(ds["datasource_id"])
        self.assertFalse(validation["is_valid"])
        self.assertEqual(validation["failure_code"], "datasource_invalid_connection")

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

    # -- Datasource degrades malformed stored policy_json -----------------

    def test_datasource_degrades_malformed_stored_policy_json(self) -> None:
        ds = self.service.register_datasource(
            datasource_type="duckdb",
            display_name="Bad Policy JSON DS",
            connection={"path": "/tmp/test.duckdb"},
        )
        self.metadata.execute(
            "UPDATE datasources SET policy_json = ? WHERE datasource_id = ?",
            ["{bad-json", ds["datasource_id"]],
        )
        fetched = self.service.get_datasource(ds["datasource_id"])
        self.assertEqual(fetched["readiness_status"], "not_ready")
        self.assertEqual(fetched["failure_code"], "datasource_invalid_policy")

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
        cls.client = TestClient(create_app(cls.db_path, metadata_store=cls.metadata_store))

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
        self.assertEqual(ds["sync_mode"], "selected")
        self.assertEqual(ds["policy"]["allow_live_browse"], True)
        self.assertEqual(ds["policy"]["allow_sync"], True)
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
        self.assertEqual(ds["policy"]["allow_identity_reuse"], False)
        self.assertEqual(ds["readiness_status"], "ready")
        self.assertIsNone(ds["failure_code"])

    # -- Register rejects unsupported type --------------------------------

    def test_register_datasource_rejects_unsupported_type(self) -> None:
        resp = self.client.post(
            "/datasources",
            json={
                "datasource_type": "mysql",
                "display_name": "Unsupported DS",
                "connection": {},
            },
        )
        self.assertEqual(resp.status_code, 422)

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
        self.assertEqual(resp.json()["sync_mode"], "selected")
        self.assertEqual(resp.json()["policy"]["allow_sync"], True)
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

    # -- Update policy ----------------------------------------------------

    def test_update_datasource_policy_via_api(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_trino_datasource_payload("Policy Update API DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        resp = self.client.put(
            f"/datasources/{datasource_id}",
            json={"policy": {"allow_identity_reuse": True, "allow_live_browse": False}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["policy"]["allow_identity_reuse"], True)
        self.assertEqual(resp.json()["policy"]["allow_live_browse"], False)

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
        self.assertEqual(resp.json()["status"], "deleted")
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

    # -- Delete blocked by typed binding ----------------------------------

    def test_delete_datasource_blocked_by_typed_binding(self) -> None:
        """DELETE returns 409 when typed bindings reference source objects."""
        from tests.semantic_test_helpers import create_typed_metric, create_typed_metric_binding

        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Bound DS"),
        )
        datasource_id = resp.json()["datasource_id"]
        # Sync to get source_objects
        self.client.post(
            f"/datasources/{datasource_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        self.client.post(f"/datasources/{datasource_id}/sync")
        objects = self.client.get(f"/datasources/{datasource_id}/objects?type=table").json()
        object_id = objects[0]["object_id"]
        metric = create_typed_metric(
            self.client,
            name="ds_test_metric",
            display_name="DS Test Metric",
            definition_sql="COUNT(*)",
            dimensions=["event_date"],
        )
        create_typed_metric_binding(
            self.client,
            metric_ref="metric.ds_test_metric",
            object_id=object_id,
            carrier_locator=str(objects[0]["fqn"]),
        )

        resp = self.client.delete(f"/datasources/{datasource_id}")
        self.assertEqual(resp.status_code, 409)
        detail = resp.json()["detail"]
        self.assertIn("binding", detail["message"].lower())
        self.assertIn("binding.ds_test_metric_primary", detail["dependencies"][0])

    # -- OpenAPI uses explicit response model -----------------------------

    def test_datasource_openapi_uses_explicit_response_model(self) -> None:
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        schemas = payload["components"]["schemas"]
        self.assertIn("DatasourceResponse", schemas)
        self.assertIn("DatasourcePolicyResponse", schemas)

        ds_get = payload["paths"]["/datasources"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(ds_get["items"]["$ref"], "#/components/schemas/DatasourceResponse")

        ds_post = payload["paths"]["/datasources"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        self.assertEqual(ds_post["$ref"], "#/components/schemas/DatasourceResponse")


# ---------------------------------------------------------------------------
# Sync mode and selection tests via API
# ---------------------------------------------------------------------------


class DatasourceSyncModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_ds_sync_mode.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "test_ds_sync_mode.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path))
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _create_datasource(self, name: str, mode: str = "selected") -> dict:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), name, mode=mode),
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_sync_mode_none_returns_400(self) -> None:
        ds = self._create_datasource("None Mode DS", mode="none")
        datasource_id = ds["datasource_id"]

        resp = self.client.post(f"/datasources/{datasource_id}/sync")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("disabled", resp.json()["detail"].lower())

    def test_sync_mode_selected_no_selections_returns_400(self) -> None:
        ds = self._create_datasource("Selected No Sel DS")
        datasource_id = ds["datasource_id"]
        store = self.client.app.state.metadata_store
        store.execute(
            "UPDATE datasources SET sync_mode = 'selected' WHERE datasource_id = ?",
            [datasource_id],
        )

        resp = self.client.post(f"/datasources/{datasource_id}/sync")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("no sync selections", resp.json()["detail"].lower())

    def test_selection_crud(self) -> None:
        ds = self._create_datasource("Selection CRUD DS")
        datasource_id = ds["datasource_id"]

        # Initially empty
        resp = self.client.get(f"/datasources/{datasource_id}/sync/selections")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

        # Add selections
        resp = self.client.post(
            f"/datasources/{datasource_id}/sync/selections",
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
        resp = self.client.get(f"/datasources/{datasource_id}/sync/selections")
        self.assertEqual(len(resp.json()), 2)

        # Remove one
        sel_id = sels[0]["selection_id"]
        resp = self.client.delete(f"/datasources/{datasource_id}/sync/selections/{sel_id}")
        self.assertEqual(resp.status_code, 200)

        resp = self.client.get(f"/datasources/{datasource_id}/sync/selections")
        self.assertEqual(len(resp.json()), 1)

        # Clear all
        resp = self.client.delete(f"/datasources/{datasource_id}/sync/selections")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(f"/datasources/{datasource_id}/sync/selections")
        self.assertEqual(resp.json(), [])

    def test_sync_and_browse_objects(self) -> None:
        resp = self.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(self.db_path), "Sync Browse DS"),
        )
        datasource_id = resp.json()["datasource_id"]

        # Add selections for all tables
        self.client.post(
            f"/datasources/{datasource_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        sync_resp = self.client.post(f"/datasources/{datasource_id}/sync")
        self.assertEqual(sync_resp.status_code, 200)
        self.assertEqual(sync_resp.json()["status"], "succeeded")

        # Browse objects
        resp = self.client.get(f"/datasources/{datasource_id}/objects?type=table")
        self.assertEqual(resp.status_code, 200)
        tables = resp.json()
        self.assertEqual(len(tables), 4)
        table_names = {t["native_name"] for t in tables}
        self.assertIn("watch_events", table_names)

    def test_browse_catalog_schemas(self) -> None:
        ds = self._create_datasource("Browse Schema DS")
        datasource_id = ds["datasource_id"]

        resp = self.client.get(f"/datasources/{datasource_id}/browse/schemas")
        self.assertEqual(resp.status_code, 200)
        schemas = resp.json()
        self.assertGreater(len(schemas), 0)
        self.assertEqual(schemas[0]["name"], "analytics")

    def test_browse_catalog_tables(self) -> None:
        ds = self._create_datasource("Browse Table DS")
        datasource_id = ds["datasource_id"]

        resp = self.client.get(f"/datasources/{datasource_id}/browse/tables?schema_name=analytics")
        self.assertEqual(resp.status_code, 200)
        tables = resp.json()
        self.assertEqual(len(tables), 4)
        names = {t["name"] for t in tables}
        self.assertIn("watch_events", names)


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
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path))
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


# ---------------------------------------------------------------------------
# Column properties tests via /datasources API
# ---------------------------------------------------------------------------


class DatasourceColumnPropertiesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "col_props_ds.duckdb"
        cls.meta_path = Path(cls.temp_dir.name) / "col_props_ds.meta.sqlite"
        get_seeded_duckdb_path(cls.db_path)
        cls.client = TestClient(
            create_app(cls.db_path, metadata_store=SQLiteMetadataStore(cls.meta_path))
        )

        # Register and sync a DuckDB datasource
        resp = cls.client.post(
            "/datasources",
            json=_build_duckdb_datasource_payload(str(cls.db_path), "ColProps DS"),
        )
        cls.datasource_id = resp.json()["datasource_id"]
        # Add sync selections for all tables and sync
        cls.client.post(
            f"/datasources/{cls.datasource_id}/sync/selections",
            json={
                "selections": [
                    {"schema_name": "analytics", "table_name": "watch_events"},
                    {"schema_name": "analytics", "table_name": "player_qoe"},
                    {"schema_name": "analytics", "table_name": "ad_events"},
                    {"schema_name": "analytics", "table_name": "recommendation_events"},
                ]
            },
        )
        cls.client.post(f"/datasources/{cls.datasource_id}/sync")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.temp_dir.cleanup()

    def _get_column_object_id(self) -> str:
        resp = self.client.get(
            f"/datasources/{self.datasource_id}/objects", params={"type": "column"}
        )
        objects = resp.json()
        self.assertGreater(len(objects), 0, "No column objects found after sync")
        return objects[0]["object_id"]

    def _get_table_object_id(self) -> str:
        resp = self.client.get(
            f"/datasources/{self.datasource_id}/objects", params={"type": "table"}
        )
        objects = resp.json()
        self.assertGreater(len(objects), 0, "No table objects found after sync")
        return objects[0]["object_id"]

    def test_patch_unit_on_column(self) -> None:
        object_id = self._get_column_object_id()
        resp = self.client.patch(
            f"/datasources/{self.datasource_id}/objects/{object_id}/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["properties"]["unit"], "seconds")
        self.assertIn("data_type", result["properties"])

    def test_patch_unit_400_non_column(self) -> None:
        table_object_id = self._get_table_object_id()
        resp = self.client.patch(
            f"/datasources/{self.datasource_id}/objects/{table_object_id}/properties",
            json={"unit": "seconds"},
        )
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Trino-specific adapter tests (mock-based, no real Trino needed)
# ---------------------------------------------------------------------------


class TrinoCatalogAdapterDatasourceTests(unittest.TestCase):
    """Unit tests for TrinoCatalogAdapter in the datasource context."""

    def _make_cursor(self, rows: list[tuple], columns: list[str]):
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

    def test_adapter_source_type_and_capabilities(self) -> None:
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        adapter = TrinoCatalogAdapter(host="localhost")
        self.assertEqual(adapter.source_type(), "trino")
        caps = adapter.capabilities()
        self.assertTrue(caps.supports_schemas)
        self.assertTrue(caps.supports_column_stats)
        self.assertFalse(caps.supports_partitions)

    def test_build_adapter_trino(self) -> None:
        from app.registry.factories import build_catalog_adapter

        adapter = build_catalog_adapter("trino", {"host": "trino.example.com", "port": 9090})
        from app.adapters.trino_adapter import TrinoCatalogAdapter

        self.assertIsInstance(adapter, TrinoCatalogAdapter)


if __name__ == "__main__":
    unittest.main()
