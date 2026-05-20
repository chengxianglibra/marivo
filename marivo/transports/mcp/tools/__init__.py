"""Register MCP tools on a FastMCP server instance.

Transport modes determine which tool groups are available:
- "http": full tool surface (requires FastAPI app wired for OpenAPI introspection)
- "stdio": no OpenAPI/catalog tools (local runtime lacks app/analytics wiring)
"""

from __future__ import annotations

from typing import Any, Literal

from marivo.transports.mcp.tools.calendar import register_calendar_tools
from marivo.transports.mcp.tools.catalog import register_catalog_tools
from marivo.transports.mcp.tools.datasource import register_datasource_tools
from marivo.transports.mcp.tools.intents import (
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
from marivo.transports.mcp.tools.report import register_report_tools
from marivo.transports.mcp.tools.semantic import register_semantic_tools
from marivo.transports.mcp.tools.session import register_session_tools

TransportMode = Literal["http", "stdio"]


def register_tools(
    server: Any,
    runtime: Any,
    *,
    transport: TransportMode = "http",
) -> None:
    # Intent tools — available in all modes
    register_observe(server, runtime)
    register_compare(server, runtime)
    register_decompose(server, runtime)
    register_detect(server, runtime)
    register_correlate(server, runtime)
    register_forecast(server, runtime)
    register_attribute(server, runtime)
    register_diagnose(server, runtime)
    register_test_intent(server, runtime)
    register_validate(server, runtime)

    # Session tools — available in all modes
    register_session_tools(server, runtime)

    # Datasource tools — available in all modes
    register_datasource_tools(server, runtime)

    # Semantic tools — available in all modes
    register_semantic_tools(server, runtime)

    # Calendar data tools — available in all modes
    register_calendar_tools(server, runtime)

    # Report tools — available in all modes
    register_report_tools(server, runtime)

    # Catalog / OpenAPI introspection — HTTP only (requires wired app + analytics)
    if transport == "http":
        register_catalog_tools(server, runtime)
