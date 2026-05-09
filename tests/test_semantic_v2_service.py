"""Tests for SemanticModelV2Service — OSI-aligned semantic layer CRUD."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from marivo.api.models.osi import OSI_SPEC_VERSION
from marivo.semantic_service_v2.service import SemanticModelV2Service
from marivo.semantic_service_v2.validation import SemanticValidationError
from tests.shared_fixtures import (
    ManagedSQLiteMetadataStore,
    make_temp_metadata_store,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestSemanticV2ServiceTestFixtures(unittest.TestCase):
    def test_make_store_cleans_temp_metadata_dir_when_closed(self) -> None:
        temp_root = Path(tempfile.gettempdir())
        before = {
            path
            for path in temp_root.glob("marivo_v2_*")
            if not path.name.startswith("marivo_v2_api_")
        }

        store = _make_store()
        created = {
            path
            for path in temp_root.glob("marivo_v2_*")
            if not path.name.startswith("marivo_v2_api_")
        } - before

        self.assertEqual(len(created), 1)
        temp_dir = created.pop()
        self.assertTrue((temp_dir / "meta.sqlite").exists())

        store.close()

        self.assertFalse(temp_dir.exists())


def _make_store() -> ManagedSQLiteMetadataStore:
    """Create a fresh metadata store with the current OSI v2 schema."""
    import uuid

    return make_temp_metadata_store(prefix=f"marivo_v2_{uuid.uuid4().hex[:8]}_")


def _make_svc() -> SemanticModelV2Service:
    return SemanticModelV2Service(_make_store())


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
                "custom_extensions": [
                    {
                        "vendor_name": "MARIVO",
                        "data": json.dumps({"datasource_id": "ds_001"}),
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
            }
        ],
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": json.dumps(marivo_data),
            }
        ],
    }


def _get_revision(model_dict: dict) -> int | None:
    """Extract revision from a semantic model dict's MARIVO custom_extension."""
    for ext in model_dict.get("custom_extensions", []):
        if ext.get("vendor_name") == "MARIVO":
            data = json.loads(ext["data"])
            return data.get("revision")
    return None


def _make_relationship_dict(
    name: str = "orders_to_customers",
    from_ds: str = "orders",
    to_ds: str = "customers",
) -> dict:
    return {
        "name": name,
        "from": from_ds,
        "to": to_ds,
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
    }


