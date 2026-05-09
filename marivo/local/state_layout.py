"""State layout helpers for local mode.

Defines the canonical `.marivo/` directory structure used by profiles, CLI, and adapters.
All path functions take a workspace_root Path and return a Path within `.marivo/`.
"""

from __future__ import annotations

from pathlib import Path


def dot_marivo_path(workspace_root: Path) -> Path:
    """Return the .marivo directory path."""
    return workspace_root / ".marivo"


def bootstrap_config_path(workspace_root: Path) -> Path:
    """Return the YAML bootstrap config path (legacy)."""
    return workspace_root / ".marivo" / "marivo.yaml"


def toml_config_path(workspace_root: Path) -> Path:
    """Return the TOML config path."""
    return workspace_root / ".marivo" / "marivo.toml"


def metadata_db_path(workspace_root: Path) -> Path:
    """Return the metadata SQLite database path."""
    return workspace_root / ".marivo" / "metadata.sqlite"


def runtime_manifest_path(workspace_root: Path) -> Path:
    """Return the runtime manifest JSON path."""
    return workspace_root / ".marivo" / "runtime.json"


def pid_file_path(workspace_root: Path) -> Path:
    """Return the PID file path."""
    return workspace_root / ".marivo" / "run" / "marivo.pid"


def log_dir_path(workspace_root: Path) -> Path:
    """Return the logs directory path."""
    return workspace_root / ".marivo" / "logs"


def models_dir(workspace_root: Path) -> Path:
    """Return the models directory path."""
    return workspace_root / ".marivo" / "models"


def evidence_dir(workspace_root: Path) -> Path:
    """Return the evidence directory path."""
    return workspace_root / ".marivo" / "evidence"


def state_db_path(workspace_root: Path) -> Path:
    """Return the state SQLite database path."""
    return workspace_root / ".marivo" / "state.db"


def artifacts_dir(workspace_root: Path) -> Path:
    """Return the artifacts directory path."""
    return workspace_root / ".marivo" / "artifacts"


def audit_log_path(workspace_root: Path) -> Path:
    """Return the audit log path."""
    return workspace_root / ".marivo" / "audit.jsonl"


def telemetry_log_path(workspace_root: Path) -> Path:
    """Return the telemetry log path."""
    return workspace_root / ".marivo" / "telemetry.jsonl"
