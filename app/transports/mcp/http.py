"""Mount MCP streamable-http transport on a FastAPI app."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def mount_mcp_app(
    fastapi_app: FastAPI,
    runtime: Any,
    *,
    path: str = "/mcp",
) -> None:
    """Mount FastMCP streamable-http app under the given FastAPI app.

    Must be called AFTER UserIdentityMiddleware and TimingMiddleware are
    registered so the middleware covers /mcp/... requests. Both middlewares
    are pure ASGI and do not buffer SSE responses.
    """
    from mcp.server.fastmcp import FastMCP

    from app.transports.mcp.resources import register_resources
    from app.transports.mcp.tools import register_tools

    server = FastMCP(
        "marivo",
        stateless_http=True,
        json_response=True,
    )
    register_tools(server, runtime)
    register_resources(server, runtime)
    fastapi_app.mount(path, server.streamable_http_app())
