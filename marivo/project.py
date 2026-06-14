"""Shared project-root resolution for the marivo project layout."""

from __future__ import annotations

import os
from pathlib import Path

from marivo.config import PROJECT_MANIFEST


def resolve_project_root(start: Path | None = None) -> Path:
    """Resolve the Marivo project root directory.

    Args:
        start: Optional directory to resolve from. When given, it takes
            precedence over the environment.

    Returns:
        The explicit ``start`` ancestor containing ``marivo.toml`` (or
        ``start`` itself when none exists), else the
        ``MARIVO_PROJECT_ROOT`` env path, else the nearest ancestor of
        the current directory containing ``marivo.toml`` (or the current
        directory when none exists).

    Example:
        >>> from marivo.project import resolve_project_root
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
        if (candidate / PROJECT_MANIFEST).is_file():
            return candidate
    return base
