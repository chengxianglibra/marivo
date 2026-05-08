"""Registration functions for MCP intent tools."""

from __future__ import annotations

from typing import Any

from app.transports.mcp.tools._async_bridge import call_runtime
from app.transports.mcp.tools.schemas import McpObserveTimeScope, ObserveScope


def register_observe(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore[misc]
    async def observe(
        session_id: str,
        metric: str,
        time_scope: McpObserveTimeScope,
        granularity: str | None = None,
        dimensions: list[str] | None = None,
        scope: ObserveScope | None = None,
        result_mode: str | None = None,
        calendar_policy_ref: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"metric": metric, "time_scope": time_scope.model_dump()}
        if granularity is not None:
            params["granularity"] = granularity
        if dimensions is not None:
            params["dimensions"] = dimensions
        if scope is not None:
            params["scope"] = scope.model_dump()
        if result_mode is not None:
            params["result_mode"] = result_mode
        if calendar_policy_ref is not None:
            params["calendar_policy_ref"] = calendar_policy_ref
        return await call_runtime(runtime.observe, session_id=session_id, params=params)
