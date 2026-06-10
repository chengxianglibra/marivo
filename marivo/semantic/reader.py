"""SemanticProject reader API for marivo.semantic v1.1.

All read-only access to the loaded semantic model goes through
``SemanticProject`` methods.  Free-function readers are removed.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import ibis
import ibis.expr.types as ir

from marivo.datasource.ir import DatasourceIR
from marivo.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PREVIEW_DEFAULT_LIMIT,
    PREVIEW_MAX_LIMIT,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    preview_ibis_table,
    preview_ibis_value,
    validate_preview_limit,
)
from marivo.semantic.discovery import DiscoveryResult
from marivo.semantic.dtos import (
    AssessmentIssue,
    AuthoringAssessment,
    AuthoringObjectKind,
    AuthoringSourceInput,
    BoundedProfilePolicy,
    ColumnEvidence,
    DatasetSource,
    SamplePolicy,
    SelectedColumnsPolicy,
    SourceEvidencePack,
    TableSource,
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
    DimensionKind,
    EntityIR,
    EntityProvenance,
    MetricIR,
    ParityStatus,
    SymbolKind,
)
from marivo.semantic.loader import LoadResult, load_project
from marivo.semantic.materializer import EntityRuntimeMetadata, Materializer
from marivo.semantic.parity import ParityResult, parity_check, propagated_parity_status
from marivo.semantic.readiness import (
    ParitySummary,
    PreviewSummary,
    ReadinessInputSummary,
    ReadinessIssue,
    ReadinessReport,
    RichnessSummary,
    _ReadinessEvidence,
    build_readiness_report,
)
from marivo.semantic.richness import (
    DemandSignal,
    RichnessReport,
    build_richness_report,
)
from marivo.semantic.validator import Registry, Sidecar

__all__ = [
    "DatasourceSummary",
    "DimensionSummary",
    "DomainSummary",
    "EntitySummary",
    "MetricSummary",
    "ParitySummary",
    "PreviewSummary",
    "ReadinessInputSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "RichnessSummary",
    "SemanticProject",
]


_FIELD_PREVIEW_CONTEXT_COLUMNS = 3


# ---------------------------------------------------------------------------
# Summary types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainSummary:
    """Summary of a domain returned by ``project.list_domains()``."""

    name: str
    description: str | None
    default: bool
    object_counts: dict[str, int]  # kind -> count


@dataclass(frozen=True)
class DatasourceSummary:
    """Summary of a datasource returned by ``project.list_datasources()``."""

    semantic_id: str
    name: str
    backend_type: str
    description: str | None


@dataclass(frozen=True)
class EntitySummary:
    """Summary of an entity returned by ``project.list_entities()``."""

    semantic_id: str
    domain: str
    name: str
    datasource: str
    description: str | None
    entity_provenance: EntityProvenance | None  # None = not yet materialized

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.semantic_id!r})"


@dataclass(frozen=True)
class MetricSummary:
    """Summary of a metric returned by ``project.list_metrics()``."""

    semantic_id: str
    domain: str
    name: str
    description: str | None
    decomposition_kind: Literal["sum", "ratio", "weighted_average"]
    is_derived: bool
    parity_status: ParityStatus
    python_symbol: str

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.semantic_id!r})"


@dataclass(frozen=True)
class DimensionSummary:
    """Summary of a dimension returned by ``project.list_dimensions()`` / ``project.list_time_dimensions()``."""

    semantic_id: str
    domain: str
    entity: str
    name: str
    description: str | None
    is_time_dimension: bool
    kind: DimensionKind
    data_type: str | None
    granularity: str | None
    is_default: bool
    format: str | None = None
    timezone: str | None = None
    required_prefix: str | None = None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.semantic_id!r})"


@dataclass(frozen=True)
class RelationshipSummary:
    """Summary of a relationship returned by ``project.list_relationships()``."""

    semantic_id: str
    domain: str
    name: str
    from_entity: str
    to_entity: str
    from_dimensions: tuple[str, ...]
    to_dimensions: tuple[str, ...]
    description: str | None

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.semantic_id!r})"


# Deprecated aliases


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


def _semantic_leaf_name(semantic_id: str) -> str:
    return semantic_id.rsplit(".", 1)[-1]


def _raw_preview_ref(
    datasource: str,
    table: str,
    database: str | tuple[str, ...] | None,
) -> str:
    if database is None:
        return f"{datasource}.{table}"
    namespace = ".".join(database) if isinstance(database, tuple) else database
    return f"{datasource}.{namespace}.{table}"


@dataclass(frozen=True)
class _DepNode:
    """Lightweight internal node for dependency traversal."""

    semantic_id: str
    kind: SymbolKind
    children: tuple[_DepNode, ...]


class SemanticProject:
    """Primary reader for a loaded semantic project.

    For agent-facing reading, use ms.load() which returns a SemanticCatalog.
    The list_* methods are internal helpers for the authoring and
    materialization workflow.

    Usage::

        project = SemanticProject()  # uses cwd or MARIVO_PROJECT_ROOT
        # or:
        project = SemanticProject(workspace_dir="/path/to/project")
        result = project.load()
        if project.is_ready():
            domains = project.list_domains()
            domains.show()   # print bounded preview
            for d in domains:
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
            self._semantic_root = self._workspace_dir / ".marivo" / "semantic"
        self._status: str = "unloaded"  # unloaded | ready | errored
        self._errors: tuple[SemanticError, ...] = ()
        self._warnings: tuple[StructuredWarning, ...] = ()
        self._load_result: LoadResult | None = None
        self._registry: Registry | None = None
        self._sidecar: Sidecar | None = None
        self._filtered_domains: tuple[str, ...] = ()
        self._runtime_metadata: dict[str, EntityRuntimeMetadata] = {}
        self._parity_results: dict[str, ParityResult] = {}
        self._bound_inspect_source: Callable[..., Any] | None = None
        self._bound_backend_factory: Callable[[str], Any] | None = None
        self._datasource_irs: tuple[DatasourceIR, ...] = ()

    @property
    def semantic_root(self) -> Path:
        """Return the semantic root path (.marivo/semantic/)."""
        return self._semantic_root

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
        if self._registry is not None:
            from marivo.semantic.auto_record import (
                auto_record_authoring_decisions,
                backfill_blast_radii,
            )

            auto_record_authoring_decisions(
                self._registry,
                self._semantic_root,
                blast_radius_of=self.blast_radius_of,
            )
            backfill_blast_radii(
                self._semantic_root,
                blast_radius_of=self.blast_radius_of,
            )
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

    # -- listings -----------------------------------------------------------

    def list_domains(self) -> DiscoveryResult[DomainSummary]:
        """Return all domain summaries.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Returns:
            DiscoveryResult[DomainSummary] — iterate, call .ids(), .show(), etc.
        """
        reg = _require_registry(self._registry, project=self)
        results: list[DomainSummary] = []
        for model_ir in reg.models.values():
            # Compute object counts for this model
            obj_counts: dict[str, int] = {}
            obj_counts["entity"] = sum(
                1 for d in reg.datasets.values() if d.domain == model_ir.name
            )
            obj_counts["dimension"] = sum(
                1
                for f in reg.fields.values()
                if f.entity.startswith(f"{model_ir.name}.") and not f.is_time_dimension
            )
            obj_counts["time_dimension"] = sum(
                1
                for f in reg.fields.values()
                if f.entity.startswith(f"{model_ir.name}.") and f.is_time_dimension
            )
            obj_counts["metric"] = sum(1 for m in reg.metrics.values() if m.domain == model_ir.name)
            obj_counts["datasource"] = 0
            obj_counts["relationship"] = sum(
                1 for r in reg.relationships.values() if r.domain == model_ir.name
            )
            results.append(
                DomainSummary(
                    name=model_ir.name,
                    description=model_ir.description,
                    default=model_ir.default,
                    object_counts=obj_counts,
                )
            )
        return DiscoveryResult(results, item_type_name="DomainSummary", has_ids=False)

    def list_datasources(self) -> DiscoveryResult[DatasourceSummary]:
        """Return all datasource summaries.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Returns:
            DiscoveryResult[DatasourceSummary] — iterate, call .ids(), .show(), etc.
        """
        irs = self._datasource_irs or (
            tuple(self._registry.datasources.values()) if self._registry is not None else ()
        )
        results = [
            DatasourceSummary(
                semantic_id=ds_ir.semantic_id,
                name=ds_ir.name,
                backend_type=ds_ir.backend_type,
                description=ds_ir.description,
            )
            for ds_ir in irs
        ]
        return DiscoveryResult(results, item_type_name="DatasourceSummary")

    def list_entities(self, *, domain: str | None = None) -> DiscoveryResult[EntitySummary]:
        """Return entity summaries, optionally filtered by domain name.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Args:
            domain: Optional domain name to filter entities.

        Returns:
            DiscoveryResult[EntitySummary] — iterate, call .ids(), .show(), etc.
        """
        reg = _require_registry(self._registry, project=self)
        datasets = list(reg.datasets.values())
        if domain is not None:
            datasets = [d for d in datasets if d.domain == domain]
        results = [
            EntitySummary(
                semantic_id=d.semantic_id,
                domain=d.domain,
                name=d.name,
                datasource=d.datasource,
                description=d.description,
                entity_provenance=(
                    self._runtime_metadata[d.semantic_id].entity_provenance
                    if d.semantic_id in self._runtime_metadata
                    else None
                ),
            )
            for d in datasets
        ]
        return DiscoveryResult(results, item_type_name="EntitySummary")

    def list_dimensions(
        self,
        *,
        domain: str | None = None,
        entity: str | None = None,
    ) -> DiscoveryResult[DimensionSummary]:
        """Return dimension summaries, optionally filtered by domain or entity.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Dimensions are all @ms.dimension declarations that are not time dimensions.
        For time dimensions, use list_time_dimensions().

        Args:
            domain: Optional domain name to filter dimensions.
            entity: Optional entity semantic_id to filter dimensions.

        Returns:
            DiscoveryResult[DimensionSummary] — iterate, call .ids(), .show(), etc.
        """
        reg = _require_registry(self._registry, project=self)
        irs = [f for f in reg.fields.values() if not f.is_time_dimension]
        if domain is not None:
            irs = [f for f in irs if f.domain == domain]
        if entity is not None:
            irs = [f for f in irs if f.entity == entity]
        results = [
            DimensionSummary(
                semantic_id=f.semantic_id,
                domain=f.domain,
                entity=f.entity,
                name=f.name,
                description=f.description,
                is_time_dimension=f.is_time_dimension,
                kind=f.kind,
                data_type=f.data_type,
                granularity=f.granularity,
                is_default=f.is_default,
                format=f.format,
                timezone=f.timezone,
                required_prefix=f.required_prefix,
            )
            for f in irs
        ]
        return DiscoveryResult(results, item_type_name="DimensionSummary")

    def list_time_dimensions(
        self,
        *,
        domain: str | None = None,
        entity: str | None = None,
    ) -> DiscoveryResult[DimensionSummary]:
        """Return time dimension summaries, optionally filtered by domain or entity.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Args:
            domain: Optional domain name to filter time dimensions.
            entity: Optional entity semantic_id to filter time dimensions.

        Returns:
            DiscoveryResult[DimensionSummary] — iterate, call .ids(), .show(), etc.
        """
        reg = _require_registry(self._registry, project=self)
        irs = [f for f in reg.fields.values() if f.is_time_dimension]
        if domain is not None:
            irs = [f for f in irs if f.domain == domain]
        if entity is not None:
            irs = [f for f in irs if f.entity == entity]
        results = [
            DimensionSummary(
                semantic_id=f.semantic_id,
                domain=f.domain,
                entity=f.entity,
                name=f.name,
                description=f.description,
                is_time_dimension=f.is_time_dimension,
                kind=f.kind,
                data_type=f.data_type,
                granularity=f.granularity,
                is_default=f.is_default,
                format=f.format,
                timezone=f.timezone,
                required_prefix=f.required_prefix,
            )
            for f in irs
        ]
        return DiscoveryResult(results, item_type_name="DimensionSummary")

    def list_metrics(
        self,
        *,
        entity: str | None = None,
        decomposition: Literal["sum", "ratio", "weighted_average"] | None = None,
        provenance_status: ParityStatus | None = None,
    ) -> DiscoveryResult[MetricSummary]:
        """Return metric summaries, optionally filtered.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Args:
            entity: Optional entity semantic_id to filter metrics.
            decomposition: Optional decomposition kind to filter metrics.
            provenance_status: Optional parity status to filter metrics.

        Returns:
            DiscoveryResult[MetricSummary] — iterate, call .ids(), .show(), etc.
        """
        reg = _require_registry(self._registry, project=self)
        metrics = list(reg.metrics.values())
        if entity is not None:
            metrics = [m for m in metrics if entity in m.entities]
        if decomposition is not None:
            metrics = [m for m in metrics if m.decomposition.kind == decomposition]
        if provenance_status is not None:
            metrics = [
                m
                for m in metrics
                if propagated_parity_status(self, m.semantic_id) == provenance_status
            ]
        results = [
            MetricSummary(
                semantic_id=m.semantic_id,
                domain=m.domain,
                name=m.name,
                description=m.description,
                decomposition_kind=m.decomposition.kind,
                is_derived=m.is_derived,
                parity_status=propagated_parity_status(self, m.semantic_id),
                python_symbol=m.python_symbol,
            )
            for m in metrics
        ]
        return DiscoveryResult(results, item_type_name="MetricSummary")

    def list_relationships(
        self, *, domain: str | None = None
    ) -> DiscoveryResult[RelationshipSummary]:
        """Return relationship summaries, optionally filtered by domain.

        Internal helper — agents should use catalog.list() or catalog.get() instead.

        Args:
            domain: Optional domain name to filter relationships.

        Returns:
            DiscoveryResult[RelationshipSummary] — iterate, call .ids(), .show(), etc.
        """
        reg = _require_registry(self._registry, project=self)
        rel_irs = list(reg.relationships.values())
        if domain is not None:
            rel_irs = [r for r in rel_irs if r.domain == domain]
        results = [
            RelationshipSummary(
                semantic_id=r.semantic_id,
                domain=r.domain,
                name=r.name,
                from_entity=r.from_entity,
                to_entity=r.to_entity,
                from_dimensions=r.from_dimensions,
                to_dimensions=r.to_dimensions,
                description=r.description,
            )
            for r in rel_irs
        ]
        return DiscoveryResult(results, item_type_name="RelationshipSummary")

    # -- single-object accessors -------------------------------------------

    def get_entity(self, name: str) -> EntityIR | None:
        """Return an entity IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.datasets.get(name)

    def get_metric(self, name: str) -> MetricIR | None:
        """Return a metric IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.metrics.get(name)

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

    # -- materialize --------------------------------------------------------

    def materialize_dataset(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
    ) -> ibis.Table:
        """Materialize a dataset by semantic_id using the given backend_factory.

        Each call creates a fresh Materializer instance.
        """
        mat = Materializer(self, self._resolve_backend_factory(backend_factory))
        return mat.entity(name)

    def materialize_field(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
    ) -> ir.Value:
        """Materialize a field by semantic_id using the given backend_factory.

        Each call creates a fresh Materializer instance.
        """
        mat = Materializer(self, self._resolve_backend_factory(backend_factory))
        return mat.dimension(name)

    def materialize_metric(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
    ) -> ir.Value:
        """Materialize a metric by semantic_id using the given backend_factory.

        Each call creates a fresh Materializer instance.
        """
        mat = Materializer(self, self._resolve_backend_factory(backend_factory))
        return mat.metric(name)

    # -- preview ---------------------------------------------------------------

    def collect_source_preview(
        self,
        *,
        datasource: str,
        table: str,
        database: str | tuple[str, ...] | None = None,
        backend_factory: Callable[[str], Any] | None = None,
        columns: Iterable[str] | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Collect a bounded raw preview for a datasource table source.

        The returned preview is the datasource-table preview. A successful call
        records the physical preview ref as raw preview evidence for subsequent
        readiness checks on this project instance.

        If *backend_factory* is not provided, the bound factory (set via
        :meth:`bind_datasource_access`) is used.
        """
        factory = self._resolve_backend_factory(backend_factory)
        validate_preview_limit(limit)
        backend = factory(datasource)
        source_table = table
        preview_table = (
            backend.table(source_table)
            if database is None
            else backend.table(source_table, database=database)
        )
        selected_columns = tuple(columns or ())
        if selected_columns:
            preview_table = preview_table.select(*selected_columns)

        ref = _raw_preview_ref(datasource, source_table, database)
        preview = preview_ibis_table(
            preview_table,
            kind="datasource_table",
            ref=ref,
            limit=limit,
            sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=limit),
            include_types=include_types,
            redact=redact,
        )
        from datetime import UTC, datetime

        from marivo.semantic.ledger import LedgerStore, RawPreviewEvidence

        sample_policy: dict[str, object] = {
            "method": preview.sample_policy.method,
            "limit": preview.sample_policy.limit,
            "order_by": list(preview.sample_policy.order_by),
            "filters": [dict(filter_) for filter_ in preview.sample_policy.filters],
        }
        LedgerStore(self._semantic_root).write_raw_preview(
            RawPreviewEvidence(
                ref=preview.ref,
                datasource=datasource,
                table=source_table,
                database=database,
                columns=preview.columns,
                types=preview.types,
                requested_limit=preview.requested_limit,
                returned_row_count=preview.returned_row_count,
                sample_policy=sample_policy,
                collected_at=datetime.now(UTC).isoformat(),
                status="success",
            )
        )
        return preview

    def record_primary_key_sample(self, dataset: str) -> None:
        """Record that primary key uniqueness was sampled for a dataset."""
        from marivo.semantic.ledger import LedgerStore

        LedgerStore(self._semantic_root).write_primary_key_sample(dataset)

    def preview_dataset(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Return a bounded preview of a semantic dataset."""
        factory = self._resolve_backend_factory(backend_factory)
        limit = validate_preview_limit(limit)
        table = self.materialize_dataset(name, backend_factory=factory)
        return preview_ibis_table(
            table,
            kind="semantic_dataset",
            ref=name,
            limit=limit,
            sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=limit),
            include_types=include_types,
            redact=redact,
        )

    def preview_field(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        context_columns: Iterable[str] | None = None,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Return a bounded preview of a semantic field with parent dataset context."""
        factory = self._resolve_backend_factory(backend_factory)
        limit = validate_preview_limit(limit)
        reg = _require_registry(self._registry, project=self)
        field_ir = reg.fields.get(name)
        if field_ir is None:
            _raise(
                ErrorKind.DIMENSION_NOT_FOUND,
                f"Dimension {name!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(name,),
            )

        mat = Materializer(self, factory)
        parent_table = mat.entity(field_ir.entity)
        field_value = mat.dimension(name)
        field_column_name = _semantic_leaf_name(name)

        if context_columns is None:
            selected_context = tuple(
                column for column in parent_table.columns if column != field_column_name
            )[:_FIELD_PREVIEW_CONTEXT_COLUMNS]
        else:
            selected_context = tuple(context_columns)

        missing_context = [
            column for column in selected_context if column not in parent_table.columns
        ]
        if missing_context:
            _raise(
                ErrorKind.MATERIALIZE_FAILED,
                f"Field preview context columns are not present on parent dataset: {missing_context}",
                cls=SemanticRuntimeError,
                refs=(name,),
            )

        projections = [parent_table[column] for column in selected_context]
        projections.append(field_value.name(field_column_name))
        preview_table = parent_table.select(*projections)
        return preview_ibis_table(
            preview_table,
            kind="semantic_field",
            ref=name,
            limit=limit,
            sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=limit),
            include_types=include_types,
            redact=redact,
        )

    def preview_metric(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Return a bounded preview of a semantic metric.

        Metric previews use a pre-aggregate-limit strategy: input datasets
        are bounded to ``METRIC_PREVIEW_SAMPLE_SIZE`` rows before the
        metric callable runs, so aggregation never scans the full table.
        The result is approximate.
        """
        factory = self._resolve_backend_factory(backend_factory)
        limit = validate_preview_limit(limit)
        mat = Materializer(self, factory, sample_size=METRIC_PREVIEW_SAMPLE_SIZE)
        metric_value = mat.metric(name)
        result = preview_ibis_value(
            metric_value,
            kind="semantic_metric",
            ref=name,
            limit=limit,
            column_name="value",
            sample_policy=PreviewSamplePolicy(method="pre_aggregate_limit", limit=limit),
            include_types=include_types,
            redact=redact,
        )
        result_with_warning = PreviewResult(
            kind=result.kind,
            ref=result.ref,
            columns=result.columns,
            types=result.types,
            rows=result.rows,
            requested_limit=result.requested_limit,
            returned_row_count=result.returned_row_count,
            is_truncated=result.is_truncated,
            warnings=(
                *result.warnings,
                PreviewWarning(
                    kind="approximate_preview",
                    message=f"metric computed on {METRIC_PREVIEW_SAMPLE_SIZE} row sample, result is approximate",
                ),
            ),
            sample_policy=result.sample_policy,
        )
        return result_with_warning

    # -- parity -------------------------------------------------------------

    def parity_check(
        self,
        name: str,
        *,
        backend_factory: Callable[..., Any] | None = None,
        rel_tol: float | None = None,
        abs_tol: float | None = None,
        force: bool = False,
    ) -> ParityResult:
        """Run parity check for a metric against its source SQL.

        See :func:`marivo.semantic.parity.parity_check` for details.
        """
        factory = self._resolve_backend_factory(backend_factory)
        return parity_check(
            self,
            name,
            backend_factory=factory,
            rel_tol=rel_tol,
            abs_tol=abs_tol,
            force=force,
        )

    # -- readiness ----------------------------------------------------------

    def _auto_collect_evidence(self) -> _ReadinessEvidence:
        from marivo.semantic.ledger import LedgerStore

        store = LedgerStore(self._semantic_root)

        # Raw previews: success vs failed
        raw_preview_records = store.read_raw_previews()
        raw_previews = tuple(r.ref for r in raw_preview_records if r.status == "success")
        failed_raw_previews = tuple(r.ref for r in raw_preview_records if r.status == "failed")

        # Required previews are scoped inside readiness() after requested refs
        # and dependencies have been resolved.
        required_raw_previews = ()
        required_semantic_previews = ()
        raw_sql_required_refs = ()

        # Primary keys sampled
        primary_keys_sampled = store.read_primary_key_samples()

        return _ReadinessEvidence(
            raw_previews=raw_previews,
            failed_raw_previews=failed_raw_previews,
            required_raw_previews=required_raw_previews,
            required_semantic_previews=required_semantic_previews,
            primary_keys_sampled=primary_keys_sampled,
            raw_sql_required_refs=raw_sql_required_refs,
            table_metadata=(),
            supports_federation=False,
        )

    @property
    def _backend_factory(self) -> Callable[[str], Any] | None:
        """Return the bound backend factory, if any."""
        return self._bound_backend_factory

    def bind_datasource_access(
        self,
        *,
        inspect_source: Callable[..., Any],
        backend_factory: Callable[[str], Any],
    ) -> None:
        """Bind datasource access callables for evidence collection and materialization."""
        self._bound_inspect_source = inspect_source
        self._bound_backend_factory = backend_factory

    def _resolve_backend_factory(
        self,
        backend_factory: Callable[[str], Any] | None,
    ) -> Callable[[str], Any]:
        """Return *backend_factory* or the bound factory, raising if neither is set."""
        factory = backend_factory or self._backend_factory
        if factory is None:
            _raise(
                ErrorKind.BACKEND_FACTORY_REQUIRED,
                "No backend_factory available. Call project.bind_datasource_access(...) "
                "or pass backend_factory=... explicitly.",
                cls=SemanticRuntimeError,
            )
        return factory

    def _resolve_inspect_source(
        self,
        inspect_source: Callable[..., Any] | None,
    ) -> Callable[..., Any]:
        """Return *inspect_source* or the bound callable, raising if neither is set."""
        fn = inspect_source or self._bound_inspect_source
        if fn is None:
            _raise(
                ErrorKind.INSPECT_SOURCE_REQUIRED,
                "No inspect_source available. Call project.bind_datasource_access(...) "
                "or pass inspect_source=... explicitly.",
                cls=SemanticRuntimeError,
            )
        return fn

    def readiness(
        self,
        *,
        refs: Iterable[str] | None = None,
        demand: DemandSignal | None = None,
        preview_limit: int = 20,
        parity_rel_tol: float | None = None,
        parity_abs_tol: float | None = None,
        redact: bool = True,
    ) -> ReadinessReport:
        """Return a structured semantic readiness report.

        Evidence is auto-loaded from the project's ledger and evidence store.
        Closeout uses project-bound backend access for semantic previews and
        eligible parity checks, folds richness gaps into warnings, and reports
        missing backend access as a readiness blocker. Use ``refs`` to scope
        which semantic objects to check; by default all loaded objects are
        checked.
        """
        self.load(models=list(self._filtered_domains) if self._filtered_domains else None)
        evidence = self._auto_collect_evidence()
        factory = self._backend_factory
        return build_readiness_report(
            self,
            evidence,
            backend_factory=factory,
            refs=refs,
            demand=demand,
            preview_limit=preview_limit,
            parity_rel_tol=parity_rel_tol,
            parity_abs_tol=parity_abs_tol,
            redact=redact,
        )

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

    # -- authoring evidence -------------------------------------------------

    def _datasets_by_source(self, datasource: str, source: DatasetSource) -> tuple[EntityIR, ...]:
        reg = self._registry
        if reg is None:
            return ()
        source_ir = source.to_ir()
        return tuple(
            ds
            for ds in reg.datasets.values()
            if ds.datasource == datasource and ds.source == source_ir
        )

    def inspect_source_context(
        self,
        *,
        datasource: str,
        source: DatasetSource,
        inspect_source: Callable[..., Any] | None = None,
        backend_factory: Callable[[str], Any] | None = None,
        sample_policy: SamplePolicy,
    ) -> SourceEvidencePack:
        """Collect and persist a SourceEvidencePack for one physical source.

        Folds the old inspect_source + collect_source_preview authoring steps
        into one call. When ``sample_policy`` reads rows, a bounded raw-preview
        evidence ref is also recorded so ``readiness()`` passes without a
        separate collect_source_preview call.

        If *inspect_source* or *backend_factory* is not provided, the bound
        callable (set via :meth:`bind_datasource_access`) is used.
        """
        fn = self._resolve_inspect_source(inspect_source)
        factory = self._resolve_backend_factory(backend_factory)
        from marivo.semantic.inspect import collect_source_evidence

        pack = collect_source_evidence(
            datasource=datasource,
            source=source,
            inspect_source=fn,
            backend_factory=factory,
            sample_policy=sample_policy,
        )
        if isinstance(sample_policy, (BoundedProfilePolicy, SelectedColumnsPolicy)) and isinstance(
            source, TableSource
        ):
            self.collect_source_preview(
                datasource=datasource,
                table=source.table,
                database=source.database,
                backend_factory=factory,
                limit=min(sample_policy.limit, PREVIEW_MAX_LIMIT),
                redact=sample_policy.redact,
            )
        if isinstance(sample_policy, (BoundedProfilePolicy, SelectedColumnsPolicy)):
            for ds in self._datasets_by_source(datasource, source):
                if ds.primary_key:
                    self.record_primary_key_sample(ds.semantic_id)
        return pack

    def inspect_column_context(
        self,
        *,
        datasource: str,
        source: DatasetSource,
        columns: Sequence[str],
        inspect_source: Callable[..., Any] | None = None,
        backend_factory: Callable[[str], Any] | None = None,
        sample_policy: BoundedProfilePolicy | SelectedColumnsPolicy,
    ) -> tuple[ColumnEvidence, ...]:
        """Deep-dive selected columns after inspect_source_context."""
        fn = self._resolve_inspect_source(inspect_source)
        factory = self._resolve_backend_factory(backend_factory)
        from marivo.semantic.inspect import collect_column_evidence

        result = collect_column_evidence(
            datasource=datasource,
            source=source,
            columns=columns,
            inspect_source=fn,
            backend_factory=factory,
            sample_policy=sample_policy,
        )
        column_set = set(columns)
        for ds in self._datasets_by_source(datasource, source):
            if ds.primary_key and set(ds.primary_key) <= column_set:
                self.record_primary_key_sample(ds.semantic_id)
        return result

    def assess_authoring(
        self,
        *,
        object_kind: AuthoringObjectKind,
        subject_ref: str,
        sources: Sequence[AuthoringSourceInput] = (),
        semantic_refs: Sequence[str] = (),
    ) -> AuthoringAssessment:
        """Collect current source context and assess semantic authoring inputs.

        Parameters
        ----------
        object_kind:
            Kind of semantic object being authored, such as ``"metric"`` or
            ``"derived_metric"``.
        subject_ref:
            Fully qualified semantic ref the object will use after authoring.
        sources:
            Physical source declarations to inspect before checking the authoring
            inputs. Each source is profiled with a bounded sample; selected
            columns receive a focused column profile.
        semantic_refs:
            Existing semantic objects this authored object depends on.

        Returns
        -------
        AuthoringAssessment
            Facts, issues, questions, and final status from the composed
            evidence collection and static authoring check.

        Usage
        -----
        ``project.assess_authoring(object_kind="metric", subject_ref="sales.revenue",
        sources=(AuthoringSourceInput(...),))``

        Constraints
        -----------
        This method accepts only source and semantic references, not drafted
        object content. For physical sources, call
        ``project.bind_datasource_access(...)`` first so Marivo can collect
        current datasource evidence.
        """
        if sources and (self._bound_inspect_source is None or self._bound_backend_factory is None):
            issue = AssessmentIssue(
                kind="missing_source",
                severity="blocker",
                refs=(subject_ref,),
                rule_id="datasource_access_bound",
                message=(
                    "Datasource access is required before assessing physical sources. "
                    "Call project.bind_datasource_access(...) with inspect_source and "
                    "backend_factory."
                ),
            )
            return AuthoringAssessment(
                status="blocked",
                facts=(),
                issues=(issue,),
                questions=(),
            )

        from marivo.semantic.authoring_check import check_authoring_inputs as _check
        from marivo.semantic.inspect import collect_source_evidence

        collected_packs: list[SourceEvidencePack] = []
        for source_input in sources:
            pack = collect_source_evidence(
                datasource=source_input.datasource,
                source=source_input.source,
                inspect_source=self._resolve_inspect_source(None),
                backend_factory=self._resolve_backend_factory(None),
                sample_policy=BoundedProfilePolicy(limit=100, max_profiled_columns=50),
            )
            collected_packs.append(pack)

        return _check(
            packs=collected_packs,
            object_kind=object_kind,
            subject_ref=subject_ref,
            sources=sources,
            semantic_refs=semantic_refs,
        )

    def inspect_authored_object(self, ref: str) -> AuthoringAssessment:
        """Cheap post-reload inspection of a loaded authored object.

        Backend-free: inspects the registry and evidence ledger only. It never
        materializes tables, previews, runs parity, or scans relationships.
        """
        from marivo.semantic.authoring_check import inspect_authored_object as _inspect
        from marivo.semantic.ledger import LedgerStore

        reg = _require_registry(self._registry, project=self)
        return _inspect(registry=reg, ledger_store=LedgerStore(self._semantic_root), ref=ref)
