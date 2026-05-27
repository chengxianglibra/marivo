"""Project root resolution and active session pointer management."""

from __future__ import annotations

from pathlib import Path

_DOT_MARIVO = ".marivo"
_ANALYSIS_DIR = "analysis"
_ACTIVE_FILE = "active"


def resolve_project_root(start: Path | None = None) -> Path:
    """Return nearest ancestor containing .marivo, or the starting directory."""
    base = Path(start) if start is not None else Path.cwd()
    base = base.resolve()
    for candidate in (base, *base.parents):
        if (candidate / _DOT_MARIVO).is_dir():
            return candidate
    return base


def _active_path(project_root: Path) -> Path:
    return Path(project_root) / _DOT_MARIVO / _ANALYSIS_DIR / _ACTIVE_FILE


def read_active_session_name(project_root: Path) -> str | None:
    path = _active_path(project_root)
    if not path.is_file():
        return None
    return path.read_text().strip() or None


def write_active_session_name(project_root: Path, name: str) -> None:
    path = _active_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n")


def clear_active_session(project_root: Path) -> None:
    path = _active_path(project_root)
    if path.is_file():
        path.unlink()
