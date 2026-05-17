from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marivo.transports.cli._exitcodes import (
    EXIT_CONFIG_INVALID,
    EXIT_RUNTIME_NOT_RUNNING,
    EXIT_SUCCESS,
    EXIT_WORKSPACE_ROOT_UNAVAILABLE,
)
from marivo.transports.cli._manifest import read_manifest, validate_manifest_schema
from marivo.transports.cli._output import CliError
from marivo.transports.cli._workspace import (
    bootstrap_config_path,
    dot_marivo_path,
    runtime_manifest_path,
)

if TYPE_CHECKING:
    from marivo.config import MarivoConfig


class _LazyHttpx:
    def get(self, *args: Any, **kwargs: Any) -> Any:
        import httpx

        return httpx.get(*args, **kwargs)


httpx = _LazyHttpx()


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-w", "--workspace", type=str, default=None, help="Workspace root directory"
    )
    parser.add_argument(
        "-f", "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo doctor' — run diagnostic checks."""
    checks: list[dict[str, Any]] = []
    workspace_failure = False
    config_failure = False
    runtime_failure = False

    # Check 1: workspace_root
    check, code = _check_workspace_root(getattr(args, "workspace", None))
    checks.append(check)
    if code == EXIT_WORKSPACE_ROOT_UNAVAILABLE:
        workspace_failure = True

    # Remaining checks need a valid workspace root
    workspace_root: Path | None = None
    if check["ok"]:
        from marivo.transports.cli._workspace import resolve_workspace_root as _resolve

        workspace_root = _resolve(getattr(args, "workspace", None))

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

    all_ok = all(c["ok"] for c in checks)
    summary = f"{sum(c['ok'] for c in checks)}/{len(checks)} checks passed"
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
        from marivo.transports.cli._workspace import resolve_workspace_root

        root = resolve_workspace_root(explicit_root)
        return _check(
            "workspace_root", "ok", f"{root} is an absolute directory", path=root
        ), EXIT_SUCCESS
    except Exception as e:
        return (
            _check("workspace_root", "failed", str(e)),
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
        )


def _check_dot_marivo(workspace_root: Path) -> tuple[dict[str, Any], int]:
    """Check 2: .marivo/ exists and is writable."""
    dot_marivo = dot_marivo_path(workspace_root)
    if not dot_marivo.is_dir():
        return {
            **_check("dot_marivo_dir", "failed", f"{dot_marivo} does not exist"),
        }, EXIT_CONFIG_INVALID
    if not os.access(dot_marivo, os.W_OK):
        return {
            **_check("dot_marivo_dir", "failed", f"{dot_marivo} is not writable"),
        }, EXIT_CONFIG_INVALID
    return _check(
        "dot_marivo_dir", "ok", f"{dot_marivo} exists and is writable", path=dot_marivo
    ), EXIT_SUCCESS


def _check_config_file(workspace_root: Path) -> tuple[dict[str, Any], int, MarivoConfig | None]:
    """Check 3: marivo.yaml exists, parseable, valid schema."""
    import yaml

    from marivo.config import MarivoConfig

    config_path = bootstrap_config_path(workspace_root)
    if not config_path.is_file():
        return (
            _check("config_file", "failed", f"{config_path} does not exist"),
            EXIT_CONFIG_INVALID,
            None,
        )

    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
        config = MarivoConfig.model_validate(raw)
    except Exception as e:
        return (
            _check("config_file", "failed", str(e)),
            EXIT_CONFIG_INVALID,
            None,
        )

    return (
        _check("config_file", "ok", f"{config_path} is valid", path=config_path),
        EXIT_SUCCESS,
        config,
    )


def _check_metadata_store(
    workspace_root: Path, config: MarivoConfig | None
) -> tuple[dict[str, Any], int]:
    """Check 4: metadata store is accessible."""
    from marivo.config import resolve_metadata_path

    if config is None or config.metadata is None:
        return (
            _check("metadata_store", "failed", "metadata config missing"),
            EXIT_CONFIG_INVALID,
        )
    if config.metadata.engine != "sqlite" or config.metadata.path is None:
        return (
            _check(
                "metadata_store",
                "failed",
                "local doctor requires metadata.engine=sqlite and metadata.path",
            ),
            EXIT_CONFIG_INVALID,
        )
    meta_path = resolve_metadata_path(bootstrap_config_path(workspace_root), config.metadata.path)
    if not meta_path.exists():
        return (
            _check(
                "metadata_store",
                "warning",
                f"{meta_path} does not exist (will be created on first start)",
                path=meta_path,
            ),
            EXIT_SUCCESS,
        )
    if not os.access(meta_path, os.R_OK):
        return (
            _check("metadata_store", "failed", f"{meta_path} is not readable", path=meta_path),
            EXIT_CONFIG_INVALID,
        )
    return _check("metadata_store", "ok", f"{meta_path} is readable", path=meta_path), EXIT_SUCCESS


def _check_runtime_manifest(
    workspace_root: Path,
) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
    """Check 5: runtime.json is parseable and schema-valid."""
    manifest_path = runtime_manifest_path(workspace_root)

    if not manifest_path.is_file():
        return (
            _check(
                "runtime_manifest", "not_found", "No runtime manifest found", path=manifest_path
            ),
            EXIT_RUNTIME_NOT_RUNNING,
            None,
        )

    try:
        data = read_manifest(manifest_path)
        validate_manifest_schema(data, str(manifest_path))
    except Exception as e:
        return (
            _check("runtime_manifest", "failed", str(e), path=manifest_path),
            EXIT_CONFIG_INVALID,
            None,
        )

    return (
        {
            **_check("runtime_manifest", "ok", f"{manifest_path} is valid", path=manifest_path),
            "pid": data["pid"],
            "base_url": data["base_url"],
        },
        EXIT_SUCCESS,
        data,
    )


def _check_runtime_health(manifest_data: dict[str, Any] | None) -> tuple[dict[str, Any], int]:
    """Check 6: runtime PID alive and /health OK."""
    if manifest_data is None:
        return _check("runtime_health", "skipped", "No valid manifest"), EXIT_RUNTIME_NOT_RUNNING

    pid: int = manifest_data["pid"]
    base_url: str = manifest_data["base_url"]

    # PID check
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return _check(
            "runtime_health", "failed", f"Process {pid} is not running"
        ), EXIT_RUNTIME_NOT_RUNNING
    except PermissionError:
        pass  # Process exists but owned by different user
    except OSError:
        return _check(
            "runtime_health", "failed", f"Process {pid} check failed"
        ), EXIT_RUNTIME_NOT_RUNNING

    # Health check
    try:
        resp = httpx.get(f"{base_url}/health", timeout=2.0)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            return {
                **_check("runtime_health", "ok", f"{base_url}/health returned ok"),
                "pid": pid,
                "base_url": base_url,
            }, EXIT_SUCCESS
        return (
            _check("runtime_health", "failed", f"Health check returned non-ok at {base_url}"),
            EXIT_RUNTIME_NOT_RUNNING,
        )
    except Exception as e:
        return _check("runtime_health", "failed", str(e)), EXIT_RUNTIME_NOT_RUNNING


def _check(
    name: str,
    status: str,
    detail: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "status": status,
        "ok": status in {"ok", "warning"},
        "detail": detail,
    }
    if path is not None:
        result["path"] = str(path)
    if status != "ok":
        result["message"] = detail
    return result
