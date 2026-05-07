from __future__ import annotations

import sys
from pathlib import Path

from marivo_mcp.config import MarivoMcpConfig, MarivoMcpConfigError, load_config_from_env
from marivo_mcp.diagnostics import emit_diagnostic
from marivo_mcp.http_client import ResolvingMarivoHttpClient
from marivo_mcp.init_cli import main as init_main
from marivo_mcp.resources import register_resources
from marivo_mcp.sdk import FastMcpServer, MarivoMcpDependencyError, load_fastmcp
from marivo_mcp.target_resolution import resolve_target
from marivo_mcp.tools import register_tools


def build_server() -> FastMcpServer:
    """Build the standalone stdio MCP application."""
    config = _resolve_startup_config(load_config_from_env())
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
    if _should_embed(typed_config):
        # Embedded mode: use EmbeddedBackend instead of HTTP client.
        # The backend is attached as server state; tools/resources still
        # register normally but use the embedded path at call time.
        server._marivo_embedded_backend = _create_embedded_backend(typed_config)  # type: ignore[attr-defined]
    if _should_defer_target_resolution(typed_config):
        register_tools(server, typed_config, client_factory=ResolvingMarivoHttpClient)
        register_resources(server, typed_config, client_factory=ResolvingMarivoHttpClient)
    else:
        register_tools(server, typed_config)
        register_resources(server, typed_config)
    return server


def main() -> None:
    """Entrypoint for the standalone marivo-mcp subprocess."""
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        init_main(sys.argv[2:])
        return
    try:
        config = _resolve_startup_config(load_config_from_env())
        if config.transport == "streamable-http":
            _run_streamable_http(config)
            return
        _run_stdio(config)
    except (MarivoMcpConfigError, MarivoMcpDependencyError) as error:
        if isinstance(error, MarivoMcpConfigError) and hasattr(error, "code"):
            emit_diagnostic(
                "startup_failed",
                code=getattr(error, "code", None),
                detail=getattr(error, "detail", None),
                guidance=getattr(error, "guidance", None),
            )
        raise SystemExit(str(error)) from error


def main_http() -> None:
    """Entrypoint for the standalone marivo-mcp Streamable HTTP subprocess."""
    try:
        raw_config = load_config_from_env().model_copy(update={"transport": "streamable-http"})
        config = resolve_target(raw_config).config
        _run_streamable_http(config)
    except (MarivoMcpConfigError, MarivoMcpDependencyError) as error:
        if isinstance(error, MarivoMcpConfigError) and hasattr(error, "code"):
            emit_diagnostic(
                "startup_failed",
                code=getattr(error, "code", None),
                detail=getattr(error, "detail", None),
                guidance=getattr(error, "guidance", None),
            )
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


def _resolve_startup_config(config: MarivoMcpConfig) -> MarivoMcpConfig:
    if _should_defer_target_resolution(config):
        return config
    return resolve_target(config).config


def _should_defer_target_resolution(config: MarivoMcpConfig) -> bool:
    return config.transport == "stdio" and config.mode != "remote" and config.base_url is None


def _should_embed(config: MarivoMcpConfig) -> bool:
    """Determine if we should create an embedded runtime."""
    if config.mode == "remote":
        return False
    if config.mode == "local" and config.embedded:
        return True
    if config.mode == "auto":
        workspace = config.workspace_root
        if workspace:
            return (Path(workspace) / ".marivo" / "marivo.toml").is_file()
        return (Path.cwd() / ".marivo" / "marivo.toml").is_file()
    return False


def _create_embedded_backend(config: MarivoMcpConfig) -> object:
    """Lazy import + create embedded backend."""
    try:
        from app.profiles.local import LocalConfig, create_local_runtime
        from app.transports.mcp.backend import EmbeddedBackend

        workspace_root = Path(config.workspace_root) if config.workspace_root else Path.cwd()
        local_config = LocalConfig(workspace_root=workspace_root)
        runtime = create_local_runtime(local_config, explicit_local=True)
        session_id = runtime.create_session(goal="MCP session")
        backend = EmbeddedBackend(runtime)
        backend._default_session_id = session_id
        return backend
    except ImportError as e:
        raise RuntimeError(
            f"Embedded mode requires marivo-mcp[local]: pip install marivo-mcp[local]\n{e}"
        ) from e
