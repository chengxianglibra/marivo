"""Smoke test: observe tool registered on both transports."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from marivo.transports.mcp.tools import register_tools
from marivo.transports.mcp.tools.schemas import McpTimeScope, McpTimeScopeValidated


def _local_datetime(value: str) -> str:
    from datetime import datetime

    return datetime.fromisoformat(value).astimezone().isoformat()


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


# --- McpTimeScope validation ---


def test_mcp_time_scope_accepts_valid_input():
    ts = McpTimeScope(
        field="log_time",
        start="2024-01-01T00:00:00Z",
        end="2024-01-08T00:00:00Z",
    )
    assert ts.field == "log_time"
    assert ts.start == "2024-01-01T00:00:00Z"
    assert ts.end == "2024-01-08T00:00:00Z"


def test_mcp_time_scope_defaults_naive_datetimes_to_system_timezone():
    ts = McpTimeScope(
        field="log_time",
        start="2024-01-01T00:00:00",
        end="2024-01-08 00:00:00",
    )

    assert ts.start == _local_datetime("2024-01-01T00:00:00")
    assert ts.end == _local_datetime("2024-01-08T00:00:00")


def test_mcp_time_scope_preserves_timezone_aware_input():
    ts = McpTimeScope(
        field="log_time",
        start="2024-01-01T00:00:00+08:00",
        end="2024-01-08T00:00:00+08:00",
    )

    assert ts.start == "2024-01-01T00:00:00+08:00"
    assert ts.end == "2024-01-08T00:00:00+08:00"


def test_mcp_time_scope_defaults_date_only_input_to_system_timezone_midnight():
    ts = McpTimeScope(field="log_time", start="2024-01-01", end="2024-01-08")

    assert ts.start == _local_datetime("2024-01-01T00:00:00")
    assert ts.end == _local_datetime("2024-01-08T00:00:00")


def test_mcp_time_scope_rejects_missing_field():
    with pytest.raises(ValidationError, match="field"):
        McpTimeScope(start="2024-01-01T00:00:00Z", end="2024-01-08T00:00:00Z")


def test_mcp_time_scope_rejects_empty_field():
    with pytest.raises(ValidationError, match="field"):
        McpTimeScope(field="", start="2024-01-01T00:00:00Z", end="2024-01-08T00:00:00Z")


def test_mcp_time_scope_rejects_start_ge_end():
    with pytest.raises(ValidationError, match="start must be strictly before"):
        McpTimeScope(
            field="log_time",
            start="2024-01-08T00:00:00Z",
            end="2024-01-01T00:00:00Z",
        )


def test_mcp_time_scope_compares_parsed_datetimes():
    ts = McpTimeScope(
        field="log_time",
        start="2024-01-01T01:00:00+01:00",
        end="2024-01-01T00:30:00Z",
    )

    assert ts.start == "2024-01-01T01:00:00+01:00"
    assert ts.end == "2024-01-01T00:30:00Z"


def test_mcp_time_scope_rejects_extra_fields():
    with pytest.raises(ValidationError, match="kind"):
        McpTimeScope(
            field="log_time",
            start="2024-01-01T00:00:00Z",
            end="2024-01-08T00:00:00Z",
            kind="range",
        )


def test_mcp_time_scope_validated_rejects_string():
    from pydantic import TypeAdapter

    with pytest.raises(ValidationError, match="time_scope_canonical_required"):
        TypeAdapter(McpTimeScopeValidated).validate_python("2024-01-01~2024-01-08")
