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

    def create_semantic_model(self, **kw):
        return {}

    def list_semantic_models(self, **kw):
        return {}

    def import_osi_document(self, **kw):
        return {}

    def export_osi_document(self, **kw):
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

    def create_field(self, **kw):
        return {}

    def list_fields(self, **kw):
        return {}

    def get_field(self, **kw):
        return {}

    def update_field(self, **kw):
        return {}

    def delete_field(self, **kw):
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


def test_semantic_tools_include_import_export_and_field_crud() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = _tool_names(server)

    assert "import_osi_document" in tools
    assert "export_osi_document" in tools
    assert "create_field" in tools
    assert "list_fields" in tools
    assert "get_field" in tools
    assert "update_field" in tools
    assert "delete_field" in tools


def test_mcp_semantic_tools_do_not_expose_requesting_user() -> None:
    server = FastMCP("test")
    register_tools(server, FakeRuntime(), transport="stdio")
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}

    checked_names = [
        "list_semantic_models",
        "get_semantic_model",
        "get_semantic_model_readiness",
        "list_datasets",
        "get_dataset",
        "list_fields",
        "get_field",
        "list_relationships",
        "get_relationship",
        "list_metrics",
        "get_metric",
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
