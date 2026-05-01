"""Tests for Semantic V2 API endpoints — OSI-aligned semantic layer routes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.models.osi import OSI_SPEC_VERSION
from app.api.semantic_v2 import router as semantic_v2_router
from app.semantic_service_v2.service import SemanticModelV2Service
from app.storage.sqlite_metadata import SQLiteMetadataStore


class _TestMetadataStore(SQLiteMetadataStore):
    """A SQLiteMetadataStore subclass that bypasses the conftest-patched initialize().

    The conftest.py monkey-patches SQLiteMetadataStore.initialize() with a
    fast-path that copies a stale cached template.  This subclass overrides
    initialize() to always run the real DDL, ensuring the OSI v2 schema
    is created correctly.
    """

    def initialize(self) -> None:
        """Apply the current schema DDL directly, ignoring the conftest patch."""
        import sqlite3

        from app.storage.schema import METADATA_DDL, metadata_schema_marker_row

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        if self.db_path.exists():
            self.db_path.unlink()

        con = sqlite3.connect(str(self.db_path))
        try:
            for ddl in METADATA_DDL:
                con.execute(ddl)
            marker = metadata_schema_marker_row("sqlite")
            con.execute(
                """
                INSERT OR IGNORE INTO metadata_schema_marker (
                    backend, schema_version, ddl_fingerprint
                ) VALUES (?, ?, ?)
                """,
                [marker["backend"], marker["schema_version"], marker["ddl_fingerprint"]],
            )
            con.commit()
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> TestClient:
    """Create a FastAPI app with semantic_v2 router and an in-memory service."""
    import uuid

    tmp = tempfile.mkdtemp(prefix=f"marivo_v2_api_{uuid.uuid4().hex[:8]}_")
    db_path = Path(tmp) / "meta.sqlite"
    store = _TestMetadataStore(db_path)
    store.initialize()
    service = SemanticModelV2Service(store)

    app = FastAPI()
    app.include_router(semantic_v2_router)
    app.state.semantic_v2_service = service

    return TestClient(app)


def _make_model_dict(
    name: str = "test_model",
    visibility: str = "public",
    owner_user: str | None = None,
) -> dict:
    """Build a minimal OSI-conformant model dict for testing."""
    marivo_data: dict = {"visibility": visibility}
    if owner_user:
        marivo_data["owner_user"] = owner_user
    return {
        "name": name,
        "datasets": [
            {
                "name": "orders",
                "source": "analytics.orders",
                "primary_key": ["order_id"],
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
                        "custom_extensions": [
                            {
                                "vendor_name": "MARIVO",
                                "data": json.dumps({"data_type": "datetime"}),
                            }
                        ],
                    },
                    {
                        "name": "amount",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}]
                        },
                        "custom_extensions": [
                            {
                                "vendor_name": "MARIVO",
                                "data": json.dumps({"data_type": "number"}),
                            }
                        ],
                    },
                ],
                "custom_extensions": [
                    {
                        "vendor_name": "MARIVO",
                        "data": json.dumps({"datasource_id": "ds_001"}),
                    }
                ],
            }
        ],
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": json.dumps(marivo_data),
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
            json=_make_model_dict(visibility="private", owner_user="alice"),
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
            json=_make_model_dict(visibility="private", owner_user="alice"),
        )
        body = resp.json()
        model = body["semantic_model"][0]
        self.assertEqual(len(model["datasets"]), 1)
        self.assertEqual(model["datasets"][0]["name"], "orders")

    def test_create_private_model(self) -> None:
        client = _make_app()
        model_data = _make_model_dict(
            name="private_model", visibility="private", owner_user="alice"
        )
        resp = client.post("/semantic-models", json=model_data)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["semantic_model"][0]["name"], "private_model")

    def test_create_private_without_owner_returns_422(self) -> None:
        client = _make_app()
        model_data = _make_model_dict(visibility="private")
        resp = client.post("/semantic-models", json=model_data)
        self.assertIn(resp.status_code, (400, 422))


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
            json=_make_model_dict(name="model_a", visibility="private", owner_user="alice"),
        )
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="model_b", visibility="private", owner_user="alice"),
        )
        resp = client.get("/semantic-models", params={"requesting_user": "alice"})
        body = resp.json()
        names = [m["name"] for m in body["semantic_model"]]
        self.assertIn("model_a", names)
        self.assertIn("model_b", names)

    def test_list_with_requesting_user(self) -> None:
        client = _make_app()
        # Use import to create a public model
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
        client.post("/semantic-models/import", json=doc)
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="private_model", visibility="private", owner_user="alice"),
        )
        # Without requesting_user, only public models are visible
        resp = client.get("/semantic-models")
        names = [m["name"] for m in resp.json()["semantic_model"]]
        self.assertIn("public_model", names)
        self.assertNotIn("private_model", names)
        # With requesting_user=alice, private model is also visible
        resp = client.get("/semantic-models", params={"requesting_user": "alice"})
        names = [m["name"] for m in resp.json()["semantic_model"]]
        self.assertIn("public_model", names)
        self.assertIn("private_model", names)
        # With requesting_user=bob, private model is not visible
        resp = client.get("/semantic-models", params={"requesting_user": "bob"})
        names = [m["name"] for m in resp.json()["semantic_model"]]
        self.assertIn("public_model", names)
        self.assertNotIn("private_model", names)


# ---------------------------------------------------------------------------
# GET /semantic-models/{model} — get semantic model
# ---------------------------------------------------------------------------


class TestGetSemanticModelAPI(unittest.TestCase):
    def test_get_existing(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(visibility="private", owner_user="alice"),
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
            json=_make_model_dict(name="private_model", visibility="private", owner_user="alice"),
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
            json=_make_model_dict(visibility="private", owner_user="alice"),
        )
        resp = client.delete("/semantic-models/test_model")
        self.assertEqual(resp.status_code, 204)
        resp = client.get("/semantic-models/test_model", params={"requesting_user": "alice"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_nonexistent_returns_404(self) -> None:
        client = _make_app()
        resp = client.delete("/semantic-models/nonexistent")
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# POST /semantic-models/{model}/datasets — create dataset
# ---------------------------------------------------------------------------


class TestCreateDatasetAPI(unittest.TestCase):
    def test_create_dataset_in_model(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(visibility="private", owner_user="alice"),
        )
        resp = client.post("/semantic-models/test_model/datasets", json=_make_dataset_dict())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["name"], "customers")
        self.assertEqual(len(body["fields"]), 2)

    def test_create_dataset_in_nonexistent_model(self) -> None:
        client = _make_app()
        resp = client.post("/semantic-models/nonexistent/datasets", json=_make_dataset_dict())
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# GET /semantic-models/{model}/readiness — get readiness
# ---------------------------------------------------------------------------


class TestReadinessAPI(unittest.TestCase):
    def test_get_readiness(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(visibility="private", owner_user="alice"),
        )
        resp = client.get("/semantic-models/test_model/readiness")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "not_ready")
        self.assertIsInstance(body["blockers"], list)

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
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        names = [m["name"] for m in body["semantic_model"]]
        self.assertIn("imported_model", names)

    def test_import_rejects_private_model(self) -> None:
        client = _make_app()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "private_import",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
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
                    "custom_extensions": [
                        {
                            "vendor_name": "MARIVO",
                            "data": json.dumps({"visibility": "private", "owner_user": "alice"}),
                        }
                    ],
                }
            ],
        }
        resp = client.post("/semantic-models/import", json=doc)
        self.assertEqual(resp.status_code, 400)


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
        client.post("/semantic-models/import", json=doc)
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
                                    "custom_extensions": [
                                        {
                                            "vendor_name": "MARIVO",
                                            "data": json.dumps({"data_type": "number"}),
                                        }
                                    ],
                                },
                            ],
                        }
                    ],
                },
            ],
        }
        resp = client.post("/semantic-models/import", json=doc2)
        self.assertEqual(resp.status_code, 200)
        # commerce updated
        commerce = client.get("/semantic-models/commerce").json()["semantic_model"][0]
        self.assertEqual(commerce["datasets"][0]["source"], "analytics.orders_v2")
        self.assertEqual(len(commerce["datasets"][0]["fields"]), 2)
        # growth unchanged
        growth = client.get("/semantic-models/growth").json()["semantic_model"][0]
        self.assertEqual(growth["datasets"][0]["source"], "analytics.events")

    def test_import_increments_revision(self) -> None:
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
        client.post("/semantic-models/import", json=doc)
        self.assertEqual(
            client.get("/semantic-models/commerce").json()["semantic_model"][0]["revision"], 1
        )
        client.post("/semantic-models/import", json=doc)
        self.assertEqual(
            client.get("/semantic-models/commerce").json()["semantic_model"][0]["revision"], 2
        )

    def test_import_official_model_with_same_name_as_private_succeeds(self) -> None:
        """Importing official model when private model with same name exists should succeed."""
        client = _make_app()
        # Create private model first
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="commerce", visibility="private", owner_user="alice"),
        )
        # Import official model with same name
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
        resp = client.post("/semantic-models/import", json=doc)
        self.assertEqual(resp.status_code, 200)
        # Both models should exist
        models = client.get("/semantic-models", params={"requesting_user": "alice"}).json()[
            "semantic_model"
        ]
        commerce_models = [m for m in models if m["name"] == "commerce"]
        self.assertEqual(len(commerce_models), 2)  # one official, one private

    def test_import_new_model_revision_is_1(self) -> None:
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
        client.post("/semantic-models/import", json=doc)
        self.assertEqual(
            client.get("/semantic-models/commerce").json()["semantic_model"][0]["revision"], 1
        )


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
            json=_make_model_dict(name="new_private", visibility="private", owner_user="alice"),
        )
        self.assertEqual(resp.status_code, 200)

    def test_update_official_model_returns_403(self) -> None:
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
        client.post("/semantic-models/import", json=doc)
        resp = client.put("/semantic-models/official_model", json={"description": "new"})
        self.assertEqual(resp.status_code, 403)

    def test_update_private_model_succeeds(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model", visibility="private", owner_user="alice"),
        )
        resp = client.put("/semantic-models/priv_model", json={"description": "new"})
        self.assertEqual(resp.status_code, 200)

    def test_delete_official_model_returns_403(self) -> None:
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
        client.post("/semantic-models/import", json=doc)
        resp = client.delete("/semantic-models/official_model")
        self.assertEqual(resp.status_code, 403)

    def test_delete_private_model_succeeds(self) -> None:
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="priv_model", visibility="private", owner_user="alice"),
        )
        resp = client.delete("/semantic-models/priv_model")
        self.assertEqual(resp.status_code, 204)


# ---------------------------------------------------------------------------
# Visibility guard on sub-entity CRUD writes
# ---------------------------------------------------------------------------


class TestVisibilityGuardOnSubEntityWrites(unittest.TestCase):
    def _create_official_model(self, client: TestClient) -> None:
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
        client.post("/semantic-models/import", json=doc)

    def test_create_dataset_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        resp = client.post("/semantic-models/official_model/datasets", json=_make_dataset_dict())
        self.assertEqual(resp.status_code, 403)

    def test_update_dataset_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        resp = client.put(
            "/semantic-models/official_model/datasets/orders", json={"description": "new"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_delete_dataset_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        resp = client.delete("/semantic-models/official_model/datasets/orders")
        self.assertEqual(resp.status_code, 403)

    def test_create_relationship_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        rel = {
            "name": "r",
            "from": "orders",
            "to": "orders",
            "from_columns": ["order_id"],
            "to_columns": ["order_id"],
        }
        resp = client.post("/semantic-models/official_model/relationships", json=rel)
        self.assertEqual(resp.status_code, 403)

    def test_create_metric_in_official_returns_403(self) -> None:
        client = _make_app()
        self._create_official_model(client)
        metric = {
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(order_id)"}]},
        }
        resp = client.post("/semantic-models/official_model/metrics", json=metric)
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Same-name validation for private model creation
# ---------------------------------------------------------------------------


class TestSameNameValidation(unittest.TestCase):
    def test_duplicate_private_name_same_owner_returns_409(self) -> None:
        """Two private models with same name for same owner is not allowed."""
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="alice"),
        )
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="alice"),
        )
        self.assertEqual(resp.status_code, 409)

    def test_duplicate_private_name_different_owner_succeeds(self) -> None:
        """Two private models with same name for different owners is allowed."""
        client = _make_app()
        client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="alice"),
        )
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="explore", visibility="private", owner_user="bob"),
        )
        self.assertEqual(resp.status_code, 200)

    def test_private_same_name_as_official_succeeds(self) -> None:
        """Private model with same name as official model is allowed."""
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
        client.post("/semantic-models/import", json=doc)
        resp = client.post(
            "/semantic-models",
            json=_make_model_dict(name="commerce", visibility="private", owner_user="alice"),
        )
        self.assertEqual(resp.status_code, 200)
