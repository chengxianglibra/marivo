"""Smoke test: observe tool registered on both transports."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from marivo.transports.mcp.tools import register_tools


class _FakeRuntime:
    """Minimal stub satisfying register_tools' runtime contract."""

    _services: dict[str, Any] = {"semantic_v2": None, "datasource": None}

    def get_service(self, name: str) -> Any:
        return self._services[name]

    def observe(self, session_id: str, params: dict) -> dict:
        return {"step_type": "observe", "session_id": session_id}


def test_observe_tool_registered():
    """Verify observe tool is registered on a FastMCP server instance."""
    server = FastMCP("test-observe")
    register_tools(server, _FakeRuntime())
    tools = server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    assert "observe" in tool_names, f"observe not found in {tool_names}"


def test_marivo_mcp_entry_point_importable():
    """Verify marivo mcp subcommand handler is importable."""
    from marivo.transports.cli.cmd_mcp import handle

    assert callable(handle)
