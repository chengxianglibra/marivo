from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn, cast
from urllib.parse import unquote, urlparse

import httpx

from marivo_mcp.config import MarivoMcpConfig, TargetResolutionError
from marivo_mcp.diagnostics import emit_diagnostic

TargetKind = Literal["remote", "local"]
RuntimeState = Literal[
    "remote",
    "manifest_valid_healthy",
    "no_manifest",
    "manifest_stale_pid_dead",
    "manifest_stale_unhealthy",
]

_MANIFEST_VERSION = "0.1.0"
_MANIFEST_MODE = "local"
_REQUIRED_FIELDS = (
    "version",
    "workspace_root",
    "mode",
    "base_url",
    "host",
    "port",
    "pid",
    "started_at",
    "config_path",
    "metadata_path",
)
_SYSTEM_WORKSPACE_ROOTS = {
    "/",
    "/tmp",
    "/var",
    "/etc",
    str(Path.home()),
}


@dataclass(frozen=True)
class RuntimeManifest:
    workspace_root: str
    base_url: str
    host: str
    port: int
    pid: int
    started_at: str
    config_path: str
    metadata_path: str


@dataclass(frozen=True)
class RuntimeManifestStatus:
    state: RuntimeState
    manifest_path: Path
    manifest: RuntimeManifest | None = None


@dataclass(frozen=True)
class TargetResolution:
    target_kind: TargetKind
    base_url: str
    config: MarivoMcpConfig
    workspace_root: str | None = None
    manifest_path: Path | None = None
    runtime_state: RuntimeState = "remote"


HealthChecker = Callable[[str, int, str | None], bool]
PidChecker = Callable[[int], bool]
CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


def resolve_target(
    config: MarivoMcpConfig,
    *,
    workspace_roots: Iterable[str] = (),
    cwd: str | None = None,
    health_checker: HealthChecker | None = None,
    pid_checker: PidChecker | None = None,
    command_runner: CommandRunner | None = None,
) -> TargetResolution:
    """Resolve the Marivo HTTP target from adapter config."""
    _validate_timeout_contract(config)
    if config.mode == "remote" or (config.mode == "auto" and config.base_url is not None):
        return _resolve_remote(config, health_checker=health_checker)
    return _resolve_local(
        config,
        workspace_roots=workspace_roots,
        cwd=cwd,
        health_checker=health_checker,
        pid_checker=pid_checker,
        command_runner=command_runner,
    )


def inspect_runtime_manifest(
    workspace_root: str,
    *,
    healthcheck_timeout_ms: int,
    health_checker: HealthChecker | None = None,
    pid_checker: PidChecker | None = None,
) -> RuntimeManifestStatus:
    """Read and classify the workspace runtime manifest without starting anything."""
    manifest_path = Path(workspace_root) / ".marivo" / "runtime.json"
    if not manifest_path.is_file():
        return RuntimeManifestStatus(state="no_manifest", manifest_path=manifest_path)

    data = _read_manifest_json(manifest_path)
    manifest = _validate_manifest(data, manifest_path)
    resolved_pid_checker = pid_checker or _is_pid_alive
    if not resolved_pid_checker(manifest.pid):
        return RuntimeManifestStatus(
            state="manifest_stale_pid_dead",
            manifest_path=manifest_path,
            manifest=manifest,
        )

    resolved_health_checker = health_checker or _check_health
    if not resolved_health_checker(manifest.base_url, healthcheck_timeout_ms, None):
        return RuntimeManifestStatus(
            state="manifest_stale_unhealthy",
            manifest_path=manifest_path,
            manifest=manifest,
        )

    return RuntimeManifestStatus(
        state="manifest_valid_healthy",
        manifest_path=manifest_path,
        manifest=manifest,
    )


