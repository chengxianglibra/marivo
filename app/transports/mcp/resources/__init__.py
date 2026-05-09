"""Register read-only MCP resources that call runtime methods directly."""

from __future__ import annotations

from typing import Any

from app.transports.mcp.tools._async_bridge import call_runtime

_SUPPORTED_SEMANTIC_FAMILIES = ("models",)


def register_resources(server: Any, runtime: Any) -> None:
    """Register read-only MCP resources on a FastMCP server instance."""

    @server.resource("marivo://server/config")  # type: ignore
    async def server_config() -> str:
        """Expose minimal non-secret runtime configuration for local debugging."""
        return "marivo local\ntransport=stdio\n"

    @server.resource("marivo://sessions/{session_id}/state")  # type: ignore
    async def session_state(session_id: str) -> dict[str, Any]:
        """Mirror GET /sessions/{session_id}/state as a read-only MCP resource."""
        return await call_runtime(runtime.get_session_state, session_id=session_id)

    @server.resource("marivo://sessions/{session_id}/propositions/{proposition_id}/context")  # type: ignore
    async def proposition_context(session_id: str, proposition_id: str) -> dict[str, Any]:
        """Mirror GET /sessions/{session_id}/propositions/{proposition_id}/context as a read-only MCP resource."""
        return await call_runtime(
            runtime.get_proposition_context,
            session_id=session_id,
            proposition_id=proposition_id,
        )

    @server.resource("marivo://semantic/{family}")  # type: ignore
    async def semantic_family(family: str) -> dict[str, Any]:
        """Mirror semantic family list endpoints without adding MCP-only filtering semantics."""
        if family == "models":
            return await call_runtime(runtime.get_service("semantic_v2").list_semantic_models)
        supported = ", ".join(_SUPPORTED_SEMANTIC_FAMILIES)
        raise ValueError(
            f"Unsupported semantic family {family!r}. "
            f"Supported families: {supported}. "
            f"Datasets, relationships, and metrics require a model parameter; "
            f"use the corresponding MCP tools instead."
        )
