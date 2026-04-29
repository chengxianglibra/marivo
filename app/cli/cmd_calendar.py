"""CLI command: marivo calendar load <file.csv> --version <version>."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

from app.cli._exitcodes import EXIT_INVALID_USAGE
from app.cli._output import CliError

_REQUIRED_COLUMNS = {"calendar_date", "weekday", "is_weekend", "is_workday"}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="calendar_command")

    load_parser = sub.add_parser("load", help="Validate a calendar CSV file")
    load_parser.add_argument("file", type=str, help="Path to the CSV file")
    load_parser.add_argument(
        "--version", type=str, required=True, help="Calendar version identifier"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    command = getattr(args, "calendar_command", None)
    if command == "load":
        return _handle_load(args)
    raise CliError(EXIT_INVALID_USAGE, "Usage: marivo calendar load <file.csv> --version <version>")


def _handle_load(args: argparse.Namespace) -> dict[str, Any]:
    file_path = Path(args.file)
    version = args.version

    if not file_path.is_file():
        raise CliError(EXIT_INVALID_USAGE, f"File not found: {file_path}")

    try:
        with file_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise CliError(
                    EXIT_INVALID_USAGE, f"CSV file is empty or has no header: {file_path}"
                )

            missing = _REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                raise CliError(
                    EXIT_INVALID_USAGE,
                    f"Missing required columns: {', '.join(sorted(missing))}",
                )

            row_count = 0
            errors: list[str] = []
            for line_num, row in enumerate(reader, start=2):
                row_count += 1
                # Validate weekday
                try:
                    weekday = int(row["weekday"])
                    if weekday < 1 or weekday > 7:
                        errors.append(f"Line {line_num}: weekday must be 1-7, got {weekday}")
                except (ValueError, TypeError):
                    errors.append(f"Line {line_num}: weekday is not an integer: {row['weekday']}")

                # Validate is_weekend
                try:
                    is_weekend = int(row["is_weekend"])
                    if is_weekend not in (0, 1):
                        errors.append(
                            f"Line {line_num}: is_weekend must be 0 or 1, got {is_weekend}"
                        )
                except (ValueError, TypeError):
                    errors.append(
                        f"Line {line_num}: is_weekend is not an integer: {row['is_weekend']}"
                    )

                # Validate is_workday
                try:
                    is_workday = int(row["is_workday"])
                    if is_workday not in (0, 1):
                        errors.append(
                            f"Line {line_num}: is_workday must be 0 or 1, got {is_workday}"
                        )
                except (ValueError, TypeError):
                    errors.append(
                        f"Line {line_num}: is_workday is not an integer: {row['is_workday']}"
                    )

    except CliError:
        raise
    except Exception as e:
        raise CliError(EXIT_INVALID_USAGE, f"Failed to read CSV: {e}") from e

    if errors:
        raise CliError(
            EXIT_INVALID_USAGE,
            f"Validation failed with {len(errors)} error(s):\n" + "\n".join(errors[:10]),
        )

    return {
        "status": "validated",
        "calendar_version": version,
        "row_count": row_count,
        "file": str(file_path),
        "message": (
            f"CSV validated ({row_count} rows). "
            "Load via API: POST /calendar/data with the validated rows."
        ),
    }
