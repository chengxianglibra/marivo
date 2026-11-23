"""Construct a Catalog from a completed project load."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from marivo.semantic.catalog import (
    SemanticCatalog,
)
from marivo.semantic.errors import SemanticLoadFailed
from marivo.semantic.reader import SemanticProject
from marivo.telemetry import traced


@traced(surface="semantic", operation="load", stage="resolve")
def load(
    *,
    workspace_dir: str | Path | None = None,
    domains: str | Sequence[str] | None = None,
    allow_partial: bool = False,
) -> SemanticCatalog:
    """Load a semantic project and return a browseable SemanticCatalog.

    This is the single public entry point for loading the semantic layer.  The
    lower layers (``SemanticProject.load()`` and
    ``marivo.semantic.loader.load_project()``) return a raw ``LoadResult``
    rather than a catalog; they are for inspecting load status/errors or for
    re-using an already-loaded project, not for agent-facing browsing.

    Args:
        workspace_dir: Optional exact project root. When omitted, resolves
            from ``MARIVO_PROJECT_ROOT``, the nearest ancestor manifest, or
            the current directory. The local ``models/`` root is always
            loaded; external roots can be added with
            ``marivo.toml [semantic].layer_paths``.
        domains: None or an empty sequence loads all domain directories.
            A name or nonempty sequence selects only those directories,
            without automatic dependency expansion.
            Include every domain required by the selected declarations.
            Filtered Relationship references may warn. Fields require their
            owning Entity, and Metrics require their dependencies in scope.
        allow_partial: Browse the validated subset after datasource declaration
            execution failures. Search pages disclose incomplete scope, and
            catalog.load_errors and catalog.excluded_refs explain omissions.

    Returns:
        SemanticCatalog on success.

    Example:
        >>> import marivo.semantic as ms
        >>> catalog = ms.load()
        >>> catalog.search(kind="domain").show()
        >>> catalog = ms.load(domains=["sales"])
        >>> catalog.search(kind="domain").show()

    Constraints:
        Strict by default. A partial Catalog supports discovery and lookup.
        Readiness, validation and preview require a successful strict load.
        Does not query a datasource or print to stdout. Source validation is
        separate from this project-level static validation event.
        Configured layer paths must point at authored ``models/`` roots that
        contain both ``datasources/`` and ``semantic/``.
        Model declarations and relative imports use current source in fresh
        namespaces for each load, without modifying sys.path. Model-to-model absolute
        imports are unsupported. Strict loads retain modules until process exit.
        Existing semantic snapshots are unchanged by later loads.
        Restart Python after package upgrades or changes to ordinary imported
        dependencies; those dependencies retain standard Python module caching.
        Execution failures expose their original exception type and traceback.
    """

    project = SemanticProject(workspace_dir=workspace_dir)
    result = project.load(domains=domains, allow_partial=allow_partial)
    if result.status == "errored":
        raise SemanticLoadFailed(result.errors)
    return project.catalog()
