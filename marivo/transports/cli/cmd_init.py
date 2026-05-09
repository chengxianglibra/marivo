from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from marivo.contracts.values import LAYOUT_VERSION
from marivo.transports.cli._exitcodes import EXIT_WORKSPACE_ROOT_UNAVAILABLE
from marivo.transports.cli._output import CliError
from marivo.transports.cli._workspace import (
    dot_marivo_path,
    resolve_workspace_root,
    toml_config_path,
)

DEFAULT_TOML = (
    '[profile]\nmode = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace-root", type=str, default=None, help="Workspace root directory")
    parser.add_argument(
        "--format", type=str, choices=["json", "text"], default=None, help="Output format"
    )


def handle(args: argparse.Namespace) -> dict[str, Any]:
    """Execute 'marivo init' -- create .marivo/ with TOML layout."""
    workspace_root = resolve_workspace_root(getattr(args, "workspace_root", None))
    marivo_dir = dot_marivo_path(workspace_root)

    try:
        marivo_dir.mkdir(parents=True, exist_ok=True)

        # Check VERSION compatibility
        version_path = marivo_dir / "VERSION"
        if version_path.is_file():
            existing_version = version_path.read_text().strip()
            if existing_version != str(LAYOUT_VERSION):
                raise CliError(
                    EXIT_WORKSPACE_ROOT_UNAVAILABLE,
                    f"Layout version {existing_version} is not supported "
                    f"(expected {LAYOUT_VERSION}). Run `marivo migrate` or reinitialize.",
                )

        # Create subdirectories
        (marivo_dir / "models").mkdir(exist_ok=True)
        (marivo_dir / "evidence").mkdir(exist_ok=True)

        # Write VERSION file
        if not version_path.is_file():
            version_path.write_text(str(LAYOUT_VERSION))

        # Check if already fully initialized
        toml_path = toml_config_path(workspace_root)
        db_path = marivo_dir / "state.db"
        if toml_path.is_file() and db_path.is_file():
            return {
                "status": "already_initialized",
                "workspace_root": str(workspace_root),
                "marivo_dir": str(marivo_dir),
            }

        # Write default TOML config
        if not toml_path.is_file():
            _write_atomic(toml_path, DEFAULT_TOML)

        # Initialize state.db
        if not db_path.is_file():
            _init_state_db(db_path)

    except CliError:
        raise
    except OSError as e:
        raise CliError(
            EXIT_WORKSPACE_ROOT_UNAVAILABLE,
            f"Workspace root is not writable: {workspace_root}",
        ) from e

    return {
        "status": "initialized",
        "workspace_root": str(workspace_root),
        "marivo_dir": str(marivo_dir),
    }


def _write_atomic(path: Path, content: str) -> None:
    """Atomic write via temp file + os.replace."""
    tmp_path = path.parent / f"tmp-{os.getpid()}"
    try:
        tmp_path.write_text(content)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _init_state_db(db_path: Path) -> None:
    """Create the local state SQLite database with initial schema."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS session_events (
                session_id  TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                event_type  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                payload     TEXT NOT NULL,
                actor       TEXT,
                PRIMARY KEY (session_id, seq)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cache_entries (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                expires_at  TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()
