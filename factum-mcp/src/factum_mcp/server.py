from __future__ import annotations

from factum_mcp.config import FactumMcpConfig, FactumMcpConfigError, load_config_from_env
from factum_mcp.resources import register_resources
from factum_mcp.sdk import FactumMcpDependencyError, FastMcpServer, load_fastmcp
from factum_mcp.tools import register_tools


def build_server() -> FastMcpServer:
    """Build the standalone stdio MCP application."""
    config = load_config_from_env()
    return build_server_with_config(config)


def build_server_with_config(config: object) -> FastMcpServer:
    """Build the standalone MCP application from a validated config object."""
    fastmcp_cls = load_fastmcp()
    typed_config = _coerce_config(config)
    server = fastmcp_cls(
        "factum-mcp",
        stateless_http=typed_config.http.stateless_http,
        json_response=typed_config.http.json_response,
        streamable_http_path=typed_config.http.streamable_http_path,
    )
    server.settings.host = typed_config.http.host
    server.settings.port = typed_config.http.port
    register_tools(server, typed_config)
    register_resources(server, typed_config)
    return server


def main() -> None:
    """Entrypoint for the standalone factum-mcp subprocess."""
    try:
        config = load_config_from_env()
        if config.transport == "streamable-http":
            _run_streamable_http(config)
            return
        _run_stdio(config)
    except (FactumMcpConfigError, FactumMcpDependencyError) as error:
        raise SystemExit(str(error)) from error


def main_http() -> None:
    """Entrypoint for the standalone factum-mcp Streamable HTTP subprocess."""
    try:
        config = load_config_from_env()
        _run_streamable_http(config)
    except (FactumMcpConfigError, FactumMcpDependencyError) as error:
        raise SystemExit(str(error)) from error


def _run_stdio(config: object) -> None:
    server = build_server_with_config(config)
    server.run()


def _run_streamable_http(config: object) -> None:
    server = build_server_with_config(config)
    server.run(transport="streamable-http")


def _coerce_config(config: object) -> FactumMcpConfig:
    if not isinstance(config, FactumMcpConfig):
        raise TypeError("Expected FactumMcpConfig.")
    return config