def _make_metric_dict(
    name: str = "total_revenue",
    observed_dataset: str | None = "orders",
) -> dict:
    marivo_data: dict = {}
    if observed_dataset:
        marivo_data["observed_dataset"] = observed_dataset
    return {
        "name": name,
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]},
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
        "custom_extensions": [
            {
                "vendor_name": "MARIVO",
                "data": json.dumps({"datasource_id": "ds_001"}),
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
# SemanticModel CRUD
# ---------------------------------------------------------------------------


class TestCreateSemanticModel(unittest.TestCase):
    def test_create_public_model_returns_403(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        model_data = _make_model_dict()
        with self.assertRaises(HTTPException) as ctx:
            svc.create_semantic_model(model_data)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_create_private_model(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(
            name="private_model", visibility="private", owner_user="alice"
        )
        result = svc.create_semantic_model(model_data)
        self.assertEqual(result["name"], "private_model")
        marivo_ext = None
        for ext in result["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                marivo_ext = json.loads(ext["data"])
        self.assertEqual(marivo_ext["visibility"], "private")
        self.assertEqual(marivo_ext["owner_user"], "alice")

    def test_create_private_without_owner_fails(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private")
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("owner_user" in e["message"] for e in ctx.exception.errors))

    def test_create_model_with_datasets_and_fields(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        result = svc.create_semantic_model(model_data)
        ds = result["datasets"][0]
        self.assertEqual(ds["name"], "orders")
        self.assertEqual(len(ds["fields"]), 3)
        field_names = [f["name"] for f in ds["fields"]]
        self.assertIn("order_id", field_names)
        self.assertIn("order_date", field_names)
        self.assertIn("amount", field_names)

    def test_create_model_with_relationships(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        model_data["relationships"] = [_make_relationship_dict()]
        result = svc.create_semantic_model(model_data)
        self.assertEqual(len(result["relationships"]), 1)
        self.assertEqual(result["relationships"][0]["name"], "orders_to_customers")
        self.assertEqual(result["relationships"][0]["from"], "orders")
        self.assertEqual(result["relationships"][0]["to"], "customers")

    def test_create_model_with_metrics(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["metrics"] = [_make_metric_dict()]
        result = svc.create_semantic_model(model_data)
        self.assertEqual(len(result["metrics"]), 1)
        self.assertEqual(result["metrics"][0]["name"], "total_revenue")

    def test_create_model_revision_starts_at_1(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        result = svc.create_semantic_model(model_data)
        self.assertEqual(_get_revision(result), 1)
        model_row = store.query_one(
            "SELECT revision FROM semantic_models WHERE name = 'test_model'"
        )
        self.assertEqual(model_row["revision"], 1)


class TestGetSemanticModel(unittest.TestCase):
    def test_get_existing_model(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        result = svc.get_semantic_model("test_model", requesting_user="alice")
        self.assertEqual(result["name"], "test_model")

    def test_get_nonexistent_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        with self.assertRaises(HTTPException) as ctx:
            svc.get_semantic_model("nonexistent")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_private_model_by_owner(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        result = svc.get_semantic_model("private_model", requesting_user="alice")
        self.assertEqual(result["name"], "private_model")

    def test_get_private_model_by_non_owner(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        with self.assertRaises(HTTPException) as ctx:
            svc.get_semantic_model("private_model", requesting_user="bob")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_private_model_without_user(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        with self.assertRaises(HTTPException) as ctx:
            svc.get_semantic_model("private_model")
        self.assertEqual(ctx.exception.status_code, 404)


class TestListSemanticModels(unittest.TestCase):
    def test_list_public_models(self) -> None:
        svc = _make_svc()
        # Use import to create public models
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "model_a",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
                    "name": "model_b",
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "primary_key": ["order_id"],
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
        svc.import_osi_document(doc)
        results = svc.list_semantic_models()
        names = [r["name"] for r in results]
        self.assertIn("model_a", names)
        self.assertIn("model_b", names)

    def test_list_includes_private_for_owner(self) -> None:
        svc = _make_svc()
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
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
        svc.import_osi_document(doc)
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        results = svc.list_semantic_models(requesting_user="alice")
        names = [r["name"] for r in results]
        self.assertIn("public_model", names)
        self.assertIn("private_model", names)

    def test_list_excludes_private_for_other_user(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        results = svc.list_semantic_models(requesting_user="bob")
        names = [r["name"] for r in results]
        self.assertNotIn("private_model", names)

    def test_list_excludes_private_without_user(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        results = svc.list_semantic_models()
        names = [r["name"] for r in results]
        self.assertNotIn("private_model", names)


class TestUpdateSemanticModel(unittest.TestCase):
    def test_update_description(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        result = svc.update_semantic_model(
            "test_model", {"description": "Updated"}, owner_user="alice"
        )
        self.assertEqual(result["description"], "Updated")

    def test_update_nonexistent_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        with self.assertRaises(HTTPException) as ctx:
            svc.update_semantic_model("nonexistent", {"description": "x"})
        self.assertEqual(ctx.exception.status_code, 404)

    def test_update_official_model_returns_403(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        # Create official model via import
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
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
        svc.import_osi_document(doc)
        with self.assertRaises(HTTPException) as ctx:
            svc.update_semantic_model("official_model", {"description": "new"})
        self.assertEqual(ctx.exception.status_code, 403)


class TestDeleteSemanticModel(unittest.TestCase):
    def test_delete_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.delete_semantic_model("test_model", owner_user="alice")
        with self.assertRaises(HTTPException) as ctx:
            svc.get_semantic_model("test_model", requesting_user="alice")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_nonexistent_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        with self.assertRaises(HTTPException) as ctx:
            svc.delete_semantic_model("nonexistent")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_delete_official_model_returns_403(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        # Create official model via import
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
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
        svc.import_osi_document(doc)
        with self.assertRaises(HTTPException) as ctx:
            svc.delete_semantic_model("official_model")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_delete_cascades_datasets(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.delete_semantic_model("test_model", owner_user="alice")
        rows = store.query_rows("SELECT * FROM semantic_datasets")
        self.assertEqual(len(rows), 0)


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------


class TestDatasetCRUD(unittest.TestCase):
    def test_create_dataset(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        ds_data = _make_dataset_dict()
        result = svc.create_dataset("test_model", ds_data, owner_user="alice")
        self.assertEqual(result["name"], "customers")
        self.assertEqual(len(result["fields"]), 2)

    def test_get_dataset(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        result = svc.get_dataset("test_model", "orders", requesting_user="alice")
        self.assertEqual(result["name"], "orders")

    def test_get_nonexistent_dataset(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        with self.assertRaises(HTTPException) as ctx:
            svc.get_dataset("test_model", "nonexistent", requesting_user="alice")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_list_datasets(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.create_dataset("test_model", _make_dataset_dict(), owner_user="alice")
        results = svc.list_datasets("test_model", requesting_user="alice")
        names = [r["name"] for r in results]
        self.assertIn("orders", names)
        self.assertIn("customers", names)

    def test_update_dataset(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        result = svc.update_dataset(
            "test_model", "orders", {"description": "Updated orders"}, owner_user="alice"
        )
        self.assertEqual(result["description"], "Updated orders")

    def test_delete_dataset(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.delete_dataset("test_model", "orders", owner_user="alice")
        with self.assertRaises(HTTPException) as ctx:
            svc.get_dataset("test_model", "orders", requesting_user="alice")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_dataset_field_data_type_preserved(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        ds = svc.get_dataset("test_model", "orders", requesting_user="alice")
        order_date = next(f for f in ds["fields"] if f["name"] == "order_date")
        marivo_ext = None
        for ext in order_date.get("custom_extensions", []):
            if ext["vendor_name"] == "MARIVO":
                marivo_ext = json.loads(ext["data"])
        self.assertIsNotNone(marivo_ext)
        self.assertEqual(marivo_ext["data_type"], "datetime")

    def test_create_duplicate_dataset_returns_409(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        # "orders" already exists in the model
        ds_data = _make_dataset_dict(name="orders", source="analytics.orders_v2")
        with self.assertRaises(HTTPException) as ctx:
            svc.create_dataset("test_model", ds_data, owner_user="alice")
        self.assertEqual(ctx.exception.status_code, 409)


# ---------------------------------------------------------------------------
# Relationship CRUD
# ---------------------------------------------------------------------------


class TestRelationshipCRUD(unittest.TestCase):
    def test_create_relationship(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        svc.create_semantic_model(model_data)
        rel_data = _make_relationship_dict()
        result = svc.create_relationship("test_model", rel_data, owner_user="alice")
        self.assertEqual(result["name"], "orders_to_customers")
        self.assertEqual(result["from"], "orders")
        self.assertEqual(result["to"], "customers")

    def test_create_relationship_invalid_from(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        rel_data = _make_relationship_dict(from_ds="nonexistent")
        with self.assertRaises(HTTPException) as ctx:
            svc.create_relationship("test_model", rel_data, owner_user="alice")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_relationship_invalid_to(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        rel_data = _make_relationship_dict(to_ds="nonexistent")
        with self.assertRaises(HTTPException) as ctx:
            svc.create_relationship("test_model", rel_data, owner_user="alice")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_relationship_mismatched_column_lengths(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        svc.create_semantic_model(model_data)
        rel_data = _make_relationship_dict()
        rel_data["from_columns"] = ["customer_id", "region_id"]
        rel_data["to_columns"] = ["customer_id"]
        with self.assertRaises(HTTPException) as ctx:
            svc.create_relationship("test_model", rel_data, owner_user="alice")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_get_relationship(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        model_data["relationships"] = [_make_relationship_dict()]
        svc.create_semantic_model(model_data)
        result = svc.get_relationship("test_model", "orders_to_customers", requesting_user="alice")
        self.assertEqual(result["name"], "orders_to_customers")

    def test_list_relationships(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        model_data["relationships"] = [_make_relationship_dict()]
        svc.create_semantic_model(model_data)
        results = svc.list_relationships("test_model", requesting_user="alice")
        self.assertEqual(len(results), 1)

    def test_update_relationship(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        model_data["relationships"] = [_make_relationship_dict()]
        svc.create_semantic_model(model_data)
        result = svc.update_relationship(
            "test_model", "orders_to_customers", {"cardinality": "many_to_one"}, owner_user="alice"
        )
        marivo_ext = None
        for ext in result.get("custom_extensions", []):
            if ext["vendor_name"] == "MARIVO":
                marivo_ext = json.loads(ext["data"])
        self.assertIsNotNone(marivo_ext)
        self.assertEqual(marivo_ext["cardinality"], "many_to_one")

    def test_delete_relationship(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["datasets"].append(_make_dataset_dict())
        model_data["relationships"] = [_make_relationship_dict()]
        svc.create_semantic_model(model_data)
        svc.delete_relationship("test_model", "orders_to_customers", owner_user="alice")
        with self.assertRaises(HTTPException) as ctx:
            svc.get_relationship("test_model", "orders_to_customers", requesting_user="alice")
        self.assertEqual(ctx.exception.status_code, 404)


# ---------------------------------------------------------------------------
# Metric CRUD
# ---------------------------------------------------------------------------


class TestMetricCRUD(unittest.TestCase):
    def test_create_metric(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        metric_data = _make_metric_dict()
        result = svc.create_metric("test_model", metric_data, owner_user="alice")
        self.assertEqual(result["name"], "total_revenue")

    def test_create_metric_invalid_observed_dataset(self) -> None:
        from marivo.semantic_service_v2.validation import SemanticValidationError

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        metric_data = _make_metric_dict(observed_dataset="nonexistent")
        with self.assertRaises(SemanticValidationError):
            svc.create_metric("test_model", metric_data, owner_user="alice")

    def test_get_metric(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.create_metric("test_model", _make_metric_dict(), owner_user="alice")
        result = svc.get_metric("test_model", "total_revenue", requesting_user="alice")
        self.assertEqual(result["name"], "total_revenue")

    def test_list_metrics(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.create_metric("test_model", _make_metric_dict(), owner_user="alice")
        svc.create_metric(
            "test_model",
            _make_metric_dict(name="order_count", observed_dataset="orders"),
            owner_user="alice",
        )
        results = svc.list_metrics("test_model", requesting_user="alice")
        self.assertEqual(len(results), 2)

    def test_update_metric(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.create_metric("test_model", _make_metric_dict(), owner_user="alice")
        result = svc.update_metric(
            "test_model",
            "total_revenue",
            {"description": "Total revenue metric"},
            owner_user="alice",
        )
        self.assertEqual(result["description"], "Total revenue metric")

    def test_delete_metric(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        svc.create_metric("test_model", _make_metric_dict(), owner_user="alice")
        svc.delete_metric("test_model", "total_revenue", owner_user="alice")
        with self.assertRaises(HTTPException) as ctx:
            svc.get_metric("test_model", "total_revenue", requesting_user="alice")
        self.assertEqual(ctx.exception.status_code, 404)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation(unittest.TestCase):
    def test_invalid_visibility(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        model_data = _make_model_dict()
        for ext in model_data["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                ext["data"] = json.dumps({"visibility": "secret"})
        # Non-private visibility is blocked by CRUD guard before validation
        with self.assertRaises(HTTPException) as ctx:
            svc.create_semantic_model(model_data)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_private_without_owner(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private")
        for ext in model_data["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                ext["data"] = json.dumps({"visibility": "private"})
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("owner_user" in e["path"] for e in ctx.exception.errors))

    def test_relationship_references_unknown_dataset(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["relationships"] = [_make_relationship_dict(from_ds="nonexistent")]
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("nonexistent" in e["message"] for e in ctx.exception.errors))

    def test_metric_references_unknown_dataset(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["metrics"] = [_make_metric_dict(observed_dataset="nonexistent")]
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("nonexistent" in e["message"] for e in ctx.exception.errors))

    def test_metric_observation_grain_unknown_field(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        metric = _make_metric_dict()
        for ext in metric["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                data = json.loads(ext["data"])
                data["observation_grain"] = ["nonexistent_field"]
                ext["data"] = json.dumps(data)
        model_data["metrics"] = [metric]
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("nonexistent_field" in e["message"] for e in ctx.exception.errors))

    def test_metric_primary_time_field_not_time(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        metric = _make_metric_dict()
        for ext in metric["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                data = json.loads(ext["data"])
                data["primary_time_field"] = "amount"  # not a time field
                ext["data"] = json.dumps(data)
        model_data["metrics"] = [metric]
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("not a time field" in e["message"] for e in ctx.exception.errors))

    def test_metric_primary_time_field_valid(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        metric = _make_metric_dict()
        for ext in metric["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                data = json.loads(ext["data"])
                data["primary_time_field"] = "order_date"  # is a time field
                ext["data"] = json.dumps(data)
        model_data["metrics"] = [metric]
        result = svc.create_semantic_model(model_data)
        self.assertEqual(result["name"], "test_model")

    def test_metric_additivity_subset_invalid_dimension(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        metric = _make_metric_dict()
        for ext in metric["custom_extensions"]:
            if ext["vendor_name"] == "MARIVO":
                data = json.loads(ext["data"])
                data["additivity"] = {
                    "dimension_policy": "subset",
                    "additive_dimensions": ["nonexistent_dim"],
                    "time_axis_policy": "additive",
                }
                ext["data"] = json.dumps(data)
        model_data["metrics"] = [metric]
        with self.assertRaises(SemanticValidationError) as ctx:
            svc.create_semantic_model(model_data)
        self.assertTrue(any("nonexistent_dim" in e["message"] for e in ctx.exception.errors))


# ---------------------------------------------------------------------------
# Visibility filtering
# ---------------------------------------------------------------------------


class TestVisibilityFiltering(unittest.TestCase):
    def test_owner_can_see_private_model(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        result = svc.get_semantic_model("private_model", requesting_user="alice")
        self.assertEqual(result["name"], "private_model")

    def test_other_user_cannot_see_private_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        with self.assertRaises(HTTPException) as ctx:
            svc.get_semantic_model("private_model", requesting_user="bob")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_anonymous_cannot_see_private_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="private_model", visibility="private", owner_user="alice")
        )
        with self.assertRaises(HTTPException) as ctx:
            svc.get_semantic_model("private_model")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_list_returns_public_and_owned_private(self) -> None:
        svc = _make_svc()
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
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
        svc.import_osi_document(doc)
        svc.create_semantic_model(
            _make_model_dict(name="alice_private", visibility="private", owner_user="alice")
        )
        svc.create_semantic_model(
            _make_model_dict(name="bob_private", visibility="private", owner_user="bob")
        )
        results = svc.list_semantic_models(requesting_user="alice")
        names = [r["name"] for r in results]
        self.assertIn("public_model", names)
        self.assertIn("alice_private", names)
        self.assertNotIn("bob_private", names)


# ---------------------------------------------------------------------------
# Per-model revision
# ---------------------------------------------------------------------------


class TestPerModelRevision(unittest.TestCase):
    def test_new_model_revision_is_1(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        result = svc.create_semantic_model(
            _make_model_dict(name="model_a", visibility="private", owner_user="alice")
        )
        self.assertEqual(_get_revision(result), 1)
        model_row = store.query_one("SELECT revision FROM semantic_models WHERE name = 'model_a'")
        self.assertEqual(model_row["revision"], 1)

    def test_each_model_has_independent_revision(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        result_a = svc.create_semantic_model(
            _make_model_dict(name="model_a", visibility="private", owner_user="alice")
        )
        result_b = svc.create_semantic_model(
            _make_model_dict(name="model_b", visibility="private", owner_user="alice")
        )
        self.assertEqual(_get_revision(result_a), 1)
        self.assertEqual(_get_revision(result_b), 1)
        # Revisions are independent — no shared version row


# ---------------------------------------------------------------------------
# Import OSI document
# ---------------------------------------------------------------------------


class TestImportOSIDocument(unittest.TestCase):
    def test_import_osi_document(self) -> None:
        svc = _make_svc()
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
                                    "data": json.dumps({"datasource_id": "ds_001"}),
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "sale_id",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                    "custom_extensions": [
                        {
                            "vendor_name": "MARIVO",
                            "data": json.dumps({"visibility": "public"}),
                        }
                    ],
                }
            ],
        }
        results = svc.import_osi_document(doc)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "imported_model")

    def test_import_rejects_private_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "private_import",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "sale_id",
                                            }
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
        with self.assertRaises(HTTPException) as ctx:
            svc.import_osi_document(doc)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_import_increments_revision_on_reimport(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        # Create an initial official model via import
        doc_initial = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "existing",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "sale_id",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        svc.import_osi_document(doc_initial)
        model_row_before = store.query_one(
            "SELECT revision FROM semantic_models WHERE name = 'existing'"
        )
        self.assertEqual(model_row_before["revision"], 1)

        # Import a document that updates the same model
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "existing",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "sale_id",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        results = svc.import_osi_document(doc)
        self.assertEqual(len(results), 1)
        self.assertEqual(_get_revision(results[0]), 2)

        model_row_after = store.query_one(
            "SELECT revision FROM semantic_models WHERE name = 'existing'"
        )
        self.assertEqual(model_row_after["revision"], 2)

    def test_import_official_coexists_with_same_name_private(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        # Create a private model first
        svc.create_semantic_model(
            _make_model_dict(name="shared_name", visibility="private", owner_user="alice")
        )
        rows_before = store.query_rows(
            "SELECT visibility, revision FROM semantic_models WHERE name = 'shared_name'"
        )
        self.assertEqual(len(rows_before), 1)
        self.assertEqual(rows_before[0]["visibility"], "private")

        # Import an official model with the same name
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "shared_name",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "sale_id",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        results = svc.import_osi_document(doc)
        self.assertEqual(len(results), 1)
        self.assertEqual(_get_revision(results[0]), 1)

        # Both models should now exist
        rows_after = store.query_rows(
            "SELECT visibility, revision FROM semantic_models WHERE name = 'shared_name' ORDER BY visibility"
        )
        self.assertEqual(len(rows_after), 2)
        visibilities = [r["visibility"] for r in rows_after]
        self.assertIn("private", visibilities)
        self.assertIn("public", visibilities)

    def test_import_new_model_revision_starts_at_1(self) -> None:
        store = _make_store()
        svc = SemanticModelV2Service(store)
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": "brand_new",
                    "datasets": [
                        {
                            "name": "sales",
                            "source": "analytics.sales",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
                                }
                            ],
                            "fields": [
                                {
                                    "name": "sale_id",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "sale_id",
                                            }
                                        ]
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        results = svc.import_osi_document(doc)
        self.assertEqual(len(results), 1)
        self.assertEqual(_get_revision(results[0]), 1)
        model_row = store.query_one("SELECT revision FROM semantic_models WHERE name = 'brand_new'")
        self.assertEqual(model_row["revision"], 1)


# ---------------------------------------------------------------------------
# Same-name shadowing (private shadows public)
# ---------------------------------------------------------------------------


class TestSameNameShadowing(unittest.TestCase):
    """Tests that private models shadow public models when requesting_user matches."""

    def _make_public_model(self, svc: SemanticModelV2Service, name: str = "commerce") -> None:
        """Import a public model via import_osi_document."""
        doc = {
            "version": OSI_SPEC_VERSION,
            "semantic_model": [
                {
                    "name": name,
                    "datasets": [
                        {
                            "name": "orders",
                            "source": "analytics.orders",
                            "custom_extensions": [
                                {
                                    "vendor_name": "MARIVO",
                                    "data": json.dumps({"datasource_id": "ds_001"}),
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
                    "custom_extensions": [
                        {"vendor_name": "MARIVO", "data": json.dumps({"visibility": "public"})}
                    ],
                }
            ],
        }
        svc.import_osi_document(doc)

    def test_get_model_prefers_private_over_public_for_owner(self) -> None:
        svc = _make_svc()
        self._make_public_model(svc, "commerce")
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        # alice should see her private model
        result = svc.get_semantic_model("commerce", requesting_user="alice")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["visibility"], "private")

    def test_get_model_returns_public_for_non_owner(self) -> None:
        svc = _make_svc()
        self._make_public_model(svc, "commerce")
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        # bob should see the public model
        result = svc.get_semantic_model("commerce", requesting_user="bob")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["visibility"], "public")

    def test_get_model_returns_public_when_no_user(self) -> None:
        svc = _make_svc()
        self._make_public_model(svc, "commerce")
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        # No requesting_user → public model
        result = svc.get_semantic_model("commerce")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["visibility"], "public")

    def test_update_private_model_finds_correct_row(self) -> None:
        svc = _make_svc()
        self._make_public_model(svc, "commerce")
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        result = svc.update_semantic_model(
            "commerce", {"description": "alice's version"}, owner_user="alice"
        )
        self.assertEqual(result["description"], "alice's version")
        # Public model should be unchanged
        public = svc.get_semantic_model("commerce", requesting_user="bob")
        self.assertNotEqual(public.get("description"), "alice's version")

    def test_delete_private_model_finds_correct_row(self) -> None:
        svc = _make_svc()
        self._make_public_model(svc, "commerce")
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        svc.delete_semantic_model("commerce", owner_user="alice")
        # Public model should still exist
        result = svc.get_semantic_model("commerce", requesting_user="bob")
        self.assertEqual(result["name"], "commerce")

    def test_import_finds_public_model_when_private_exists(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="shared", visibility="private", owner_user="alice")
        )
        # Import a public model with the same name
        self._make_public_model(svc, "shared")
        # Both models should exist
        result = svc.get_semantic_model("shared", requesting_user="alice")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["visibility"], "private")
        result = svc.get_semantic_model("shared", requesting_user="bob")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["visibility"], "public")

    def test_readiness_respects_visibility(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        # Non-owner should get 404
        with self.assertRaises(HTTPException) as ctx:
            svc.get_readiness("test_model", requesting_user="bob")
        self.assertEqual(ctx.exception.status_code, 404)
        # Owner should succeed
        result = svc.get_readiness("test_model", requesting_user="alice")
        self.assertEqual(result["status"], "ready")

    def test_two_private_models_same_name_different_owners(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="bob")
        )
        # alice sees alice's model
        result = svc.get_semantic_model("commerce", requesting_user="alice")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["owner_user"], "alice")
        # bob sees bob's model
        result = svc.get_semantic_model("commerce", requesting_user="bob")
        for ext in result.get("custom_extensions", []):
            if ext.get("vendor_name") == "MARIVO":
                data = json.loads(ext["data"])
                self.assertEqual(data["owner_user"], "bob")

    def test_update_private_model_without_owner_returns_403(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        self._make_public_model(svc, "commerce")
        svc.create_semantic_model(
            _make_model_dict(name="commerce", visibility="private", owner_user="alice")
        )
        # Without owner_user, should find the public model and return 403
        with self.assertRaises(HTTPException) as ctx:
            svc.update_semantic_model("commerce", {"description": "new"})
        self.assertEqual(ctx.exception.status_code, 403)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


class TestReadiness(unittest.TestCase):
    def test_get_readiness(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        result = svc.get_readiness("test_model", requesting_user="alice")
        self.assertEqual(result["status"], "ready")
        self.assertIsInstance(result["blockers"], list)
        self.assertEqual(result["semantic_version_id"], None)
        self.assertEqual(result["evaluated_semantic_version_id"], None)

    def test_get_readiness_nonexistent_model(self) -> None:
        from fastapi import HTTPException

        svc = _make_svc()
        with self.assertRaises(HTTPException) as ctx:
            svc.get_readiness("nonexistent")
        self.assertEqual(ctx.exception.status_code, 404)


# ---------------------------------------------------------------------------
# Roundtrip: storage mapping
# ---------------------------------------------------------------------------


class TestStorageRoundtrip(unittest.TestCase):
    def test_model_roundtrip(self) -> None:
        svc = _make_svc()
        model_data = _make_model_dict(visibility="private", owner_user="alice")
        model_data["description"] = "Test model description"
        model_data["datasets"].append(_make_dataset_dict())
        model_data["relationships"] = [_make_relationship_dict()]
        model_data["metrics"] = [_make_metric_dict()]

        created = svc.create_semantic_model(model_data)
        fetched = svc.get_semantic_model("test_model", requesting_user="alice")

        self.assertEqual(created["name"], fetched["name"])
        self.assertEqual(created["description"], fetched["description"])
        self.assertEqual(len(created["datasets"]), len(fetched["datasets"]))
        self.assertEqual(len(created["relationships"]), len(fetched["relationships"]))
        self.assertEqual(len(created["metrics"]), len(fetched["metrics"]))

    def test_field_dimension_preserved(self) -> None:
        svc = _make_svc()
        svc.create_semantic_model(_make_model_dict(visibility="private", owner_user="alice"))
        ds = svc.get_dataset("test_model", "orders", requesting_user="alice")
        order_date = next(f for f in ds["fields"] if f["name"] == "order_date")
        self.assertEqual(order_date["dimension"], {"is_time": True})

        # Non-time field should not have dimension set to True
        order_id = next(f for f in ds["fields"] if f["name"] == "order_id")
        self.assertNotEqual(order_id.get("dimension"), {"is_time": True})
