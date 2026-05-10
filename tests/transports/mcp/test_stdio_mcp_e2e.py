"""Stdio MCP E2E test: verify marivo mcp server construction and tool registration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


class _FakeSvc:
    """Minimal stub satisfying semantic_v2 / datasource service contracts."""

    def create_semantic_model(self, **kw):
        return {}

    def list_semantic_models(self, **kw):
        return {}

    def import_osi_document(self, **kw):
        return {}

    def get_semantic_model(self, **kw):
        return {}

    def update_semantic_model(self, **kw):
        return {}

    def delete_semantic_model(self, **kw):
        return {}

    def get_readiness(self, **kw):
        return {}

    def create_dataset(self, **kw):
        return {}

    def list_datasets(self, **kw):
        return {}

    def get_dataset(self, **kw):
        return {}

    def update_dataset(self, **kw):
        return {}

    def delete_dataset(self, **kw):
        return {}

    def create_relationship(self, **kw):
        return {}

    def list_relationships(self, **kw):
        return {}

    def get_relationship(self, **kw):
        return {}

    def update_relationship(self, **kw):
        return {}

    def delete_relationship(self, **kw):
        return {}

    def create_metric(self, **kw):
        return {}

    def list_metrics(self, **kw):
        return {}

    def get_metric(self, **kw):
        return {}

    def update_metric(self, **kw):
        return {}

    def delete_metric(self, **kw):
        return {}

    def register_datasource(self, **kw):
        return {}

    def list_datasources(self, **kw):
        return {}

    def get_datasource(self, **kw):
        return {}

    def update_datasource(self, **kw):
        return {}

    def delete_datasource(self, **kw):
        return {}

    def browse_catalog_schemas(self, **kw):
        return {}

    def browse_catalog_tables(self, **kw):
        return {}

    def browse_catalog_columns(self, **kw):
        return {}

    def preview_table(self, **kw):
        return {}


class FakeRuntime:
    """Minimal stub satisfying register_tools' runtime contract."""

    _services: dict[str, _FakeSvc] = {"semantic_v2": _FakeSvc(), "datasource": _FakeSvc()}

    def get_service(self, name: str) -> _FakeSvc:
        return self._services[name]

    # Methods called by call_runtime in intent tools
    def observe(self, **kw):
        return {}

    def compare(self, **kw):
        return {}

    def decompose(self, **kw):
        return {}

    def detect(self, **kw):
        return {}

    def correlate(self, **kw):
        return {}

    def test(self, **kw):
        return {}

    def forecast(self, **kw):
        return {}

    def attribute(self, **kw):
        return {}

    def diagnose(self, **kw):
        return {}

    def validate(self, **kw):
        return {}

    def create_session(self, **kw):
        return {}

    def list_sessions(self, **kw):
        return {}

    def get_session(self, **kw):
        return {}

    def terminate_session(self, **kw):
        return {}

    def get_session_state(self, **kw):
        return {}

    def query_session_state(self, **kw):
        return {}

    def get_proposition_context(self, **kw):
        return {}


def test_marivo_mcp_entry_point_callable():
    """The marivo mcp subcommand handler is callable."""
    from marivo.transports.cli.cmd_mcp import handle

    assert callable(handle)


def test_stdio_server_registers_tools():
    """A stdio-configured FastMCP server registers tools without catalog/OpenAPI group."""
    from marivo.transports.mcp.tools import register_tools

    server = FastMCP("marivo")  # Same name as cmd_mcp.py uses
    register_tools(server, FakeRuntime(), transport="stdio")

    tools = server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]

    # Catalog / OpenAPI tools must NOT be present in stdio mode
    for excluded in (
        "health_check",
        "list_openapi_paths",
        "get_openapi_schema",
        "get_openapi_fragment",
        "get_openapi_path_fragment",
    ):
        assert excluded not in tool_names, f"Stdio mode should not expose {excluded}"

    # Verify all expected stdio tools are present
    expected_tools = [
        # Intent tools
        "observe",
        "compare",
        "decompose",
        "detect",
        "correlate",
        "test_intent",
        "forecast",
        "attribute",
        "diagnose",
        "validate",
        # Session tools
        "create_session",
        "list_sessions",
        "get_session",
        "terminate_session",
        "get_session_state",
        "query_session_state",
        "get_proposition_context",
        # Semantic tools
        "create_semantic_model",
        "list_semantic_models",
        "import_osi_document",
        "get_semantic_model",
        "update_semantic_model",
        "delete_semantic_model",
        "get_semantic_model_readiness",
        "create_dataset",
        "list_datasets",
        "get_dataset",
        "update_dataset",
        "delete_dataset",
        "create_relationship",
        "list_relationships",
        "get_relationship",
        "update_relationship",
        "delete_relationship",
        "create_metric",
        "list_metrics",
        "get_metric",
        "update_metric",
        "delete_metric",
        # Datasource tools
        "list_datasources",
        "create_datasource",
        "get_datasource",
        "update_datasource",
        "delete_datasource",
        "browse_schemas",
        "browse_tables",
        "browse_columns",
        "preview_table",
    ]

    for tool_name in expected_tools:
        assert tool_name in tool_names, f"Missing tool: {tool_name}"


def test_marivo_mcp_help_flag():
    """marivo mcp subcommand is registered and responds to --help."""
    marivo_bin = Path(sys.executable).parent / "marivo"
    result = subprocess.run(
        [str(marivo_bin), "mcp", "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
