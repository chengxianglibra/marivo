"""Tests for the Semantic V2 HTTP document surface."""

from __future__ import annotations

import unittest
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marivo.adapters.server.semantic_service_adapter import SemanticServiceAdapter
from marivo.datasources import DatasourceService
from marivo.identity import reset_current_user, set_current_user
from marivo.transports.http.models.osi import OSI_SPEC_VERSION
from marivo.transports.http.semantic_v2 import router as semantic_v2_router
from tests.shared_fixtures import ManagedSQLiteMetadataStore, make_temp_metadata_store


def _make_model_dict(name: str = "commerce") -> dict[str, Any]:
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
                        "name": "order_time",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "order_time"}]
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
        "metrics": [
            {
                "name": "revenue",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(amount)"}]},
            }
        ],
    }


def _make_document(name: str = "commerce") -> dict[str, Any]:
    return {"version": OSI_SPEC_VERSION, "semantic_model": [_make_model_dict(name=name)]}


def _valid_validation_result() -> dict[str, Any]:
    return {
        "valid": True,
        "schema_version": OSI_SPEC_VERSION,
        "errors": [],
        "warnings": [],
        "summary": {
            "models": 1,
            "datasets": 1,
            "fields": 3,
            "metrics": 1,
            "relationships": 0,
        },
    }


def _invalid_validation_result() -> dict[str, Any]:
    return {
        "valid": False,
        "schema_version": OSI_SPEC_VERSION,
        "errors": [
            {
                "code": "DUPLICATE_NAME",
                "message": "Duplicate dataset name 'orders'",
                "json_pointer": "/semantic_model/0/datasets/1/name",
                "severity": "error",
                "hint": "Use unique dataset names inside a semantic model.",
                "context": {"name": "orders"},
            }
        ],
        "warnings": [],
        "summary": {
            "models": 1,
            "datasets": 2,
            "fields": 6,
            "metrics": 1,
            "relationships": 0,
        },
    }


class FakeSemanticDocumentService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.models = {"commerce": _make_model_dict()}
        self.validation_result = _valid_validation_result()

    def list_semantic_models(self, requesting_user: str | None = None) -> list[dict[str, Any]]:
        self.calls.append(("list", requesting_user))
        return list(self.models.values())

    def get_semantic_model(self, name: str, requesting_user: str | None = None) -> dict[str, Any]:
        self.calls.append(("get", name, requesting_user))
        return self.models[name]

    def validate_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("validate", doc_data))
        return self.validation_result

    def import_osi_semantic_models(self, doc_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("import", doc_data))
        if not self.validation_result["valid"]:
            return {**self.validation_result, "import_report": None}
        model = doc_data["semantic_model"][0]
        self.models[model["name"]] = model
        return {
            **self.validation_result,
            "import_report": {
                "models": [
                    {
                        "name": model["name"],
                        "created": True,
                        "updated": False,
                        "datasets": {"created": 1, "updated": 0, "unchanged": 0},
                        "fields": {"created": 3, "updated": 0, "unchanged": 0},
                        "metrics": {"created": 1, "updated": 0, "unchanged": 0},
                        "relationships": {"created": 0, "updated": 0, "unchanged": 0},
                        "datasource_bindings": [],
                    }
                ],
                "errors": [],
            },
        }

    def export_osi_semantic_models(self, semantic_model_name: str | None = None) -> dict[str, Any]:
        self.calls.append(("export", semantic_model_name))
        models = list(self.models.values())
        if semantic_model_name is not None:
            models = [self.models[semantic_model_name]]
        return {"version": OSI_SPEC_VERSION, "semantic_model": models}

    def delete_semantic_model(self, name: str, owner_user: str | None = None) -> None:
        self.calls.append(("delete", name, owner_user))
        del self.models[name]


def _make_app(
    service: FakeSemanticDocumentService | None = None,
) -> tuple[TestClient, FakeSemanticDocumentService]:
    test_service = service or FakeSemanticDocumentService()
    app = FastAPI()
    app.include_router(semantic_v2_router)
    app.state.semantic_v2_service = test_service
    return TestClient(app), test_service


