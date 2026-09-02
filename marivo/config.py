"""Project configuration and path constants for the marivo project layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marivo._compat import tomllib

# ---------------------------------------------------------------------------
# Path constants — single source of truth for directory names
# ---------------------------------------------------------------------------

PROJECT_MANIFEST = "marivo.toml"

AUTHORED_DIR = "models"
DATASOURCES_DIR = "models/datasources"
SEMANTIC_DIR = "models/semantic"

STATE_DIR = ".marivo"
EVIDENCE_DIR = ".marivo/evidence"
ANALYSIS_DIR = ".marivo/analysis"
AUTHORING_DIR = ".marivo/authoring"
AUTHORING_SNAPSHOT_DIR = ".marivo/authoring/snapshots"

CLAUDE_SKILLS_DIR = ".claude/skills"
CODEX_SKILLS_DIR = ".codex/skills"
AGENTS_SKILLS_DIR = ".agents/skills"
SKILL_SEMANTIC = "marivo-semantic"
SKILL_ANALYSIS = "marivo-analysis"


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectConfig:
    """Parsed project-level configuration from ``marivo.toml``.

    Args:
        name: Project name.
        semantic_layer_paths: External authored ``models/`` roots.
        telemetry_enabled: Whether local telemetry is enabled by default.

    Returns:
        ProjectConfig with project identity metadata.

    Example:
        >>> config = load_project_config(Path("/work/sales"))
        >>> config.name
        'sales'

    Constraints:
        Missing files and fields use deterministic project-local defaults.
        Explicit invalid values fail closed.
    """

    name: str
    semantic_layer_paths: tuple[Path, ...] = ()
    telemetry_enabled: bool = True


def load_project_config(project_root: Path) -> ProjectConfig:
    """Parse ``marivo.toml`` from the given project root.

    Args:
        project_root: Directory containing ``marivo.toml``.

    Returns:
        Effective project configuration. Missing files and fields default to
        the project directory name, no external semantic layers, and local
        telemetry enabled.

    Raises:
        ValueError: If a configured table or field has an invalid shape or
            value. Invalid TOML propagates as ``TOMLDecodeError``.

    Example:
        >>> config = load_project_config(Path("/work/sales"))
        >>> config.name
        'sales'

    Constraints:
        Unknown keys are silently ignored. A missing manifest is not created.
    """
    project_root = project_root.resolve()
    manifest_path = project_root / PROJECT_MANIFEST
    default_name = project_root.name
    if not manifest_path.is_file():
        return ProjectConfig(name=default_name)
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)

    project_table = data.get("project")
    if project_table is None:
        name = default_name
    elif not isinstance(project_table, dict):
        raise ValueError("marivo.toml [project] must be a table.")
    else:
        raw_name = project_table.get("name")
        if raw_name is None:
            name = default_name
        elif not isinstance(raw_name, str) or not raw_name:
            raise ValueError("marivo.toml [project].name must be a non-empty string.")
        else:
            name = raw_name

    semantic_table = data.get("semantic")
    if semantic_table is None:
        semantic_layer_paths: tuple[Path, ...] = ()
    elif not isinstance(semantic_table, dict):
        raise ValueError("marivo.toml [semantic] must be a table.")
    else:
        raw_paths = semantic_table.get("layer_paths")
        if raw_paths is None:
            semantic_layer_paths = ()
        elif not isinstance(raw_paths, list):
            raise ValueError("marivo.toml [semantic].layer_paths must be a list of strings.")
        else:
            resolved_paths: list[Path] = []
            for index, raw_path in enumerate(raw_paths):
                if not isinstance(raw_path, str):
                    raise ValueError(
                        f"marivo.toml [semantic].layer_paths[{index}] must be a string."
                    )
                path = Path(raw_path)
                if not path.is_absolute():
                    path = project_root / path
                resolved_paths.append(path.resolve())
            semantic_layer_paths = tuple(resolved_paths)

    telemetry_table = data.get("telemetry")
    if telemetry_table is None:
        telemetry_enabled = True
    elif not isinstance(telemetry_table, dict):
        raise ValueError("marivo.toml [telemetry] must be a table.")
    else:
        raw_enabled = telemetry_table.get("enabled")
        if raw_enabled is None:
            telemetry_enabled = True
        elif not isinstance(raw_enabled, str) or raw_enabled not in {"on", "off"}:
            raise ValueError("marivo.toml [telemetry].enabled must be 'on' or 'off'.")
        else:
            telemetry_enabled = raw_enabled == "on"

    return ProjectConfig(
        name=name,
        semantic_layer_paths=semantic_layer_paths,
        telemetry_enabled=telemetry_enabled,
    )


def load_semantic_layer_paths(project_root: Path) -> tuple[Path, ...]:
    """Return configured external semantic layer models roots.

    Args:
        project_root: Directory containing the active project's ``marivo.toml``.

    Returns:
        Absolute paths from ``[semantic].layer_paths``. Relative paths are
        resolved against ``project_root``. Missing ``marivo.toml`` and missing
        ``[semantic]`` config both return an empty tuple.

    Raises:
        ValueError: If any explicit project configuration is invalid.

    Example:
        >>> paths = load_semantic_layer_paths(Path.cwd())
        >>> paths
        ()

    Constraints:
        Delegates to the single effective project-configuration loader and
        returns only its semantic-layer path projection.
    """
    return load_project_config(project_root).semantic_layer_paths


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
