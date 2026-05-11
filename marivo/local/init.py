"""Workspace initialization logic for local mode.

Provides pure functions for creating the `.marivo/` directory structure,
independent of transport layer (CLI, HTTP, etc.).
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from marivo.contracts.errors import ErrorCode, ValidationError
from marivo.contracts.values import LAYOUT_VERSION
from marivo.local.state_layout import (
    dot_marivo_path,
    evidence_dir,
    log_dir_path,
    models_dir,
    state_db_path,
    toml_config_path,
)

DEFAULT_TOML = (
    '[profile]\nmode = "local"\n\n[datasource]\ntype = "duckdb"\n\n[telemetry]\nsink = "none"\n'
)


def initialize_workspace(workspace_root: Path, *, force: bool = False) -> dict[str, Any]:
    """Create .marivo/ directory structure.

    Args:
        workspace_root: Absolute path to workspace root directory
        force: If True, reinitialize even if already initialized

    Returns:
        Dictionary with status information:
        - status: "initialized" or "already_initialized"
        - workspace_root: str path
        - marivo_dir: str path

    Raises:
        ValidationError: If workspace is not writable or version mismatch
    """
    marivo_dir = dot_marivo_path(workspace_root)

    try:
        marivo_dir.mkdir(parents=True, exist_ok=True)

        # Check VERSION compatibility
        version_path = marivo_dir / "VERSION"
        if version_path.is_file():
            existing_version = version_path.read_text().strip()
            if existing_version != str(LAYOUT_VERSION):
                raise ValidationError(
                    code=ErrorCode.VALIDATION,
                    message=f"Layout version {existing_version} is not supported "
                    f"(expected {LAYOUT_VERSION}). Run `marivo migrate` or reinitialize.",
                )

        # Create subdirectories
        models_dir(workspace_root).mkdir(exist_ok=True)
        evidence_dir(workspace_root).mkdir(exist_ok=True)
        log_dir_path(workspace_root).mkdir(exist_ok=True)

        # Write VERSION file
        if not version_path.is_file():
            version_path.write_text(str(LAYOUT_VERSION))

        # Check if already fully initialized
        toml_path = toml_config_path(workspace_root)
        db_path = state_db_path(workspace_root)
        if not force and toml_path.is_file() and db_path.is_file():
            return {
                "status": "already_initialized",
                "workspace_root": str(workspace_root),
                "marivo_dir": str(marivo_dir),
            }

        # Write default TOML config
        if not toml_path.is_file() or force:
            _write_atomic(toml_path, DEFAULT_TOML)

        # Initialize state.db
        if not db_path.is_file() or force:
            _init_state_db(db_path)

    except ValidationError:
        raise
    except OSError as e:
        raise ValidationError(
            code=ErrorCode.VALIDATION,
            message=f"Workspace root is not writable: {workspace_root}",
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
                event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                event_type  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                actor       TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(session_id, seq)
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_events_sid ON session_events (session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_events_owner "
            "ON session_events (event_type, actor)"
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
