"""Stdio MCP E2E test: verify marivo mcp server construction and tool registration."""

from __future__ import annotations

import getpass
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP


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

    def delete_semantic_model(self, **kw):
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


class RecordingSemanticSvc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_semantic_models(self, **kw):
        return {}

    def validate_osi_semantic_models(self, **kw):
        self.calls.append(("validate", kw))
        return {"valid": True}

    def import_osi_semantic_models(self, **kw):
        self.calls.append(("import", kw))
        return {"imported": True}

    def export_osi_semantic_models(self, **kw):
        self.calls.append(("export", kw))
        return {"version": "0.1.1", "semantic_model": []}

    def get_semantic_model(self, **kw):
        return {}

    def delete_semantic_model(self, **kw):
        self.calls.append(("delete", kw))
        return None


class RecordingDatasourceSvc(_FakeSvc):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def preview_table(self, **kw):
        self.calls.append(("preview", kw))
        return {"previewed": True}


class RecordingRuntime(FakeRuntime):
    def __init__(self) -> None:
        self.semantic = RecordingSemanticSvc()
        self.datasource = RecordingDatasourceSvc()
        self._services = {"semantic_v2": self.semantic, "datasource": self.datasource}


def test_marivo_mcp_entry_point_callable():
    """The marivo mcp subcommand handler is callable."""
    from marivo.transports.cli.cmd_mcp import handle

    assert callable(handle)


def test_stdio_mcp_falls_back_to_system_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.transports.cli.cmd_mcp import _resolve_stdio_user

    monkeypatch.delenv("MARIVO_USER", raising=False)

    assert _resolve_stdio_user() == getpass.getuser()


def test_stdio_mcp_falls_back_on_blank_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.transports.cli.cmd_mcp import _resolve_stdio_user

    monkeypatch.setenv("MARIVO_USER", "   ")

    assert _resolve_stdio_user() == getpass.getuser()


def test_stdio_mcp_uses_explicit_marivo_user(monkeypatch: pytest.MonkeyPatch) -> None:
    from marivo.transports.cli.cmd_mcp import _resolve_stdio_user

    monkeypatch.setenv("MARIVO_USER", "  alice  ")

    assert _resolve_stdio_user() == "alice"


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
        "forecast",
        "attribute",
        "diagnose",
        # Session tools
        "create_session",
        "list_sessions",
        "get_session",
        "terminate_session",
        "get_session_state",
        "query_session_state",
        "get_proposition_context",
        # Semantic tools
        "list_semantic_models",
        "get_semantic_model",
        "validate_osi_semantic_models",
        "import_osi_semantic_models",
        "export_osi_semantic_models",
        "delete_semantic_model",
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


@pytest.mark.asyncio
async def test_stdio_semantic_document_tools_support_local_json_files(tmp_path: Path):
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = FastMCP("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    doc = {"version": "0.1.1", "semantic_model": []}
    input_path = tmp_path / "semantic.json"
    output_path = tmp_path / "exported.json"
    input_path.write_text('{"version":"0.1.1","semantic_model":[]}', encoding="utf-8")

    await tools["validate_osi_semantic_models"].run({"input": {"input_path": str(input_path)}})
    await tools["import_osi_semantic_models"].run({"input": {"document": doc}})
    export_result = await tools["export_osi_semantic_models"].run(
        {"semantic_model_name": "commerce", "output_path": str(output_path)}
    )
    delete_result = await tools["delete_semantic_model"].run({"model": "commerce"})

    assert runtime.semantic.calls == [
        ("validate", {"doc_data": doc}),
        ("import", {"doc_data": doc}),
        ("export", {"semantic_model_name": "commerce"}),
        ("delete", {"name": "commerce", "owner_user": "test_user"}),
    ]
    assert output_path.read_text(encoding="utf-8") == (
        '{\n  "version": "0.1.1",\n  "semantic_model": []\n}\n'
    )
    assert export_result["data"]["output_path"] == str(output_path)
    assert delete_result == {"data": None, "error": None}


@pytest.mark.asyncio
async def test_stdio_preview_table_accepts_structured_filter_dict() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = FastMCP("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    result = await tools["preview_table"].run(
        {
            "datasource_id": "ds_test",
            "schema": "analytics",
            "table": "jobs",
            "filters": {"state": "FAILED"},
        }
    )

    assert result == {"data": {"previewed": True}, "error": None}
    assert runtime.datasource.calls == [
        (
            "preview",
            {
                "datasource_id": "ds_test",
                "schema_name": "analytics",
                "table_name": "jobs",
                "limit": 100,
                "filters": {"state": "FAILED"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_stdio_preview_table_omits_missing_filters() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = FastMCP("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    await tools["preview_table"].run(
        {
            "datasource_id": "ds_test",
            "schema": "analytics",
            "table": "jobs",
        }
    )

    assert runtime.datasource.calls == [
        (
            "preview",
            {
                "datasource_id": "ds_test",
                "schema_name": "analytics",
                "table_name": "jobs",
                "limit": 100,
            },
        )
    ]


@pytest.mark.asyncio
async def test_stdio_preview_table_rejects_filter_array() -> None:
    from marivo.transports.mcp.tools import register_tools

    runtime = RecordingRuntime()
    server = FastMCP("marivo")
    register_tools(server, runtime, transport="stdio")
    tools = {t.name: t for t in server._tool_manager.list_tools()}

    with pytest.raises(Exception, match="Input should be a valid dictionary"):
        await tools["preview_table"].run(
            {
                "datasource_id": "ds_test",
                "schema": "analytics",
                "table": "jobs",
                "filters": [{"column": "state", "value": "FAILED"}],
            }
        )


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
