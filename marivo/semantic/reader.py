"""SemanticProject lifecycle and internal semantic runtime helpers.

Agent-facing semantic reading goes through ``ms.load()`` and ``SemanticCatalog``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from marivo.datasource.ir import DatasourceIR, EntitySourceIR
from marivo.datasource.runtime import DatasourceConnectionService
from marivo.datasource.scan import ScanReport, ScanScope
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringObjectKind,
    CrossEntityMetricBrief,
    DerivedMetricBrief,
    DimensionBrief,
    DomainBrief,
    EntityBrief,
    MetricBrief,
    RelationshipBrief,
    TimeDimensionBrief,
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
            self._semantic_root = self._workspace_dir / "marivo" / "semantic"
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
        """Return the semantic root path (marivo/semantic/)."""
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

    def load(self, models: str | Sequence[str] | None = None) -> LoadResult:
        """Load the project from disk.

        When *models* is specified, only those model directories are loaded.
        Pass a single model name as a string or a list of names.
        Cross-model references to filtered-out models produce warnings instead
        of errors, so the registry remains usable.
        """
        if isinstance(models, str):
            models = [models]
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
        if models is not None and len(models) > 0:
            self._filtered_domains = tuple(models)
        else:
            self._filtered_domains = ()
        result = load_project(
            self._semantic_root, models=self._filtered_domains if self._filtered_domains else None
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

        if name in reg.datasets:
            return self._dependents_dataset(name, reg)

        if name in reg.fields:
            f_ir = reg.fields[name]
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
        for f_id, f_ir in reg.fields.items():
            if f_ir.entity == name:
                kind = SymbolKind.TIME_DIMENSION if f_ir.is_time_dimension else SymbolKind.DIMENSION
                ds_children.append(_DepNode(semantic_id=f_id, kind=kind, children=()))
        return _DepNode(
            semantic_id=name,
            kind=SymbolKind.ENTITY,
            children=tuple(ds_children),
        )

    def _dependents_metric(self, name: str, reg: Registry) -> _DepNode:
        metric_children: list[_DepNode] = []
        for m_id, m_ir in reg.metrics.items():
            if m_id == name:
                continue
            for comp_ref in m_ir.decomposition.components.values():
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
        when constructing a ``DecisionRecord`` via ``record_decision``."""
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
                project_root=self._workspace_dir
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
        refs: Iterable[str] | None = None,
    ) -> ReadinessReport:
        """Return a structural semantic readiness report.

        Performs pure in-memory checks without datasource connectivity:
        load errors, unknown refs, evidence ledger blockers, cross-datasource
        unfederated metrics, raw SQL requirements, strict enrichment issues,
        and load warnings forwarding. Use ``refs`` to scope which semantic
        objects to check; by default all loaded objects are checked.

        For runtime validation, use ``catalog.preview(...)``,
        ``project.parity_check(...)``, and ``project.richness()``.

        Args:
            refs: Semantic refs to scope the check. None checks all loaded objects.
        """
        from marivo.semantic.readiness import build_structural_readiness_report

        return build_structural_readiness_report(self, refs=refs)

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

    # -- prepare (registry-only) --------------------------------------------

    def prepare_domain(self, *, name: str) -> DomainBrief:
        """Prepare a domain authoring brief from the project registry."""
        from marivo.semantic.prepare import prepare_domain

        return prepare_domain(self, name=name)

    def prepare_derived_metric(
        self,
        *,
        numerator: str,
        denominator: str | None = None,
        weight: str | None = None,
    ) -> DerivedMetricBrief:
        """Prepare a derived metric brief from component metric refs."""
        from marivo.semantic.prepare import prepare_derived_metric

        return prepare_derived_metric(
            self, numerator=numerator, denominator=denominator, weight=weight
        )

    def prepare_entity(
        self,
        *,
        datasource: str,
        source: EntitySourceIR,
        domain: str,
        scope: ScanScope | None = None,
    ) -> EntityBrief:
        """Prepare an entity authoring brief with datasource evidence."""
        from marivo.semantic.prepare import prepare_entity

        if scope is None:
            scope = ScanScope()
        return prepare_entity(
            self, datasource=datasource, source=source, domain=domain, scope=scope
        )

    def prepare_dimensions(
        self,
        *,
        entity: str,
        columns: Sequence[str],
        scope: ScanScope | None = None,
    ) -> tuple[DimensionBrief, ...]:
        """Prepare dimension authoring briefs for the given entity columns."""
        from marivo.semantic.prepare import prepare_dimensions

        if scope is None:
            scope = ScanScope()
        return prepare_dimensions(self, entity=entity, columns=columns, scope=scope)

    def prepare_time_dimension(
        self,
        *,
        entity: str,
        column: str,
        scope: ScanScope | None = None,
    ) -> TimeDimensionBrief:
        """Prepare a time dimension authoring brief with format detection."""
        from marivo.semantic.prepare import prepare_time_dimension

        if scope is None:
            scope = ScanScope()
        return prepare_time_dimension(self, entity=entity, column=column, scope=scope)

    def prepare_metric(
        self,
        *,
        entity: str,
        measure_columns: Sequence[str] = (),
        filter_dimensions: Sequence[str] = (),
        scope: ScanScope | None = None,
    ) -> MetricBrief:
        """Prepare a metric authoring brief with measure evidence."""
        from marivo.semantic.prepare import prepare_metric

        if scope is None:
            scope = ScanScope()
        return prepare_metric(
            self,
            entity=entity,
            measure_columns=measure_columns,
            filter_dimensions=filter_dimensions,
            scope=scope,
        )

    def prepare_relationship(
        self,
        *,
        from_entity: str,
        to_entity: str,
        from_dimensions: Sequence[str],
        to_dimensions: Sequence[str],
        scope: ScanScope | None = None,
    ) -> RelationshipBrief:
        """Prepare a relationship authoring brief with join-key probe evidence."""
        from marivo.semantic.prepare import prepare_relationship

        if scope is None:
            scope = ScanScope()
        return prepare_relationship(
            self,
            from_entity=from_entity,
            to_entity=to_entity,
            from_dimensions=from_dimensions,
            to_dimensions=to_dimensions,
            scope=scope,
        )

    def prepare_cross_entity_metric(
        self,
        *,
        root_entity: str,
        entities: Sequence[str],
        measure_columns: Sequence[str] = (),
        scope: ScanScope | None = None,
    ) -> CrossEntityMetricBrief:
        """Prepare a cross-entity metric brief with relationship path evidence."""
        from marivo.semantic.prepare import prepare_cross_entity_metric

        if scope is None:
            scope = ScanScope()
        return prepare_cross_entity_metric(
            self,
            root_entity=root_entity,
            entities=entities,
            measure_columns=measure_columns,
            scope=scope,
        )

    # -- verify object -------------------------------------------------------

    _DEFAULT_SCOPE = ScanScope()  # module-level singleton for default parameter

    def verify_object(
        self,
        ref: str,
        *,
        scope: ScanScope | None = None,
    ) -> VerifyResult:
        """Verify a single authored semantic object is reachable and valid.

        For domains and relationships this is a static-only check. For entities,
        a scoped preview confirms the datasource is reachable and the expression
        is valid. For dimensions, time dimensions, metrics, and derived metrics
        the check is static-only for now.

        Parameters
        ----------
        ref:
            Fully qualified semantic ref (e.g. ``"sales.orders"``).
        scope:
            Scan scope controlling partition, max rows, and timeout.
            Defaults to ``ScanScope()``.

        Returns
        -------
        VerifyResult
            Status, issues, and optional scan report for entity verification.
        """
        from marivo.semantic.scope import scoped_entity_expression

        if scope is None:
            scope = self._DEFAULT_SCOPE

        self.load(models=list(self._filtered_domains) if self._filtered_domains else None)
        kind = self._kind_for_ref(ref)

        if kind == "domain" or kind == "relationship":
            return VerifyResult(
                status="passed",
                ref=ref,
                kind=kind,
                issues=(),
                warnings=(),
                scan=None,
                auto_recorded=(),
            )

        if kind == "entity":
            entity = self._registry.datasets.get(ref) if self._registry is not None else None
            if entity is None:
                return self._failed_verify(
                    ref, "entity", "authored_object_invalid", "Object is not loaded."
                )
            try:
                service = DatasourceConnectionService(project_root=self._workspace_dir)
                with service.use_backend(entity.datasource) as backend:
                    scoped = scoped_entity_expression(
                        backend=backend,
                        entity_source=entity.source,
                        partition=scope.partition if isinstance(scope.partition, dict) else None,
                    )
                    preview = scoped.expr.limit(scope.max_rows).execute()
                    scan = ScanReport(
                        partition_used=scoped.scan.partition_used,
                        partition_resolution=scoped.scan.partition_resolution,
                        rows_scanned=len(preview),
                        columns_scanned=tuple(preview.columns),
                        truncated=len(preview) >= scope.max_rows,
                        elapsed_seconds=scoped.scan.elapsed_seconds,
                        warnings=scoped.scan.warnings,
                    )
                return VerifyResult(
                    status="passed",
                    ref=ref,
                    kind="entity",
                    issues=(),
                    warnings=(),
                    scan=scan,
                    auto_recorded=(),
                )
            except Exception as exc:
                issue = AssessmentIssue(
                    kind="datasource_unreachable",
                    severity="blocker",
                    refs=(ref,),
                    message=str(exc),
                    rule_id="verify_object_datasource_access",
                )
                return VerifyResult(
                    status="failed",
                    ref=ref,
                    kind="entity",
                    issues=(issue,),
                    warnings=(),
                    scan=None,
                    auto_recorded=(),
                )

        if kind == "dimension":
            return VerifyResult(
                status="passed",
                ref=ref,
                kind="dimension",
                issues=(),
                warnings=(),
                scan=None,
                auto_recorded=(),
            )
        if kind == "time_dimension":
            return VerifyResult(
                status="passed",
                ref=ref,
                kind="time_dimension",
                issues=(),
                warnings=(),
                scan=None,
                auto_recorded=(),
            )
        if kind == "metric":
            return VerifyResult(
                status="passed",
                ref=ref,
                kind="metric",
                issues=(),
                warnings=(),
                scan=None,
                auto_recorded=(),
            )
        if kind == "derived_metric":
            return VerifyResult(
                status="passed",
                ref=ref,
                kind="derived_metric",
                issues=(),
                warnings=(),
                scan=None,
                auto_recorded=(),
            )

        # Unknown kind fallback
        return self._failed_verify(
            ref, "entity", "static_check_failed", "Verification is not implemented for this kind."
        )

    def _kind_for_ref(self, ref: str) -> AuthoringObjectKind | Literal["unknown"]:
        """Determine the kind of a semantic ref from the registry."""
        if self._registry is None:
            return "unknown"
        if ref in self._registry.models:
            return "domain"
        if ref in self._registry.datasets:
            return "entity"
        if ref in self._registry.fields:
            field = self._registry.fields[ref]
            return "time_dimension" if field.is_time_dimension else "dimension"
        if ref in self._registry.metrics:
            return "metric"
        if ref in self._registry.relationships:
            return "relationship"
        return "unknown"

    def _failed_verify(
        self,
        ref: str,
        kind: AuthoringObjectKind,
        issue_kind: Literal[
            "authored_object_invalid", "datasource_unreachable", "static_check_failed"
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
            issues=(issue,),
            warnings=(),
            scan=None,
            auto_recorded=(),
        )
