"""Shared project-root resolution for the .marivo project layout."""

from __future__ import annotations

import os
from pathlib import Path

_DOT_MARIVO = ".marivo"


def resolve_project_root(start: Path | None = None) -> Path:
    """Resolve the Marivo project root directory.

    Args:
        start: Optional directory to resolve from. When given, it takes
            precedence over the environment.

    Returns:
        The explicit ``start`` ancestor containing ``.marivo`` (or ``start``
        itself when none exists), else the ``MARIVO_PROJECT_ROOT`` env path,
        else the nearest ancestor of the current directory containing
        ``.marivo`` (or the current directory when none exists).

    Example:
        >>> from marivo.datasource._project import resolve_project_root
        >>> root = resolve_project_root()

    Constraints:
        Purely filesystem/env based; never creates directories.
    """
    if start is None:
        env = os.environ.get("MARIVO_PROJECT_ROOT")
        if env:
            return Path(env).resolve()
        base = Path.cwd().resolve()
    else:
        base = Path(start).resolve()
    for candidate in (base, *base.parents):
        if (candidate / _DOT_MARIVO).is_dir():
            return candidate
    return base
