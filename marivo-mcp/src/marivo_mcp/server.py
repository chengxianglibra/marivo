from __future__ import annotations

from marivo_mcp.config import MarivoMcpConfig, MarivoMcpConfigError, load_config_from_env
from marivo_mcp.resources import register_resources
from marivo_mcp.sdk import FastMcpServer, MarivoMcpDependencyError, load_fastmcp
from marivo_mcp.tools import register_tools


def build_server() -> FastMcpServer:
    """Build the standalone stdio MCP application."""
    config = load_config_from_env()
    return build_server_with_config(config)


def build_server_with_config(config: object) -> FastMcpServer:
    """Build the standalone MCP application from a validated config object."""
    fastmcp_cls = load_fastmcp()
    typed_config = _coerce_config(config)
    server = fastmcp_cls(
        "marivo-mcp",
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
    """Entrypoint for the standalone marivo-mcp subprocess."""
    try:
        config = load_config_from_env()
        if config.transport == "streamable-http":
            _run_streamable_http(config)
            return
        _run_stdio(config)
    except (MarivoMcpConfigError, MarivoMcpDependencyError) as error:
        raise SystemExit(str(error)) from error


def main_http() -> None:
    """Entrypoint for the standalone marivo-mcp Streamable HTTP subprocess."""
    try:
        config = load_config_from_env()
        _run_streamable_http(config)
    except (MarivoMcpConfigError, MarivoMcpDependencyError) as error:
        raise SystemExit(str(error)) from error


def _run_stdio(config: object) -> None:
    server = build_server_with_config(config)
    server.run()


def _run_streamable_http(config: object) -> None:
    server = build_server_with_config(config)
    server.run(transport="streamable-http")


def _coerce_config(config: object) -> MarivoMcpConfig:
    if not isinstance(config, MarivoMcpConfig):
        raise TypeError("Expected MarivoMcpConfig.")
    return config
