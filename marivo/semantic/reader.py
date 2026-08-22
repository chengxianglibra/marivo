"""SemanticProject lifecycle and internal semantic runtime helpers.

Agent-facing semantic reading goes through ``ms.load()`` and ``SemanticCatalog``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from marivo.config import AUTHORED_DIR, SEMANTIC_DIR, load_semantic_layer_paths
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import Ref, SemanticKind, SemanticKindTag
from marivo.semantic._compiled_state import CompiledSemanticState
from marivo.semantic._expression_binding import CompiledExpressionSidecar
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    SemanticRuntimeError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.loader import LoadResult, load_project
from marivo.semantic.materializer import EntityRuntimeMetadata
from marivo.semantic.parity import ParityResult, parity_check
from marivo.semantic.readiness import (
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
)
from marivo.semantic.richness import (
    DemandSignal,
    RichnessReport,
    build_richness_report,
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


def _require_registry(
    self_or_registry: Registry | None, project: SemanticProject | None = None
) -> Registry:
    """Return the registry or raise SemanticLoadFailed with the actual errors."""
    if self_or_registry is not None:
        return self_or_registry
    from marivo.semantic.errors import SemanticLoadFailed

    if project is not None and project._errors:
        raise SemanticLoadFailed(project._errors)
    raise SemanticLoadFailed(
        [
            SemanticRuntimeError(
                kind=ErrorKind.PROJECT_NOT_LOADED,
                message="Project is not loaded. Call ms.load() to load the semantic project.",
            )
        ]
    )


@dataclass(frozen=True)
class _DepNode:
    """Lightweight internal node for dependency traversal."""

    semantic_id: str
    kind: SemanticKind
    children: tuple[_DepNode, ...]


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

        project = SemanticProject()  # uses cwd or MARIVO_PROJECT_ROOT
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
                env = os.environ.get("MARIVO_PROJECT_ROOT")
                workspace_dir = env if env else "."
            self._workspace_dir = Path(workspace_dir).resolve()
            self._semantic_root = self._workspace_dir / SEMANTIC_DIR
        self._status: str = "unloaded"  # unloaded | ready | errored
        self._errors: tuple[SemanticError, ...] = ()
        self._warnings: tuple[StructuredWarning, ...] = ()
        self._load_result: LoadResult | None = None
        self._registry: Registry | None = None
        self._expression_sidecar: CompiledExpressionSidecar | None = None
        self._compiled_state: CompiledSemanticState | None = None
        self._filtered_domains: tuple[str, ...] = ()
        self._runtime_metadata: dict[str, EntityRuntimeMetadata] = {}
        self._parity_results: dict[str, ParityResult] = {}
        self._connection_service_instance: DatasourceConnectionService | None = None
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

    @property
    def root(self) -> Path:
        """Return the semantic root path for compatibility."""
        return self._semantic_root

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
            self._parity_results = {}
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
                    hint="Fix marivo.toml [semantic].layer_paths and rerun ms.load().",
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
            )
        self._load_result = result
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

    # -- dependency graph (internal) -----------------------------------------

    def _dependents(self, name: str) -> _DepNode:
        """Internal: return objects that depend on the named object."""
        reg = _require_registry(self._registry, project=self)

        if name in reg.entities:
            return self._dependents_dataset(name, reg)

        if name in reg.measures:
            return self._dependents_measure(name, reg)

        if name in reg.dimensions:
            f_ir = reg.dimensions[name]
            kind = SemanticKind.TIME_DIMENSION if f_ir.is_time_dimension else SemanticKind.DIMENSION
            return _DepNode(semantic_id=name, kind=kind, children=())

        if name in reg.metrics:
            return self._dependents_metric(name, reg)

        if name in reg.relationships:
            return _DepNode(semantic_id=name, kind=SemanticKind.RELATIONSHIP, children=())
        if name in reg.events:
            children = tuple(
                _DepNode(
                    semantic_id=model.semantic_id,
                    kind=SemanticKind.STATE_MODEL,
                    children=(),
                )
                for model in reg.state_models.values()
                if any(item.trigger.event_ref == name for item in model.inceptions)
                or any(item.trigger.event_ref == name for item in model.transitions)
            )
            return _DepNode(
                semantic_id=name,
                kind=SemanticKind.EVENT,
                children=children,
            )
        if name in reg.state_models:
            return _DepNode(semantic_id=name, kind=SemanticKind.STATE_MODEL, children=())

        _raise(
            ErrorKind.NOT_FOUND,
            f"Object {name!r} not found in registry.",
            cls=SemanticRuntimeError,
            refs=(name,),
        )

    def _dependents_dataset(self, name: str, reg: Registry) -> _DepNode:
        ds_children: list[_DepNode] = []
        for m_id, m_ir in reg.metrics.items():
            if name in m_ir.entities:
                ds_children.append(
                    _DepNode(semantic_id=m_id, kind=SemanticKind.METRIC, children=())
                )
        for f_id, f_ir in reg.dimensions.items():
            if f_ir.entity == name:
                kind = (
                    SemanticKind.TIME_DIMENSION
                    if f_ir.is_time_dimension
                    else SemanticKind.DIMENSION
                )
                ds_children.append(_DepNode(semantic_id=f_id, kind=kind, children=()))
        for measure_id, measure_ir in reg.measures.items():
            if measure_ir.entity == name:
                ds_children.append(
                    _DepNode(semantic_id=measure_id, kind=SemanticKind.MEASURE, children=())
                )
        for model in reg.state_models.values():
            if model.subject == name:
                ds_children.append(
                    _DepNode(
                        semantic_id=model.semantic_id,
                        kind=SemanticKind.STATE_MODEL,
                        children=(),
                    )
                )
        return _DepNode(
            semantic_id=name,
            kind=SemanticKind.ENTITY,
            children=tuple(ds_children),
        )

    def _dependents_measure(self, name: str, reg: Registry) -> _DepNode:
        metric_children = [
            _DepNode(semantic_id=m_id, kind=SemanticKind.METRIC, children=())
            for m_id, m_ir in reg.metrics.items()
            if m_ir.measure == name
        ]
        return _DepNode(
            semantic_id=name,
            kind=SemanticKind.MEASURE,
            children=tuple(metric_children),
        )

    def _dependents_metric(self, name: str, reg: Registry) -> _DepNode:
        from marivo.semantic.ir import composition_components

        metric_children: list[_DepNode] = []
        for m_id, m_ir in reg.metrics.items():
            if m_id == name:
                continue
            if m_ir.composition is not None:
                for comp_ref in composition_components(m_ir.composition).values():
                    if comp_ref == name:
                        metric_children.append(
                            _DepNode(semantic_id=m_id, kind=SemanticKind.METRIC, children=())
                        )
        return _DepNode(
            semantic_id=name,
            kind=SemanticKind.METRIC,
            children=tuple(metric_children),
        )

    def _flatten_ids(self, node: _DepNode) -> set[str]:
        ids: set[str] = set()
        for child in node.children:
            ids.add(child.semantic_id)
            ids |= self._flatten_ids(child)
        return ids

    def blast_radius_of(self, refs: tuple[str, ...]) -> int:
        """Count distinct transitive dependents of the given refs, excluding the
        refs themselves. Unknown (not-yet-declared) refs contribute zero.

        Public API for callers who need the real transitive-dependent count
        when assessing semantic change blast radius."""
        seen: set[str] = set()
        for ref in refs:
            try:
                node = self._dependents(ref)
            except SemanticRuntimeError:
                continue
            seen |= self._flatten_ids(node)
        return len(seen - set(refs))

    # -- parity -------------------------------------------------------------

    def parity_check(
        self,
        name: str,
        *,
        rel_tol: float | None = None,
        abs_tol: float | None = None,
        force: bool = False,
    ) -> ParityResult:
        """Run parity check for a metric against its source SQL.

        See :func:`marivo.semantic.parity.parity_check` for details.
        Datasource backends are resolved internally via
        ``DatasourceConnectionService``.
        """
        return parity_check(
            self,
            name,
            rel_tol=rel_tol,
            abs_tol=abs_tol,
            force=force,
        )

    # -- readiness ----------------------------------------------------------

    def _connection_service(self) -> DatasourceConnectionService:
        """Return the lazily-created DatasourceConnectionService."""
        if self._connection_service_instance is None:
            self._connection_service_instance = DatasourceConnectionService(
                project_root=self._workspace_dir,
                include_semantic_layers=True,
            )
        return self._connection_service_instance

    def _session_backend_factory(self) -> Callable[[str], Any]:
        """Return a factory callable backed by the internal connection service.

        This is used by Materializer and other callers that expect a
        ``Callable[[str], Any]`` backend factory.
        """
        service = self._connection_service()

        def _factory(name: str) -> Any:
            return service.session_backend(name)

        return _factory

    def readiness(
        self,
        *,
        refs: Iterable[Ref[SemanticKindTag] | str] | None = None,
    ) -> ReadinessReport:
        """Return a query-free semantic readiness report.

        Performs in-memory checks and reads dedicated certified artifact state:
        load errors, unknown refs, cross-datasource unfederated metrics,
        recursive metric-graph lowering and budgets, SQL parity unverified
        warnings, temporal artifact integrity, and load warnings forwarding. Use
        ``refs`` to scope which semantic objects to check; by default all loaded
        objects are checked.

        Ordinary discovery snapshots and preview history never affect this
        report; readiness never executes datasource calls itself.

        Args:
            refs: Exact refs supplied by the catalog boundary. String paths are
                retained only for private readiness diagnostics.
        """
        from marivo.semantic.readiness import build_readiness_report

        scoped_refs = list(refs) if refs is not None else None
        return build_readiness_report(self, refs=scoped_refs)

    # -- richness -----------------------------------------------------------

    def richness(
        self,
        *,
        demand: DemandSignal | None = None,
    ) -> RichnessReport:
        """Return a demand-ranked advisory richness report.

        Pure advisory: it never blocks and never mutates readiness. ``demand``
        seeds coverage/depth ranking from example questions, analysis intents,
        run-history refs, and the build purpose.
        """
        return build_richness_report(self, demand=demand)
