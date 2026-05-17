from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import FastAPI

from marivo.transports.http.router import include_api_routers

SCOPED_PATH_PREFIXES = (
    "/semantic-models",  # fully typed, GREEN
    "/datasources",  # fully typed, GREEN
    "/routing",  # fully typed, GREEN
    "/policies",
    "/quality-rules",
)

SCOPED_SESSION_PATHS = {
    "/sessions",
    "/sessions/{session_id}",
    "/sessions/{session_id}/state",
    "/sessions/{session_id}/state/query",
    "/sessions/{session_id}/runtime-status",
    "/sessions/{session_id}/artifacts/{artifact_id}/runtime-status",
    "/sessions/{session_id}/propositions/{proposition_id}/context",
    "/sessions/{session_id}/propositions/{proposition_id}/runtime-status",
    "/sessions/{session_id}/terminate",
}

SCHEMA_KEYS_THAT_MAKE_A_LEAF_TYPED = {
    "type",
    "$ref",
    "oneOf",
    "anyOf",
    "allOf",
    "enum",
    "const",
}

ENVELOPE_OPEN_DICT_POINTERS = {
    "/components/schemas/ExecutionEnvelope/properties/result/additionalProperties",
    "/components/schemas/ExecutionEnvelope/properties/provenance/anyOf/0/additionalProperties",
    "/components/schemas/ExecutionEnvelope/properties/product_metadata/anyOf/0/additionalProperties",
    "/components/schemas/DerivedBundleResult/additionalProperties",
}

for _intent_response_name in (
    "AttributeResponse",
    "CompareResponse",
    "CorrelateResponse",
    "DecomposeResponse",
    "DetectResponse",
    "DiagnoseResponse",
    "ForecastResponse",
    "ObserveResponse",
    "TestResponse",
    "ValidateResponse",
):
    ENVELOPE_OPEN_DICT_POINTERS.add(
        f"/components/schemas/{_intent_response_name}/properties/provenance/anyOf/0/additionalProperties"
    )
    ENVELOPE_OPEN_DICT_POINTERS.add(
        f"/components/schemas/{_intent_response_name}/properties/product_metadata/anyOf/0/additionalProperties"
    )
del _intent_response_name


def _router_only_openapi() -> dict[str, Any]:
    app = FastAPI(title="Marivo Semantic Layer", version="0.1.0")
    include_api_routers(app)
    return app.openapi()


def test_removed_catalog_routes_are_not_registered() -> None:
    openapi = _router_only_openapi()
    paths = set(openapi["paths"])

    removed_paths = {
        "/catalog/search",
        "/catalog/objects/{object_kind}/{object_id}",
        "/catalog/graph",
        "/semantic/resolve/{name}",
        "/sessions/{session_id}/planner-context",
    }

    assert paths.isdisjoint(removed_paths)


def test_dataset_native_grounding_removed_routes_are_not_registered() -> None:
    openapi = _router_only_openapi()
    paths = set(openapi["paths"])

    removed_paths = {
        "/datasources/{datasource_id}/sync",
        "/datasources/{datasource_id}/sync/{job_id}",
        "/datasources/{datasource_id}/sync/selections",
        "/datasources/{datasource_id}/sync/selections/{selection_id}",
        "/datasources/{datasource_id}/objects",
        "/datasources/{datasource_id}/objects/{object_id}",
        "/datasources/{datasource_id}/objects/{object_id}/properties",
        "/semantic/bindings",
        "/semantic/bindings/{binding_id}",
        "/semantic/bindings/{binding_id}/validate",
        "/semantic/bindings/{binding_id}/activate",
        "/semantic/bindings/{binding_id}/deprecate",
        "/semantic/bindings/{binding_id}/publish",
        "/calendar/data",
    }

    assert paths.isdisjoint(removed_paths)
    assert "/datasources/{datasource_id}/browse/columns" in paths


def _is_scoped_path(path: str) -> bool:
    return (
        path.startswith(SCOPED_PATH_PREFIXES)
        or path in SCOPED_SESSION_PATHS
        or path.startswith("/sessions/{session_id}/intents/")
    )


