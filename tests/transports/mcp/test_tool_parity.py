"""Verify stdio and HTTP MCP transports register an identical tool surface."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.transports.mcp.tools import register_tools


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

    def discover_catalog(self, **kw):
        return {}

    def list_openapi_paths(self, **kw):
        return {}

    def get_openapi_schema(self, **kw):
        return {}

    def get_openapi_fragment(self, **kw):
        return {}

    def get_openapi_path_fragment(self, **kw):
        return {}


def test_tool_surface_parity():
    """stdio and HTTP MCP must register identical tool surfaces."""
    stdio_server = FastMCP("test-stdio")
    http_server = FastMCP("test-http", stateless_http=True, json_response=True)
    register_tools(stdio_server, FakeRuntime())
    register_tools(http_server, FakeRuntime())

    stdio_tools = {t.name: t.parameters for t in stdio_server._tool_manager.list_tools()}
    http_tools = {t.name: t.parameters for t in http_server._tool_manager.list_tools()}

    assert stdio_tools.keys() == http_tools.keys(), (
        f"Tool name mismatch: stdio={sorted(stdio_tools)} http={sorted(http_tools)}"
    )
    for name in stdio_tools:
        assert stdio_tools[name] == http_tools[name], f"Schema diverged for {name}"
