from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.api import (
    approvals,
    calendar,
    datasources,
    governance,
    health,
    jobs,
    metrics,
    openapi_fragments,
    routing,
    semantic_v2,
    sessions,
)


def _patch_default_validation_error_schema(openapi_schema: dict[str, Any]) -> None:
    schemas = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    json_value_schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
            {"type": "array", "items": {"$ref": "#/components/schemas/JsonValue"}},
            {
                "type": "object",
                "additionalProperties": {"$ref": "#/components/schemas/JsonValue"},
            },
        ],
        "title": "JsonValue",
    }
    schemas["JsonValue"] = json_value_schema
    schemas["JsonValidationValue"] = json_value_schema | {"title": "JsonValidationValue"}
    validation_error = schemas.get("ValidationError")
    if not isinstance(validation_error, dict):
        return
    properties = validation_error.get("properties")
    if not isinstance(properties, dict):
        return
    if isinstance(properties.get("input"), dict):
        properties["input"] = {"$ref": "#/components/schemas/JsonValidationValue"}
    ctx = properties.get("ctx")
    if isinstance(ctx, dict) and ctx.get("type") == "object":
        ctx["additionalProperties"] = {"$ref": "#/components/schemas/JsonValidationValue"}


def _install_openapi_schema_patch(app: FastAPI) -> None:
    original_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = original_openapi()
        _patch_default_validation_error_schema(openapi_schema)
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def include_api_routers(app: FastAPI) -> None:
    for router in (
        health.router,
        openapi_fragments.router,
        sessions.router,
        datasources.router,
        routing.router,
        semantic_v2.router,
        governance.router,
        jobs.router,
        approvals.router,
        metrics.router,
        calendar.router,
    ):
        app.include_router(router)
    _install_openapi_schema_patch(app)
