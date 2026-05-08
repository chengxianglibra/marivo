"""Registration functions for MCP health, catalog, and OpenAPI discovery tools."""

from __future__ import annotations

from typing import Any

from app.transports.mcp.tools._async_bridge import call_runtime


def register_catalog_tools(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def health_check() -> dict[str, Any]:
        """Check Marivo service health via GET /health using the shared MCP HTTP envelope."""
        return {"data": {"status": "ok"}, "error": None}

    @server.tool()  # type: ignore
    async def get_catalog() -> dict[str, Any]:
        """Read the API catalog via GET /catalog."""
        return await call_runtime(runtime.discover_catalog)

    @server.tool()  # type: ignore
    async def list_openapi_paths() -> dict[str, Any]:
        """List canonical OpenAPI paths and schema names via GET /openapi/index for low-cost contract discovery."""
        return await call_runtime(runtime.list_openapi_paths)

    @server.tool()  # type: ignore
    async def get_openapi_schema(
        schema_name: str,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Read one canonical component schema via GET /openapi/schemas/{schema_name}."""
        return await call_runtime(runtime.get_openapi_schema, schema_name=schema_name, depth=depth)

    @server.tool()  # type: ignore
    async def get_openapi_fragment(
        path: str,
        operation: str | None = None,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Read the canonical OpenAPI fragment via GET /openapi/fragment without consulting a local schema copy."""
        kwargs: dict[str, Any] = {"path": path, "depth": depth}
        if operation is not None:
            kwargs["operation"] = operation
        if expand is not None:
            kwargs["expand"] = expand
        return await call_runtime(runtime.get_openapi_fragment, **kwargs)

    @server.tool()  # type: ignore
    async def get_openapi_path_fragment(
        path: str,
        expand: list[str] | None = None,
        depth: int = 1,
    ) -> dict[str, Any]:
        """Read one canonical OpenAPI path item via GET /openapi/paths/{encoded_path}. Accepts the raw path (e.g., '/sessions'); the tool automatically encodes it as unpadded base64url. Use list_openapi_paths to discover available paths and their encoded forms."""
        kwargs: dict[str, Any] = {"path": path, "depth": depth}
        if expand is not None:
            kwargs["expand"] = expand
        return await call_runtime(runtime.get_openapi_path_fragment, **kwargs)
