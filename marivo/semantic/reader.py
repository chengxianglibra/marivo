"""SemanticProject lifecycle and internal semantic runtime helpers.

Agent-facing semantic reading goes through ``ms.load()`` and ``SemanticCatalog``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from marivo.config import AUTHORED_DIR, SEMANTIC_DIR, load_semantic_layer_paths
from marivo.datasource.ir import DatasourceIR
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.refs import SemanticRef
from marivo.semantic.catalog import CatalogObject, DerivedMetricDetails
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringObjectKind,
    VerifyResult,
)
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticLoadError,
    SemanticRuntimeError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.ir import (
    SymbolKind,
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
from marivo.semantic.validator import Registry, Sidecar

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
    kind: SymbolKind
    children: tuple[_DepNode, ...]


def _suggest_ref_level(registry: Registry, ref: str) -> str | None:
    """Return an actionable suggestion when *ref* is not found in *registry*.

    Detects the two most common wrong-level mistakes:

    * Metric referenced at entity level (e.g. ``domain.entity.metric``
      when the correct ref is ``domain.metric``).
    * Dimension referenced at domain level (e.g. ``domain.dimension``
      when the correct ref is ``domain.entity.dimension``).

    Returns ``None`` when no plausible suggestion can be derived.
    """
    parts = ref.split(".")

    # --- Metric referenced at entity level (3+ dots) ---
    # e.g. "trino_query.query_info.total_elapsed_time" → "trino_query.total_elapsed_time"
    if len(parts) >= 3:
        domain = parts[0]
        object_name = parts[-1]
        domain_level_ref = f"{domain}.{object_name}"
        if domain_level_ref in registry.metrics:
            return (
                f"Metrics are referenced at the domain level, not the entity level. "
                f"Use {domain_level_ref!r} instead of {ref!r}."
            )

    # --- Dimension / time_dimension referenced at domain level (2 dots) ---
    # e.g. "trino_query.cluster" → "trino_query.query_info.cluster"
    if len(parts) == 2:
        domain = parts[0]
        object_name = parts[1]
        matching_fields = [
            f_id
            for f_id, f_ir in registry.dimensions.items()
            if f_ir.domain == domain and f_ir.name == object_name
        ]
        if matching_fields:
            suggestions = ", ".join(repr(f) for f in matching_fields[:3])
            return (
                f"Dimensions and time dimensions are referenced at the entity level, "
                f"not the domain level. Did you mean {suggestions}?"
            )

    return None


_AUTHORING_KIND_BY_SYMBOL: dict[SymbolKind, AuthoringObjectKind] = {
    SymbolKind.DOMAIN: "domain",
    SymbolKind.ENTITY: "entity",
    SymbolKind.DIMENSION: "dimension",
    SymbolKind.TIME_DIMENSION: "time_dimension",
    SymbolKind.MEASURE: "measure",
    SymbolKind.METRIC: "metric",
    SymbolKind.RELATIONSHIP: "relationship",
}


def _verification_input(
    value: CatalogObject[SemanticRef] | SemanticRef,
) -> tuple[SemanticRef, AuthoringObjectKind]:
    """Return the typed ref and requested kind before project reload."""
    if isinstance(value, CatalogObject):
        semantic_ref = value.ref
        if isinstance(value.details(), DerivedMetricDetails):
            return semantic_ref, "derived_metric"
    elif isinstance(value, SemanticRef):
        semantic_ref = value
    else:
        _raise(
            ErrorKind.INVALID_REF,
            "SemanticProject.verify_object(ref=...) requires a CatalogObject or "
            "SemanticRef from an authoring call, ms.ref('<kind>.<semantic_id>'), "
            "or catalog.get('<kind>.<semantic_id>').",
            cls=SemanticRuntimeError,
            refs=(str(value),),
        )

    requested_kind = _AUTHORING_KIND_BY_SYMBOL.get(semantic_ref.kind)
    if requested_kind is None:
        _raise(
            ErrorKind.INVALID_REF,
            "SemanticProject.verify_object(ref=...) requires a semantic authoring object; "
            f"received {semantic_ref.kind.value!r}.",
            cls=SemanticRuntimeError,
            refs=(semantic_ref.id,),
        )
    return semantic_ref, requested_kind


class SemanticProject:
    """Primary reader for a loaded semantic project.

    For agent-facing reading, use ms.load() which returns a SemanticCatalog.

    Usage::

        project = SemanticProject()  # uses cwd or MARIVO_PROJECT_ROOT
        # or:
        project = SemanticProject(workspace_dir="/path/to/project")
        result = project.load()
        if project.is_ready():
            ...
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
        self._sidecar: Sidecar | None = None
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
            self._sidecar = None
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
        self._sidecar = result.sidecar
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
            kind = SymbolKind.TIME_DIMENSION if f_ir.is_time_dimension else SymbolKind.DIMENSION
            return _DepNode(semantic_id=name, kind=kind, children=())

        if name in reg.metrics:
            return self._dependents_metric(name, reg)

        if name in reg.relationships:
            return _DepNode(semantic_id=name, kind=SymbolKind.RELATIONSHIP, children=())

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
                ds_children.append(_DepNode(semantic_id=m_id, kind=SymbolKind.METRIC, children=()))
        for f_id, f_ir in reg.dimensions.items():
            if f_ir.entity == name:
                kind = SymbolKind.TIME_DIMENSION if f_ir.is_time_dimension else SymbolKind.DIMENSION
                ds_children.append(_DepNode(semantic_id=f_id, kind=kind, children=()))
        for measure_id, measure_ir in reg.measures.items():
            if measure_ir.entity == name:
                ds_children.append(
                    _DepNode(semantic_id=measure_id, kind=SymbolKind.MEASURE, children=())
                )
        return _DepNode(
            semantic_id=name,
            kind=SymbolKind.ENTITY,
            children=tuple(ds_children),
        )

    def _dependents_measure(self, name: str, reg: Registry) -> _DepNode:
        metric_children = [
            _DepNode(semantic_id=m_id, kind=SymbolKind.METRIC, children=())
            for m_id, m_ir in reg.metrics.items()
            if m_ir.measure == name
        ]
        return _DepNode(
            semantic_id=name,
            kind=SymbolKind.MEASURE,
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
                            _DepNode(semantic_id=m_id, kind=SymbolKind.METRIC, children=())
                        )
        return _DepNode(
            semantic_id=name,
            kind=SymbolKind.METRIC,
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
        refs: Iterable[SemanticRef | str] | None = None,
    ) -> ReadinessReport:
        """Return a query-free semantic readiness report.

        Performs in-memory checks and reads persisted row-free preview evidence:
        load errors, unknown refs, cross-datasource unfederated metrics,
        recursive metric-graph lowering and budgets, SQL parity unverified
        warnings, strict enrichment issues, and load warnings forwarding. Use
        ``refs`` to scope which semantic objects to check; by default all loaded
        objects are checked.

        Missing preview evidence is reported with state-derived acquisition or
        preview calls; readiness never executes those calls itself.

        Args:
            refs: Semantic refs to scope the check. Accepts strings or
                SemanticRef objects. None checks all loaded objects.
        """
        from marivo.semantic.readiness import build_readiness_report

        str_refs = [str(r) for r in refs] if refs is not None else None
        return build_readiness_report(self, refs=str_refs)

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

    # -- verify object -------------------------------------------------------

    def verify_object(
        self,
        ref: CatalogObject[SemanticRef] | SemanticRef,
    ) -> VerifyResult:
        """Statically verify one authored semantic object against the loaded project.

        Reloading runs the existing load, assembly, dependency, type, cycle,
        and expression-contract validators. Verification never opens a
        datasource or executes user data; runtime execution belongs to the
        explicit catalog preview path.

        Parameters
        ----------
        ref:
            CatalogObject or SemanticRef returned by authoring calls,
            ``ms.ref(...)``, or ``catalog.get(...)``.
        Returns
        -------
        VerifyResult
            Static validation status, issues, and warnings.
        """
        semantic_ref, requested_kind = _verification_input(ref)
        ref_str = semantic_ref.id

        self.load(domains=list(self._filtered_domains) if self._filtered_domains else None)

        # If the project failed to load, report the load failure directly
        # instead of falling through to the misleading "not found" path.
        if self._status == "errored":
            load_errors = self._errors
            error_summary = "; ".join(str(e) for e in load_errors[:3])
            if len(load_errors) > 3:
                error_summary += f"; ... and {len(load_errors) - 3} more"
            message = (
                f"Cannot verify {ref_str!r}: project failed to load. "
                f"Fix the following errors and try again: {error_summary}"
            )
            return self._failed_verify(ref_str, requested_kind, "project_load_failed", message)

        kind = self._kind_for_ref(ref_str)

        if kind != "unknown":
            return VerifyResult(
                status="passed",
                ref=ref_str,
                kind=kind,
                validation_level="static",
                runtime_checked=False,
                issues=(),
                warnings=(),
            )

        # Unknown kind fallback — check for common wrong-level refs before
        # returning a generic message.  When the registry is unavailable,
        # report a project-load failure (belt-and-suspenders; the early
        # check above should normally prevent reaching this branch).
        if self._registry is None:
            message = (
                f"Cannot verify {ref_str!r}: project registry is not available. "
                f"Call ms.load() to check for errors."
            )
            return self._failed_verify(ref_str, requested_kind, "project_load_failed", message)
        suggestion = _suggest_ref_level(self._registry, ref_str)
        if suggestion is not None:
            message = f"Semantic object {ref_str!r} was not found. {suggestion}"
        else:
            message = (
                f"Semantic object {ref_str!r} was not found. "
                "Use catalog.domains.show() to browse available refs."
            )
        return self._failed_verify(ref_str, requested_kind, "static_check_failed", message)

    def _kind_for_ref(self, ref: str) -> AuthoringObjectKind | Literal["unknown"]:
        """Determine the kind of a semantic ref from the registry."""
        if self._registry is None:
            return "unknown"
        if ref in self._registry.domains:
            return "domain"
        if ref in self._registry.entities:
            return "entity"
        if ref in self._registry.dimensions:
            field = self._registry.dimensions[ref]
            return "time_dimension" if field.is_time_dimension else "dimension"
        if ref in self._registry.measures:
            return "measure"
        if ref in self._registry.metrics:
            metric = self._registry.metrics[ref]
            return "derived_metric" if metric.metric_type == "derived" else "metric"
        if ref in self._registry.relationships:
            return "relationship"
        return "unknown"

    def _failed_verify(
        self,
        ref: str,
        kind: AuthoringObjectKind,
        issue_kind: Literal[
            "authored_object_invalid",
            "datasource_unreachable",
            "static_check_failed",
            "project_load_failed",
        ],
        message: str,
    ) -> VerifyResult:
        """Build a failed VerifyResult with a single blocker issue."""
        issue = AssessmentIssue(
            kind=issue_kind,
            severity="blocker",
            refs=(ref,),
            message=message,
            rule_id=f"verify_object_{issue_kind}",
        )
        return VerifyResult(
            status="failed",
            ref=ref,
            kind=kind,
            validation_level="static",
            runtime_checked=False,
            issues=(issue,),
            warnings=(),
        )
