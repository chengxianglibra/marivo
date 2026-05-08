"""Smoke test: observe tool registered on both transports."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.transports.mcp.tools import register_tools


class _FakeRuntime:
    """Minimal stub satisfying register_tools' runtime contract."""

    semantic_v2_svc = None
    datasource_svc = None

    def observe(self, session_id: str, params: dict) -> dict:
        return {"step_type": "observe", "session_id": session_id}


def test_observe_tool_registered():
    """Verify observe tool is registered on a FastMCP server instance."""
    server = FastMCP("test-observe")
    register_tools(server, _FakeRuntime())
    tools = server._tool_manager.list_tools()
    tool_names = [t.name for t in tools]
    assert "observe" in tool_names, f"observe not found in {tool_names}"


def test_marivo_stdio_entry_point_importable():
    """Verify marivo-stdio entry point is importable."""
    from app.transports.mcp.stdio import main

    assert callable(main)
