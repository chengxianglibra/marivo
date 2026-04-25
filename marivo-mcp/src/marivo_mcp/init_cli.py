from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Literal, NoReturn

from marivo_mcp.config import MarivoMcpConfigError, TargetResolutionError

SupportedClient = Literal["generic", "codex"]
SupportedTransport = Literal["stdio", "streamable-http"]

_SUPPORTED_CLIENTS: tuple[SupportedClient, ...] = ("generic", "codex")


def main(argv: list[str] | None = None) -> None:
    try:
        args = _parse_args(argv)
        output = build_init_config(
            mode=args.mode,
            base_url=args.base_url,
            api_token=args.api_token,
            workspace_root=args.workspace_root,
            client=args.client,
            server_name=args.server_name,
            transport=args.transport,
            http_host=args.http_host,
            http_port=args.http_port,
            http_path=args.http_path,
        )
    except TargetResolutionError as error:
        _print_target_error(error)
        raise SystemExit(1) from error
    except MarivoMcpConfigError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error

    if args.write and args.client == "codex":
        path = write_client_config(output, client=args.client, config_path=args.config_path)
        print(f"Wrote marivo MCP config to {path}")
        return
    if args.write:
        print(
            f"Automatic config writing is not supported for client {args.client!r}; "
            "printing config instead.",
            file=sys.stderr,
        )
    print(render_client_config(output, client=args.client))


def build_init_config(
    *,
    mode: Literal["auto", "remote", "local"],
    base_url: str | None,
    workspace_root: str | None,
    client: str,
    api_token: str | None = None,
    cwd: str | None = None,
    server_name: str = "marivo",
    command: str | None = None,
    transport: SupportedTransport = "stdio",
    http_host: str = "127.0.0.1",
    http_port: int = 8000,
    http_path: str = "/mcp",
) -> dict[str, object]:
    if client not in _SUPPORTED_CLIENTS:
        raise TargetResolutionError(
            code="mcp_init_client_unsupported",
            message=f"不支持的客户端类型：{client}",
            detail={"client": client, "supported": list(_SUPPORTED_CLIENTS)},
            guidance="请使用 --print-config 手动配置",
        )
    if transport not in ("stdio", "streamable-http"):
        raise TargetResolutionError(
            code="config_invalid",
            message=f"Unsupported MCP transport: {transport}",
            detail={"transport": transport, "supported": ["stdio", "streamable-http"]},
            guidance="请使用 stdio 或 streamable-http",
        )
    normalized_server_name = _normalize_server_name(server_name)
    normalized_http_host = _normalize_http_host(http_host)
    normalized_http_port = _normalize_http_port(http_port)
    normalized_http_path = _normalize_http_path(http_path)

    normalized_base_url = _normalize_optional(base_url)
    normalized_api_token = _normalize_optional(api_token)
    if normalized_api_token is None:
        normalized_api_token = _normalize_optional(os.environ.get("MARIVO_API_TOKEN"))
    normalized_workspace_root = _resolve_workspace_root(
        workspace_root,
        cwd=cwd,
        allow_cwd=transport == "stdio",
    )
    resolved_mode: Literal["remote", "local"]
    if mode == "remote" or (mode == "auto" and normalized_base_url is not None):
        resolved_mode = "remote"
    else:
        resolved_mode = "local"

    if resolved_mode == "remote":
        if normalized_base_url is None:
            raise TargetResolutionError(
                code="remote_target_required",
                message="远程模式需要提供 Marivo 服务地址",
                detail={},
                guidance="请设置 MARIVO_BASE_URL",
            )
        env = {"MARIVO_MODE": "remote", "MARIVO_BASE_URL": normalized_base_url}
        if normalized_api_token is not None:
            env["MARIVO_API_TOKEN"] = normalized_api_token
    else:
        if normalized_workspace_root is None:
            detail: dict[str, object] = {
                "tried_sources": _workspace_tried_sources(transport),
            }
            if transport == "streamable-http":
                detail["transport"] = transport
            raise TargetResolutionError(
                code="workspace_root_required",
                message="本地模式需要工作区目录",
                detail=detail,
                guidance=_workspace_guidance(transport),
            )
        env = {"MARIVO_MODE": "local", "MARIVO_WORKSPACE_ROOT": normalized_workspace_root}

    resolved_command = command
    if resolved_command is None:
        resolved_command = "marivo-mcp-http" if transport == "streamable-http" else "marivo-mcp"

    if transport == "streamable-http":
        env = {
            **env,
            "MARIVO_MCP_TRANSPORT": "streamable-http",
            "MARIVO_MCP_HOST": normalized_http_host,
            "MARIVO_MCP_PORT": str(normalized_http_port),
            "MARIVO_MCP_STREAMABLE_HTTP_PATH": normalized_http_path,
        }
        client_url = _http_client_url(
            host=normalized_http_host,
            port=normalized_http_port,
            path=normalized_http_path,
        )
        return {
            "client": client,
            "server_name": normalized_server_name,
            "target_kind": resolved_mode,
            "transport": transport,
            "mcpServers": {
                normalized_server_name: {
                    "url": client_url,
                }
            },
            "mcp_server": {
                "command": resolved_command,
                "env": env,
            },
        }

    return {
        "client": client,
        "server_name": normalized_server_name,
        "target_kind": resolved_mode,
        "transport": transport,
        "mcpServers": {
            normalized_server_name: {
                "command": resolved_command,
                "env": env,
            }
        },
        "mcp_server": {
            "command": resolved_command,
            "env": env,
        },
    }


