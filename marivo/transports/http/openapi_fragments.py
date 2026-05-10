from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

router = APIRouter()

_SCHEMA_REF_PREFIX = "#/components/schemas/"
_ALLOWED_EXPANDS = frozenset({"request", "response", "schemas"})
_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_MAX_EXPANSION_DEPTH = 5


def _get_openapi_schema(request: Request) -> dict[str, Any]:
    schema = request.app.openapi()
    if not isinstance(schema, dict):
        raise HTTPException(status_code=500, detail="FastAPI OpenAPI schema is not a JSON object.")
    return schema


def _get_openapi_paths(schema: dict[str, Any]) -> dict[str, Any]:
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        raise HTTPException(status_code=500, detail="FastAPI OpenAPI schema is missing 'paths'.")
    return paths


def _get_component_schemas(schema: dict[str, Any]) -> dict[str, Any]:
    components = schema.get("components")
    if not isinstance(components, dict):
        return {}
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return {}
    return schemas


def _get_openapi_revision(schema: dict[str, Any]) -> str:
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _set_revision_headers(schema: dict[str, Any], response: Response) -> str:
    revision = _get_openapi_revision(schema)
    response.headers["ETag"] = f'W/"{revision}"'
    response.headers["X-OpenAPI-Revision"] = revision
    return revision


