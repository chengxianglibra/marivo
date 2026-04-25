from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Literal, NoReturn

from marivo_mcp.config import MarivoMcpConfigError, TargetResolutionError

SupportedClient = Literal["generic", "codex"]

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
    command: str = "marivo-mcp",
) -> dict[str, object]:
    if client not in _SUPPORTED_CLIENTS:
        raise TargetResolutionError(
            code="mcp_init_client_unsupported",
            message=f"不支持的客户端类型：{client}",
            detail={"client": client, "supported": list(_SUPPORTED_CLIENTS)},
            guidance="请使用 --print-config 手动配置",
        )
    normalized_server_name = _normalize_server_name(server_name)

    normalized_base_url = _normalize_optional(base_url)
    normalized_api_token = _normalize_optional(api_token)
    if normalized_api_token is None:
        normalized_api_token = _normalize_optional(os.environ.get("MARIVO_API_TOKEN"))
    normalized_workspace_root = _resolve_workspace_root(workspace_root, cwd=cwd)
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
            raise TargetResolutionError(
                code="workspace_root_required",
                message="本地模式需要工作区目录",
                detail={"tried_sources": ["--workspace-root", "MARIVO_WORKSPACE_ROOT", "cwd"]},
                guidance="请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动",
            )
        env = {"MARIVO_MODE": "local", "MARIVO_WORKSPACE_ROOT": normalized_workspace_root}

    return {
        "client": client,
        "server_name": normalized_server_name,
        "target_kind": resolved_mode,
        "mcpServers": {
            normalized_server_name: {
                "command": command,
                "env": env,
            }
        },
        "mcp_server": {
            "command": command,
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


def _resolve_workspace_root(value: str | None, *, cwd: str | None) -> str | None:
    for candidate in (
        _normalize_optional(value),
        _normalize_optional(os.environ.get("MARIVO_WORKSPACE_ROOT")),
        _normalize_optional(cwd),
        _getcwd(),
    ):
        resolved = _valid_workspace_root(candidate)
        if resolved is not None:
            return resolved
    return None


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