def _resolve_remote(
    config: MarivoMcpConfig,
    *,
    health_checker: HealthChecker | None,
) -> TargetResolution:
    if config.base_url is None:
        raise TargetResolutionError(
            code="remote_target_required",
            message="远程模式需要提供 Marivo 服务地址",
            detail={},
            guidance="请设置 MARIVO_BASE_URL",
        )

    resolved_health_checker = health_checker or _check_health
    if not resolved_health_checker(
        config.base_url, config.healthcheck_timeout_ms, config.api_token
    ):
        emit_diagnostic("remote_unreachable", base_url=config.base_url)
        raise TargetResolutionError(
            code="remote_target_unreachable",
            message=f"无法连接到远程 Marivo 服务：{config.base_url}",
            detail={
                "base_url": config.base_url,
                "status_code": None,
                "timeout": False,
            },
            guidance="请检查地址是否正确、服务是否运行",
        )

    resolution = TargetResolution(
        target_kind="remote",
        base_url=config.base_url,
        config=config,
    )
    emit_diagnostic("target_resolved", target_kind="remote", base_url=config.base_url)
    return resolution


def _resolve_local(
    config: MarivoMcpConfig,
    *,
    workspace_roots: Iterable[str],
    cwd: str | None,
    health_checker: HealthChecker | None,
    pid_checker: PidChecker | None,
    command_runner: CommandRunner | None,
) -> TargetResolution:
    _emit_local_config_warnings(config)
    workspace_root = _resolve_workspace_root(
        config,
        workspace_roots=workspace_roots,
        cwd=cwd,
    )
    emit_diagnostic("workspace_root_resolved", workspace_root=workspace_root)
    if config.transport == "streamable-http":
        _check_http_local_guard(workspace_root)

    status = inspect_runtime_manifest(
        workspace_root,
        healthcheck_timeout_ms=config.healthcheck_timeout_ms,
        health_checker=health_checker,
        pid_checker=pid_checker,
    )
    if status.state == "manifest_valid_healthy" and status.manifest is not None:
        emit_diagnostic(
            "manifest_reused",
            workspace_root=workspace_root,
            manifest_path=str(status.manifest_path),
            base_url=status.manifest.base_url,
        )
    if status.state in {"no_manifest", "manifest_stale_pid_dead"}:
        status = _start_local_runtime(
            config,
            workspace_root,
            health_checker=health_checker,
            pid_checker=pid_checker,
            command_runner=command_runner,
        )
    elif status.state == "manifest_stale_unhealthy":
        status = _restart_local_runtime(
            config,
            workspace_root,
            health_checker=health_checker,
            pid_checker=pid_checker,
            command_runner=command_runner,
        )

    if status.state != "manifest_valid_healthy" or status.manifest is None:
        _raise_local_runtime_start_failed(
            workspace_root,
            timeout_ms=config.start_timeout_ms,
            exit_code=None,
            health_checked=status.state == "manifest_stale_unhealthy",
        )

    resolved_config = config.model_copy(
        update={
            "base_url": status.manifest.base_url,
            "api_token": None,
            "workspace_root": workspace_root,
        }
    )
    emit_diagnostic(
        "target_resolved",
        target_kind="local",
        base_url=status.manifest.base_url,
        workspace_root=workspace_root,
        manifest_path=str(status.manifest_path),
        runtime_state=status.state,
    )
    return TargetResolution(
        target_kind="local",
        base_url=status.manifest.base_url,
        config=resolved_config,
        workspace_root=workspace_root,
        manifest_path=status.manifest_path,
        runtime_state=status.state,
    )


def _start_local_runtime(
    config: MarivoMcpConfig,
    workspace_root: str,
    *,
    health_checker: HealthChecker | None,
    pid_checker: PidChecker | None,
    command_runner: CommandRunner | None,
) -> RuntimeManifestStatus:
    command = [
        "serve-local",
        "--workspace-root",
        workspace_root,
        "--host",
        config.local_host,
        "--port",
        str(config.local_port),
        "--start-timeout-ms",
        str(config.start_timeout_ms),
        "--format",
        "json",
    ]
    emit_diagnostic("local_start_attempted", workspace_root=workspace_root, command=command)
    result = _run_marivo_command(
        command,
        timeout_ms=config.start_timeout_ms,
        command_runner=command_runner,
    )
    if result.returncode != 0:
        if result.returncode == 3:
            _raise_workspace_root_required(["MARIVO_WORKSPACE_ROOT", "cwd"])
        _raise_local_runtime_start_failed(
            workspace_root,
            timeout_ms=config.start_timeout_ms,
            exit_code=result.returncode,
            health_checked=result.returncode == 5,
        )

    return inspect_runtime_manifest(
        workspace_root,
        healthcheck_timeout_ms=config.healthcheck_timeout_ms,
        health_checker=health_checker,
        pid_checker=pid_checker,
    )