def _encode_openapi_path(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_openapi_path(encoded_path: str) -> str:
    padding = "=" * (-len(encoded_path) % 4)
    try:
        decoded = base64.urlsafe_b64decode((encoded_path + padding).encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as error:
        raise HTTPException(
            status_code=400,
            detail="Invalid encoded path. Use unpadded base64url for the raw OpenAPI path.",
        ) from error
    if not decoded.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid encoded path. Decoded OpenAPI paths must start with '/'.",
        )
    return decoded


def _parse_expand(raw_expand: list[str] | None) -> set[str]:
    if not raw_expand:
        return set()
    expand: set[str] = set()
    for item in raw_expand:
        for token in item.split(","):
            normalized = token.strip()
            if normalized:
                expand.add(normalized)
    invalid = sorted(expand - _ALLOWED_EXPANDS)
    if invalid:
        allowed = ", ".join(sorted(_ALLOWED_EXPANDS))
        rejected = ", ".join(invalid)
        raise HTTPException(
            status_code=400,
            detail=f"Invalid expand values: {rejected}. Allowed values: {allowed}.",
        )
    return expand


def _collect_schema_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith(_SCHEMA_REF_PREFIX):
            refs.add(ref.removeprefix(_SCHEMA_REF_PREFIX))
        for nested in value.values():
            _collect_schema_refs(nested, refs)
        return
    if isinstance(value, list):
        for nested in value:
            _collect_schema_refs(nested, refs)


def _expand_schema_refs(
    component_schemas: dict[str, Any],
    root_refs: Iterable[str],
    depth: int,
) -> dict[str, Any]:
    expanded: dict[str, Any] = {}
    frontier = set(root_refs)
    for _ in range(depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for schema_name in sorted(frontier):
            if schema_name in expanded:
                continue
            schema = component_schemas.get(schema_name)
            if not isinstance(schema, dict):
                raise HTTPException(
                    status_code=500,
                    detail=f"OpenAPI schema references missing component schema {schema_name!r}.",
                )
            expanded[schema_name] = schema
            discovered_refs: set[str] = set()
            _collect_schema_refs(schema, discovered_refs)
            next_frontier.update(discovered_refs)
        frontier = next_frontier.difference(expanded)
    return {schema_name: expanded[schema_name] for schema_name in sorted(expanded)}


def _operation_fragment(path_item: dict[str, Any], operation: str) -> dict[str, Any]:
    operation_fragment = path_item.get(operation)
    if not isinstance(operation_fragment, dict):
        raise HTTPException(
            status_code=404,
            detail=f"OpenAPI operation {operation!r} not found for path.",
        )
    return operation_fragment


@router.get("/openapi/index")
def get_openapi_index(request: Request, response: Response) -> dict[str, Any]:
    schema = _get_openapi_schema(request)
    revision = _set_revision_headers(schema, response)
    paths = _get_openapi_paths(schema)
    component_schemas = _get_component_schemas(schema)

    path_entries: list[dict[str, Any]] = []
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            raise HTTPException(
                status_code=500,
                detail=f"OpenAPI path entry for {path!r} is not an object.",
            )
        operations: list[dict[str, Any]] = []
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            tags = operation.get("tags")
            operations.append(
                {
                    "method": method,
                    "operation_id": operation.get("operationId"),
                    "summary": operation.get("summary"),
                    "tags": tags if isinstance(tags, list) else [],
                }
            )
        path_entries.append(
            {
                "path": path,
                "encoded_path": _encode_openapi_path(path),
                "operations": operations,
            }
        )

    return {
        "revision": revision,
        "openapi": schema.get("openapi"),
        "info": schema.get("info"),
        "paths": path_entries,
        "schemas": sorted(component_schemas),
    }


@router.get("/openapi/paths/{encoded_path}")
def get_openapi_path_fragment(
    encoded_path: str,
    request: Request,
    response: Response,
    expand: Annotated[list[str] | None, Query()] = None,
    depth: Annotated[int, Query(ge=0, le=_MAX_EXPANSION_DEPTH)] = 1,
) -> dict[str, Any]:
    schema = _get_openapi_schema(request)
    revision = _set_revision_headers(schema, response)
    path = _decode_openapi_path(encoded_path)
    expand_values = _parse_expand(expand)
    paths = _get_openapi_paths(schema)
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        raise HTTPException(status_code=404, detail=f"OpenAPI path {path!r} not found.")

    result: dict[str, Any] = {
        "revision": revision,
        "path": path,
        "encoded_path": encoded_path,
        "expand": sorted(expand_values),
        "depth": depth,
        "path_item": path_item,
    }
    if "schemas" in expand_values:
        schema_refs: set[str] = set()
        _collect_schema_refs(path_item, schema_refs)
        result["schemas"] = _expand_schema_refs(_get_component_schemas(schema), schema_refs, depth)
    return result


@router.get("/openapi/schemas/{schema_name}")
def get_openapi_component_schema(
    schema_name: str,
    request: Request,
    response: Response,
    depth: int = Query(default=1, ge=0, le=_MAX_EXPANSION_DEPTH),
) -> dict[str, Any]:
    schema = _get_openapi_schema(request)
    revision = _set_revision_headers(schema, response)
    component_schemas = _get_component_schemas(schema)
    component_schema = component_schemas.get(schema_name)
    if not isinstance(component_schema, dict):
        raise HTTPException(status_code=404, detail=f"OpenAPI schema {schema_name!r} not found.")

    schema_refs: set[str] = set()
    _collect_schema_refs(component_schema, schema_refs)
    schema_refs.discard(schema_name)
    return {
        "revision": revision,
        "schema_name": schema_name,
        "depth": depth,
        "schema": component_schema,
        "schemas": _expand_schema_refs(component_schemas, schema_refs, depth),
    }


@router.get("/openapi/fragment")
def get_openapi_fragment(
    request: Request,
    response: Response,
    path: str = Query(...),
    operation: str | None = Query(
        default=None, pattern="^(get|put|post|delete|options|head|patch|trace)$"
    ),
    expand: Annotated[list[str] | None, Query()] = None,
    depth: Annotated[int, Query(ge=0, le=_MAX_EXPANSION_DEPTH)] = 1,
) -> dict[str, Any]:
    schema = _get_openapi_schema(request)
    revision = _set_revision_headers(schema, response)
    expand_values = _parse_expand(expand)
    paths = _get_openapi_paths(schema)
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        raise HTTPException(status_code=404, detail=f"OpenAPI path {path!r} not found.")

    fragment: dict[str, Any]
    schema_source: Any
    if operation is None:
        if "request" in expand_values or "response" in expand_values:
            raise HTTPException(
                status_code=400,
                detail="'operation' is required when expand includes 'request' or 'response'.",
            )
        fragment = {"path_item": path_item}
        schema_source = path_item
    else:
        operation_fragment = _operation_fragment(path_item, operation)
        fragment = {"operation": operation_fragment}
        if "request" in expand_values and "requestBody" in operation_fragment:
            fragment["request_body"] = operation_fragment["requestBody"]
        if "response" in expand_values and "responses" in operation_fragment:
            fragment["responses"] = operation_fragment["responses"]
        schema_source = fragment

    if "schemas" in expand_values:
        schema_refs: set[str] = set()
        _collect_schema_refs(schema_source, schema_refs)
        fragment["schemas"] = _expand_schema_refs(
            _get_component_schemas(schema), schema_refs, depth
        )

    return {
        "revision": revision,
        "path": path,
        "operation": operation,
        "expand": sorted(expand_values),
        "depth": depth,
        "fragment": fragment,
    }
