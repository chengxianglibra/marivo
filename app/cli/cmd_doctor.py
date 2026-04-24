from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.cli._exitcodes import (
    EXIT_CONFIG_INVALID,
    EXIT_RUNTIME_NOT_RUNNING,
    EXIT_SUCCESS,
    EXIT_WORKSPACE_ROOT_UNAVAILABLE,
)
from app.cli._manifest import read_manifest, validate_manifest_schema
from app.cli._output import CliError
from app.cli._workspace import (
    bootstrap_config_path,
    dot_marivo_path,
    runtime_manifest_path,
)
from app.config import MarivoConfig, resolve_metadata_path


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", type=str, default=None, help="Workspace root directory")
    parser.add_argument(
        "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo doctor' — run diagnostic checks."""
    checks: list[dict[str, Any]] = []
    workspace_failure = False
    config_failure = False
    runtime_failure = False

    # Check 1: workspace_root
    check, code = _check_workspace_root(getattr(args, "workspace_root", None))
    checks.append(check)
    if code == EXIT_WORKSPACE_ROOT_UNAVAILABLE:
        workspace_failure = True

    # Remaining checks need a valid workspace root
    workspace_root: Path | None = None
    if check["status"] == "ok":
        from app.cli._workspace import resolve_workspace_root as _resolve

        workspace_root = _resolve(getattr(args, "workspace_root", None))

    # Check 2: .marivo/ exists and is writable
    if workspace_root is not None:
        check, code = _check_dot_marivo(workspace_root)
        checks.append(check)
        config_failure = config_failure or code == EXIT_CONFIG_INVALID

        # Check 3: config file
        check, code, config = _check_config_file(workspace_root)
        checks.append(check)
        config_failure = config_failure or code == EXIT_CONFIG_INVALID

        # Check 4: metadata store
        check, code = _check_metadata_store(workspace_root, config)
        checks.append(check)
        config_failure = config_failure or code == EXIT_CONFIG_INVALID

        # Check 5: runtime manifest
        manifest_data: dict[str, Any] | None = None
        check, code, manifest_data = _check_runtime_manifest(workspace_root)
        checks.append(check)
        if code == EXIT_CONFIG_INVALID:
            config_failure = True
        elif code == EXIT_RUNTIME_NOT_RUNNING:
            runtime_failure = True

        # Check 6: runtime health
        check, code = _check_runtime_health(manifest_data)
        checks.append(check)
        if code == EXIT_CONFIG_INVALID:
            config_failure = True
        elif code == EXIT_RUNTIME_NOT_RUNNING:
            runtime_failure = True

    all_ok = all(c["status"] == "ok" for c in checks)
    summary = f"{sum(c['status'] == 'ok' for c in checks)}/{len(checks)} checks passed"
    if runtime_failure and not config_failure and not workspace_failure:
        summary = f"{summary}; runtime not running"
    elif config_failure:
        summary = f"{summary}; configuration issues found"
    elif workspace_failure:
        summary = f"{summary}; workspace root unavailable"

    result = {
        "workspace_root": str(workspace_root) if workspace_root else None,
        "checks": checks,
        "ok": all_ok,
        "summary": summary,
    }

    if workspace_failure:
        raise CliError(
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
            "Doctor found issues",
            json_data=result,
        )
    if config_failure:
        raise CliError(EXIT_CONFIG_INVALID, "Doctor found issues", json_data=result)
    if runtime_failure:
        raise CliError(EXIT_RUNTIME_NOT_RUNNING, "Doctor found issues", json_data=result)

    return result


def _check_workspace_root(explicit_root: str | None) -> tuple[dict[str, Any], int]:
    """Check 1: workspace root is valid."""
    try:
        from app.cli._workspace import resolve_workspace_root

        root = resolve_workspace_root(explicit_root)
        return {"name": "workspace_root", "status": "ok", "path": str(root)}, EXIT_SUCCESS
    except Exception as e:
        return (
            {"name": "workspace_root", "status": "failed", "message": str(e)},
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
        )


def _check_dot_marivo(workspace_root: Path) -> tuple[dict[str, Any], int]:
    """Check 2: .marivo/ exists and is writable."""
    dot_marivo = dot_marivo_path(workspace_root)
    if not dot_marivo.is_dir():
        return {
            "name": "dot_marivo_dir",
            "status": "failed",
            "message": f"{dot_marivo} does not exist",
        }, EXIT_CONFIG_INVALID
    if not os.access(dot_marivo, os.W_OK):
        return {
            "name": "dot_marivo_dir",
            "status": "failed",
            "message": f"{dot_marivo} is not writable",
        }, EXIT_CONFIG_INVALID
    return {"name": "dot_marivo_dir", "status": "ok", "path": str(dot_marivo)}, EXIT_SUCCESS


def _check_config_file(workspace_root: Path) -> tuple[dict[str, Any], int, MarivoConfig | None]:
    """Check 3: marivo.yaml exists, parseable, valid schema."""
    config_path = bootstrap_config_path(workspace_root)
    if not config_path.is_file():
        return (
            {"name": "config_file", "status": "failed", "message": f"{config_path} does not exist"},
            EXIT_CONFIG_INVALID,
            None,
        )

    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
        config = MarivoConfig.model_validate(raw)
    except Exception as e:
        return (
            {"name": "config_file", "status": "failed", "message": str(e)},
            EXIT_CONFIG_INVALID,
            None,
        )

    return {"name": "config_file", "status": "ok", "path": str(config_path)}, EXIT_SUCCESS, config


def _check_metadata_store(
    workspace_root: Path, config: MarivoConfig | None
) -> tuple[dict[str, Any], int]:
    """Check 4: metadata store is accessible."""
    if config is None or config.metadata is None:
        return (
            {"name": "metadata_store", "status": "failed", "message": "metadata config missing"},
            EXIT_CONFIG_INVALID,
        )
    meta_path = resolve_metadata_path(bootstrap_config_path(workspace_root), config.metadata.path)
    if not meta_path.exists():
        return (
            {
                "name": "metadata_store",
                "status": "warning",
                "message": f"{meta_path} does not exist (will be created on first start)",
            },
            EXIT_SUCCESS,
        )
    if not os.access(meta_path, os.R_OK):
        return (
            {
                "name": "metadata_store",
                "status": "failed",
                "message": f"{meta_path} is not readable",
            },
            EXIT_CONFIG_INVALID,
        )
    return {"name": "metadata_store", "status": "ok", "path": str(meta_path)}, EXIT_SUCCESS


def _check_runtime_manifest(
    workspace_root: Path,
) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
    """Check 5: runtime.json is parseable and schema-valid."""
    manifest_path = runtime_manifest_path(workspace_root)

    if not manifest_path.is_file():
        return (
            {
                "name": "runtime_manifest",
                "status": "not_found",
                "message": "No runtime manifest found",
            },
            EXIT_RUNTIME_NOT_RUNNING,
            None,
        )

    try:
        data = read_manifest(manifest_path)
        validate_manifest_schema(data, str(manifest_path))
    except Exception as e:
        return (
            {"name": "runtime_manifest", "status": "failed", "message": str(e)},
            EXIT_CONFIG_INVALID,
            None,
        )

    return (
        {
            "name": "runtime_manifest",
            "status": "ok",
            "pid": data["pid"],
            "base_url": data["base_url"],
        },
        EXIT_SUCCESS,
        data,
    )


def _check_runtime_health(manifest_data: dict[str, Any] | None) -> tuple[dict[str, Any], int]:
    """Check 6: runtime PID alive and /health OK."""
    if manifest_data is None:
        return {
            "name": "runtime_health",
            "status": "skipped",
            "message": "No valid manifest",
        }, EXIT_RUNTIME_NOT_RUNNING

    pid: int = manifest_data["pid"]
    base_url: str = manifest_data["base_url"]

    # PID check
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {
            "name": "runtime_health",
            "status": "failed",
            "message": f"Process {pid} is not running",
        }, EXIT_RUNTIME_NOT_RUNNING
    except PermissionError:
        pass  # Process exists but owned by different user
    except OSError:
        return {
            "name": "runtime_health",
            "status": "failed",
            "message": f"Process {pid} check failed",
        }, EXIT_RUNTIME_NOT_RUNNING

    # Health check
    try:
        resp = httpx.get(f"{base_url}/health", timeout=2.0)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            return {
                "name": "runtime_health",
                "status": "ok",
                "pid": pid,
                "base_url": base_url,
            }, EXIT_SUCCESS
        return (
            {
                "name": "runtime_health",
                "status": "failed",
                "message": f"Health check returned non-ok at {base_url}",
            },
            EXIT_RUNTIME_NOT_RUNNING,
        )
    except Exception as e:
        return {
            "name": "runtime_health",
            "status": "failed",
            "message": str(e),
        }, EXIT_RUNTIME_NOT_RUNNING
