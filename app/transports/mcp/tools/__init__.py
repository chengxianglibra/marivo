"""Register all MCP tools on a FastMCP server instance."""

from __future__ import annotations

from typing import Any

from app.transports.mcp.tools.catalog import register_catalog_tools
from app.transports.mcp.tools.intents import (
    register_attribute,
    register_compare,
    register_correlate,
    register_decompose,
    register_detect,
    register_diagnose,
    register_forecast,
    register_observe,
    register_test_intent,
    register_validate,
)
from app.transports.mcp.tools.session import register_session_tools


def register_tools(server: Any, runtime: Any) -> None:
    register_observe(server, runtime)
    register_compare(server, runtime)
    register_decompose(server, runtime)
    register_detect(server, runtime)
    register_correlate(server, runtime)
    register_test_intent(server, runtime)
    register_forecast(server, runtime)
    register_attribute(server, runtime)
    register_diagnose(server, runtime)
    register_validate(server, runtime)
    register_session_tools(server, runtime)
    register_catalog_tools(server, runtime)
