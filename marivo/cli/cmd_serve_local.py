from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from marivo.cli._exitcodes import (
    EXIT_CONFIG_INVALID,
    EXIT_FAILURE,
    EXIT_HEALTH_CHECK_FAILED,
    EXIT_INVALID_USAGE,
    EXIT_PORT_UNAVAILABLE,
)
from marivo.cli._manifest import RuntimeManifest
from marivo.cli._output import CliError
from marivo.cli._workspace import (
    bootstrap_config_path,
    dot_marivo_path,
    log_dir_path,
    pid_file_path,
    resolve_workspace_root,
    runtime_manifest_path,
)
from marivo.cli.cmd_init_local import BOOTSTRAP_CONFIG_YAML
from marivo.config import load_config, resolve_metadata_path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", type=str, default=None, help="Workspace root directory")
    parser.add_argument("--host", type=str, default=None, help="Bind address (default: 127.0.0.1)")
    parser.add_argument(
        "--port", type=int, default=None, help="Bind port; 0 = OS assigns (default: 0)"
    )
    parser.add_argument(
        "--start-timeout-ms",
        type=int,
        default=None,
        help="Startup health-check timeout in ms (default: 15000)",
    )
    parser.add_argument(
        "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo serve-local' — start a workspace-scoped local daemon."""
    workspace_root = resolve_workspace_root(getattr(args, "workspace_root", None))
    host = _resolve_host(getattr(args, "host", None))
    port = _resolve_port(getattr(args, "port", None))
    start_timeout_ms = _resolve_start_timeout(getattr(args, "start_timeout_ms", None))

    dot_marivo = dot_marivo_path(workspace_root)
    config_path = bootstrap_config_path(workspace_root)
    manifest_path = runtime_manifest_path(workspace_root)
    pid_path = pid_file_path(workspace_root)
    logs_dir = log_dir_path(workspace_root)

    # Ensure .marivo/ exists
    dot_marivo.mkdir(parents=True, exist_ok=True)

    # Ensure bootstrap config exists (idempotent, never overwrites)
    if not config_path.is_file():
        _write_atomic(config_path, BOOTSTRAP_CONFIG_YAML)

    try:
        config = load_config(config_path)
    except Exception as e:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Config file is invalid: {config_path}: {e}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Config file is invalid: {config_path}: {e}",
                }
            },
        ) from e

    if config.metadata is None:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Config file is missing metadata configuration: {config_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Config file is missing metadata configuration: {config_path}",
                }
            },
        )
    if config.metadata.engine != "sqlite" or config.metadata.path is None:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"serve-local requires sqlite metadata configuration: {config_path}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": ("serve-local requires metadata.engine=sqlite and metadata.path"),
                }
            },
        )

    resolved_metadata_path = resolve_metadata_path(config_path, config.metadata.path)

    # Ensure metadata parent dir exists
    resolved_metadata_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure logs/ and run/ dirs exist
    logs_dir.mkdir(parents=True, exist_ok=True)
    pid_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve port 0 to an actual available port
    actual_port = _discover_port(host, port)

    # Start daemon subprocess
    env = os.environ.copy()
    env["MARIVO_CONFIG"] = str(config_path)

    log_file_path = logs_dir / "marivo.log"
    with log_file_path.open("a", encoding="utf-8") as log_file:
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "marivo.main:app",
                    "--host",
                    host,
                    "--port",
                    str(actual_port),
                ],
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as e:
            raise CliError(
                EXIT_FAILURE,
                f"Failed to start local Marivo daemon: {e}",
                json_data={
                    "error": {
                        "code": EXIT_FAILURE,
                        "message": f"Failed to start local Marivo daemon: {e}",
                    }
                },
            ) from e

        # Poll /health until success or timeout
        base_url = f"http://{host}:{actual_port}"
        deadline = time.monotonic() + start_timeout_ms / 1000.0
        healthy = False

        while time.monotonic() < deadline:
            time.sleep(0.2)
            try:
                resp = httpx.get(f"{base_url}/health", timeout=2.0)
                if resp.status_code == 200:
                    body = resp.json()
                    if body.get("status") == "ok":
                        healthy = True
                        break
            except httpx.HTTPError:
                pass

        if not healthy:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise CliError(
                EXIT_HEALTH_CHECK_FAILED,
                f"Local Marivo failed health check within {start_timeout_ms}ms at {base_url}",
                json_data={
                    "error": {
                        "code": EXIT_HEALTH_CHECK_FAILED,
                        "message": (
                            f"Local Marivo failed health check within {start_timeout_ms}ms "
                            f"at {base_url}"
                        ),
                        "base_url": base_url,
                        "pid": proc.pid,
                    }
                },
            )

        manifest = RuntimeManifest(
            workspace_root=str(workspace_root),
            host=host,
            port=actual_port,
            pid=proc.pid,
            config_path=str(config_path),
            metadata_path=str(resolved_metadata_path),
        )
        manifest.write_atomic(manifest_path)
        pid_path.write_text(f"{proc.pid}\n")

    return {
        "status": "serving",
        "host": host,
        "port": actual_port,
        "base_url": base_url,
        "pid": proc.pid,
        "workspace_root": str(workspace_root),
        "config_path": str(config_path),
        "metadata_path": str(resolved_metadata_path),
    }