def _restart_local_runtime(
    config: MarivoMcpConfig,
    workspace_root: str,
    *,
    health_checker: HealthChecker | None,
    pid_checker: PidChecker | None,
    command_runner: CommandRunner | None,
) -> RuntimeManifestStatus:
    stop_result = _run_marivo_command(
        [
            "runtime",
            "stop",
            "--workspace-root",
            workspace_root,
            "--format",
            "json",
        ],
        timeout_ms=5_000,
        command_runner=command_runner,
    )
    if stop_result.returncode not in {0, 4}:
        _raise_local_runtime_start_failed(
            workspace_root,
            timeout_ms=config.start_timeout_ms,
            exit_code=stop_result.returncode,
            health_checked=True,
        )

    return _start_local_runtime(
        config,
        workspace_root,
        health_checker=health_checker,
        pid_checker=pid_checker,
        command_runner=command_runner,
    )


def _run_marivo_command(
    args: list[str],
    *,
    timeout_ms: int,
    command_runner: CommandRunner | None,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("marivo") or "marivo"
    try:
        if command_runner is not None:
            return command_runner(args, timeout_ms)
        return subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _raise_local_runtime_start_failed(
            _workspace_root_from_command(args),
            timeout_ms=timeout_ms,
            exit_code=None,
            health_checked=args[:1] == ["serve-local"],
        )
    except OSError:
        _raise_local_runtime_start_failed(
            _workspace_root_from_command(args),
            timeout_ms=timeout_ms,
            exit_code=None,
            health_checked=False,
        )


def _workspace_root_from_command(args: list[str]) -> str:
    try:
        return args[args.index("--workspace-root") + 1]
    except (ValueError, IndexError):
        return ""


def _raise_local_runtime_start_failed(
    workspace_root: str,
    *,
    timeout_ms: int,
    exit_code: int | None,
    health_checked: bool,
) -> NoReturn:
    emit_diagnostic(
        "local_start_failed",
        workspace_root=workspace_root,
        timeout_ms=timeout_ms,
        exit_code=exit_code,
        health_checked=health_checked,
    )
    raise TargetResolutionError(
        code="local_runtime_start_failed",
        message="本地 Marivo 启动失败",
        detail={
            "workspace_root": workspace_root,
            "timeout_ms": timeout_ms,
            "exit_code": exit_code,
            "health_checked": health_checked,
        },
        guidance="请运行 marivo doctor 诊断本地环境",
    )


def _raise_workspace_root_required(tried_sources: list[str]) -> NoReturn:
    raise TargetResolutionError(
        code="workspace_root_required",
        message="本地模式需要工作区目录",
        detail={"tried_sources": tried_sources},
        guidance="请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动",
    )


def _raise_config_invalid(message: str, detail: dict[str, Any]) -> NoReturn:
    raise TargetResolutionError(
        code="config_invalid",
        message=message,
        detail=detail,
        guidance="请检查 marivo-mcp 目标解析配置",
    )


def _validate_timeout_contract(config: MarivoMcpConfig) -> None:
    if config.start_timeout_ms <= config.healthcheck_timeout_ms:
        _raise_config_invalid(
            "MARIVO_START_TIMEOUT_MS must be greater than MARIVO_HEALTHCHECK_TIMEOUT_MS",
            {
                "start_timeout_ms": config.start_timeout_ms,
                "healthcheck_timeout_ms": config.healthcheck_timeout_ms,
            },
        )


def _emit_local_config_warnings(config: MarivoMcpConfig) -> None:
    if config.mode != "local":
        return
    if config.base_url is not None:
        emit_diagnostic(
            "config_warning",
            warning="MARIVO_BASE_URL is set but mode=local; base_url will be ignored",
        )
    if config.api_token is not None:
        emit_diagnostic(
            "config_warning",
            warning="MARIVO_API_TOKEN is set but mode=local; api_token will be ignored",
        )


def _resolve_workspace_root(
    config: MarivoMcpConfig,
    *,
    workspace_roots: Iterable[str],
    cwd: str | None,
) -> str:
    tried_sources: list[str] = []
    if config.workspace_root is not None:
        tried_sources.append("MARIVO_WORKSPACE_ROOT")
        resolved = _valid_workspace_root(config.workspace_root)
        if resolved is not None:
            return resolved

    if config.transport == "stdio":
        tried_sources.append("mcp_roots")
        for root in workspace_roots:
            candidate = _workspace_root_from_root(root)
            if candidate is None:
                continue
            resolved = _valid_workspace_root(candidate)
            if resolved is not None:
                return resolved

    tried_sources.append("cwd")
    try:
        cwd_candidate = cwd or os.getcwd()
    except OSError:
        cwd_candidate = None
    if cwd_candidate is not None:
        resolved = _valid_workspace_root(cwd_candidate)
        if resolved is not None:
            return resolved

    raise TargetResolutionError(
        code="workspace_root_required",
        message="本地模式需要工作区目录",
        detail={"tried_sources": tried_sources},
        guidance="请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动",
    )


def _workspace_root_from_root(root: str) -> str | None:
    parsed = urlparse(root)
    if parsed.scheme == "file":
        return unquote(parsed.path)
    if parsed.scheme:
        return None
    return root


def _valid_workspace_root(candidate: str) -> str | None:
    if not candidate.strip() or not os.path.isabs(candidate):
        return None
    resolved = os.path.realpath(candidate)
    if os.path.isabs(resolved) and os.path.isdir(resolved):
        return resolved
    return None


def _check_http_local_guard(workspace_root: str) -> None:
    if workspace_root in _SYSTEM_WORKSPACE_ROOTS:
        raise TargetResolutionError(
            code="workspace_root_required",
            message="本地模式需要工作区目录",
            detail={"tried_sources": ["MARIVO_WORKSPACE_ROOT", "cwd"]},
            guidance="请设置 MARIVO_WORKSPACE_ROOT 或在项目目录中启动",
        )

    dot_marivo = Path(workspace_root) / ".marivo"
    writable_path = dot_marivo if dot_marivo.exists() else Path(workspace_root)
    if not os.access(writable_path, os.W_OK):
        raise TargetResolutionError(
            code="local_runtime_start_failed",
            message="本地 Marivo 启动失败",
            detail={
                "workspace_root": workspace_root,
                "timeout_ms": 0,
                "exit_code": None,
                "health_checked": False,
            },
            guidance="请运行 marivo doctor 诊断本地环境",
        )

    if shutil.which("marivo") is None:
        raise TargetResolutionError(
            code="local_runtime_start_failed",
            message="本地 Marivo 启动失败",
            detail={
                "workspace_root": workspace_root,
                "timeout_ms": 0,
                "exit_code": None,
                "health_checked": False,
            },
            guidance="请运行 marivo doctor 诊断本地环境",
        )


def _read_manifest_json(manifest_path: Path) -> dict[str, Any]:
    try:
        raw_data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as error:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=str(error),
            missing_fields=None,
        )
    if not isinstance(raw_data, dict):
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error="manifest is not a JSON object",
            missing_fields=None,
        )
    return cast("dict[str, Any]", raw_data)


