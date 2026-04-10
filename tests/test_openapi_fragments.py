from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from tests.shared_fixtures import get_seeded_duckdb_path


def _encode_path(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


class OpenApiFragmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = Path(cls.tmp.name) / "test.duckdb"
        get_seeded_duckdb_path(db_path)
        cls.app = create_app(db_path=db_path)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.tmp.cleanup()

    def test_openapi_index_lists_paths_and_schemas_with_revision_headers(self) -> None:
        response = self.client.get("/openapi/index")

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["revision"], response.headers["x-openapi-revision"])
        self.assertEqual(response.headers["etag"], f'W/"{payload["revision"]}"')
        self.assertIn("SessionCreateRequest", payload["schemas"])
        session_entry = next(entry for entry in payload["paths"] if entry["path"] == "/sessions")
        self.assertEqual(session_entry["encoded_path"], _encode_path("/sessions"))
        methods = {operation["method"] for operation in session_entry["operations"]}
        self.assertEqual(methods, {"get", "post"})

    def test_openapi_path_fragment_can_expand_referenced_schemas(self) -> None:
        encoded_path = _encode_path("/sessions")

        response = self.client.get(
            f"/openapi/paths/{encoded_path}",
            params={"expand": "schemas", "depth": 1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["path"], "/sessions")
        self.assertIn("post", payload["path_item"])
        self.assertIn("SessionCreateRequest", payload["schemas"])

    def test_openapi_schema_returns_requested_component_schema(self) -> None:
        response = self.client.get("/openapi/schemas/SessionCreateRequest", params={"depth": 0})

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["schema_name"], "SessionCreateRequest")
        self.assertEqual(payload["schemas"], {})
        self.assertEqual(
            payload["schema"], self.app.openapi()["components"]["schemas"]["SessionCreateRequest"]
        )

    def test_openapi_fragment_returns_operation_request_response_and_schemas(self) -> None:
        response = self.client.get(
            "/openapi/fragment",
            params=[
                ("path", "/sessions"),
                ("operation", "post"),
                ("expand", "request"),
                ("expand", "response"),
                ("expand", "schemas"),
                ("depth", "1"),
            ],
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        fragment = payload["fragment"]

        self.assertEqual(payload["path"], "/sessions")
        self.assertEqual(payload["operation"], "post")
        self.assertIn("request_body", fragment)
        self.assertIn("responses", fragment)
        self.assertIn("SessionCreateRequest", fragment["schemas"])

    def test_openapi_fragment_rejects_request_or_response_expand_without_operation(self) -> None:
        response = self.client.get(
            "/openapi/fragment",
            params=[("path", "/sessions"), ("expand", "request")],
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("operation", response.json()["detail"])

    def test_openapi_path_fragment_rejects_invalid_encoded_paths(self) -> None:
        response = self.client.get("/openapi/paths/not-valid@@@")

        self.assertEqual(response.status_code, 400)
        self.assertIn("encoded path", response.json()["detail"])

    def test_semantic_create_routes_publish_typed_request_schemas(self) -> None:
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        schema = response.json()
        components = schema["components"]["schemas"]

        self.assertIn("TypedEntityCreateRequest", components)
        self.assertIn("TypedMetricCreateRequest", components)
        self.assertIn("examples", components["TypedEntityCreateRequest"])
        self.assertIn("examples", components["TypedMetricCreateRequest"])

        entity_request_body = schema["paths"]["/semantic/entities"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]
        metric_request_body = schema["paths"]["/semantic/metrics"]["post"]["requestBody"][
            "content"
        ]["application/json"]["schema"]

        self.assertEqual(
            entity_request_body["$ref"], "#/components/schemas/TypedEntityCreateRequest"
        )
        self.assertEqual(
            metric_request_body["$ref"], "#/components/schemas/TypedMetricCreateRequest"
        )
        self.assertNotIn("additionalProperties", entity_request_body)
        self.assertNotIn("additionalProperties", metric_request_body)
