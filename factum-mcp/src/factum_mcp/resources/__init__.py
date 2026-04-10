from __future__ import annotations

from factum_mcp.config import FactumMcpConfig
from factum_mcp.sdk import FastMcpServer


def register_resources(server: FastMcpServer, config: FactumMcpConfig) -> None:
    """Register scaffold resources.

    Resources are intentionally minimal in T2. This keeps the adapter startup
    path in place without inventing canonical read behavior before T9.
    """

    @server.resource("factum://server/config")
    def server_config() -> str:
        """Expose minimal non-secret runtime configuration for local debugging."""
        return (
            "factum-mcp scaffold\n"
            f"base_url={config.base_url}\n"
            f"timeout_ms={config.timeout_ms}\n"
            f"openapi_cache_ttl_sec={config.openapi_cache_ttl_sec}\n"
            f"default_source_id={config.default_source_id or ''}\n"
        )
