from __future__ import annotations

import os
from pathlib import Path

from marivo.local.state_layout import (
    bootstrap_config_path,
    dot_marivo_path,
    pid_file_path,
    runtime_manifest_path,
    toml_config_path,
)
from marivo.transports.cli._exitcodes import EXIT_WORKSPACE_ROOT_UNAVAILABLE
from marivo.transports.cli._output import CliError

# Re-export path helpers for backward compatibility
__all__ = [
    "bootstrap_config_path",
    "dot_marivo_path",
    "pid_file_path",
    "resolve_workspace_root",
    "runtime_manifest_path",
    "toml_config_path",
]


def resolve_workspace_root(explicit_root: str | None) -> Path:
    """Resolve workspace root per T1.3 priority chain (CLI subset).

    Priority: explicit -w/--workspace > MARIVO_WORKSPACE_ROOT env > os.getcwd() > error.

    Returns absolute real path.
    """
    candidates: list[tuple[str, str | None]] = [
        ("MARIVO_WORKSPACE_ROOT", explicit_root if explicit_root else None),
        ("MARIVO_WORKSPACE_ROOT", os.getenv("MARIVO_WORKSPACE_ROOT")),
        ("cwd", None),
    ]

    for source, value in candidates:
        if source == "cwd":
            try:
                path = Path(os.getcwd())
            except OSError:
                continue
        else:
            if value is None or not value.strip():
                continue
            path = Path(value)

        try:
            validated = _validate(path)
        except CliError:
            continue

        return validated

    tried: list[str] = []
    if explicit_root is not None:
        tried.append("-w/--workspace")
    if os.getenv("MARIVO_WORKSPACE_ROOT"):
        tried.append("MARIVO_WORKSPACE_ROOT")
    tried.append("cwd")

    raise CliError(
        EXIT_WORKSPACE_ROOT_UNAVAILABLE,
        "Workspace root is required but could not be resolved.",
        json_data={
            "error": {
                "code": EXIT_WORKSPACE_ROOT_UNAVAILABLE,
                "message": "Workspace root is required but could not be resolved.",
                "tried_sources": tried,
            }
        },
    )


def _validate(path: Path) -> Path:
    """Validate and canonicalize a workspace root path.

    Must be absolute, exist, and be a directory. Symlinks are resolved.
    """
    real = Path(os.path.realpath(path))
    if not os.path.isabs(str(real)):
        raise CliError(
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
            f"Workspace root must be an absolute path: {path}",
        )
    if not real.is_dir():
        raise CliError(
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
            f"Workspace root does not exist or is not a directory: {real}",
        )
    return real
