"""HTTP MCP E2E integration test.

Verifies the MCP streamable-http transport works end-to-end: create a FastMCP
server with real runtime, connect via the MCP client library, and exercise
tools through the full protocol stack (initialize -> list_tools -> call_tool).

The test bypasses the FastAPI mount (which adds a trailing-slash redirect that
breaks MCP clients) by constructing the MCP Starlette sub-app directly and
testing it with httpx.ASGITransport.  This is the same sub-app that
``mount_mcp_app`` would mount, so wiring bugs are caught here.

Transport security (DNS-rebinding protection) is disabled because the
``httpx.ASGITransport`` synthetic host "testserver" would otherwise be
rejected.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.api.app_factory import create_app
from app.transports.mcp.tools import register_tools

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime():
    """Create a real MarivoRuntime backed by in-memory storage."""
    app = create_app(db_path=":memory:")
    return app.state.runtime


@asynccontextmanager
async def _mcp_client(
    server: FastMCP,
) -> AsyncGenerator[ClientSession, None]:
    """Yield an initialized MCP ClientSession connected to *server*.

    Handles the session-manager lifespan, httpx transport, and MCP
    handshake so individual tests stay concise.
    """
    mcp_app = server.streamable_http_app()
    session_manager = server.session_manager

    async with session_manager.run():
        http_transport = httpx.ASGITransport(app=mcp_app)
        async with (
            httpx.AsyncClient(
                transport=http_transport,
                base_url="http://testserver",
            ) as http_client,
            streamable_http_client(
                "http://testserver/mcp",
                http_client=http_client,
            ) as (read, write, _get_session_id),
            ClientSession(read, write) as client_session,
        ):
            await client_session.initialize()
            yield client_session


def _make_server(runtime) -> FastMCP:
    """Build a FastMCP server identical to the one ``mount_mcp_app`` creates."""
    server = FastMCP(
        "marivo",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )
    register_tools(server, runtime)
    return server


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_initialize_and_list_tools(runtime):
    """MCP client can initialize, handshake, and list all registered tools."""
    server = _make_server(runtime)

    async with _mcp_client(server) as session:
        tools_result = await session.list_tools()
        tool_names = {t.name for t in tools_result.tools}

        # Spot-check a representative subset of expected tools
        for expected in (
            "observe",
            "compare",
            "decompose",
            "create_session",
            "health_check",
        ):
            assert expected in tool_names, f"Tool {expected!r} missing from MCP surface"


@pytest.mark.asyncio
async def test_mcp_health_check(runtime):
    """health_check tool returns ok status through the MCP transport."""
    server = _make_server(runtime)

    async with _mcp_client(server) as session:
        result = await session.call_tool("health_check", {})
        payload = json.loads(result.content[0].text)
        assert payload["data"]["status"] == "ok"
        assert payload["error"] is None


@pytest.mark.asyncio
async def test_mcp_create_and_get_session(runtime):
    """create_session -> get_session round-trips through MCP transport."""
    server = _make_server(runtime)

    async with _mcp_client(server) as session:
        # Create a session
        create_result = await session.call_tool("create_session", {"goal": "e2e test session"})
        create_payload = json.loads(create_result.content[0].text)
        assert create_payload["error"] is None
        session_id = create_payload["data"]["session_id"]
        assert session_id.startswith("sess_")

        # Retrieve the same session
        get_result = await session.call_tool("get_session", {"session_id": session_id})
        get_payload = json.loads(get_result.content[0].text)
        assert get_payload["data"]["session_id"] == session_id


@pytest.mark.asyncio
async def test_mcp_list_sessions(runtime):
    """list_sessions returns the sessions created through MCP."""
    server = _make_server(runtime)

    async with _mcp_client(server) as session:
        # Create a session
        await session.call_tool("create_session", {"goal": "list test"})
        # List sessions
        result = await session.call_tool("list_sessions", {})
        payload = json.loads(result.content[0].text)
        assert payload["error"] is None
        items = payload["data"]["items"]
        assert len(items) >= 1


@pytest.mark.asyncio
async def test_mcp_tool_error_propagation(runtime):
    """MCP tools propagate domain errors as structured error payloads."""
    server = _make_server(runtime)

    async with _mcp_client(server) as session:
        # get_session with a non-existent ID should return an error payload
        result = await session.call_tool("get_session", {"session_id": "sess_nonexistent"})
        payload = json.loads(result.content[0].text)
        assert payload["error"] is not None
        assert payload["data"] is None
