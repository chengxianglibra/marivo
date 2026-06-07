"""SemanticProject reader API for marivo.semantic v1.1.

All read-only access to the loaded semantic model goes through
``SemanticProject`` methods.  Free-function readers are removed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

import ibis
import ibis.expr.types as ir

from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation
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
from marivo.semantic.constraints import ConstraintId
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticRuntimeError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.evidence import (
    AssessmentResult,
    AuthoringEvidenceInput,
    BoundedProfilePolicy,
    ColumnEvidence,
    DatasetSource,
    SamplePolicy,
    SelectedColumnsPolicy,
    SourceEvidencePack,
    TableSource,
)
from marivo.semantic.evidence import (
    EvidenceRef as AuthoringEvidenceRef,
)
from marivo.semantic.evidence_store import EvidenceStore
from marivo.semantic.ir import (
    DatasetIR,
    DatasetProvenance,
    FieldIR,
    MetricIR,
    ParityStatus,
    RelationshipIR,
    SourceLocation,
    SymbolKind,
)
from marivo.semantic.loader import LoadResult, load_project
from marivo.semantic.materializer import DatasetRuntimeMetadata, Materializer
from marivo.semantic.parity import ParityResult, parity_check, propagated_parity_status
from marivo.semantic.readiness import (
    EvidenceSummary,
    ParitySummary,
    PreviewSummary,
    ReadinessIssue,
    ReadinessReport,
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
    "DatasetSummary",
    "DatasourceSummary",
    "DependencyNode",
    "Description",
    "EvidenceSummary",
    "MetricSummary",
    "ModelSummary",
    "ParitySummary",
    "PreviewSummary",
    "ReadinessIssue",
    "ReadinessReport",
    "SearchHit",
    "SemanticProject",
]


_FIELD_PREVIEW_CONTEXT_COLUMNS = 3


# ---------------------------------------------------------------------------
# Summary types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSummary:
    """Summary of a model returned by ``project.list_models()``."""

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
class DatasetSummary:
    """Summary of a dataset returned by ``project.list_datasets()``."""

    semantic_id: str
    model: str
    name: str
    datasource: str
    description: str | None
    dataset_provenance: DatasetProvenance | None  # None = not yet materialized


@dataclass(frozen=True)
class MetricSummary:
    """Summary of a metric returned by ``project.list_metrics()``."""

    semantic_id: str
    model: str
    name: str
    description: str | None
    decomposition_kind: Literal["sum", "ratio", "weighted_average"]
    is_derived: bool
    parity_status: ParityStatus
    python_symbol: str


@dataclass(frozen=True)
class FieldSummary:
    """Summary of a field returned by ``project.list_fields()`` / ``project.list_time_fields()``."""

    semantic_id: str
    model: str
    dataset: str
    name: str
    description: str | None
    is_time_field: bool
    data_type: str | None
    granularity: str | None
    is_default: bool
    format: str | None = None
    timezone: str | None = None
    required_prefix: str | None = None


@dataclass(frozen=True)
class RelationshipSummary:
    """Summary of a relationship returned by ``project.list_relationships()``."""

    semantic_id: str
    model: str
    name: str
    from_dataset: str
    to_dataset: str
    from_fields: tuple[str, ...]
    to_fields: tuple[str, ...]
    description: str | None


# ---------------------------------------------------------------------------
# SearchHit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    """A single search hit from ``project.search()``."""

    semantic_id: str
    kind: SymbolKind
    matched_field: Literal[
        "semantic_id",
        "name",
        "description",
        "business_definition",
        "synonyms",
        "examples",
    ]
    matched_snippet: str  # matched substring with short context


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).replace("\n", " ")


def _print_table(
    rows: Sequence[Mapping[str, object]],
    *,
    columns: Sequence[str],
    empty_message: str,
) -> None:
    if not rows:
        print(empty_message)
        return

    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(_display_value(row.get(column)) for column in columns))


# ---------------------------------------------------------------------------
# DependencyNode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DependencyNode:
    """Recursive dependency node for ``project.dependencies()`` / ``project.dependents()``."""

    semantic_id: str
    kind: SymbolKind
    children: tuple[DependencyNode, ...]  # upstream or downstream


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Description:
    """Full description of a semantic object returned by ``project.describe()``."""

    semantic_id: str
    kind: SymbolKind
    model: str
    name: str
    python_symbol: str
    description: str | None
    business_definition: str | None
    guardrails: tuple[str, ...]
    synonyms: tuple[str, ...]
    examples: tuple[str, ...]
    parity_status: ParityStatus | None  # metric only
    source_sql: str | None
    source_dialect: str | None
    source_document: str | None
    compiled_sql: str | None
    compile_error: dict[str, Any] | None  # {kind, message, refs}
    dependencies: tuple[str, ...]
    dependents: tuple[str, ...]
    source_location: SourceLocation | DatasourceSourceLocation
    dataset_provenance: DatasetProvenance | None
    primary_key: tuple[str, ...] | None
    granularity: str | None
    required_prefix: str | None
    format: str | None
    from_dataset: str | None = None
    to_dataset: str | None = None
    from_fields: tuple[str, ...] | None = None
    to_fields: tuple[str, ...] | None = None

    def to_text(self) -> str:
        """Render this description as human-readable text."""
        lines: list[str] = [f"[{self.kind}] {self.semantic_id}"]

        if self.name and self.name != self.semantic_id.split(".")[-1]:
            lines.append(f"  name: {self.name!r}")
        if self.description is not None:
            lines.append(f"  description: {self.description!r}")
        if self.business_definition is not None:
            lines.append(f"  business_definition: {self.business_definition!r}")
        if self.guardrails:
            lines.append(f"  guardrails: {self.guardrails!r}")
        if self.synonyms:
            lines.append(f"  synonyms: {self.synonyms!r}")
        if self.examples:
            lines.append(f"  examples: {self.examples!r}")
        if self.parity_status is not None:
            lines.append(f"  parity_status: {self.parity_status!r}")
        if self.source_sql is not None:
            lines.append(f"  source_sql: {self.source_sql!r}")
        if self.source_dialect is not None:
            lines.append(f"  source_dialect: {self.source_dialect!r}")
        if self.source_document is not None:
            lines.append(f"  source_document: {self.source_document!r}")
        if self.compiled_sql is not None:
            lines.append(f"  compiled_sql: {self.compiled_sql!r}")
        if self.compile_error is not None:
            lines.append(f"  compile_error: {self.compile_error!r}")
        if self.dependencies:
            lines.append(f"  dependencies: {self.dependencies!r}")
        if self.dependents:
            lines.append(f"  dependents: {self.dependents!r}")
        if self.dataset_provenance is not None:
            lines.append(f"  dataset_provenance: {self.dataset_provenance!r}")
        if self.primary_key is not None:
            lines.append(f"  primary_key: {self.primary_key!r}")
        if self.granularity is not None:
            lines.append(f"  granularity: {self.granularity!r}")
        if self.format is not None:
            lines.append(f"  format: {self.format!r}")
        if self.required_prefix is not None:
            lines.append(f"  required_prefix: {self.required_prefix!r}")
        if self.from_dataset is not None:
            lines.append(f"  from_dataset: {self.from_dataset!r}")
        if self.to_dataset is not None:
            lines.append(f"  to_dataset: {self.to_dataset!r}")
        if self.from_fields is not None:
            lines.append(f"  from_fields: {self.from_fields!r}")
        if self.to_fields is not None:
            lines.append(f"  to_fields: {self.to_fields!r}")

        lines.append(f"  source_location: {self.source_location.file}:{self.source_location.line}")
        return "\n".join(lines)


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
                kind=ErrorKind.NOT_FOUND,
                message="Project is not loaded. Call project.load() first.",
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


def _search_match(
    query: str,
    semantic_id: str,
    name: str,
    description: str | None,
    business_definition: str | None,
    synonyms: tuple[str, ...],
    examples: tuple[str, ...],
) -> (
    tuple[
        Literal[
            "semantic_id",
            "name",
            "description",
            "business_definition",
            "synonyms",
            "examples",
        ],
        str,
    ]
    | None
):
    """Try to match query against fields in priority order.

    Returns (matched_field, matched_snippet) on first hit, or None.
    Priority: semantic_id > name > description > business_definition > synonyms > examples.
    """
    q = query.lower()

    # semantic_id
    if q in semantic_id.lower():
        idx = semantic_id.lower().index(q)
        start = max(0, idx - 10)
        end = min(len(semantic_id), idx + len(q) + 10)
        snippet = semantic_id[start:end]
        return ("semantic_id", snippet)

    # name
    if q in name.lower():
        idx = name.lower().index(q)
        start = max(0, idx - 10)
        end = min(len(name), idx + len(q) + 10)
        snippet = name[start:end]
        return ("name", snippet)

    # description
    if description and q in description.lower():
        idx = description.lower().index(q)
        start = max(0, idx - 10)
        end = min(len(description), idx + len(q) + 10)
        snippet = description[start:end]
        return ("description", snippet)

    # business_definition
    if business_definition and q in business_definition.lower():
        idx = business_definition.lower().index(q)
        start = max(0, idx - 10)
        end = min(len(business_definition), idx + len(q) + 10)
        snippet = business_definition[start:end]
        return ("business_definition", snippet)

    # synonyms
    for syn in synonyms:
        if q in syn.lower():
            return ("synonyms", syn)

    # examples
    for ex in examples:
        if q in ex.lower():
            idx = ex.lower().index(q)
            start = max(0, idx - 10)
            end = min(len(ex), idx + len(q) + 10)
            snippet = ex[start:end]
            return ("examples", snippet)

    return None


class SemanticProject:
    """Primary reader for a loaded semantic project.

    Usage::

        project = SemanticProject(root="/path/to/.marivo/semantic")
        result = project.load()
        if project.is_ready():
            models = project.list_models(display=False)
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._status: str = "unloaded"  # unloaded | ready | errored
        self._errors: tuple[SemanticError, ...] = ()
        self._warnings: tuple[StructuredWarning, ...] = ()
        self._load_result: LoadResult | None = None
        self._registry: Registry | None = None
        self._sidecar: Sidecar | None = None
        self._filtered_models: tuple[str, ...] = ()
        self._runtime_metadata: dict[str, DatasetRuntimeMetadata] = {}
        self._parity_results: dict[str, ParityResult] = {}
        self._raw_preview_evidence: tuple[str, ...] = ()
        self._bound_inspect_source: Callable[..., Any] | None = None
        self._bound_backend_factory: Callable[[str], Any] | None = None

    @property
    def root(self) -> Path:
        """Return the project root path."""
        return self._root

    def _record_raw_preview_evidence(self, *refs: str) -> None:
        self._raw_preview_evidence = tuple(dict.fromkeys((*self._raw_preview_evidence, *refs)))

    def _persisted_raw_preview_evidence(self) -> tuple[str, ...]:
        from marivo.semantic.ledger import LedgerStore

        store = LedgerStore(self._root)
        return tuple(record.ref for record in store.read_raw_previews())

    # -- lifecycle -----------------------------------------------------------

    def load(self, *, models: Sequence[str] | None = None) -> LoadResult:
        """Load the project from disk.

        When *models* is specified, only those model directories are loaded.
        Cross-model references to filtered-out models produce warnings instead
        of errors, so the registry remains usable.
        """
        if models is not None and len(models) > 0:
            self._filtered_models = tuple(models)
        else:
            self._filtered_models = ()
        result = load_project(
            self._root, models=self._filtered_models if self._filtered_models else None
        )
        self._load_result = result
        self._status = result.status
        self._errors = result.errors
        self._warnings = result.warnings
        self._registry = result.registry
        self._sidecar = result.sidecar
        if self._registry is not None:
            from marivo.semantic.auto_record import (
                auto_record_authoring_decisions,
                backfill_blast_radii,
            )

            auto_record_authoring_decisions(
                self._registry,
                self._root,
                blast_radius_of=self.blast_radius_of,
            )
            backfill_blast_radii(
                self._root,
                blast_radius_of=self.blast_radius_of,
            )
        return result

    def reload(self, *, models: Sequence[str] | None = None) -> LoadResult:
        """Re-load the project from disk.

        If *models* is not provided, re-applies the filter from the last load.
        """
        # Reset state before reload
        self._status = "unloaded"
        self._errors = ()
        self._warnings = ()
        self._registry = None
        self._sidecar = None
        self._runtime_metadata = {}
        self._parity_results = {}
        # Use provided models, or fall back to the previously stored filter
        effective_models = (
            models
            if models is not None
            else (self._filtered_models if self._filtered_models else None)
        )
        return self.load(models=effective_models)

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

    def list_models(self, display: bool = True) -> list[ModelSummary]:
        """Return all model summaries."""
        reg = _require_registry(self._registry, project=self)
        results: list[ModelSummary] = []
        for model_ir in reg.models.values():
            # Compute object counts for this model
            obj_counts: dict[str, int] = {}
            obj_counts["dataset"] = sum(
                1 for d in reg.datasets.values() if d.model == model_ir.name
            )
            obj_counts["field"] = sum(
                1
                for f in reg.fields.values()
                if f.dataset.startswith(f"{model_ir.name}.") and not f.is_time_field
            )
            obj_counts["time_field"] = sum(
                1
                for f in reg.fields.values()
                if f.dataset.startswith(f"{model_ir.name}.") and f.is_time_field
            )
            obj_counts["metric"] = sum(1 for m in reg.metrics.values() if m.model == model_ir.name)
            obj_counts["datasource"] = 0
            obj_counts["relationship"] = sum(
                1 for r in reg.relationships.values() if r.model == model_ir.name
            )
            results.append(
                ModelSummary(
                    name=model_ir.name,
                    description=model_ir.description,
                    default=model_ir.default,
                    object_counts=obj_counts,
                )
            )
        if display:
            _print_table(
                [
                    {
                        "name": item.name,
                        "default": item.default,
                        "datasets": item.object_counts.get("dataset", 0),
                        "fields": item.object_counts.get("field", 0),
                        "time_fields": item.object_counts.get("time_field", 0),
                        "metrics": item.object_counts.get("metric", 0),
                        "relationships": item.object_counts.get("relationship", 0),
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=(
                    "name",
                    "default",
                    "datasets",
                    "fields",
                    "time_fields",
                    "metrics",
                    "relationships",
                    "description",
                ),
                empty_message="No models found.",
            )
        return results

    def list_datasources(self, display: bool = True) -> list[DatasourceSummary]:
        """Return all datasource summaries."""
        reg = _require_registry(self._registry, project=self)
        results = [
            DatasourceSummary(
                semantic_id=ds_ir.semantic_id,
                name=ds_ir.name,
                backend_type=ds_ir.backend_type,
                description=ds_ir.description,
            )
            for ds_ir in reg.datasources.values()
        ]
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "name": item.name,
                        "backend_type": item.backend_type,
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=("semantic_id", "name", "backend_type", "description"),
                empty_message="No datasources found.",
            )
        return results

    def list_datasets(
        self, *, model: str | None = None, display: bool = True
    ) -> list[DatasetSummary]:
        """Return dataset summaries, optionally filtered by model name."""
        reg = _require_registry(self._registry, project=self)
        datasets = list(reg.datasets.values())
        if model is not None:
            datasets = [d for d in datasets if d.model == model]
        results = [
            DatasetSummary(
                semantic_id=d.semantic_id,
                model=d.model,
                name=d.name,
                datasource=d.datasource,
                description=d.description,
                dataset_provenance=(
                    self._runtime_metadata[d.semantic_id].dataset_provenance
                    if d.semantic_id in self._runtime_metadata
                    else None
                ),
            )
            for d in datasets
        ]
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "model": item.model,
                        "datasource": item.datasource,
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=("semantic_id", "model", "datasource", "description"),
                empty_message="No datasets found.",
            )
        return results

    def list_fields(
        self, *, dataset: str | None = None, display: bool = True
    ) -> list[FieldSummary]:
        """Return field summaries, optionally filtered by dataset.

        Fields are all @ms.field declarations that are not time fields.
        For time fields, use list_time_fields().
        """
        reg = _require_registry(self._registry, project=self)
        irs = [f for f in reg.fields.values() if not f.is_time_field]
        if dataset is not None:
            irs = [f for f in irs if f.dataset == dataset]
        results = [
            FieldSummary(
                semantic_id=f.semantic_id,
                model=f.model,
                dataset=f.dataset,
                name=f.name,
                description=f.description,
                is_time_field=f.is_time_field,
                data_type=f.data_type,
                granularity=f.granularity,
                is_default=f.is_default,
                format=f.format,
                timezone=f.timezone,
                required_prefix=f.required_prefix,
            )
            for f in irs
        ]
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "dataset": item.dataset,
                        "name": item.name,
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=("semantic_id", "dataset", "name", "description"),
                empty_message="No fields found.",
            )
        return results

    def list_time_fields(
        self, *, dataset: str | None = None, display: bool = True
    ) -> list[FieldSummary]:
        """Return time field summaries, optionally filtered by dataset."""
        reg = _require_registry(self._registry, project=self)
        irs = [f for f in reg.fields.values() if f.is_time_field]
        if dataset is not None:
            irs = [f for f in irs if f.dataset == dataset]
        results = [
            FieldSummary(
                semantic_id=f.semantic_id,
                model=f.model,
                dataset=f.dataset,
                name=f.name,
                description=f.description,
                is_time_field=f.is_time_field,
                data_type=f.data_type,
                granularity=f.granularity,
                is_default=f.is_default,
                format=f.format,
                timezone=f.timezone,
                required_prefix=f.required_prefix,
            )
            for f in irs
        ]
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "dataset": item.dataset,
                        "name": item.name,
                        "data_type": item.data_type,
                        "granularity": item.granularity,
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=(
                    "semantic_id",
                    "dataset",
                    "name",
                    "data_type",
                    "granularity",
                    "description",
                ),
                empty_message="No time fields found.",
            )
        return results

    def list_metrics(
        self,
        *,
        dataset: str | None = None,
        decomposition: Literal["sum", "ratio", "weighted_average"] | None = None,
        provenance_status: ParityStatus | None = None,
        display: bool = True,
    ) -> list[MetricSummary]:
        """Return metric summaries, optionally filtered."""
        reg = _require_registry(self._registry, project=self)
        metrics = list(reg.metrics.values())
        if dataset is not None:
            metrics = [m for m in metrics if dataset in m.datasets]
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
                model=m.model,
                name=m.name,
                description=m.description,
                decomposition_kind=m.decomposition.kind,
                is_derived=m.is_derived,
                parity_status=propagated_parity_status(self, m.semantic_id),
                python_symbol=m.python_symbol,
            )
            for m in metrics
        ]
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "model": item.model,
                        "name": item.name,
                        "decomposition_kind": item.decomposition_kind,
                        "is_derived": item.is_derived,
                        "parity_status": item.parity_status,
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=(
                    "semantic_id",
                    "model",
                    "name",
                    "decomposition_kind",
                    "is_derived",
                    "parity_status",
                    "description",
                ),
                empty_message="No metrics found.",
            )
        return results

    def list_relationships(
        self, *, model: str | None = None, display: bool = True
    ) -> list[RelationshipSummary]:
        """Return relationship summaries, optionally filtered by model."""
        reg = _require_registry(self._registry, project=self)
        rel_irs = list(reg.relationships.values())
        if model is not None:
            rel_irs = [r for r in rel_irs if r.model == model]
        results = [
            RelationshipSummary(
                semantic_id=r.semantic_id,
                model=r.model,
                name=r.name,
                from_dataset=r.from_dataset,
                to_dataset=r.to_dataset,
                from_fields=r.from_fields,
                to_fields=r.to_fields,
                description=r.description,
            )
            for r in rel_irs
        ]
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "model": item.model,
                        "from_dataset": item.from_dataset,
                        "to_dataset": item.to_dataset,
                        "description": item.description,
                    }
                    for item in results
                ],
                columns=("semantic_id", "model", "from_dataset", "to_dataset", "description"),
                empty_message="No relationships found.",
            )
        return results

    # -- single-object accessors -------------------------------------------

    def get_dataset(self, name: str) -> DatasetIR | None:
        """Return a dataset IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.datasets.get(name)

    def get_datasource(self, name: str) -> DatasourceIR | None:
        """Return a datasource IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.datasources.get(name)

    def get_field(self, name: str) -> FieldIR | None:
        """Return a field IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.fields.get(name)

    def get_metric(self, name: str) -> MetricIR | None:
        """Return a metric IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.metrics.get(name)

    def get_relationship(self, name: str) -> RelationshipIR | None:
        """Return a relationship IR by semantic_id, or None if not found."""
        reg = _require_registry(self._registry, project=self)
        return reg.relationships.get(name)

    # -- discovery ---------------------------------------------------------

    def search(
        self, query: str, *, kind: SymbolKind | None = None, display: bool = True
    ) -> list[SearchHit]:
        """Search across all IR objects by name, description, ai_context.

        Case-insensitive substring match.  Field priority:
        semantic_id > name > description > business_definition > synonyms > examples.
        Within priority, sort by semantic_id lexicographically.
        """
        reg = _require_registry(self._registry, project=self)
        results: list[SearchHit] = []

        # Search datasources
        if kind is None or kind == SymbolKind.DATASOURCE:
            for sid, ds_ir in reg.datasources.items():
                match = _search_match(
                    query,
                    sid,
                    ds_ir.name,
                    ds_ir.description,
                    ds_ir.ai_context.business_definition,
                    ds_ir.ai_context.synonyms,
                    ds_ir.ai_context.examples,
                )
                if match is not None:
                    results.append(
                        SearchHit(
                            semantic_id=sid,
                            kind=SymbolKind.DATASOURCE,
                            matched_field=match[0],
                            matched_snippet=match[1],
                        )
                    )

        # Search datasets
        if kind is None or kind == SymbolKind.DATASET:
            for sid, dt_ir in reg.datasets.items():
                match = _search_match(
                    query,
                    sid,
                    dt_ir.name,
                    dt_ir.description,
                    dt_ir.ai_context.business_definition,
                    dt_ir.ai_context.synonyms,
                    dt_ir.ai_context.examples,
                )
                if match is not None:
                    results.append(
                        SearchHit(
                            semantic_id=sid,
                            kind=SymbolKind.DATASET,
                            matched_field=match[0],
                            matched_snippet=match[1],
                        )
                    )

        # Search fields (non-time)
        if kind is None or kind == SymbolKind.FIELD:
            for sid, f_ir in reg.fields.items():
                if f_ir.is_time_field:
                    continue
                match = _search_match(
                    query,
                    sid,
                    f_ir.name,
                    f_ir.description,
                    f_ir.ai_context.business_definition,
                    f_ir.ai_context.synonyms,
                    f_ir.ai_context.examples,
                )
                if match is not None:
                    results.append(
                        SearchHit(
                            semantic_id=sid,
                            kind=SymbolKind.FIELD,
                            matched_field=match[0],
                            matched_snippet=match[1],
                        )
                    )

        # Search time fields
        if kind is None or kind == SymbolKind.TIME_FIELD:
            for sid, f_ir in reg.fields.items():
                if not f_ir.is_time_field:
                    continue
                match = _search_match(
                    query,
                    sid,
                    f_ir.name,
                    f_ir.description,
                    f_ir.ai_context.business_definition,
                    f_ir.ai_context.synonyms,
                    f_ir.ai_context.examples,
                )
                if match is not None:
                    results.append(
                        SearchHit(
                            semantic_id=sid,
                            kind=SymbolKind.TIME_FIELD,
                            matched_field=match[0],
                            matched_snippet=match[1],
                        )
                    )

        # Search metrics
        if kind is None or kind == SymbolKind.METRIC:
            for sid, m_ir in reg.metrics.items():
                match = _search_match(
                    query,
                    sid,
                    m_ir.name,
                    m_ir.description,
                    m_ir.ai_context.business_definition,
                    m_ir.ai_context.synonyms,
                    m_ir.ai_context.examples,
                )
                if match is not None:
                    results.append(
                        SearchHit(
                            semantic_id=sid,
                            kind=SymbolKind.METRIC,
                            matched_field=match[0],
                            matched_snippet=match[1],
                        )
                    )

        # Sort: by matched_field priority, then by semantic_id lexicographically
        _field_priority = {
            "semantic_id": 0,
            "name": 1,
            "description": 2,
            "business_definition": 3,
            "synonyms": 4,
            "examples": 5,
        }
        results.sort(key=lambda h: (_field_priority.get(h.matched_field, 99), h.semantic_id))
        if display:
            _print_table(
                [
                    {
                        "semantic_id": item.semantic_id,
                        "kind": item.kind,
                        "matched_field": item.matched_field,
                        "matched_snippet": item.matched_snippet,
                    }
                    for item in results
                ],
                columns=("semantic_id", "kind", "matched_field", "matched_snippet"),
                empty_message="No search results found.",
            )
        return results

    # -- dependency graph ---------------------------------------------------

    def dependencies(self, name: str) -> DependencyNode:
        """Return the dependency tree for a named object.

        For metrics: walks dataset refs, component metric refs, and
        their transitive dependencies (datasources).
        For datasets: includes the datasource.
        For fields: includes the parent dataset.
        For relationships: includes from_dataset and to_dataset.
        """
        reg = _require_registry(self._registry, project=self)

        # Check if it is a metric
        metric_ir = reg.metrics.get(name)
        if metric_ir is not None:
            return self._build_deps_metric(name, metric_ir, reg)

        # Check if it is a dataset
        dataset_ir = reg.datasets.get(name)
        if dataset_ir is not None:
            return self._build_deps_dataset(name, dataset_ir, reg)

        # Check if it is a field
        field_ir = reg.fields.get(name)
        if field_ir is not None:
            kind = SymbolKind.TIME_FIELD if field_ir.is_time_field else SymbolKind.FIELD
            ds_child: tuple[DependencyNode, ...] = ()
            if reg.datasets.get(field_ir.dataset) is not None:
                ds_child = (
                    DependencyNode(
                        semantic_id=field_ir.dataset,
                        kind=SymbolKind.DATASET,
                        children=(),
                    ),
                )
            return DependencyNode(semantic_id=name, kind=kind, children=ds_child)

        # Check if it is a relationship
        rel_ir = reg.relationships.get(name)
        if rel_ir is not None:
            return self._deps_relationship(name, rel_ir, reg)

        # Not found
        _raise(
            ErrorKind.NOT_FOUND,
            f"Object {name!r} not found in registry.",
            cls=SemanticRuntimeError,
            refs=(name,),
        )

    def _build_deps_metric(
        self,
        name: str,
        metric_ir: MetricIR,
        reg: Registry,
    ) -> DependencyNode:
        """Build dependency tree for a metric."""
        children: list[DependencyNode] = []
        visited: set[str] = set()

        # Dataset dependencies
        for ds_ref in metric_ir.datasets:
            ds_ir = reg.datasets.get(ds_ref)
            if ds_ir is not None and ds_ref not in visited:
                children.append(self._build_deps_dataset(ds_ref, ds_ir, reg, _visited=visited))
                visited.add(ds_ref)

        # Component metric dependencies (for derived metrics)
        for _comp_key, comp_ref in metric_ir.decomposition.components.items():
            comp_metric = reg.metrics.get(comp_ref)
            if comp_metric is not None and comp_ref not in visited:
                children.append(self._build_deps_metric(comp_ref, comp_metric, reg))
                visited.add(comp_ref)

        return DependencyNode(
            semantic_id=name,
            kind=SymbolKind.METRIC,
            children=tuple(children),
        )

    def _build_deps_dataset(
        self,
        name: str,
        dataset_ir: DatasetIR,
        reg: Registry,
        *,
        _visited: set[str] | None = None,
    ) -> DependencyNode:
        """Build dependency tree for a dataset."""
        visited = _visited if _visited is not None else set()
        if name in visited:
            return DependencyNode(semantic_id=name, kind=SymbolKind.DATASET, children=())
        visited.add(name)

        children: list[DependencyNode] = []
        ds_ir = reg.datasources.get(dataset_ir.datasource)
        if ds_ir is not None and dataset_ir.datasource not in visited:
            children.append(
                DependencyNode(
                    semantic_id=dataset_ir.datasource,
                    kind=SymbolKind.DATASOURCE,
                    children=(),
                )
            )
            visited.add(dataset_ir.datasource)

        return DependencyNode(
            semantic_id=name,
            kind=SymbolKind.DATASET,
            children=tuple(children),
        )

    def _deps_relationship(
        self,
        name: str,
        rel_ir: RelationshipIR,
        reg: Registry,
    ) -> DependencyNode:
        """Build dependency tree for a relationship."""
        children: list[DependencyNode] = []
        for ds_ref in (rel_ir.from_dataset, rel_ir.to_dataset):
            if reg.datasets.get(ds_ref) is not None:
                children.append(
                    DependencyNode(semantic_id=ds_ref, kind=SymbolKind.DATASET, children=())
                )
        return DependencyNode(
            semantic_id=name,
            kind=SymbolKind.RELATIONSHIP,
            children=tuple(children),
        )

    def dependents(self, name: str) -> DependencyNode:
        """Return objects that depend on the named object.

        Reverse of ``dependencies()``.
        """
        reg = _require_registry(self._registry, project=self)

        # For a dataset: find metrics and fields that depend on it
        if name in reg.datasets:
            return self._dependents_dataset(name, reg)

        # For a field/time_field: find the parent dataset
        if name in reg.fields:
            return self._dependents_field(name, reg)

        # For a metric: find derived metrics that reference it as a component
        if name in reg.metrics:
            return self._dependents_metric(name, reg)

        # For a relationship: nothing depends on a relationship
        if name in reg.relationships:
            return DependencyNode(semantic_id=name, kind=SymbolKind.RELATIONSHIP, children=())

        # Not found
        _raise(
            ErrorKind.NOT_FOUND,
            f"Object {name!r} not found in registry.",
            cls=SemanticRuntimeError,
            refs=(name,),
        )

    def _dependents_dataset(self, name: str, reg: Registry) -> DependencyNode:
        """Build dependents tree for a dataset."""
        ds_children: list[DependencyNode] = []
        for m_id, m_ir in reg.metrics.items():
            if name in m_ir.datasets:
                ds_children.append(
                    DependencyNode(
                        semantic_id=m_id,
                        kind=SymbolKind.METRIC,
                        children=(),
                    )
                )
        for f_id, f_ir in reg.fields.items():
            if f_ir.dataset == name:
                kind = SymbolKind.TIME_FIELD if f_ir.is_time_field else SymbolKind.FIELD
                ds_children.append(
                    DependencyNode(
                        semantic_id=f_id,
                        kind=kind,
                        children=(),
                    )
                )
        return DependencyNode(
            semantic_id=name,
            kind=SymbolKind.DATASET,
            children=tuple(ds_children),
        )

    def _dependents_field(self, name: str, reg: Registry) -> DependencyNode:
        """Build dependents tree for a field/time_field."""
        f_ir = reg.fields[name]
        kind = SymbolKind.TIME_FIELD if f_ir.is_time_field else SymbolKind.FIELD
        return DependencyNode(semantic_id=name, kind=kind, children=())

    def _dependents_metric(self, name: str, reg: Registry) -> DependencyNode:
        """Build dependents tree for a metric."""
        metric_children: list[DependencyNode] = []
        for m_id, m_ir in reg.metrics.items():
            if m_id == name:
                continue
            for comp_ref in m_ir.decomposition.components.values():
                if comp_ref == name:
                    metric_children.append(
                        DependencyNode(
                            semantic_id=m_id,
                            kind=SymbolKind.METRIC,
                            children=(),
                        )
                    )
        return DependencyNode(
            semantic_id=name,
            kind=SymbolKind.METRIC,
            children=tuple(metric_children),
        )

    def _flatten_ids(self, node: DependencyNode) -> set[str]:
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
                node = self.dependents(ref)
            except SemanticRuntimeError:
                continue
            seen |= self._flatten_ids(node)
        return len(seen - set(refs))

    # -- describe -----------------------------------------------------------

    def describe(
        self,
        name: str,
        *,
        kind: SymbolKind | None = None,
        compile_sql: bool = False,
        backend_factory: Callable[..., Any] | None = None,
    ) -> Description:
        """Describe a semantic object by name.

        Returns a ``Description`` frozen dataclass.  When ``compile_sql``
        is True and the object is a metric, the ``compiled_sql`` field is
        populated.  Requires ``backend_factory`` when ``compile_sql=True``.

        When ``kind`` is given, the search is narrowed to the matching
        collection (e.g., ``kind=SymbolKind.METRIC``).  This resolves
        ambiguity when a name appears in multiple kind-scoped dicts.
        """
        reg = _require_registry(self._registry, project=self)
        obj = self._find_ir(name, reg, kind=kind)
        if obj is None:
            not_found_kind = (
                self._KIND_TO_NOT_FOUND.get(kind, ErrorKind.NOT_FOUND)
                if kind is not None
                else ErrorKind.NOT_FOUND
            )
            _raise(
                not_found_kind,
                f"Object {name!r} not found.",
                cls=SemanticRuntimeError,
                refs=(name,),
            )

        kind = self._ir_kind(obj)
        compiled_sql: str | None = None
        compile_error: dict[str, Any] | None = None

        factory = backend_factory if backend_factory is not None else self._backend_factory
        if compile_sql and isinstance(obj, MetricIR) and factory is not None:
            try:
                compiled_sql = self.compile_sql(obj.semantic_id, backend_factory=factory)
            except SemanticRuntimeError as exc:
                compile_error = {
                    "kind": exc.kind,
                    "message": exc.message,
                    "refs": list(exc.semantic_refs),
                }

        # Compute dependencies and dependents from tree API
        dep_names = sorted(self._flatten_ids(self.dependencies(name)))
        dep_of_names = sorted(self._flatten_ids(self.dependents(name)))

        # Dataset provenance
        ds_provenance: DatasetProvenance | None = None
        if isinstance(obj, DatasetIR):
            meta = self._runtime_metadata.get(obj.semantic_id)
            if meta is not None:
                ds_provenance = meta.dataset_provenance

        # Parity status (metric only)
        parity_status: ParityStatus | None = None
        if isinstance(obj, MetricIR):
            parity_status = propagated_parity_status(self, obj.semantic_id)

        desc = Description(
            semantic_id=obj.semantic_id,
            kind=kind,
            model=obj.model if hasattr(obj, "model") else "",
            name=obj.name,
            python_symbol=getattr(obj, "python_symbol", ""),
            description=obj.description,
            business_definition=obj.ai_context.business_definition,
            guardrails=obj.ai_context.guardrails,
            synonyms=obj.ai_context.synonyms,
            examples=obj.ai_context.examples,
            parity_status=parity_status,
            source_sql=obj.provenance.source_sql if isinstance(obj, MetricIR) else None,
            source_dialect=obj.provenance.source_dialect if isinstance(obj, MetricIR) else None,
            source_document=obj.provenance.source_document if isinstance(obj, MetricIR) else None,
            compiled_sql=compiled_sql,
            compile_error=compile_error,
            dependencies=tuple(dep_names),
            dependents=tuple(dep_of_names),
            source_location=obj.location,
            dataset_provenance=ds_provenance,
            primary_key=obj.primary_key if isinstance(obj, DatasetIR) else None,
            granularity=obj.granularity if isinstance(obj, FieldIR) else None,
            required_prefix=obj.required_prefix if isinstance(obj, FieldIR) else None,
            format=obj.format if isinstance(obj, FieldIR) else None,
            from_dataset=obj.from_dataset if isinstance(obj, RelationshipIR) else None,
            to_dataset=obj.to_dataset if isinstance(obj, RelationshipIR) else None,
            from_fields=obj.from_fields if isinstance(obj, RelationshipIR) else None,
            to_fields=obj.to_fields if isinstance(obj, RelationshipIR) else None,
        )

        return desc

    # -- compile_sql --------------------------------------------------------

    def compile_sql(
        self,
        metric: str,
        *,
        backend_factory: Callable[[str], Any] | None = None,
    ) -> str:
        """Compile a metric expression to SQL.

        Materializes the metric ibis expression, then compiles it
        using ``ibis.to_sql()``.

        Raises SemanticRuntimeError (COMPILE_ERROR) if the metric
        is not found or if ibis compilation fails.
        """
        factory = self._resolve_backend_factory(backend_factory)
        reg = _require_registry(self._registry, project=self)
        metric_ir = reg.metrics.get(metric)
        if metric_ir is None:
            _raise(
                ErrorKind.COMPILE_ERROR,
                f"Metric {metric!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(metric,),
            )

        try:
            mat = Materializer(self, factory)
            expr = mat.metric(metric)
            return str(ibis.to_sql(expr))
        except SemanticRuntimeError:
            raise
        except Exception as exc:
            _raise(
                ErrorKind.COMPILE_ERROR,
                f"Failed to compile metric {metric!r}: {exc}",
                cls=SemanticRuntimeError,
                refs=(metric,),
            )

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
        return mat.dataset(name)

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
        return mat.field(name)

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

    def raw_preview_evidence(self) -> tuple[str, ...]:
        """Return raw preview evidence collected for this project."""
        return tuple(
            dict.fromkeys((*self._persisted_raw_preview_evidence(), *self._raw_preview_evidence))
        )

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
        LedgerStore(self._root).write_raw_preview(
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
        self._record_raw_preview_evidence(preview.ref)
        return preview

    def record_failed_preview(
        self,
        *,
        datasource: str,
        table: str,
        database: str | tuple[str, ...] | None = None,
    ) -> None:
        """Record that a raw preview attempt failed for this datasource.table."""
        from datetime import UTC, datetime

        from marivo.semantic.ledger import LedgerStore, RawPreviewEvidence

        ref = _raw_preview_ref(datasource, table, database)
        LedgerStore(self._root).write_raw_preview(
            RawPreviewEvidence(
                ref=ref,
                datasource=datasource,
                table=table,
                database=database,
                columns=(),
                types={},
                requested_limit=0,
                returned_row_count=0,
                sample_policy={},
                collected_at=datetime.now(UTC).isoformat(),
                status="failed",
            )
        )

    def record_primary_key_sample(self, dataset: str) -> None:
        """Record that primary key uniqueness was sampled for a dataset."""
        from marivo.semantic.ledger import LedgerStore

        LedgerStore(self._root).write_primary_key_sample(dataset)

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
                ErrorKind.FIELD_NOT_FOUND,
                f"Field {name!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(name,),
            )

        mat = Materializer(self, factory)
        parent_table = mat.dataset(field_ir.dataset)
        field_value = mat.field(name)
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
        )

    # -- readiness ----------------------------------------------------------

    def _auto_collect_evidence(self) -> _ReadinessEvidence:
        from marivo.semantic.evidence_store import EvidenceStore
        from marivo.semantic.ledger import LedgerStore
        from marivo.semantic.readiness import (
            _dataset_raw_preview_refs,
            _default_checked_refs,
            _derive_raw_sql_required_refs,
            _object_maps,
            _semantic_preview_refs,
        )

        store = LedgerStore(self._root)
        evidence_store = EvidenceStore(self._root)

        # Raw previews: success vs failed
        raw_preview_records = store.read_raw_previews()
        raw_previews = tuple(r.ref for r in raw_preview_records if r.status == "success")
        failed_raw_previews = tuple(r.ref for r in raw_preview_records if r.status == "failed")

        # Merge with in-memory evidence
        raw_previews = tuple(dict.fromkeys((*raw_previews, *self._raw_preview_evidence)))

        # Required previews: derived from IR
        if self.is_ready():
            kinds, objects = _object_maps(self)
            checked_refs = _default_checked_refs(kinds)
            required_raw_previews = _dataset_raw_preview_refs(checked_refs, objects, kinds)
            required_semantic_previews = _semantic_preview_refs(checked_refs, kinds)
            raw_sql_required_refs = _derive_raw_sql_required_refs(kinds, objects)
        else:
            required_raw_previews = ()
            required_semantic_previews = ()
            raw_sql_required_refs = ()

        # Knowledge documents from evidence store
        kd_refs = evidence_store.list_authoring_by_kind("knowledge_document")
        knowledge_documents = tuple(r.id for r in kd_refs)

        # User confirmations from evidence store
        uc_subject_refs = evidence_store.list_authoring_subject_refs_by_kind("user_confirmation")
        user_confirmations = tuple(dict.fromkeys(s[0] for s in uc_subject_refs if s))

        # Confirmed relationships from evidence store
        rc_subject_refs = evidence_store.list_authoring_subject_refs_by_kind(
            "relationship_confirmation"
        )
        confirmed_relationships = tuple(dict.fromkeys(s[0] for s in rc_subject_refs if s))

        # Primary keys sampled
        primary_keys_sampled = store.read_primary_key_samples()

        return _ReadinessEvidence(
            raw_previews=raw_previews,
            failed_raw_previews=failed_raw_previews,
            required_raw_previews=required_raw_previews,
            required_semantic_previews=required_semantic_previews,
            knowledge_documents=knowledge_documents,
            user_confirmations=user_confirmations,
            confirmed_relationships=confirmed_relationships,
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
    ) -> ReadinessReport:
        """Return a structured semantic readiness report.

        Evidence is auto-loaded from the project's ledger and evidence store.
        Use ``refs`` to scope which semantic objects to check; by default all
        loaded objects are checked.
        """
        evidence = self._auto_collect_evidence()
        # Intentionally not using _resolve_backend_factory: readiness can
        # produce a useful (if degraded) report without a backend — semantic
        # previews are skipped but other checks still run.
        factory = self._backend_factory
        return build_readiness_report(
            self,
            evidence,
            backend_factory=factory,
            refs=refs,
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

    def _evidence_store(self) -> EvidenceStore:
        return EvidenceStore(self._root)

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
            store=self._evidence_store(),
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

        return collect_column_evidence(
            datasource=datasource,
            source=source,
            columns=columns,
            inspect_source=fn,
            backend_factory=factory,
            sample_policy=sample_policy,
            store=self._evidence_store(),
        )

    def list_evidence(
        self,
        *,
        datasource: str | None = None,
        source: DatasetSource | None = None,
        subject_refs: Iterable[str] | None = None,
    ) -> tuple[AuthoringEvidenceRef, ...]:
        """Retrieve evidence refs by source identity or by subject refs."""
        return self._evidence_store().list_evidence(
            datasource=datasource,
            source=source,
            subject_refs=tuple(subject_refs) if subject_refs is not None else None,
        )

    def get_evidence_pack(self, evidence_id: str) -> SourceEvidencePack | ColumnEvidence | None:
        """Return a persisted source/column evidence pack by id."""
        return self._evidence_store().read_pack(evidence_id)

    def record_authoring_evidence(self, evidence: AuthoringEvidenceInput) -> AuthoringEvidenceRef:
        """Record non-sample evidence (source SQL, knowledge docs, owner notes,
        user confirmations) and return its EvidenceRef."""
        return self._evidence_store().write_authoring_evidence(evidence)

    def check_authoring_inputs(
        self,
        *,
        object_kind: str,
        subject_ref: str,
        datasource: str,
        source: DatasetSource,
        columns: Sequence[str] = (),
        semantic_refs: Sequence[str] = (),
        evidence_refs: Sequence[str] = (),
        ai_context: Any | None = None,
    ) -> AssessmentResult:
        """Cheap pre-authoring guardrail for refs, columns, and evidence."""
        from marivo.semantic.authoring_check import check_authoring_inputs as _check

        return _check(
            store=self._evidence_store(),
            object_kind=object_kind,  # type: ignore[arg-type]
            subject_ref=subject_ref,
            datasource=datasource,
            source=source,
            columns=columns,
            semantic_refs=semantic_refs,
            evidence_refs=evidence_refs,
            ai_context=ai_context,
        )

    def inspect_authored_object(self, ref: str) -> AssessmentResult:
        """Cheap post-reload inspection of a loaded authored object.

        Backend-free: inspects the registry and evidence ledger only. It never
        materializes tables, previews, runs parity, or scans relationships.
        """
        from marivo.semantic.authoring_check import inspect_authored_object as _inspect
        from marivo.semantic.ledger import LedgerStore

        reg = _require_registry(self._registry, project=self)
        return _inspect(registry=reg, ledger_store=LedgerStore(self._root), ref=ref)

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _find_ir(
        name: str,
        reg: Registry,
        kind: SymbolKind | None = None,
    ) -> DatasetIR | DatasourceIR | FieldIR | MetricIR | RelationshipIR | None:
        """Look up an IR object by semantic_id.

        When kind is given, search only the matching collection.
        When kind is None, search all collections. If exactly one match is
        found, return it. If zero matches, return None. If multiple matches
        across different kinds, raise AMBIGUOUS_REFERENCE.
        """
        if kind is not None:
            collection_map: dict[SymbolKind, dict[str, Any]] = {
                SymbolKind.DATASOURCE: reg.datasources,
                SymbolKind.DATASET: reg.datasets,
                SymbolKind.FIELD: reg.fields,
                SymbolKind.TIME_FIELD: reg.fields,
                SymbolKind.METRIC: reg.metrics,
                SymbolKind.RELATIONSHIP: reg.relationships,
            }
            collection = collection_map.get(kind)
            if collection is not None and name in collection:
                return cast(
                    "DatasetIR | DatasourceIR | FieldIR | MetricIR | RelationshipIR",
                    collection[name],
                )
            return None
        # kind=None: collect matches across all collections
        matches: list[tuple[SymbolKind, Any]] = []
        search_order: list[tuple[SymbolKind, dict[str, Any]]] = [
            (SymbolKind.DATASOURCE, reg.datasources),
            (SymbolKind.DATASET, reg.datasets),
            (SymbolKind.FIELD, reg.fields),
            (SymbolKind.METRIC, reg.metrics),
            (SymbolKind.RELATIONSHIP, reg.relationships),
        ]
        for sym_kind, collection in search_order:
            if name in collection:
                obj = collection[name]
                actual_kind = (
                    SymbolKind.TIME_FIELD
                    if isinstance(obj, FieldIR) and obj.is_time_field
                    else sym_kind
                )
                matches.append((actual_kind, obj))
        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return cast(
                "DatasetIR | DatasourceIR | FieldIR | MetricIR | RelationshipIR", matches[0][1]
            )
        candidates = [(mk, obj.semantic_id) for mk, obj in matches]
        _raise(
            ErrorKind.AMBIGUOUS_REFERENCE,
            f"Name {name!r} matches multiple object kinds.",
            cls=SemanticRuntimeError,
            refs=(name,),
            details={"candidates": [(str(mk), sid) for mk, sid in candidates]},
            constraint_id=ConstraintId.AMBIGUOUS_REFERENCE,
        )

    @staticmethod
    def _ir_kind(obj: DatasetIR | DatasourceIR | FieldIR | MetricIR | RelationshipIR) -> SymbolKind:
        """Return the SymbolKind for an IR object."""
        if isinstance(obj, DatasourceIR):
            return SymbolKind.DATASOURCE
        if isinstance(obj, DatasetIR):
            return SymbolKind.DATASET
        if isinstance(obj, FieldIR):
            return SymbolKind.TIME_FIELD if obj.is_time_field else SymbolKind.FIELD
        if isinstance(obj, MetricIR):
            return SymbolKind.METRIC
        if isinstance(obj, RelationshipIR):
            return SymbolKind.RELATIONSHIP
        return SymbolKind.MODEL  # fallback, should not happen

    _KIND_TO_NOT_FOUND: ClassVar[dict[SymbolKind, ErrorKind]] = {
        SymbolKind.DATASOURCE: ErrorKind.NOT_FOUND,
        SymbolKind.DATASET: ErrorKind.DATASET_NOT_FOUND,
        SymbolKind.FIELD: ErrorKind.FIELD_NOT_FOUND,
        SymbolKind.TIME_FIELD: ErrorKind.FIELD_NOT_FOUND,
        SymbolKind.METRIC: ErrorKind.METRIC_NOT_FOUND,
        SymbolKind.RELATIONSHIP: ErrorKind.NOT_FOUND,
        SymbolKind.MODEL: ErrorKind.NOT_FOUND,
    }
