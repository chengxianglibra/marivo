from __future__ import annotations

import argparse
import contextlib
import os
import signal
import time
from typing import Any

import httpx

from app.cli._exitcodes import (
    EXIT_FAILURE,
    EXIT_HEALTH_CHECK_FAILED,
    EXIT_INVALID_USAGE,
    EXIT_RUNTIME_NOT_RUNNING,
)
from app.cli._manifest import delete_manifest, read_manifest, validate_manifest_schema
from app.cli._output import CliError
from app.cli._workspace import pid_file_path, resolve_workspace_root, runtime_manifest_path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    runtime_subparsers = parser.add_subparsers(dest="runtime_command")

    # marivo runtime status
    status_parser = runtime_subparsers.add_parser("status", help="Show local runtime status")
    status_parser.add_argument(
        "--workspace-root", type=str, default=None, help="Workspace root directory"
    )
    status_parser.add_argument(
        "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )
    status_parser.set_defaults(runtime_command="status")

    # marivo runtime stop
    stop_parser = runtime_subparsers.add_parser("stop", help="Stop local runtime")
    stop_parser.add_argument(
        "--workspace-root", type=str, default=None, help="Workspace root directory"
    )
    stop_parser.add_argument("--force", action="store_true", help="Send SIGKILL if SIGTERM fails")
    stop_parser.add_argument(
        "--timeout-ms",
        type=int,
        default=5000,
        help="Grace period after SIGTERM in ms (default: 5000)",
    )
    stop_parser.add_argument(
        "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )
    stop_parser.set_defaults(runtime_command="stop")


def handle(args: argparse.Namespace) -> dict[str, Any]:
    runtime_command = getattr(args, "runtime_command", None)
    if runtime_command == "status":
        return _handle_status(args)
    if runtime_command == "stop":
        return _handle_stop(args)
    raise CliError(
        EXIT_INVALID_USAGE,
        "Usage: marivo runtime {status|stop}",
    )


def _handle_status(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo runtime status'."""
    workspace_root = resolve_workspace_root(getattr(args, "workspace_root", None))
    manifest_path = runtime_manifest_path(workspace_root)

    try:
        data = read_manifest(manifest_path)
    except CliError as e:
        if e.exit_code == EXIT_RUNTIME_NOT_RUNNING:
            raise CliError(
                EXIT_RUNTIME_NOT_RUNNING,
                f"No local runtime running for {workspace_root}",
                json_data={
                    "status": "stopped",
                    "workspace_root": str(workspace_root),
                },
            ) from e
        raise
    validate_manifest_schema(data, str(manifest_path))

    pid: int = data["pid"]
    base_url: str = data["base_url"]

    # PID check
    if not _is_pid_alive(pid):
        raise CliError(
            EXIT_RUNTIME_NOT_RUNNING,
            f"Runtime process (pid {pid}) is not running.",
            json_data={
                "status": "stopped",
                "workspace_root": data["workspace_root"],
            },
        )

    # Health check
    if not _check_health(base_url):
        raise CliError(
            EXIT_HEALTH_CHECK_FAILED,
            f"Runtime process (pid {pid}) is alive but health check failed at {base_url}",
            json_data={
                "status": "unhealthy",
                "pid": pid,
                "base_url": base_url,
                "workspace_root": data["workspace_root"],
                "config_path": data["config_path"],
                "metadata_path": data["metadata_path"],
            },
        )

    return {
        "status": "running",
        "base_url": base_url,
        "host": data["host"],
        "port": data["port"],
        "pid": pid,
        "workspace_root": data["workspace_root"],
        "started_at": data["started_at"],
        "config_path": data["config_path"],
        "metadata_path": data["metadata_path"],
    }


def _handle_stop(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo runtime stop'."""
    workspace_root = resolve_workspace_root(getattr(args, "workspace_root", None))
    manifest_path = runtime_manifest_path(workspace_root)
    pid_path = pid_file_path(workspace_root)
    force: bool = getattr(args, "force", False)
    timeout_ms: int = getattr(args, "timeout_ms", 5000)
    if timeout_ms <= 0:
        raise CliError(
            EXIT_INVALID_USAGE,
            "--timeout-ms must be greater than 0",
        )

    try:
        data = read_manifest(manifest_path)
    except CliError as e:
        if e.exit_code == EXIT_RUNTIME_NOT_RUNNING:
            raise CliError(
                EXIT_RUNTIME_NOT_RUNNING,
                f"No local runtime running for {workspace_root}",
                json_data={
                    "status": "already_stopped",
                    "workspace_root": str(workspace_root),
                },
            ) from e
        raise
    validate_manifest_schema(data, str(manifest_path))

    pid: int = data["pid"]

    if not _is_pid_alive(pid):
        # Already stopped — just clean up manifest and pid file
        _cleanup(manifest_path, pid_path)
        return {
            "status": "already_stopped",
            "workspace_root": str(workspace_root),
        }

    # Send SIGTERM
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _cleanup(manifest_path, pid_path)
        return {"status": "already_stopped", "workspace_root": str(workspace_root)}
    except PermissionError as e:
        raise CliError(
            EXIT_FAILURE,
            f"Permission denied to stop process {pid}",
        ) from e

    # Wait for process to exit
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            _cleanup(manifest_path, pid_path)
            return {"status": "stopped", "pid": pid, "workspace_root": str(workspace_root)}
        time.sleep(0.1)

    # Still alive
    if force:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            raise CliError(EXIT_FAILURE, f"Permission denied to kill process {pid}") from e
        _cleanup(manifest_path, pid_path)
        return {
            "status": "stopped",
            "pid": pid,
            "signal": "SIGKILL",
            "workspace_root": str(workspace_root),
        }

    raise CliError(
        EXIT_FAILURE,
        f"Process {pid} did not exit within {timeout_ms}ms. Use --force to send SIGKILL.",
        json_data={"status": "stop_failed", "pid": pid, "timeout_ms": timeout_ms},
    )


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is alive using os.kill(pid, 0)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Different user — process exists
        return True
    except OSError:
        return False


def _check_health(base_url: str) -> bool:
    """Check /health endpoint. Returns True if healthy."""
    try:
        resp = httpx.get(f"{base_url}/health", timeout=2.0)
        if resp.status_code == 200:
            body = resp.json()
            return str(body.get("status")) == "ok"
    except (httpx.HTTPError, ValueError):
        pass
    return False


def _cleanup(manifest_path: Any, pid_path: Any) -> None:
    """Delete manifest and PID files."""
    delete_manifest(manifest_path)
    with contextlib.suppress(FileNotFoundError):
        pid_path.unlink()
