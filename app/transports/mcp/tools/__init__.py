"""Register all MCP tools on a FastMCP server instance."""

from __future__ import annotations

from typing import Any

from app.transports.mcp.tools.intents import register_observe
from app.transports.mcp.tools.session import register_session_tools


def register_tools(server: Any, runtime: Any) -> None:
    register_observe(server, runtime)
    register_session_tools(server, runtime)
