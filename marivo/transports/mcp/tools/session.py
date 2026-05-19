"""Registration functions for MCP session lifecycle tools."""

from __future__ import annotations

from typing import Any

from marivo.transports.mcp.tools._async_bridge import call_runtime


def register_session_tools(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def create_session(
        goal: str,
        budget: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create an investigation session via POST /sessions using the canonical session root request fields."""
        kwargs: dict[str, Any] = {}
        if budget is not None:
            kwargs["budget"] = budget
        if policy is not None:
            kwargs["policy"] = policy
        return await call_runtime(runtime.create_session, goal=goal, **kwargs)

    @server.tool()  # type: ignore
    async def list_sessions(
        status: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """List investigation sessions via GET /sessions."""
        kwargs: dict[str, Any] = {}
        if status is not None:
            kwargs["status"] = status
        if session_id is not None:
            kwargs["session_id"] = session_id
        if limit is not None:
            kwargs["limit"] = limit
        if page_token is not None:
            kwargs["page_token"] = page_token
        return await call_runtime(runtime.list_sessions, **kwargs)

    @server.tool()  # type: ignore
    async def get_session(session_id: str) -> dict[str, Any]:
        """Read one canonical session root via GET /sessions/{session_id} without inlining state or proposition context."""
        return await call_runtime(runtime.get_session, session_id=session_id)

    @server.tool()  # type: ignore
    async def get_session_trace(session_id: str) -> dict[str, Any]:
        """Read the agent-facing execution trace via GET /sessions/{session_id}/trace; use state/context tools for evidence conclusions."""
        return await call_runtime(runtime.get_session_trace, session_id=session_id)

    @server.tool()  # type: ignore
    async def terminate_session(
        session_id: str,
        terminal_reason: str = "user_closed",
    ) -> dict[str, Any]:
        """Explicitly terminate one session via POST /sessions/{session_id}/terminate using the canonical session lifecycle contract."""
        return await call_runtime(
            runtime.terminate_session,
            session_id=session_id,
            terminal_reason=terminal_reason,
        )

    @server.tool()  # type: ignore
    async def get_session_state(
        session_id: str,
        metric: str | None = None,
        entity: str | None = None,
        slice: dict[str, Any] | None = None,
        proposition_types: list[str] | None = None,
        origin_kinds: list[str] | None = None,
        assessment_presence: str | None = None,
        assessment_statuses: list[str] | None = None,
        has_blocking_gaps: bool | None = None,
        limit: int | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Read the session-level canonical decision surface via GET /sessions/{session_id}/state."""
        kwargs: dict[str, Any] = {}
        if metric is not None:
            kwargs["metric"] = metric
        if entity is not None:
            kwargs["entity"] = entity
        if slice is not None:
            kwargs["slice"] = slice
        if proposition_types is not None:
            kwargs["proposition_types"] = proposition_types
        if origin_kinds is not None:
            kwargs["origin_kinds"] = origin_kinds
        if assessment_presence is not None:
            kwargs["assessment_presence"] = assessment_presence
        if assessment_statuses is not None:
            kwargs["assessment_statuses"] = assessment_statuses
        if has_blocking_gaps is not None:
            kwargs["has_blocking_gaps"] = has_blocking_gaps
        if limit is not None:
            kwargs["limit"] = limit
        if page_token is not None:
            kwargs["page_token"] = page_token
        return await call_runtime(runtime.get_session_state, session_id=session_id, **kwargs)

    @server.tool()  # type: ignore
    async def get_proposition_context(
        session_id: str,
        proposition_id: str,
    ) -> dict[str, Any]:
        """Read the proposition-level canonical minimal closure via GET /sessions/{session_id}/propositions/{proposition_id}/context."""
        return await call_runtime(
            runtime.get_proposition_context,
            session_id=session_id,
            proposition_id=proposition_id,
        )
