from __future__ import annotations

import argparse
from typing import Any

from marivo.contracts.errors import ValidationError
from marivo.local.init import initialize_workspace
from marivo.transports.cli._exitcodes import EXIT_WORKSPACE_ROOT_UNAVAILABLE
from marivo.transports.cli._output import CliError
from marivo.transports.cli._workspace import resolve_workspace_root


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-w", "--workspace", type=str, default=None, help="Workspace root directory"
    )
    parser.add_argument(
        "-f", "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo init' -- create .marivo/ with TOML layout."""
    workspace_root = resolve_workspace_root(getattr(args, "workspace", None))

    try:
        return initialize_workspace(workspace_root)
    except ValidationError as e:
        raise CliError(
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
            e.message,
        ) from e
