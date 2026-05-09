from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import uvicorn

from marivo.transports.cli._exitcodes import EXIT_CONFIG_INVALID, EXIT_PORT_UNAVAILABLE
from marivo.transports.cli._output import CliError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", type=str, default=None, help="YAML config file path")
    parser.add_argument(
        "-H", "--host", type=str, default="127.0.0.1", help="Bind address (default: 127.0.0.1)"
    )
    parser.add_argument("-p", "--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("-l", "--log-level", type=str, default=None, help="Log level")
    parser.add_argument(
        "-f", "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any] | None:
    """Execute 'marivo serve' — start a foreground HTTP server."""
    config_path: str | None = getattr(args, "config", None)
    host: str = getattr(args, "host", "127.0.0.1")
    port: int = getattr(args, "port", 8000)
    log_level: str | None = getattr(args, "log_level", None)

    resolved_config, effective_log_level = _resolve_runtime_config(config_path, log_level)
    previous_marivo_config = os.environ.get("MARIVO_CONFIG")
    os.environ["MARIVO_CONFIG"] = str(resolved_config)

    try:
        uvicorn.run(
            "marivo.main:app",
            host=host,
            port=port,
            log_level=effective_log_level.lower(),
        )
    except OSError as e:
        raise CliError(
            EXIT_PORT_UNAVAILABLE,
            f"Cannot bind to {host}:{port}: {e}",
            json_data={
                "error": {
                    "code": EXIT_PORT_UNAVAILABLE,
                    "message": f"Cannot bind to {host}:{port}: {e}",
                }
            },
        ) from e
    finally:
        if previous_marivo_config is None:
            os.environ.pop("MARIVO_CONFIG", None)
        else:
            os.environ["MARIVO_CONFIG"] = previous_marivo_config

    return None


def _resolve_runtime_config(config_path: str | None, cli_level: str | None) -> tuple[Path, str]:
    """Resolve and validate runtime config before starting uvicorn."""
    from marivo.config import load_config, resolve_config_path

    resolved = resolve_config_path(Path(config_path) if config_path else None)

    if config_path is not None and not resolved.is_file():
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Config file not found: {resolved}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Config file not found: {resolved}",
                }
            },
        )

    try:
        config = load_config(resolved)
    except Exception as e:
        raise CliError(
            EXIT_CONFIG_INVALID,
            f"Config file is invalid: {resolved}: {e}",
            json_data={
                "error": {
                    "code": EXIT_CONFIG_INVALID,
                    "message": f"Config file is invalid: {resolved}: {e}",
                }
            },
        ) from e

    effective_log_level = cli_level if cli_level is not None else config.observability.log_level
    return resolved, effective_log_level
