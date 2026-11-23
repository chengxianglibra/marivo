"""Find a semantic project from its manifest."""

from __future__ import annotations

from pathlib import Path

from marivo._kernel.config import PROJECT_MANIFEST
from marivo.semantic.errors import (
    ErrorKind,
    SemanticLoadError,
    _raise,
)
from marivo.semantic.reader import SemanticProject


def find_project(start_dir: str | Path = ".") -> SemanticProject | None:
    """Discover a semantic project by walking up from *start_dir*.

    Looks for a ``marivo.toml`` file.  Returns a
    ``SemanticProject`` on success, or ``None`` if no project is found.

    If ``marivo.toml`` exists but is a non-file entry,
    raises ``SemanticLoadError`` with ``INVALID_PROJECT``.
    """

    current = Path(start_dir).resolve()

    while True:
        manifest = current / PROJECT_MANIFEST
        if manifest.exists():
            if not manifest.is_file():
                _raise(
                    ErrorKind.INVALID_PROJECT,
                    f"{manifest} exists but is not a file.",
                    cls=SemanticLoadError,
                    refs=(str(manifest),),
                )
            return SemanticProject(workspace_dir=current)

        parent = current.parent
        if parent == current:
            return None
        current = parent