def _json_pointer_token(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _append_pointer(pointer: str, token: object) -> str:
    return f"{pointer}/{_json_pointer_token(token)}"


def _iter_scoped_operation_schemas(openapi: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    paths = openapi.get("paths")
    assert isinstance(paths, dict)
    for path, path_item in paths.items():
        if not _is_scoped_path(str(path)):
            continue
        assert isinstance(path_item, dict)
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_pointer = f"/paths/{_json_pointer_token(path)}/{method}"
            assert isinstance(operation, dict)
            yield from _iter_operation_schema_nodes(operation_pointer, operation)


def _iter_operation_schema_nodes(
    operation_pointer: str, operation: Mapping[str, Any]
) -> Iterable[tuple[str, Any]]:
    parameters = operation.get("parameters", [])
    assert isinstance(parameters, list)
    for index, parameter in enumerate(parameters):
        if isinstance(parameter, dict) and "schema" in parameter:
            yield f"{operation_pointer}/parameters/{index}/schema", parameter["schema"]

    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        yield from _iter_content_schema_nodes(f"{operation_pointer}/requestBody", request_body)

    responses = operation.get("responses", {})
    assert isinstance(responses, dict)
    for status_code, response in responses.items():
        if isinstance(response, dict):
            yield from _iter_content_schema_nodes(
                f"{operation_pointer}/responses/{_json_pointer_token(status_code)}", response
            )


def _iter_content_schema_nodes(pointer: str, node: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    content = node.get("content", {})
    assert isinstance(content, dict)
    for media_type, media_type_object in content.items():
        if isinstance(media_type_object, dict) and "schema" in media_type_object:
            yield (
                f"{pointer}/content/{_json_pointer_token(media_type)}/schema",
                media_type_object["schema"],
            )


def _iter_component_schemas(openapi: Mapping[str, Any]) -> Iterable[tuple[str, Any]]:
    schemas = openapi.get("components", {}).get("schemas", {})
    assert isinstance(schemas, dict)
    for name, schema in schemas.items():
        yield f"/components/schemas/{_json_pointer_token(name)}", schema


def _is_typed_schema(schema: Mapping[str, Any]) -> bool:
    return any(key in schema for key in SCHEMA_KEYS_THAT_MAKE_A_LEAF_TYPED)


def _walk_schema(node: Any, pointer: str, violations: list[str]) -> None:
    if isinstance(node, list):
        if not node and pointer.endswith("/items"):
            violations.append(f"{pointer}: array items must not be empty")
        for index, item in enumerate(node):
            _walk_schema(item, _append_pointer(pointer, index), violations)
        return

    if not isinstance(node, dict):
        return

    if node.get("additionalProperties") is True and f"{pointer}/additionalProperties" not in (
        ENVELOPE_OPEN_DICT_POINTERS
    ):
        violations.append(f"{pointer}/additionalProperties: additionalProperties true is forbidden")

    if node.get("type") == "array":
        items = node.get("items")
        if node.get("maxItems") != 0 and (
            not isinstance(items, dict) or not items or not _is_typed_schema(items)
        ):
            violations.append(f"{pointer}/items: array schema must declare non-empty typed items")

    child_schema_keys = {
        "properties",
        "items",
        "additionalProperties",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "prefixItems",
        "contains",
        "contentSchema",
    }
    has_schema_children = any(key in node for key in child_schema_keys)
    if not has_schema_children and not _is_typed_schema(node):
        violations.append(f"{pointer}: schema leaf must declare type/ref/composition/enum/const")

    properties = node.get("properties")
    if isinstance(properties, dict):
        for property_name, property_schema in properties.items():
            _walk_schema(
                property_schema,
                f"{pointer}/properties/{_json_pointer_token(property_name)}",
                violations,
            )

    items = node.get("items")
    if isinstance(items, (dict, list)) and not (
        node.get("type") == "array" and node.get("maxItems") == 0 and items == {}
    ):
        _walk_schema(items, f"{pointer}/items", violations)

    additional_properties = node.get("additionalProperties")
    if isinstance(additional_properties, dict):
        _walk_schema(additional_properties, f"{pointer}/additionalProperties", violations)

    for key in ("oneOf", "anyOf", "allOf", "prefixItems"):
        value = node.get(key)
        if isinstance(value, list):
            _walk_schema(value, f"{pointer}/{key}", violations)

    for key in ("not", "if", "then", "else", "contains"):
        value = node.get(key)
        if isinstance(value, dict):
            _walk_schema(value, f"{pointer}/{key}", violations)

    content_schema = node.get("contentSchema")
    if isinstance(content_schema, dict):
        _walk_schema(content_schema, f"{pointer}/contentSchema", violations)


def test_scoped_openapi_schemas_are_agent_friendly() -> None:
    openapi = _router_only_openapi()
    violations: list[str] = []

    scoped_schemas = list(_iter_scoped_operation_schemas(openapi))
    for pointer, schema in scoped_schemas:
        _walk_schema(schema, pointer, violations)

    referenced_names: set[str] = set()

    def collect_refs(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                collect_refs(item)
            return
        if not isinstance(node, dict):
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            referenced_names.add(ref.rsplit("/", 1)[-1])
        for value in node.values():
            collect_refs(value)

    for _, schema in scoped_schemas:
        collect_refs(schema)

    component_schemas = dict(_iter_component_schemas(openapi))
    visited_names: set[str] = set()
    while referenced_names:
        name = referenced_names.pop()
        if name in visited_names:
            continue
        visited_names.add(name)
        pointer = f"/components/schemas/{_json_pointer_token(name)}"
        schema = component_schemas.get(pointer)
        if schema is None:
            continue
        _walk_schema(schema, pointer, violations)
        collect_refs(schema)

    assert not violations, "\n".join(sorted(violations))


def test_contract_tables_removed_from_schema():
    """Verify that retired contract tables are not in the schema DDL."""
    from marivo.adapters.schema import METADATA_DDL

    # Join all DDL statements into one string for searching
    full_ddl = "\n".join(METADATA_DDL)

    # Contract tables that should NOT exist
    forbidden_tables = [
        "semantic_metric_contracts",
        "semantic_dimension_contracts",
        "semantic_process_objects",
        "semantic_process_exported_dimension_refs",
        "semantic_time_objects",
        "semantic_predicate_contracts",
        "compiler_compatibility_profiles",
        "semantic_enum_sets",
        "semantic_enum_set_versions",
        "semantic_enum_set_values",
        "semantic_domain_catalog",
    ]

    for table_name in forbidden_tables:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" not in full_ddl, (
            f"Contract table '{table_name}' should be removed from schema"
        )
        assert f"CREATE TABLE {table_name}" not in full_ddl, (
            f"Contract table '{table_name}' should be removed from schema"
        )
