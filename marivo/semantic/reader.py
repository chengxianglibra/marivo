"""SemanticProject lifecycle and internal semantic runtime helpers.

Agent-facing semantic reading goes through ``ms.load()`` and ``SemanticCatalog``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from marivo._symbolic import LookupSymbol, native_symbol_ref
from marivo.config import AUTHORED_DIR, SEMANTIC_DIR, load_semantic_layer_paths
from marivo.datasource import credentials as cr
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.runtime import DatasourceConnectionConfig, DatasourceConnectionService
from marivo.project import resolve_project_root
from marivo.refs import Ref, SemanticKindTag
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.loader import LoadResult, load_project
from marivo.semantic.materializer import EntityRuntimeMetadata
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.validator import Registry

if TYPE_CHECKING:
    from marivo.semantic.catalog import SemanticCatalog

__all__ = [
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "SemanticProject",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class SemanticProject:
    """Primary reader for a loaded semantic project.

    For agent-facing reading, use ms.load() which returns a SemanticCatalog.

    This reader sits one level below ``ms.load()``: ``load()`` returns the raw
    ``LoadResult``, and ``catalog()`` is the explicit bridge from that result
    to a browseable ``SemanticCatalog``.  Prefer the one-shot
    ``ms.load(workspace_dir=...)``; use ``SemanticProject`` directly only when
    you need to inspect the ``LoadResult`` (status/errors/warnings) or re-use a
    loaded project.

    Usage::

        project = SemanticProject()  # env, nearest manifest, or cwd
        # or:
        project = SemanticProject(workspace_dir="/path/to/project")
        result = project.load()
        if project.is_ready():
            catalog = project.catalog()
    """

    def __init__(
        self,
        workspace_dir: str | Path | None = None,
        *,
        root: str | Path | None = None,
    ) -> None:
        if root is not None:
            self._semantic_root = Path(root).resolve()
            self._workspace_dir = self._semantic_root.parent.parent
        else:
            if workspace_dir is None:
                workspace_dir = resolve_project_root()
            self._workspace_dir = Path(workspace_dir).resolve()
            self._semantic_root = self._workspace_dir / SEMANTIC_DIR
        self._status: str = "unloaded"  # unloaded | ready | errored
        self._errors: tuple[SemanticError, ...] = ()
        self._warnings: tuple[StructuredWarning, ...] = ()
        self._registry: Registry | None = None
        self._expression_sidecar: CompiledExpressionSidecar | None = None
        self._compiled_state: CompiledSemanticState | None = None
        self._filtered_domains: tuple[str, ...] = ()
        self._runtime_metadata: dict[str, EntityRuntimeMetadata] = {}
        self._connection_config = DatasourceConnectionConfig(
            project_root=self._workspace_dir,
            resolver=cr.current_resolver(),
        )
        self._datasource_irs: tuple[DatasourceIR, ...] = ()

    @property
    def semantic_root(self) -> Path:
        """Return the semantic root path (models/semantic/)."""
        return self._semantic_root

    @property
    def state_root(self) -> Path:
        """Return the runtime state root path (.marivo/)."""
        return self._workspace_dir / ".marivo"

    @property
    def workspace_dir(self) -> Path:
        """Return the workspace directory path."""
        return self._workspace_dir

    # -- lifecycle -----------------------------------------------------------

    def load(self, domains: str | Sequence[str] | None = None) -> LoadResult:
        """Load the project from disk.

        When *domains* is specified, only those domain directories are loaded.
        Pass a single domain name as a string or a list of names.
        Cross-domain references to filtered-out domains produce warnings instead
        of errors, so the registry remains usable.
        """
        if isinstance(domains, str):
            domains = [domains]
        if self._status != "unloaded":
            self._status = "unloaded"
            self._errors = ()
            self._warnings = ()
            self._registry = None
            self._expression_sidecar = None
            self._compiled_state = None
            self._runtime_metadata = {}
            self._datasource_irs = ()
        if self._semantic_root.exists() and not self._semantic_root.is_dir():
            _raise(
                ErrorKind.INVALID_PROJECT,
                f"{self._semantic_root} exists but is not a directory.",
                cls=SemanticLoadError,
                refs=(str(self._semantic_root),),
            )
        if domains is not None and len(domains) > 0:
            self._filtered_domains = tuple(domains)
        else:
            self._filtered_domains = ()
        configured_roots: tuple[Path, ...] = ()
        config_errors: list[SemanticError] = []
        try:
            configured_roots = load_semantic_layer_paths(self._workspace_dir)
        except ValueError as exc:
            config_errors.append(
                SemanticLoadError(
                    kind=ErrorKind.INVALID_PROJECT,
                    message=str(exc),
                    refs=(str(self._workspace_dir / "marivo.toml"),),
                    hint="Fix the explicit marivo.toml configuration and rerun ms.load().",
                )
            )
        if config_errors:
            result = LoadResult(status="errored", errors=tuple(config_errors))
        else:
            models_roots = (self._workspace_dir / AUTHORED_DIR, *configured_roots)
            result = load_project(
                self._semantic_root,
                models=self._filtered_domains if self._filtered_domains else None,
                models_roots=models_roots,
                project_root=self._workspace_dir,
            )
        self._status = result.status
        self._errors = result.errors
        self._warnings = result.warnings
        self._registry = result.registry
        self._expression_sidecar = result.expression_sidecar
        self._compiled_state = result.compiled_state
        self._datasource_irs = result.datasource_irs
        return result

    def is_ready(self) -> bool:
        """Return True if the project is in the ready state."""
        return self._status == "ready"

    def errors(self) -> tuple[SemanticError, ...]:
        """Return errors from the last load attempt."""
        return self._errors

    def warnings(self) -> tuple[StructuredWarning, ...]:
        """Return warnings from the last load attempt."""
        return self._warnings

    def catalog(self) -> SemanticCatalog:
        """Return a browseable ``SemanticCatalog`` over this project.

        This is the explicit bridge from the reader-level ``project.load()``
        path (which returns a ``LoadResult``) to the agent-facing
        ``SemanticCatalog``.  It is the same object that ``ms.load()`` returns;
        prefer ``ms.load(workspace_dir=...)`` for one-shot loading.

        Raises ``SemanticLoadFailed`` if the project is not in the 'ready'
        state (for example, when ``load()`` has not been called yet or
        errored).
        """
        from marivo.semantic.catalog import SemanticCatalog

        return SemanticCatalog(self)

    # -- readiness ----------------------------------------------------------

    def _connection_operation(self) -> AbstractContextManager[DatasourceConnectionService]:
        """Create an operation owner using this reader's captured resolver."""
        return self._connection_config.operation()

    def readiness(
        self,
        *,
        refs: Iterable[LookupSymbol | str] | None = None,
    ) -> ReadinessReport:
        """Return a query-free semantic readiness report.

        Performs in-memory checks and reads dedicated certified artifact state:
        load errors, unknown physical refs, cross-datasource dependencies,
        temporal artifact integrity, and scoped load warnings. Use
        ``refs`` to scope which semantic objects to check; by default all loaded
        objects are checked.

        Ordinary discovery snapshots and preview history never affect this
        report; readiness never executes datasource calls itself.

        Args:
            refs: Native lookup selectors. String paths are retained for reader diagnostics.
        """
        normalized: list[Ref[SemanticKindTag] | str] | None = None
        if refs is not None:
            normalized = []
            for value in refs:
                reference = native_symbol_ref(value)
                if reference is not None:
                    normalized.append(reference)
                elif isinstance(value, str):
                    normalized.append(value)
                else:
                    raise TypeError(
                        "readiness refs require native lookup symbols or diagnostic paths."
                    )
        return self._readiness(refs=normalized)

    def _readiness(
        self, *, refs: Iterable[Ref[SemanticKindTag] | str] | None = None
    ) -> ReadinessReport:
        from marivo.semantic.readiness import build_readiness_report

        return build_readiness_report(self, refs=refs)