def render_client_config(config: dict[str, object], *, client: str) -> str:
    if client == "generic":
        return json.dumps(
            {"mcpServers": config["mcpServers"]},
            indent=2,
            sort_keys=True,
        )
    if client == "codex":
        return _render_codex_toml(config)
    _raise_unsupported_client(client)


def write_client_config(
    config: dict[str, object],
    *,
    client: str,
    config_path: str | None = None,
) -> str:
    if client != "codex":
        raise TargetResolutionError(
            code="mcp_init_client_unsupported",
            message=f"不支持自动写入客户端配置：{client}",
            detail={"client": client, "supported_write_clients": ["codex"]},
            guidance="请使用 --print-config 手动配置",
        )
    target_path = Path(config_path or ".codex/config.toml")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing = target_path.read_text() if target_path.exists() else ""
    updated = _replace_codex_server_block(existing, _render_codex_toml(config))
    target_path.write_text(updated)
    return str(target_path)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="marivo-mcp init",
        description="Generate minimal marivo-mcp configuration.",
    )
    parser.add_argument("--mode", choices=["auto", "remote", "local"], default="auto")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-token", default=None)
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--client", default="generic")
    parser.add_argument("--server-name", default="marivo")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--http-host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8000)
    parser.add_argument("--http-path", default="/mcp")
    parser.add_argument("--config-path", default=None)
    parser.add_argument("--print-config", action="store_true", help="Print generated config")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write config for clients with a supported writer",
    )
    args = parser.parse_args(argv)
    return args


def _raise_unsupported_client(client: str) -> NoReturn:
    raise TargetResolutionError(
        code="mcp_init_client_unsupported",
        message=f"不支持的客户端类型：{client}",
        detail={"client": client, "supported": list(_SUPPORTED_CLIENTS)},
        guidance="请使用 --print-config 手动配置",
    )


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_server_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise TargetResolutionError(
            code="config_invalid",
            message="MCP server name must not be empty",
            detail={"server_name": value},
            guidance="请提供非空 --server-name",
        )
    return stripped


def _resolve_workspace_root(
    value: str | None,
    *,
    cwd: str | None,
    allow_cwd: bool,
) -> str | None:
    candidates = [
        _normalize_optional(value),
        _normalize_optional(os.environ.get("MARIVO_WORKSPACE_ROOT")),
    ]
    if allow_cwd:
        candidates.extend([_normalize_optional(cwd), _getcwd()])
    for candidate in candidates:
        resolved = _valid_workspace_root(candidate)
        if resolved is not None:
            return resolved
    return None