class TestSemanticDocumentSurface(unittest.TestCase):
    def test_list_semantic_models_keeps_osi_envelope(self) -> None:
        client, service = _make_app()

        resp = client.get("/semantic-models")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual(body["semantic_model"][0]["name"], "commerce")
        self.assertEqual(service.calls[-1], ("list", "test_user"))

    def test_get_semantic_model_keeps_osi_envelope(self) -> None:
        client, service = _make_app()

        resp = client.get("/semantic-models/commerce")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual(body["semantic_model"][0]["name"], "commerce")
        self.assertEqual(service.calls[-1], ("get", "commerce", "test_user"))

    def test_validate_semantic_models_returns_structured_validation_result(self) -> None:
        client, service = _make_app()

        resp = client.post("/semantic-models/validate", json=_make_document())

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["valid"], True)
        self.assertEqual(body["schema_version"], OSI_SPEC_VERSION)
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["summary"]["models"], 1)
        self.assertEqual(service.calls[-1], ("validate", _make_document()))

    def test_import_success_returns_validation_and_import_report(self) -> None:
        client, service = _make_app()
        doc = _make_document(name="growth")

        resp = client.post("/semantic-models/import", json=doc)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["valid"], True)
        self.assertEqual(body["import_report"]["models"][0]["name"], "growth")
        self.assertEqual(body["import_report"]["models"][0]["datasets"]["created"], 1)
        self.assertEqual(service.calls[-1], ("import", doc))

    def test_import_validation_failure_omits_import_report(self) -> None:
        service = FakeSemanticDocumentService()
        service.validation_result = _invalid_validation_result()
        client, _ = _make_app(service)

        resp = client.post("/semantic-models/import", json=_make_document())

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["valid"], False)
        self.assertEqual(body["errors"][0]["code"], "DUPLICATE_NAME")
        self.assertIsNone(body["import_report"])

    def test_validate_schema_failure_returns_structured_result(self) -> None:
        service = FakeSemanticDocumentService()
        service.validation_result = {
            **_invalid_validation_result(),
            "errors": [
                {
                    "code": "SCHEMA_VALIDATION_FAILED",
                    "message": "schema failure",
                    "json_pointer": "",
                    "severity": "error",
                    "hint": "Fix the document schema.",
                    "context": {},
                }
            ],
        }
        client, _ = _make_app(service)

        resp = client.post("/semantic-models/validate", json={"version": OSI_SPEC_VERSION})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["errors"][0]["code"], "SCHEMA_VALIDATION_FAILED")

    def test_validate_empty_document_is_not_importable(self) -> None:
        service = FakeSemanticDocumentService()
        service.validation_result = {
            **_invalid_validation_result(),
            "errors": [
                {
                    "code": "EMPTY_SEMANTIC_MODEL",
                    "message": "semantic_model must contain at least one semantic model.",
                    "json_pointer": "/semantic_model",
                    "severity": "error",
                    "hint": "Add a complete semantic model.",
                    "context": {},
                }
            ],
        }
        client, _ = _make_app(service)

        resp = client.post(
            "/semantic-models/validate",
            json={"version": OSI_SPEC_VERSION, "semantic_model": []},
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["errors"][0]["code"], "EMPTY_SEMANTIC_MODEL")

    def test_export_supports_optional_semantic_model_name_filter(self) -> None:
        client, service = _make_app()

        resp = client.get("/semantic-models/export", params={"semantic_model_name": "commerce"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["version"], OSI_SPEC_VERSION)
        self.assertEqual(body["semantic_model"][0]["name"], "commerce")
        self.assertEqual(service.calls[-1], ("export", "commerce"))

    def test_delete_semantic_model_uses_current_identity(self) -> None:
        client, service = _make_app()

        resp = client.delete("/semantic-models/commerce", headers={"X-Marivo-User": "alice"})

        self.assertEqual(resp.status_code, 204)
        self.assertNotIn("commerce", service.models)
        self.assertEqual(service.calls[-1], ("delete", "commerce", "test_user"))

    def test_delete_semantic_model_requires_context_identity(self) -> None:
        client, service = _make_app()

        token = set_current_user(None)
        try:
            resp = client.delete("/semantic-models/commerce")
        finally:
            reset_current_user(token)

        self.assertEqual(resp.status_code, 422)
        self.assertIn("commerce", service.models)

    def test_removed_crud_and_readiness_routes_return_405_or_404(self) -> None:
        client, _ = _make_app()

        checks = [
            ("post", "/semantic-models"),
            ("put", "/semantic-models/commerce"),
            ("post", "/semantic-models/commerce/datasets"),
            ("get", "/semantic-models/commerce/datasets"),
            ("get", "/semantic-models/commerce/datasets/orders"),
            ("put", "/semantic-models/commerce/datasets/orders"),
            ("delete", "/semantic-models/commerce/datasets/orders"),
            ("post", "/semantic-models/commerce/datasets/orders/fields"),
            ("get", "/semantic-models/commerce/datasets/orders/fields"),
            ("get", "/semantic-models/commerce/datasets/orders/fields/order_id"),
            ("patch", "/semantic-models/commerce/datasets/orders/fields/order_id"),
            ("delete", "/semantic-models/commerce/datasets/orders/fields/order_id"),
            ("post", "/semantic-models/commerce/metrics"),
            ("get", "/semantic-models/commerce/metrics"),
            ("get", "/semantic-models/commerce/metrics/revenue"),
            ("put", "/semantic-models/commerce/metrics/revenue"),
            ("delete", "/semantic-models/commerce/metrics/revenue"),
            ("post", "/semantic-models/commerce/relationships"),
            ("get", "/semantic-models/commerce/relationships"),
            ("get", "/semantic-models/commerce/relationships/order_customer"),
            ("put", "/semantic-models/commerce/relationships/order_customer"),
            ("delete", "/semantic-models/commerce/relationships/order_customer"),
            ("get", "/semantic-models/commerce/readiness"),
        ]

        for method, path in checks:
            with self.subTest(method=method, path=path):
                resp = client.request(method.upper(), path, json={})
                self.assertIn(resp.status_code, {404, 405})


class TestSemanticServiceAdapterSurface(unittest.TestCase):
    def test_adapter_exposes_only_document_surface_methods(self) -> None:
        public_methods = {
            name
            for name, value in vars(SemanticServiceAdapter).items()
            if callable(value) and not name.startswith("_")
        }

        self.assertEqual(
            public_methods,
            {
                "list_semantic_models",
                "get_semantic_model",
                "validate_osi_semantic_models",
                "import_osi_semantic_models",
                "export_osi_semantic_models",
                "delete_semantic_model",
            },
        )


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


def _make_real_app() -> _ManagedTestClient:
    import uuid

    from marivo.transports.http.middleware import UserIdentityMiddleware

    store = make_temp_metadata_store(prefix=f"marivo_v2_real_api_{uuid.uuid4().hex[:8]}_")
    datasource_service = DatasourceService(store)
    service = SemanticServiceAdapter(store)
    app = FastAPI()
    app.add_middleware(UserIdentityMiddleware)
    app.include_router(semantic_v2_router)
    app.state.semantic_v2_service = service
    app.state.datasource_service = datasource_service
    return _ManagedTestClient(app, store)


class TestSemanticRealHttpIntegration(unittest.TestCase):
    def test_import_uses_real_service_flat_response_shape(self) -> None:
        client = _make_real_app()
        try:
            resp = client.post(
                "/semantic-models/import",
                json=_make_document(),
                headers={"X-Marivo-User": "alice"},
            )
        finally:
            client.close()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["import_report"]["models"][0]["name"], "commerce")

    def test_requesting_user_query_does_not_select_other_private_models(self) -> None:
        client = _make_real_app()
        try:
            client.post(
                "/semantic-models/import",
                json=_make_document("bob_private"),
                headers={"X-Marivo-User": "bob"},
            )
            resp = client.get(
                "/semantic-models",
                params={"requesting_user": "bob"},
                headers={"X-Marivo-User": "alice"},
            )
        finally:
            client.close()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["semantic_model"], [])

    def test_delete_private_model_then_get_returns_404(self) -> None:
        client = _make_real_app()
        try:
            client.post(
                "/semantic-models/import",
                json=_make_document("commerce"),
                headers={"X-Marivo-User": "alice"},
            )
            resp = client.delete(
                "/semantic-models/commerce",
                headers={"X-Marivo-User": "alice"},
            )
            get_resp = client.get(
                "/semantic-models/commerce",
                headers={"X-Marivo-User": "alice"},
            )
        finally:
            client.close()

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(get_resp.status_code, 404)

    def test_delete_nonexistent_private_model_returns_404(self) -> None:
        client = _make_real_app()
        try:
            resp = client.delete(
                "/semantic-models/missing",
                headers={"X-Marivo-User": "alice"},
            )
        finally:
            client.close()

        self.assertEqual(resp.status_code, 404)

    def test_delete_public_only_model_returns_403(self) -> None:
        client = _make_real_app()
        try:
            service = client.app.state.semantic_v2_service.service
            service.store.execute(
                """
                INSERT INTO semantic_models (name, description, visibility, owner_user)
                VALUES ('official_model', 'official', 'public', NULL)
                """
            )
            resp = client.delete(
                "/semantic-models/official_model",
                headers={"X-Marivo-User": "alice"},
            )
        finally:
            client.close()

        self.assertEqual(resp.status_code, 403)

    def test_delete_private_model_leaves_other_owner_private_model_intact(self) -> None:
        client = _make_real_app()
        try:
            client.post(
                "/semantic-models/import",
                json=_make_document("commerce"),
                headers={"X-Marivo-User": "alice"},
            )
            client.post(
                "/semantic-models/import",
                json=_make_document("commerce"),
                headers={"X-Marivo-User": "bob"},
            )
            resp = client.delete(
                "/semantic-models/commerce",
                headers={"X-Marivo-User": "alice"},
            )
            bob_resp = client.get(
                "/semantic-models/commerce",
                headers={"X-Marivo-User": "bob"},
            )
        finally:
            client.close()

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(bob_resp.status_code, 200)