def _resolve_host(cli_host: str | None) -> str:
    if cli_host is not None:
        host = cli_host
    else:
        env_val = os.getenv("MARIVO_LOCAL_HOST")
        host = env_val if env_val else "127.0.0.1"
    if not host.strip():
        raise CliError(
            EXIT_INVALID_USAGE,
            "Local runtime host must be non-empty.",
            json_data={
                "error": {
                    "code": EXIT_INVALID_USAGE,
                    "message": "Local runtime host must be non-empty.",
                }
            },
        )
    return host


def _resolve_port(cli_port: int | None) -> int:
    if cli_port is not None:
        port = cli_port
    else:
        env_val = os.getenv("MARIVO_LOCAL_PORT")
        port = _parse_int_env("MARIVO_LOCAL_PORT", env_val) if env_val else 0
    if port < 0 or port > 65535:
        raise CliError(
            EXIT_INVALID_USAGE,
            f"Local runtime port must be between 0 and 65535: {port}",
            json_data={
                "error": {
                    "code": EXIT_INVALID_USAGE,
                    "message": f"Local runtime port must be between 0 and 65535: {port}",
                }
            },
        )
    return port


def _resolve_start_timeout(cli_timeout: int | None) -> int:
    if cli_timeout is not None:
        timeout_ms = cli_timeout
    else:
        env_val = os.getenv("MARIVO_START_TIMEOUT_MS")
        timeout_ms = _parse_int_env("MARIVO_START_TIMEOUT_MS", env_val) if env_val else 15000
    if timeout_ms <= 0:
        raise CliError(
            EXIT_INVALID_USAGE,
            f"Local runtime start timeout must be positive: {timeout_ms}",
            json_data={
                "error": {
                    "code": EXIT_INVALID_USAGE,
                    "message": f"Local runtime start timeout must be positive: {timeout_ms}",
                }
            },
        )
    return timeout_ms


def _parse_int_env(name: str, value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError as e:
        raise CliError(
            EXIT_INVALID_USAGE,
            f"{name} must be an integer: {value}",
            json_data={
                "error": {
                    "code": EXIT_INVALID_USAGE,
                    "message": f"{name} must be an integer: {value}",
                }
            },
        ) from e


def _discover_port(host: str, port: int) -> int:
    """If port is 0, discover an available port; otherwise return port as-is."""
    if port != 0:
        return port
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, 0))
            addr = s.getsockname()
            return int(addr[1])
    except OSError as e:
        raise CliError(
            EXIT_PORT_UNAVAILABLE,
            f"Cannot find available port on {host}: {e}",
            json_data={
                "error": {
                    "code": EXIT_PORT_UNAVAILABLE,
                    "message": f"Cannot find available port on {host}: {e}",
                }
            },
        ) from e


def _write_atomic(path: Path, content: str) -> None:
    """Atomic write via temp file + os.replace."""
    tmp_path = path.parent / f"{path.name}.tmp.{os.getpid()}"
    try:
        tmp_path.write_text(content)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