def _workspace_tried_sources(transport: SupportedTransport) -> list[str]:
    if transport == "streamable-http":
        return ["--workspace-root", "MARIVO_WORKSPACE_ROOT"]
    return ["--workspace-root", "MARIVO_WORKSPACE_ROOT", "cwd"]


def _workspace_guidance(transport: SupportedTransport) -> str:
    if transport == "streamable-http":
        return "HTTP MCP 本地自动托管需要显式设置 MARIVO_WORKSPACE_ROOT"
    return "请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动"


def _valid_workspace_root(value: str | None) -> str | None:
    if value is None or not os.path.isabs(value):
        return None
    resolved = os.path.realpath(value)
    if os.path.isdir(resolved):
        return resolved
    return None


def _getcwd() -> str | None:
    try:
        return os.getcwd()
    except OSError:
        return None


def _print_target_error(error: TargetResolutionError) -> None:
    payload = {
        "code": error.code,
        "message": error.message,
        "detail": error.detail,
        "guidance": error.guidance,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)


def _render_codex_toml(config: dict[str, object]) -> str:
    server_name = str(config["server_name"])
    mcp_servers = config["mcpServers"]
    if not isinstance(mcp_servers, dict):
        raise TypeError("Expected mcpServers to be a dict.")
    server = mcp_servers[server_name]
    if not isinstance(server, dict):
        raise TypeError("Expected MCP server config to be a dict.")
    if "url" in server:
        url = server["url"]
        if not isinstance(url, str):
            raise TypeError("Invalid MCP server URL.")
        return f"[mcp_servers.{_toml_key(server_name)}]\nurl = {_toml_string(url)}\n"
    command = server["command"]
    env = server["env"]
    if not isinstance(command, str) or not isinstance(env, dict):
        raise TypeError("Invalid MCP server config shape.")
    env_items = ", ".join(
        f"{_toml_key(str(key))} = {_toml_string(str(value))}" for key, value in env.items()
    )
    return (
        f"[mcp_servers.{_toml_key(server_name)}]\n"
        f"command = {_toml_string(command)}\n"
        f"env = {{ {env_items} }}\n"
    )


def _replace_codex_server_block(existing: str, block: str) -> str:
    server_header = block.splitlines()[0]
    lines = existing.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == server_header), None)
    block_lines = block.rstrip("\n").splitlines()
    if start is None:
        prefix = existing.rstrip()
        if prefix:
            return f"{prefix}\n\n{block.rstrip()}\n"
        return f"{block.rstrip()}\n"

    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            break
        end += 1
    updated_lines = [*lines[:start], *block_lines, *lines[end:]]
    return "\n".join(updated_lines).rstrip() + "\n"


def _toml_key(value: str) -> str:
    if value and all(char.isascii() and (char.isalnum() or char in {"_", "-"}) for char in value):
        return value
    return _toml_string(value)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _normalize_http_host(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise TargetResolutionError(
            code="config_invalid",
            message="HTTP MCP host must not be empty",
            detail={"http_host": value},
            guidance="请提供非空 --http-host",
        )
    return stripped


def _normalize_http_port(value: int) -> int:
    if value <= 0 or value > 65535:
        raise TargetResolutionError(
            code="config_invalid",
            message="HTTP MCP port must be between 1 and 65535",
            detail={"http_port": value},
            guidance="请提供有效 --http-port",
        )
    return value


def _normalize_http_path(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise TargetResolutionError(
            code="config_invalid",
            message="HTTP MCP path must not be empty",
            detail={"http_path": value},
            guidance="请提供非空 --http-path",
        )
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    return stripped


def _http_client_url(*, host: str, port: int, path: str) -> str:
    client_host = _http_client_host(host)
    return f"http://{client_host}:{port}{path}"


def _http_client_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    if host.startswith("[") and host.endswith("]"):
        return host
    if ":" in host:
        return f"[{host}]"
    return host
