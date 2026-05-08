"""Register all MCP tools on a FastMCP server instance."""

from __future__ import annotations

from typing import Any

from app.transports.mcp.tools.intents import register_observe


def register_tools(server: Any, runtime: Any) -> None:
    register_observe(server, runtime)
