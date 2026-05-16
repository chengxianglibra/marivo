"""Tests for the semantic service document surface."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from marivo.contracts.errors import ValidationError as DomainValidationError
from marivo.identity import reset_current_user, set_current_user
from marivo.runtime.semantic.semantic_service import SemanticModelV2Service
from tests.shared_fixtures import ManagedSQLiteMetadataStore, make_temp_metadata_store


class TestSemanticV2ServiceTestFixtures(unittest.TestCase):
    def test_make_store_cleans_temp_metadata_dir_when_closed(self) -> None:
        store = _make_store()
        assert store._temp_dir is not None
        temp_dir = Path(store._temp_dir.name)
        self.assertTrue((temp_dir / "meta.sqlite").exists())

        store.close()

        self.assertFalse(temp_dir.exists())


def _make_store() -> ManagedSQLiteMetadataStore:
    import uuid

    store = make_temp_metadata_store(prefix=f"marivo_v2_{uuid.uuid4().hex[:8]}_")
    global _ACTIVE_STORE
    _ACTIVE_STORE = store
    return store


def _make_svc() -> SemanticModelV2Service:
    return SemanticModelV2Service(_make_store())


_ACTIVE_STORE: ManagedSQLiteMetadataStore | None = None


def _model(name: str = "commerce", *, fields: list[str] | None = None) -> dict:
    field_names = fields or ["order_id", "order_date", "amount"]
    return {
        "name": name,
        "datasets": [
            {
                "name": "orders",
                "source": "analytics.orders",
                "primary_key": ["order_id"],
                "custom_extensions": [
                    {"vendor_name": "MARIVO", "data": {"datasource_id": "ds_001"}}
                ],
                "fields": [
                    {
                        "name": field_name,
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": field_name}]
                        },
                    }
                    for field_name in field_names
                ],
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]},
                "custom_extensions": [
                    {"vendor_name": "MARIVO", "data": {"additive_dimensions": ["order_id"]}}
                ],
            }
        ],
    }


def _doc(*models: dict) -> dict:
    return {"version": "0.1.1", "semantic_model": list(models)}


def _as_user(user: str | None):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        token = set_current_user(user)
        try:
            yield
        finally:
            reset_current_user(token)

    return _ctx()


def _seed_public_model(svc: SemanticModelV2Service, name: str = "public_model") -> None:
    svc.store.execute(
        """
        INSERT INTO semantic_models (name, description, visibility, owner_user)
        VALUES (?, 'public model', 'public', NULL)
        """,
        [name],
    )
    model_row = svc.store.query_one(
        "SELECT model_id FROM semantic_models WHERE name = ? AND visibility = 'public'",
        [name],
    )
    assert model_row is not None
    svc.store.execute(
        """
        INSERT INTO semantic_datasets (model_id, name, source, primary_key, datasource_id)
        VALUES (?, 'orders', 'analytics.orders', ?, 'ds_001')
        """,
        [model_row["model_id"], json.dumps(["order_id"])],
    )
    dataset_row = svc.store.query_one(
        "SELECT dataset_id FROM semantic_datasets WHERE model_id = ?",
        [model_row["model_id"]],
    )
    assert dataset_row is not None
    svc.store.execute(
        """
        INSERT INTO semantic_fields
            (dataset_id, name, expression, is_time, is_dimension, position)
        VALUES (?, 'order_id', ?, 0, 0, 0)
        """,
        [
            dataset_row["dataset_id"],
            json.dumps({"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}),
        ],
    )


class TestSemanticDocumentService(unittest.TestCase):
    def tearDown(self) -> None:
        if _ACTIVE_STORE is not None:
            _ACTIVE_STORE.close()

    def test_validate_returns_structured_summary(self) -> None:
        svc = _make_svc()

        result = svc.validate_osi_semantic_models(_doc(_model()))

        self.assertTrue(result["valid"])
        self.assertEqual(result["schema_version"], "0.1.1")
        self.assertEqual(result["summary"]["models"], 1)
        self.assertEqual(result["summary"]["fields"], 3)

    def test_validate_reports_reference_errors(self) -> None:
        svc = _make_svc()
        model = _model()
        model["datasets"][0]["primary_key"] = ["missing"]

        result = svc.validate_osi_semantic_models(_doc(model))

        self.assertFalse(result["valid"])
        self.assertEqual(result["errors"][0]["code"], "UNKNOWN_FIELD")

    def test_import_writes_current_users_private_model(self) -> None:
        svc = _make_svc()

        with _as_user("alice"):
            svc.import_osi_semantic_models(_doc(_model()))

        row = svc.store.query_one(
            "SELECT visibility, owner_user FROM semantic_models WHERE name = ?",
            ["commerce"],
        )
        self.assertEqual(row, {"visibility": "private", "owner_user": "alice"})

    def test_import_does_not_write_invalid_document(self) -> None:
        svc = _make_svc()
        invalid = _model()
        invalid["datasets"][0]["primary_key"] = ["missing"]

        with _as_user("alice"), self.assertRaises(DomainValidationError):
            svc.import_osi_semantic_models(_doc(invalid))

        self.assertIsNone(
            svc.store.query_one("SELECT * FROM semantic_models WHERE name = 'commerce'")
        )

    def test_import_replaces_same_name_model_graph(self) -> None:
        svc = _make_svc()

        with _as_user("alice"):
            svc.import_osi_semantic_models(
                _doc(_model(fields=["order_id", "order_date", "amount"]))
            )
            svc.import_osi_semantic_models(_doc(_model(fields=["order_id", "order_date"])))
            exported = svc.export_osi_semantic_models("commerce")

        fields = exported["semantic_model"][0]["datasets"][0]["fields"]
        self.assertEqual([field["name"] for field in fields], ["order_id", "order_date"])

    def test_get_prefers_requesters_private_model_over_public(self) -> None:
        svc = _make_svc()
        _seed_public_model(svc, "commerce")

        with _as_user("alice"):
            svc.import_osi_semantic_models(
                _doc(_model("commerce", fields=["order_id", "private_id"]))
            )

        result = svc.get_semantic_model("commerce", requesting_user="alice")

        self.assertEqual(
            [field["name"] for field in result["datasets"][0]["fields"]],
            ["order_id", "private_id"],
        )

    def test_list_returns_public_and_requester_private_models(self) -> None:
        svc = _make_svc()
        _seed_public_model(svc, "public_model")

        with _as_user("alice"):
            svc.import_osi_semantic_models(_doc(_model("alice_model")))
        with _as_user("bob"):
            svc.import_osi_semantic_models(_doc(_model("bob_model")))

        result = svc.list_semantic_models(requesting_user="alice")

        self.assertEqual([model["name"] for model in result], ["public_model", "alice_model"])

    def test_export_requires_current_user(self) -> None:
        svc = _make_svc()

        with self.assertRaises(RuntimeError), _as_user(None):
            svc.export_osi_semantic_models()

    def test_delete_private_model_cascades_children(self) -> None:
        svc = _make_svc()

        with _as_user("alice"):
            svc.import_osi_semantic_models(_doc(_model("commerce")))
            svc.delete_semantic_model("commerce", owner_user="alice")

        self.assertIsNone(
            svc.store.query_one("SELECT * FROM semantic_models WHERE name = ?", ["commerce"])
        )
        self.assertEqual(svc.store.query_rows("SELECT * FROM semantic_datasets"), [])
        self.assertEqual(svc.store.query_rows("SELECT * FROM semantic_fields"), [])
        self.assertEqual(svc.store.query_rows("SELECT * FROM semantic_metrics"), [])

    def test_delete_private_model_leaves_other_owner_model(self) -> None:
        svc = _make_svc()

        with _as_user("alice"):
            svc.import_osi_semantic_models(_doc(_model("commerce")))
        with _as_user("bob"):
            svc.import_osi_semantic_models(_doc(_model("commerce", fields=["order_id"])))

        svc.delete_semantic_model("commerce", owner_user="alice")

        bob_model = svc.get_semantic_model("commerce", requesting_user="bob")
        self.assertEqual(
            [field["name"] for field in bob_model["datasets"][0]["fields"]],
            ["order_id"],
        )