def _validate_manifest(data: dict[str, Any], manifest_path: Path) -> RuntimeManifest:
    missing_fields = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing_fields:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error="missing required fields",
            missing_fields=missing_fields,
        )

    extra_fields = [field for field in data if field not in _REQUIRED_FIELDS]
    if extra_fields:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"unexpected fields: {', '.join(extra_fields)}",
            missing_fields=None,
        )

    for field in (
        "version",
        "workspace_root",
        "mode",
        "base_url",
        "host",
        "started_at",
        "config_path",
        "metadata_path",
    ):
        if not isinstance(data[field], str):
            _raise_runtime_manifest_invalid(
                manifest_path,
                parse_error=f"field {field!r} has wrong type",
                missing_fields=None,
            )
    for field in ("port", "pid"):
        if not isinstance(data[field], int):
            _raise_runtime_manifest_invalid(
                manifest_path,
                parse_error=f"field {field!r} has wrong type",
                missing_fields=None,
            )

    if data["version"] != _MANIFEST_VERSION:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"unsupported manifest version: {data['version']}",
            missing_fields=None,
        )
    if data["mode"] != _MANIFEST_MODE:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"unsupported manifest mode: {data['mode']}",
            missing_fields=None,
        )
    if data["port"] <= 0 or data["port"] > 65535:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"port out of range: {data['port']}",
            missing_fields=None,
        )
    if data["pid"] <= 0:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"pid must be positive: {data['pid']}",
            missing_fields=None,
        )

    expected_base_url = f"http://{data['host']}:{data['port']}"
    if data["base_url"] != expected_base_url:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"base_url invariant violation: expected {expected_base_url}, got {data['base_url']}",
            missing_fields=None,
        )

    for field in ("workspace_root", "config_path", "metadata_path"):
        if not os.path.isabs(data[field]):
            _raise_runtime_manifest_invalid(
                manifest_path,
                parse_error=f"{field} is not an absolute path: {data[field]}",
                missing_fields=None,
            )

    workspace_root = os.path.realpath(data["workspace_root"])
    if not os.path.isdir(workspace_root):
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error=f"workspace_root does not exist or is not a directory: {data['workspace_root']}",
            missing_fields=None,
        )
    expected_manifest_path = os.path.realpath(
        os.path.join(workspace_root, ".marivo", "runtime.json")
    )
    if os.path.realpath(manifest_path) != expected_manifest_path:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error="workspace_root does not match manifest location",
            missing_fields=None,
        )
    expected_config_path = os.path.join(workspace_root, ".marivo", "marivo.yaml")
    expected_metadata_path = os.path.join(workspace_root, ".marivo", "metadata.sqlite")
    if os.path.realpath(data["config_path"]) != expected_config_path:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error="config_path does not match workspace_root/.marivo/marivo.yaml",
            missing_fields=None,
        )
    if os.path.realpath(data["metadata_path"]) != expected_metadata_path:
        _raise_runtime_manifest_invalid(
            manifest_path,
            parse_error="metadata_path does not match workspace_root/.marivo/metadata.sqlite",
            missing_fields=None,
        )

    return RuntimeManifest(
        workspace_root=workspace_root,
        base_url=data["base_url"],
        host=data["host"],
        port=data["port"],
        pid=data["pid"],
        started_at=data["started_at"],
        config_path=data["config_path"],
        metadata_path=data["metadata_path"],
    )


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (ProcessLookupError, OSError):
        return False
    return True


def _check_health(base_url: str, timeout_ms: int, api_token: str | None) -> bool:
    headers = {"Accept": "application/json", "Connection": "close"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/health",
            headers=headers,
            timeout=timeout_ms / 1000,
        )
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def _raise_runtime_manifest_invalid(
    manifest_path: Path,
    *,
    parse_error: str,
    missing_fields: list[str] | None,
) -> None:
    manifest_path_str = str(manifest_path)
    raise TargetResolutionError(
        code="runtime_manifest_invalid",
        message=f"本地运行时清单无效：{manifest_path_str}",
        detail={
            "manifest_path": manifest_path_str,
            "parse_error": parse_error,
            "missing_fields": missing_fields,
        },
        guidance=f"请运行 marivo doctor 诊断，或删除 {manifest_path_str} 重试",
    )
