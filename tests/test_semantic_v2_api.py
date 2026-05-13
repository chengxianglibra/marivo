"""Tests for Semantic V2 API endpoints — OSI-aligned semantic layer routes."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marivo.adapters.server.semantic_service_adapter import (
    SemanticServiceAdapter as SemanticModelV2Service,
)
from marivo.datasources import DatasourceService
from marivo.transports.http.models.osi import OSI_SPEC_VERSION
from marivo.transports.http.semantic_v2 import router as semantic_v2_router
from tests.shared_fixtures import ManagedSQLiteMetadataStore, make_temp_metadata_store


class _ManagedTestClient(TestClient):
    def __init__(self, app: FastAPI, store: ManagedSQLiteMetadataStore) -> None:
        super().__init__(app)
        self._store: ManagedSQLiteMetadataStore | None = store

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._close_store()

    def _close_store(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def __del__(self) -> None:
        self._close_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ACTIVE_STORE: ManagedSQLiteMetadataStore | None = None


def _make_app() -> TestClient:
    """Create a FastAPI app with semantic_v2 router and an in-memory service."""
    import uuid

    from marivo.transports.http.middleware import UserIdentityMiddleware

    store = make_temp_metadata_store(prefix=f"marivo_v2_api_{uuid.uuid4().hex[:8]}_")
    global _ACTIVE_STORE
    _ACTIVE_STORE = store
    datasource_service = DatasourceService(store)
    service = SemanticModelV2Service(store, datasource_service=datasource_service)

    app = FastAPI()
    app.add_middleware(UserIdentityMiddleware)
    app.include_router(semantic_v2_router)
    app.state.semantic_v2_service = service
    app.state.datasource_service = datasource_service

    return _ManagedTestClient(app, store)


def _make_model_dict(name: str = "test_model") -> dict:
    """Build a minimal OSI-conformant model dict for testing."""
    return {
        "name": name,
        "datasets": [
            {
                "name": "orders",
                "source": "analytics.orders",
                "primary_key": ["order_id"],
                "custom_extensions": [
                    {
                        "vendor_name": "MARIVO",
                        "data": {"datasource_id": "ds_001"},
                    }
                ],
                "fields": [
                    {
                        "name": "order_id",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]
                        },
                    },
                    {
                        "name": "order_date",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "order_date"}]
                        },
                        "dimension": {"is_time": True},
                    },
                    {
                        "name": "amount",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]
                        },
                    },
                ],
            }
        ],
    }


def _make_dataset_dict(
    name: str = "customers",
    source: str = "analytics.customers",
) -> dict:
    return {
        "name": name,
        "source": source,
        "primary_key": ["customer_id"],
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": {"datasource_id": "ds_001"},
            }
        ],
        "fields": [
            {
                "name": "customer_id",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]},
            },
            {
                "name": "customer_name",
                "expression": {
                    "dialects": [{"dialect": "ANSI_SQL", "expression": "customer_name"}]
                },
            },
        ],
    }


# ---------------------------------------------------------------------------
# POST /semantic-models — create semantic model
# ---------------------------------------------------------------------------


class TestCreateSemanticModelAPI(unittest.TestCase):
    def test_create_returns_osi_envelope(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertIsInstance(body["semantic_model"], list)
        self.assertEqual(len(body["semantic_model"]), 1)
        self.assertEqual(body["semantic_model"][0]["name"], "test_model")

    def test_create_model_with_datasets(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        body = resp.json()
        model = body["semantic_model"][0]
        self.assertEqual(len(model["datasets"]), 1)
        self.assertEqual(model["datasets"][0]["name"], "orders")

    def test_create_private_model(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="private_model"),
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["semantic_model"][0]["name"], "private_model")

    def test_create_private_without_owner_returns_403(self) -> None:
        client = _make_app()
        resp = client.post("/semantic-models", json=_make_model_dict())
        self.assertEqual(resp.status_code, 403)

    def test_create_dataset_requires_marivo_datasource_id(self) -> None:
        client = _make_app()
        model_data = _make_model_dict()
        model_data["datasets"][0]["custom_extensions"] = []

        resp = client.post(
            "/semantic-models",
            json=model_data,
            headers={"X-Marivo-User": "alice"},
        )

        self.assertEqual(resp.status_code, 422)
        self.assertIn("datasource_id", resp.json()["detail"])

    def test_create_dataset_requires_non_empty_source_fqn(self) -> None:
        client = _make_app()
        model_data = _make_model_dict()
        model_data["datasets"][0]["source"] = ""

        resp = client.post(
            "/semantic-models",
            json=model_data,
            headers={"X-Marivo-User": "alice"},
        )

        self.assertEqual(resp.status_code, 422)
        self.assertIn("source", resp.json()["detail"])


# ---------------------------------------------------------------------------
# GET /semantic-models — list semantic models
# ---------------------------------------------------------------------------


class TestListSemanticModelsAPI(unittest.TestCase):
    def test_list_empty(self) -> None:
        client = _make_app()
        resp = client.get("/semantic-models")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual(body["semantic_model"], [])

    def test_list_after_create(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="model_a"),
            headers={"X-Marivo-User": "alice"},
        )
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="model_b"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get("/semantic-models", params={"requesting_user": "alice"})
        body = resp.json()
        names = [m["name"] for m in body["semantic_model"]]
        self.assertIn("model_a", names)
        self.assertIn("model_b", names)

    def test_list_with_requesting_user(self) -> None:
        client = _make_app()
        # Import creates a private working copy for the transport-injected user.
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "imported_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post(
            "/semantic-models/import",
            json=doc,
            headers={"X-Marivo-User": "alice"},
        )
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="private_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get("/semantic-models")
        names = [m["name"] for m in resp.json()["semantic_model"]]
        self.assertNotIn("imported_model", names)
        self.assertNotIn("private_model", names)
        resp = client.get("/semantic-models", params={"requesting_user": "alice"})
        names = [m["name"] for m in resp.json()["semantic_model"]]
        self.assertIn("imported_model", names)
        self.assertIn("private_model", names)
        resp = client.get("/semantic-models", params={"requesting_user": "bob"})
        names = [m["name"] for m in resp.json()["semantic_model"]]
        self.assertNotIn("imported_model", names)
        self.assertNotIn("private_model", names)


# ---------------------------------------------------------------------------
# GET /semantic-models/{model} — get semantic model
# ---------------------------------------------------------------------------


class TestGetSemanticModelAPI(unittest.TestCase):
    def test_get_existing(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get("/semantic-models/test_model", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual(body["semantic_model"][0]["name"], "test_model")

    def test_get_nonexistent_returns_404(self) -> None:
        client = _make_app()
        resp = client.get("/semantic-models/nonexistent")
        self.assertEqual(resp.status_code, 404)

    def test_get_private_model_with_requesting_user(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="private_model"),
            headers={"X-Marivo-User": "alice"},
        )
        # Owner can see the model
        resp = client.get("/semantic-models/private_model", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["semantic_model"][0]["name"], "private_model")
        # Non-owner gets 404
        resp = client.get("/semantic-models/private_model", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 404)
        # No requesting_user gets 404
        resp = client.get("/semantic-models/private_model")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# DELETE /semantic-models/{model} — delete semantic model
# ---------------------------------------------------------------------------


class TestDeleteSemanticModelAPI(unittest.TestCase):
    def test_delete_then_get_404(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.delete("/semantic-models/test_model", headers={"X-Marivo-User": "alice"})
        self.assertEqual(resp.status_code, 204)
        resp = client.get("/semantic-models/test_model", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_returns_404(self) -> None:
        client = _make_app()
        resp = client.delete("/semantic-models/nonexistent", headers={"X-Marivo-User": "alice"})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# POST /semantic-models/{model}/datasets — create dataset
# ---------------------------------------------------------------------------


class TestCreateDatasetAPI(unittest.TestCase):
    def test_create_dataset_in_model(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.post(
            "/semantic-models/test_model/datasets",
            json=_make_dataset_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["name"], "customers")
        self.assertEqual(len(body["fields"]), 2)

    def test_create_dataset_in_nonexistent_model(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models/nonexistent/datasets",
            json=_make_dataset_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Field CRUD
# ---------------------------------------------------------------------------


class TestFieldCrudAPI(unittest.TestCase):
    def test_create_list_get_update_delete_field(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="field_model"),
            headers={"X-Marivo-User": "alice"},
        )

        field = {
            "name": "status",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
            "label": "Order Status",
        }
        resp = client.post(
            "/semantic-models/field_model/datasets/orders/fields",
            json=field,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "status")

        resp = client.get(
            "/semantic-models/field_model/datasets/orders/fields",
            params={"requesting_user": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status", [item["name"] for item in resp.json()])

        resp = client.get(
            "/semantic-models/field_model/datasets/orders/fields/status",
            params={"requesting_user": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["label"], "Order Status")

        resp = client.patch(
            "/semantic-models/field_model/datasets/orders/fields/status",
            json={"description": "Lifecycle status"},
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["description"], "Lifecycle status")

        resp = client.delete(
            "/semantic-models/field_model/datasets/orders/fields/status",
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 204)

    def test_field_write_routes_use_header_identity_not_requesting_user_param(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="field_owner_model"),
            headers={"X-Marivo-User": "alice"},
        )

        field = {
            "name": "status",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
            "description": "Alice field",
        }
        resp = client.post(
            "/semantic-models/field_owner_model/datasets/orders/fields",
            json=field,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)

        resp = client.patch(
            "/semantic-models/field_owner_model/datasets/orders/fields/status"
            "?requesting_user=alice",
            json={"description": "Bob hijack"},
            headers={"X-Marivo-User": "bob"},
        )
        self.assertEqual(resp.status_code, 404)

        resp = client.get(
            "/semantic-models/field_owner_model/datasets/orders/fields/status",
            params={"requesting_user": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["description"], "Alice field")


# ---------------------------------------------------------------------------
# GET /semantic-models/{model}/readiness — get readiness
# ---------------------------------------------------------------------------


class TestReadinessAPI(unittest.TestCase):
    def test_get_readiness(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get(
            "/semantic-models/test_model/readiness", params={"requesting_user": "alice"}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertIsInstance(body["blockers"], list)
        self.assertEqual(body["semantic_version_id"], None)
        self.assertEqual(body["evaluated_semantic_version_id"], None)

    def test_readiness_reports_missing_datasource(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )

        resp = client.get(
            "/semantic-models/test_model/readiness", params={"requesting_user": "alice"}
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertEqual(body["blockers"][0]["code"], "datasource_not_found")

    def test_readiness_nonexistent_model(self) -> None:
        client = _make_app()
        resp = client.get("/semantic-models/nonexistent/readiness")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# POST /semantic-models/import — import OSI document
# ---------------------------------------------------------------------------


class TestImportOSIDocumentAPI(unittest.TestCase):
    def test_import_osi_document(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "imported_model",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "sale_id"}
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        resp = client.post(
            "/semantic-models/import",
            json=doc,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["models"][0]["name"], "imported_model")
        self.assertTrue(body["models"][0]["created"])
        self.assertEqual(body["models"][0]["datasets"]["created"], 1)
        self.assertEqual(body["models"][0]["fields"]["created"], 1)

        get_resp = client.get(
            "/semantic-models/imported_model",
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["semantic_model"][0]["name"], "imported_model")

    def test_import_requires_x_marivo_user(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "imported_model",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "sale_id"}
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        resp = client.post("/semantic-models/import", json=doc)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("User identity", resp.text)


class TestPerModelImport(unittest.TestCase):
    def test_import_updates_only_included_models(self) -> None:
        """Import model A should not affect existing model B."""
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                },
                {
                    "name": "growth",
                    "datasets": [
                        {
                            "name": "events",
                            "source": "analytics.events",
                            "primary_key": ["event_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "event_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "event_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        # Second import: only commerce updated
        doc2 = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders_v2",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                                {
                                    "name": "amount",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "amount"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        resp = client.post(
            "/semantic-models/import",
            json=doc2,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        # commerce updated
        commerce = client.get(
            "/semantic-models/commerce",
            headers={"X-Marivo-User": "alice"},
        ).json()["semantic_model"][0]
        self.assertEqual(commerce["datasets"][0]["source"], "analytics.orders_v2")
        self.assertEqual(len(commerce["datasets"][0]["fields"]), 2)
        # growth unchanged
        growth = client.get(
            "/semantic-models/growth",
            headers={"X-Marivo-User": "alice"},
        ).json()["semantic_model"][0]
        self.assertEqual(growth["datasets"][0]["source"], "analytics.events")

    def test_reimport_updates_current_model_state(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        updated_doc = {
            **doc,
            "semantic_model": [
                {
                    **doc["semantic_model"][0],
                    "datasets": [
                        {
                            **doc["semantic_model"][0]["datasets"][0],
                            "source": "analytics.orders_v2",
                        }
                    ],
                }
            ],
        }
        client.post(
            "/semantic-models/import",
            json=updated_doc,
            headers={"X-Marivo-User": "alice"},
        )
        model = client.get(
            "/semantic-models/commerce",
            headers={"X-Marivo-User": "alice"},
        ).json()["semantic_model"][0]
        self.assertEqual(model["datasets"][0]["source"], "analytics.orders_v2")

    def test_import_merges_model_with_same_name_as_private(self) -> None:
        """Importing a model with the same name merges Alice's private copy."""
        client = _make_app()
        # Create private model first
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="commerce"),
            headers={"X-Marivo-User": "alice"},
        )
        # Import private working-copy update with same name
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        resp = client.post(
            "/semantic-models/import",
            json=doc,
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        models = client.get("/semantic-models", params={"requesting_user": "alice"}).json()[
            "semantic_model"
        ]
        commerce_models = [m for m in models if m["name"] == "commerce"]
        self.assertEqual(len(commerce_models), 1)

    def test_import_new_model_stores_current_state(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        model = client.get(
            "/semantic-models/commerce",
            headers={"X-Marivo-User": "alice"},
        ).json()["semantic_model"][0]
        self.assertEqual(model["name"], "commerce")
        self.assertEqual(model["datasets"][0]["source"], "analytics.orders")


# ---------------------------------------------------------------------------
# Visibility guard on model-level CRUD writes
# ---------------------------------------------------------------------------


class TestVisibilityGuardOnModelWrites(unittest.TestCase):
    def test_create_public_model_via_crud_returns_403(self) -> None:
        client = _make_app()
        resp = client.post("/semantic-models", json=_make_model_dict(name="new_public"))
        self.assertEqual(resp.status_code, 403)

    def test_create_private_model_via_crud_succeeds(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="new_private"),
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_update_imported_private_model_without_identity_returns_422(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "official_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        resp = client.put("/semantic-models/official_model", json={"description": "new"})
        self.assertEqual(resp.status_code, 422)

    def test_update_private_model_succeeds(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.put(
            "/semantic-models/priv_model",
            json={"description": "new"},
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_delete_imported_private_model_without_identity_returns_422(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "official_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        resp = client.delete("/semantic-models/official_model")
        self.assertEqual(resp.status_code, 422)

    def test_delete_private_model_succeeds(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.delete("/semantic-models/priv_model", headers={"X-Marivo-User": "alice"})
        self.assertEqual(resp.status_code, 204)


# ---------------------------------------------------------------------------
# Visibility guard on sub-entity CRUD writes
# ---------------------------------------------------------------------------


class TestVisibilityGuardOnSubEntityWrites(unittest.TestCase):
    def _create_imported_private_model(self, client: TestClient) -> None:
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "official_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})

    def test_create_dataset_in_imported_private_model_without_identity_returns_422(self) -> None:
        client = _make_app()
        self._create_imported_private_model(client)
        resp = client.post("/semantic-models/official_model/datasets", json=_make_dataset_dict())
        self.assertEqual(resp.status_code, 422)

    def test_update_dataset_in_imported_private_model_without_identity_returns_422(self) -> None:
        client = _make_app()
        self._create_imported_private_model(client)
        resp = client.put(
            "/semantic-models/official_model/datasets/orders", json={"description": "new"}
        )
        self.assertEqual(resp.status_code, 422)

    def test_delete_dataset_in_imported_private_model_without_identity_returns_422(self) -> None:
        client = _make_app()
        self._create_imported_private_model(client)
        resp = client.delete("/semantic-models/official_model/datasets/orders")
        self.assertEqual(resp.status_code, 422)

    def test_create_relationship_in_imported_private_model_without_identity_returns_422(
        self,
    ) -> None:
        client = _make_app()
        self._create_imported_private_model(client)
        rel = {
            "name": "r",
            "from": "orders",
            "to": "orders",
            "from_columns": ["order_id"],
            "to_columns": ["order_id"],
        }
        resp = client.post("/semantic-models/official_model/relationships", json=rel)
        self.assertEqual(resp.status_code, 422)

    def test_create_metric_in_imported_private_model_without_identity_returns_422(self) -> None:
        client = _make_app()
        self._create_imported_private_model(client)
        metric = {
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(order_id)"}]},
        }
        resp = client.post("/semantic-models/official_model/metrics", json=metric)
        self.assertEqual(resp.status_code, 422)


# ---------------------------------------------------------------------------
# Same-name validation for private model creation
# ---------------------------------------------------------------------------


class TestSameNameValidation(unittest.TestCase):
    def test_duplicate_private_name_same_owner_returns_409(self) -> None:
        """Two private models with same name for same owner is not allowed."""
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore"),
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 409)

    def test_duplicate_private_name_different_owner_succeeds(self) -> None:
        """Two private models with same name for different owners is allowed."""
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore"),
            headers={"X-Marivo-User": "bob"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_import_same_name_as_existing_private_merges(self) -> None:
        """Import with the same name as an existing private model merges that model."""
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "commerce",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="commerce"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["models"][0]["updated"])


# ---------------------------------------------------------------------------
# Sub-entity read visibility
# ---------------------------------------------------------------------------


class TestSubEntityReadVisibility(unittest.TestCase):
    def test_get_dataset_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get(
            "/semantic-models/priv_model/datasets/orders", params={"requesting_user": "alice"}
        )
        self.assertEqual(resp.status_code, 200)
        resp = client.get(
            "/semantic-models/priv_model/datasets/orders", params={"requesting_user": "bob"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_list_datasets_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get(
            "/semantic-models/priv_model/datasets", params={"requesting_user": "alice"}
        )
        self.assertEqual(resp.status_code, 200)
        resp = client.get("/semantic-models/priv_model/datasets", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 404)

    def test_get_relationship_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        # Add a relationship first
        rel = {
            "name": "self_rel",
            "from": "orders",
            "to": "orders",
            "from_columns": ["order_id"],
            "to_columns": ["order_id"],
        }
        client.post(
            "/semantic-models/priv_model/relationships",
            json=rel,
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get(
            "/semantic-models/priv_model/relationships/self_rel",
            params={"requesting_user": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        resp = client.get(
            "/semantic-models/priv_model/relationships/self_rel", params={"requesting_user": "bob"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_list_relationships_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get(
            "/semantic-models/priv_model/relationships", params={"requesting_user": "bob"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_get_metric_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        # Add a metric first
        metric = {
            "name": "total_orders",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(order_id)"}]},
        }
        client.post(
            "/semantic-models/priv_model/metrics",
            json=metric,
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get(
            "/semantic-models/priv_model/metrics/total_orders", params={"requesting_user": "alice"}
        )
        self.assertEqual(resp.status_code, 200)
        resp = client.get(
            "/semantic-models/priv_model/metrics/total_orders", params={"requesting_user": "bob"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_list_metrics_from_private_model_requires_owner(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model"),
            headers={"X-Marivo-User": "alice"},
        )
        resp = client.get("/semantic-models/priv_model/metrics", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 404)

    def test_imported_private_model_sub_entities_visible_to_owner_only(self) -> None:
        """Sub-entity reads on imported private models require the owner."""
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "public_model",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                },
                            ],
                        }
                    ],
                }
            ],
        }
        client.post("/semantic-models/import", json=doc, headers={"X-Marivo-User": "alice"})
        resp = client.get(
            "/semantic-models/public_model/datasets/orders",
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        resp = client.get("/semantic-models/public_model/datasets")
        self.assertEqual(resp.status_code, 404)
        resp = client.get(
            "/semantic-models/public_model/datasets/orders", params={"requesting_user": "bob"}
        )
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Same-name private models are isolated by owner.
# ---------------------------------------------------------------------------


class TestSameNameShadowingAPI(unittest.TestCase):
    """API-level tests that same-name private models are owner-scoped."""

    def _import_private_model(
        self,
        client: TestClient,
        name: str = "commerce",
        *,
        user: str = "alice",
        description: str = "imported model",
    ) -> None:
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": name,
                    "description": description,
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": {"datasource_id": "ds_001"},
                                }
                            ],
                            "fields": [
                                {
                                    "name": "order_id",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "order_id"}
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        resp = client.post(
            "/semantic-models/import",
            json=doc,
            headers={"X-Marivo-User": user},
        )
        self.assertEqual(resp.status_code, 200)

    def test_get_model_returns_imported_private_for_owner(self) -> None:
        client = _make_app()
        self._import_private_model(client, "commerce", user="alice", description="alice import")
        resp = client.get("/semantic-models/commerce", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["semantic_model"][0]["description"], "alice import")

    def test_get_model_returns_404_for_non_owner(self) -> None:
        client = _make_app()
        self._import_private_model(client, "commerce", user="alice")
        resp = client.get("/semantic-models/commerce", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 404)

    def test_update_private_model_finds_correct_row(self) -> None:
        client = _make_app()
        self._import_private_model(client, "commerce", user="alice")
        self._import_private_model(client, "commerce", user="bob", description="bob import")
        resp = client.put(
            "/semantic-models/commerce",
            json={"description": "alice's version"},
            headers={"X-Marivo-User": "alice"},
        )
        self.assertEqual(resp.status_code, 200)
        bob = client.get("/semantic-models/commerce", params={"requesting_user": "bob"})
        self.assertEqual(bob.json()["semantic_model"][0]["description"], "bob import")

    def test_delete_private_model_leaves_other_owners_private_model_intact(self) -> None:
        client = _make_app()
        self._import_private_model(client, "commerce", user="alice")
        self._import_private_model(client, "commerce", user="bob", description="bob import")
        resp = client.delete("/semantic-models/commerce", headers={"X-Marivo-User": "alice"})
        self.assertEqual(resp.status_code, 204)
        resp = client.get("/semantic-models/commerce", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 200)

    def test_readiness_respects_visibility(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(),
            headers={"X-Marivo-User": "alice"},
        )
        # Non-owner should get 404
        resp = client.get(
            "/semantic-models/test_model/readiness", params={"requesting_user": "bob"}
        )
        self.assertEqual(resp.status_code, 404)
        # Owner should succeed
        resp = client.get(
            "/semantic-models/test_model/readiness", params={"requesting_user": "alice"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_two_private_models_same_name_different_owners(self) -> None:
        client = _make_app()
        alice = _make_model_dict(name="commerce")
        alice["description"] = "alice model"
        client.post(
            "/semantic-models",
            json=alice,
            headers={"X-Marivo-User": "alice"},
        )
        bob = _make_model_dict(name="commerce")
        bob["description"] = "bob model"
        client.post(
            "/semantic-models",
            json=bob,
            headers={"X-Marivo-User": "bob"},
        )
        # alice sees alice's model
        resp = client.get("/semantic-models/commerce", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["semantic_model"][0]["description"], "alice model")
        # bob sees bob's model
        resp = client.get("/semantic-models/commerce", params={"requesting_user": "bob"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["semantic_model"][0]["description"], "bob model")


class TestImportExportTargetStateAPI(unittest.TestCase):
    def test_import_returns_report_not_osi_document(self) -> None:
        client = _make_app()
        resp = client.post(
            "/semantic-models/import",
            json={
                "version": OSI_SPEC_VERSION,
                "semantic_model": [_make_model_dict(name="report_model")],
            },
            headers={"X-Marivo-User": "alice"},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("models", body)
        self.assertNotIn("semantic_model", body)
        self.assertEqual(body["models"][0]["name"], "report_model")

    def test_export_returns_private_osi_document(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models/import",
            json={
                "version": OSI_SPEC_VERSION,
                "semantic_model": [_make_model_dict(name="export_model")],
            },
            headers={"X-Marivo-User": "alice"},
        )

        resp = client.get("/semantic-models/export", headers={"X-Marivo-User": "alice"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual([model["name"] for model in body["semantic_model"]], ["export_model"])
