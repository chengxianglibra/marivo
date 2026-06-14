"""Project configuration and path constants for the marivo project layout."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Path constants — single source of truth for directory names
# ---------------------------------------------------------------------------

PROJECT_MANIFEST = "marivo.toml"

AUTHORED_DIR = "marivo"
DATASOURCES_DIR = "marivo/datasources"
SEMANTIC_DIR = "marivo/semantic"

STATE_DIR = ".marivo"
EVIDENCE_DIR = ".marivo/evidence"
ANALYSIS_DIR = ".marivo/analysis"


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectConfig:
    """Parsed project-level configuration from ``marivo.toml``.

    Args:
        name: Project name.
        version: Optional project version string.

    Returns:
        ProjectConfig with project identity metadata.

    Example:
        >>> config = load_project_config(Path("/my/project"))
        >>> config.name
        'my-analytics'

    Constraints:
        ``name`` is required in ``marivo.toml``. ``version`` defaults to None.
    """

    name: str
    version: str | None = None


def load_project_config(project_root: Path) -> ProjectConfig:
    """Parse ``marivo.toml`` from the given project root.

    Args:
        project_root: Directory containing ``marivo.toml``.

    Returns:
        ProjectConfig with project identity metadata.

    Raises:
        FileNotFoundError: If ``marivo.toml`` does not exist in project_root.
        ValueError: If ``marivo.toml`` is missing the required ``[project]``
            table or ``name`` field.

    Example:
        >>> config = load_project_config(Path.cwd())
        >>> config.name
        'sales-analytics'

    Constraints:
        Only the ``[project]`` table is read. Unknown keys are silently
        ignored so that future config additions remain backward-compatible.
    """
    manifest_path = project_root / PROJECT_MANIFEST
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Project manifest {manifest_path} does not exist.")
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)
    project_table = data.get("project")
    if not isinstance(project_table, dict):
        raise ValueError("marivo.toml is missing the required [project] table.")
    name = project_table.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("marivo.toml [project] table is missing the required 'name' field.")
    version = project_table.get("version")
    if version is not None and not isinstance(version, str):
        raise ValueError(
            f"marivo.toml [project] 'version' must be a string, got {type(version).__name__}."
        )
    return ProjectConfig(name=name, version=version if isinstance(version, str) else None)


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* to find a directory containing ``marivo.toml``.

    Args:
        start: Directory to begin searching from. Defaults to the current
            working directory.

    Returns:
        The project root containing ``marivo.toml``, or None if not found.

    Example:
        >>> root = find_project_root()
        >>> root is not None
        True

    Constraints:
        Purely filesystem-based; never creates directories.
    """
    start = Path.cwd().resolve() if start is None else start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / PROJECT_MANIFEST).is_file():
            return candidate
    return None
