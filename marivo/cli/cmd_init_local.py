from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
from typing import Any

from marivo.cli._exitcodes import EXIT_WORKSPACE_ROOT_UNAVAILABLE
from marivo.cli._output import CliError
from marivo.cli._workspace import (
    bootstrap_config_path,
    dot_marivo_path,
    metadata_db_path,
    resolve_workspace_root,
)

BOOTSTRAP_CONFIG_YAML: str = (
    "metadata:\n"
    "  engine: sqlite\n"
    "  path: .marivo/metadata.sqlite\n"
    "\n"
    "observability:\n"
    "  log_level: INFO\n"
    "  metrics_enabled: true\n"
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", type=str, default=None, help="Workspace root directory")
    parser.add_argument(
        "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo init-local' — create .marivo/ and minimal config."""
    workspace_root = resolve_workspace_root(getattr(args, "workspace_root", None))
    dot_marivo = dot_marivo_path(workspace_root)
    config_path = bootstrap_config_path(workspace_root)

    try:
        # Create .marivo/ directory (idempotent)
        dot_marivo.mkdir(parents=True, exist_ok=True)

        # Write bootstrap config only if it doesn't exist (idempotent, never overwrites)
        if config_path.is_file():
            return {
                "status": "already_initialized",
                "workspace_root": str(workspace_root),
                "config_path": str(config_path),
                "metadata_path": str(metadata_db_path(workspace_root)),
            }

        _write_atomic(config_path, BOOTSTRAP_CONFIG_YAML)
    except OSError as e:
        raise CliError(
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
            f"Workspace root is not writable for local initialization: {workspace_root}",
        ) from e

    return {
        "status": "initialized",
        "workspace_root": str(workspace_root),
        "config_path": str(config_path),
        "metadata_path": str(metadata_db_path(workspace_root)),
    }


def _write_atomic(path: Path, content: str) -> None:
    """Atomic write via temp file + os.replace."""
    tmp_path = path.parent / f"marivo.yaml.tmp.{os.getpid()}"
    try:
        tmp_path.write_text(content)
        os.replace(str(tmp_path), str(path))
        os.chmod(path, 0o644)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
