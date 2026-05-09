from __future__ import annotations

import json
import sys
from typing import Any


class CliError(Exception):
    """Raised by command handlers to signal a specific exit code.

    main() catches this, emits formatted output, and exits with the code.
    """

    def __init__(
        self,
        exit_code: int,
        message: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.message = message
        self.json_data = json_data
        super().__init__(message)


def detect_format(cli_format: str | None) -> str:
    """Resolve --format flag to 'json' or 'text'.

    Non-TTY defaults to json; TTY defaults to text.
    """
    if cli_format is not None:
        return cli_format
    return "json" if not sys.stdout.isatty() else "text"


def emit_json(data: dict[str, Any]) -> None:
    """Write structured JSON to stdout."""
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")


def emit_text(text: str) -> None:
    """Write human-readable text to stdout."""
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


def emit_diagnostic(message: str) -> None:
    """Write human-readable diagnostic to stderr."""
    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")


def format_error_json(
    exit_code: int, message: str, json_data: dict[str, Any] | None
) -> dict[str, Any]:
    """Build a structured error payload for JSON output."""
    if json_data is not None:
        return json_data
    return {"error": {"code": exit_code, "message": message}}
