from __future__ import annotations

from factum_mcp.config import FactumMcpConfig
from factum_mcp.sdk import FastMcpServer


def register_tools(server: FastMcpServer, config: FactumMcpConfig) -> None:
    """Register the minimal scaffold tool set.

    T2 only proves server startup and registration. Real HTTP-backed tools land
    in T3 and later tasks.
    """

    @server.tool()
    def health_check() -> dict[str, object]:
        """Scaffold-only tool placeholder for the future Factum health endpoint."""
        return {
            "ok": False,
            "status_code": 501,
            "error": {
                "code": "NOT_IMPLEMENTED",
                "message": "health_check is a scaffold placeholder in T2. "
                "HTTP transport will be wired in T3.",
            },
            "meta": {
                "factum_path": "/health",
                "method": "GET",
                "base_url": config.base_url,
            },
        }
