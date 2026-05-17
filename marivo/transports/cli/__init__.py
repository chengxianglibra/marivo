from __future__ import annotations

import argparse
import sys
from typing import Any

from marivo.transports.cli._exitcodes import EXIT_FAILURE, EXIT_INVALID_USAGE, EXIT_SUCCESS
from marivo.transports.cli._output import (
    CliError,
    detect_format,
    emit_diagnostic,
    emit_json,
    emit_text,
    format_error_json,
)
from marivo.transports.cli.cmd_doctor import add_arguments as doctor_add_arguments
from marivo.transports.cli.cmd_doctor import handle as doctor_handle
from marivo.transports.cli.cmd_init import add_arguments as init_add_arguments
from marivo.transports.cli.cmd_init import handle as init_handle
from marivo.transports.cli.cmd_mcp import add_arguments as mcp_add_arguments
from marivo.transports.cli.cmd_mcp import handle as mcp_handle
from marivo.transports.cli.cmd_runtime import add_arguments as runtime_add_arguments
from marivo.transports.cli.cmd_runtime import handle as runtime_handle
from marivo.transports.cli.cmd_serve import add_arguments as serve_add_arguments
from marivo.transports.cli.cmd_serve import handle as serve_handle


def main() -> None:
    """CLI entry point registered as [project.scripts] marivo = "marivo.transports.cli:main"."""
    parser = _build_parser()
    args = parser.parse_args()

    if not hasattr(args, "handler"):
        parser.print_help()
        sys.exit(EXIT_INVALID_USAGE)

    fmt = detect_format(getattr(args, "format", None))

    try:
        result = args.handler(args)
        if result is not None:
            if fmt == "json":
                emit_json(result)
            else:
                emit_text(_format_text_result(result))
        sys.exit(EXIT_SUCCESS)
    except CliError as e:
        if fmt == "json":
            emit_json(format_error_json(e.exit_code, e.message, e.json_data))
        else:
            emit_diagnostic(e.message)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        emit_diagnostic(f"Unexpected error: {e}")
        sys.exit(EXIT_FAILURE)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marivo",
        description="Marivo semantic layer CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # marivo serve
    serve_parser = subparsers.add_parser(
        "serve", help="Start Marivo HTTP server with explicit config"
    )
    serve_add_arguments(serve_parser)
    serve_parser.set_defaults(handler=serve_handle)

    # marivo init
    init_parser = subparsers.add_parser("init", help="Create .marivo/ directory with TOML layout")
    init_add_arguments(init_parser)
    init_parser.set_defaults(handler=init_handle)

    # marivo doctor
    doctor_parser = subparsers.add_parser("doctor", help="Run diagnostic checks")
    doctor_add_arguments(doctor_parser)
    doctor_parser.set_defaults(handler=doctor_handle)

    # marivo runtime (subcommand group)
    runtime_parser = subparsers.add_parser("runtime", help="Manage local runtime")
    runtime_add_arguments(runtime_parser)
    runtime_parser.set_defaults(handler=runtime_handle)

    # marivo mcp
    mcp_parser = subparsers.add_parser("mcp", help="Start stdio MCP server")
    mcp_add_arguments(mcp_parser)
    mcp_parser.set_defaults(handler=mcp_handle)

    return parser


def _format_text_result(result: dict[str, Any]) -> str:
    """Format a result dict as human-readable text."""
    if result.get("status") == "serving" and "base_url" in result and "workspace_root" in result:
        return (
            f"Marivo local runtime serving on {result['base_url']} "
            f"(workspace: {result['workspace_root']})"
        )
    if result.get("status") == "running" and "base_url" in result and "pid" in result:
        return f"Marivo local runtime running at {result['base_url']} (pid {result['pid']})"
    if result.get("status") == "stopped" and "pid" in result:
        return f"Stopped Marivo local runtime (pid {result['pid']})"
    if result.get("status") in {"stopped", "already_stopped"}:
        return "No local runtime running"

    lines: list[str] = []
    _flatten_dict(result, lines, indent=0)
    return "\n".join(lines)


def _flatten_dict(data: dict[str, Any], lines: list[str], indent: int) -> None:
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            _flatten_dict(value, lines, indent + 1)
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}:")
            for item in value:
                if isinstance(item, dict):
                    _flatten_dict(item, lines, indent + 1)
                    lines.append("")
                else:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}{key}: {value}")
