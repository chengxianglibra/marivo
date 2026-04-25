from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Literal

from marivo_mcp.config import MarivoMcpConfigError, TargetResolutionError

SupportedClient = Literal["generic"]

_SUPPORTED_CLIENTS: tuple[SupportedClient, ...] = ("generic",)


def main(argv: list[str] | None = None) -> None:
    try:
        args = _parse_args(argv)
        output = build_init_config(
            mode=args.mode,
            base_url=args.base_url,
            api_token=args.api_token,
            workspace_root=args.workspace_root,
            client=args.client,
        )
    except TargetResolutionError as error:
        _print_target_error(error)
        raise SystemExit(1) from error
    except MarivoMcpConfigError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error

    print(json.dumps(output, indent=2, sort_keys=True))


def build_init_config(
    *,
    mode: Literal["auto", "remote", "local"],
    base_url: str | None,
    workspace_root: str | None,
    client: str,
    api_token: str | None = None,
    cwd: str | None = None,
) -> dict[str, object]:
    if client not in _SUPPORTED_CLIENTS:
        raise TargetResolutionError(
            code="mcp_init_client_unsupported",
            message=f"不支持的客户端类型：{client}",
            detail={"client": client, "supported": list(_SUPPORTED_CLIENTS)},
            guidance="请使用 --print-config 手动配置",
        )

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
        "server_name": "marivo",
        "target_kind": resolved_mode,
        "mcp_server": {
            "command": "marivo-mcp",
            "env": env,
        },
    }


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
    parser.add_argument("--print-config", action="store_true", help="Print generated config")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Reserved for client-specific writers in a later task",
    )
    args = parser.parse_args(argv)
    if args.write:
        raise MarivoMcpConfigError(
            "marivo-mcp init --write is not implemented yet; use --print-config"
        )
    return args


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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
