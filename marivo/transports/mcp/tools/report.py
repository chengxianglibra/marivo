"""Registration function for the MCP report export tool."""

from __future__ import annotations

from typing import Any

from marivo.transports.mcp.tools._async_bridge import call_runtime


def register_report_tools(server: Any, runtime: Any) -> None:
    @server.tool()  # type: ignore
    async def export_report(
        session_id: str,
        output_path: str,
    ) -> dict[str, Any]:
        """Generate a static HTML report for an analysis session.

        The report contains every intent step, SQL executed, artifact results,
        propositions, findings, assessments, and evidence gaps. No JavaScript
        dependencies; tables and text only.

        Can be called at any time (active or terminated session).
        """
        return await call_runtime(
            runtime.export_report,
            session_id=session_id,
            output_path=output_path,
        )
