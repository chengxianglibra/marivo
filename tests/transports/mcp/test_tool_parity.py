"""Verify stdio and HTTP MCP transports expose the correct tool surfaces.

HTTP mode registers all tool groups including catalog/OpenAPI introspection.
Stdio mode omits catalog tools because the local runtime lacks the wired
FastAPI app and analytics engine required by those tools.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from marivo.transports.mcp.tools import register_tools


class _FakeSvc:
    """Minimal stub satisfying semantic_v2 / datasource service contracts."""

    def list_semantic_models(self, **kw):
        return {}

    def validate_osi_semantic_models(self, **kw):
        return {}

    def import_osi_semantic_models(self, **kw):
        return {}

    def export_osi_semantic_models(self, **kw):
        return {}

    def get_semantic_model(self, **kw):
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


# Catalog / OpenAPI tools are only registered in HTTP mode.
_HTTP_ONLY_TOOLS = frozenset(
    {
        "health_check",
        "list_openapi_paths",
        "get_openapi_schema",
        "get_openapi_fragment",
        "get_openapi_path_fragment",
    }
)


def _tool_names(server: FastMCP) -> set[str]:
    return {t.name for t in server._tool_manager.list_tools()}


def test_http_registers_all_tools():
    """HTTP transport registers the full tool surface including catalog tools."""
    server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(server, FakeRuntime(), transport="http")
    tools = _tool_names(server)
    assert _HTTP_ONLY_TOOLS.issubset(tools), (
        f"HTTP mode missing catalog tools: {_HTTP_ONLY_TOOLS - tools}"
    )


def test_stdio_omits_catalog_tools():
    """Stdio transport omits catalog/OpenAPI tools that require a wired app."""
    server = FastMCP("test-stdio")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = _tool_names(server)
    assert _HTTP_ONLY_TOOLS.isdisjoint(tools), (
        f"Stdio mode should not expose catalog tools: {_HTTP_ONLY_TOOLS & tools}"
    )


def test_semantic_tools_expose_compact_document_inventory() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = _tool_names(server)

    assert {
        "list_semantic_models",
        "get_semantic_model",
        "validate_osi_semantic_models",
        "import_osi_semantic_models",
        "export_osi_semantic_models",
    }.issubset(tools)
    assert {
        "create_semantic_model",
        "import_osi_document",
        "export_osi_document",
        "update_semantic_model",
        "delete_semantic_model",
        "get_semantic_model_readiness",
        "create_dataset",
        "list_datasets",
        "get_dataset",
        "update_dataset",
        "delete_dataset",
        "create_field",
        "list_fields",
        "get_field",
        "update_field",
        "delete_field",
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
    }.isdisjoint(tools)


def test_semantic_document_tools_accept_inline_document_or_file_inputs() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    validate_props = tools["validate_osi_semantic_models"].parameters["$defs"][
        "McpOsiDocumentInput"
    ]["properties"]
    import_props = tools["import_osi_semantic_models"].parameters["$defs"]["McpOsiDocumentInput"][
        "properties"
    ]
    export_props = tools["export_osi_semantic_models"].parameters["$defs"]["McpOsiExportInput"][
        "properties"
    ]

    assert set(validate_props) == {"document", "input_path"}
    assert set(import_props) == {"document", "input_path"}
    assert set(export_props) == {"semantic_model_name", "output_path"}


def test_mcp_semantic_tools_do_not_expose_requesting_user() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    checked_names = [
        "list_semantic_models",
        "get_semantic_model",
    ]
    for name in checked_names:
        assert "requesting_user" not in tools[name].parameters.get("properties", {})


def test_shared_tools_identical_schema():
    """Tools present in both modes have identical parameter schemas."""
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime(), transport="stdio")
    register_tools(http_server, FakeRuntime(), transport="http")

    stdio_tools = {t.name: t.parameters for t in stdio_server._tool_manager.list_tools()}
    http_tools = {t.name: t.parameters for t in http_server._tool_manager.list_tools()}

    shared = set(stdio_tools) & set(http_tools)
    for name in sorted(shared):
        assert stdio_tools[name] == http_tools[name], f"Schema diverged for {name}"


def test_stdio_is_subset_of_http():
    """Stdio tool set is a strict subset of HTTP tool set."""
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime(), transport="stdio")
    register_tools(http_server, FakeRuntime(), transport="http")

    stdio_tools = _tool_names(stdio_server)
    http_tools = _tool_names(http_server)
    assert stdio_tools < http_tools, (
        f"Stdio tools should be a strict subset of HTTP tools; "
        f"stdio-only: {stdio_tools - http_tools}"
    )
