"""SemanticProject reader API for marivo.semantic v1.1.

All read-only access to the loaded semantic model goes through
``SemanticProject`` methods.  Free-function readers are removed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import ibis
import ibis.expr.types as ir

from marivo.datasource.ir import DatasourceIR, DatasourceSourceLocation
from marivo.preview import (
    PREVIEW_DEFAULT_LIMIT,
    PreviewResult,
    PreviewSamplePolicy,
    preview_ibis_table,
    preview_ibis_value,
    validate_preview_limit,
)
from marivo.semantic.errors import (
    ErrorKind,
    SemanticError,
    SemanticRuntimeError,
    StructuredWarning,
    _raise,
)
from marivo.semantic.ir import (
    DatasetIR,
    DatasetProvenance,
    DatasetSourceIR,
    FieldIR,
    MetricIR,
    ParityStatus,
    RelationshipIR,
    SourceLocation,
    SymbolKind,
    source_from_dict,
)
from marivo.semantic.loader import LoadResult, load_project
from marivo.semantic.materializer import DatasetRuntimeMetadata, Materializer
from marivo.semantic.parity import ParityResult, parity_check, propagated_parity_status
from marivo.semantic.proposal import ProposalResult, ResidualColumn
from marivo.semantic.readiness import (
    EvidenceSummary,
    ParitySummary,
    PreviewSummary,
    ReadinessIssue,
    ReadinessReport,
    build_readiness_report,
)
from marivo.semantic.richness import (
    DemandSignal,
    RichnessReport,
    build_richness_report,
)
from marivo.semantic.validator import Registry, Sidecar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from marivo.analysis.datasources.metadata import TableMetadata
    from marivo.semantic.classifier import Candidate, DecisionKind, Enrichment, OpenQuestion
    from marivo.semantic.ledger import DecisionRecord, RejectedCandidate

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
                kind=ErrorKind.METRIC_NOT_FOUND,
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
            models = project.list_models()
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._status: str = "unloaded"  # unloaded | ready | errored
        self._errors: tuple[SemanticError, ...] = ()
        self._warnings: tuple[StructuredWarning, ...] = ()
        self._load_result: LoadResult | None = None
        self._registry: Registry | None = None
        self._sidecar: Sidecar | None = None
        self._runtime_metadata: dict[str, DatasetRuntimeMetadata] = {}
        self._parity_results: dict[str, ParityResult] = {}
        self._raw_preview_evidence: tuple[str, ...] = ()

    @property
    def root(self) -> str:
        """Return the project root path as a string."""
        return str(self._root)

    @property
    def root_path(self) -> Path:
        """Return the project root path as a Path object."""
        return self._root

    def _record_raw_preview_evidence(self, *refs: str) -> None:
        self._raw_preview_evidence = tuple(dict.fromkeys((*self._raw_preview_evidence, *refs)))

    def _persisted_raw_preview_evidence(self) -> tuple[str, ...]:
        from marivo.semantic.ledger import LedgerStore

        store = LedgerStore(self._root)
        return tuple(record.ref for record in store.read_raw_previews())

    def answer(
        self,
        question: OpenQuestion,
        answer: object,
        *,
        evidence_fingerprint: str = "",
        rationale: str | None = None,
    ) -> None:
        """Record the user's answer to an OpenQuestion in the evidence ledger.

        The append-only confirmation log preserves the user's answer. Each affected
        object also receives a minimal DecisionRecord so readiness can re-derive the
        answered state after reload. rationale is accepted for caller ergonomics;
        it is not persisted in this phase.
        """
        from datetime import UTC, datetime

        from marivo.semantic.ledger import ConfirmationRecord, DecisionRecord, LedgerStore

        if answer is None:
            raise ValueError("answer must not be None")

        decided_at = datetime.now(UTC).isoformat()
        store = LedgerStore(self._root)
        store.append_confirmation(
            ConfirmationRecord(
                ts=decided_at,
                question_id=question.id,
                decision_kind=question.decision_kind,
                subject_refs=question.subject_refs,
                answer=answer,
                evidence_fingerprint=evidence_fingerprint,
            )
        )
        for semantic_id in question.subject_refs:
            self.record_decision(
                semantic_id,
                DecisionRecord(
                    decision_kind=question.decision_kind,
                    chosen=answer,
                    agreement_confidence="high",
                    qualifying_sources=("user_confirmation",),
                    materiality=question.materiality,
                    blast_radius=question.blast_radius,
                    evidence_fingerprint=evidence_fingerprint,
                    question_id=question.id,
                    decided_at=decided_at,
                ),
            )

    def record_decision(
        self,
        semantic_id: str,
        record: DecisionRecord,
        *,
        authored_at: str | None = None,
        rejected: tuple[RejectedCandidate, ...] = (),
    ) -> None:
        """Append a decision (and optional rejected candidates) to the object's
        ledger entry, preserving prior decisions."""
        from datetime import UTC, datetime

        from marivo.semantic.ledger import LedgerStore, ObjectEvidence

        store = LedgerStore(self._root)
        existing = store.read_object(semantic_id)
        existing_decisions = existing.decisions if existing else ()
        if record.question_id is None:
            decisions = (*existing_decisions, record)
        else:
            replacement_key = (record.question_id, record.decision_kind)
            decisions = (
                *(
                    decision
                    for decision in existing_decisions
                    if (decision.question_id, decision.decision_kind) != replacement_key
                ),
                record,
            )
        rejected_all = (existing.rejected_candidates if existing else ()) + tuple(rejected)
        store.write_object(
            ObjectEvidence(
                semantic_id=semantic_id,
                authored_at=(
                    existing.authored_at
                    if existing
                    else (authored_at or datetime.now(UTC).isoformat())
                ),
                decisions=decisions,
                rejected_candidates=rejected_all,
            )
        )

    # -- lifecycle -----------------------------------------------------------

    def load(self) -> LoadResult:
        """Load the project from disk."""
        result = load_project(self._root)
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

    def reload(self) -> LoadResult:
        """Re-load the project from disk."""
        # Reset state before reload
        self._status = "unloaded"
        self._errors = ()
        self._warnings = ()
        self._registry = None
        self._sidecar = None
        self._runtime_metadata = {}
        self._parity_results = {}
        return self.load()

    def is_ready(self) -> bool:
        """Return True if the project is in the ready state."""
        return self._status == "ready"

    def errors(self) -> tuple[SemanticError, ...]:
        """Return errors from the last load attempt."""
        return self._errors

    def warnings(self) -> tuple[StructuredWarning, ...]:
        """Return warnings from the last load attempt."""
        return self._warnings

    def registry(self) -> Registry | None:
        """Return the Registry from the last successful load, or None."""
        return self._registry

    def sidecar(self) -> Sidecar | None:
        """Return the Sidecar from the last successful load, or None."""
        return self._sidecar

    def runtime_metadata(self, dataset_semantic_id: str) -> DatasetRuntimeMetadata | None:
        """Return runtime metadata for a dataset, or None if not materialized yet."""
        return self._runtime_metadata.get(dataset_semantic_id)

    # -- listings -----------------------------------------------------------

    def list_models(self) -> list[ModelSummary]:
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
        return results

    def list_datasources(self) -> list[DatasourceSummary]:
        """Return all datasource summaries."""
        reg = _require_registry(self._registry, project=self)
        return [
            DatasourceSummary(
                semantic_id=ds_ir.semantic_id,
                name=ds_ir.name,
                backend_type=ds_ir.backend_type,
                description=ds_ir.description,
            )
            for ds_ir in reg.datasources.values()
        ]

    def list_datasets(self, *, model: str | None = None) -> list[DatasetSummary]:
        """Return dataset summaries, optionally filtered by model name."""
        reg = _require_registry(self._registry, project=self)
        datasets = list(reg.datasets.values())
        if model is not None:
            datasets = [d for d in datasets if d.model == model]
        return [
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

    def list_fields(self, *, dataset: str | None = None) -> list[FieldIR]:
        """Return field IR objects (non-time fields), optionally filtered by dataset."""
        reg = _require_registry(self._registry, project=self)
        fields = [f for f in reg.fields.values() if not f.is_time_field]
        if dataset is not None:
            fields = [f for f in fields if f.dataset == dataset]
        return fields

    def list_time_fields(self, *, dataset: str | None = None) -> list[FieldIR]:
        """Return time field IR objects, optionally filtered by dataset."""
        reg = _require_registry(self._registry, project=self)
        fields = [f for f in reg.fields.values() if f.is_time_field]
        if dataset is not None:
            fields = [f for f in fields if f.dataset == dataset]
        return fields

    def list_metrics(
        self,
        *,
        dataset: str | None = None,
        decomposition: Literal["sum", "ratio", "weighted_average"] | None = None,
        provenance_status: ParityStatus | None = None,
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
        return [
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

    def list_relationships(self, *, model: str | None = None) -> list[RelationshipIR]:
        """Return relationship IR objects, optionally filtered by model."""
        reg = _require_registry(self._registry, project=self)
        rels = list(reg.relationships.values())
        if model is not None:
            rels = [r for r in rels if r.model == model]
        return rels

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

    # -- discovery ---------------------------------------------------------

    def search(self, query: str, *, kind: SymbolKind | None = None) -> list[SearchHit]:
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
        return results

    # -- dependency graph ---------------------------------------------------

    def dependencies(self, name: str) -> DependencyNode:
        """Return the dependency tree for a named object.

        For metrics: walks dataset refs, component metric refs, and
        the fields that belong to each referenced dataset.
        For datasets: includes all fields and time fields.
        For fields: just the field itself.
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
            return DependencyNode(semantic_id=name, kind=kind, children=())

        # Not found
        _raise(
            ErrorKind.METRIC_NOT_FOUND,
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
        # Fields belonging to this dataset
        for f_id, f_ir in reg.fields.items():
            if f_ir.dataset == name and f_id not in visited:
                kind = SymbolKind.TIME_FIELD if f_ir.is_time_field else SymbolKind.FIELD
                children.append(DependencyNode(semantic_id=f_id, kind=kind, children=()))
                visited.add(f_id)

        return DependencyNode(
            semantic_id=name,
            kind=SymbolKind.DATASET,
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

        # Not found
        _raise(
            ErrorKind.METRIC_NOT_FOUND,
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
        ds_id = f_ir.dataset
        kind = SymbolKind.TIME_FIELD if f_ir.is_time_field else SymbolKind.FIELD
        field_children: list[DependencyNode] = []
        if ds_id in reg.datasets:
            field_children.append(
                DependencyNode(
                    semantic_id=ds_id,
                    kind=SymbolKind.DATASET,
                    children=(),
                )
            )
        return DependencyNode(
            semantic_id=name,
            kind=kind,
            children=tuple(field_children),
        )

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

    def _flatten_dependent_ids(self, node: DependencyNode) -> set[str]:
        ids: set[str] = set()
        for child in node.children:
            ids.add(child.semantic_id)
            ids |= self._flatten_dependent_ids(child)
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
            seen |= self._flatten_dependent_ids(node)
        return len(seen - set(refs))

    def _open_question_blast_radius_of(self, refs: tuple[str, ...]) -> int:
        """Best-effort blast radius for author-time question classification.

        ``open_questions`` must work before a model has been authored. Without a
        loaded registry there is no dependency graph, so author-time impact
        ranking falls back to zero while strict reader APIs continue to fail
        closed through ``_require_registry``.
        """
        if self._registry is None:
            return 0
        return self.blast_radius_of(refs)

    def open_questions(
        self,
        *,
        candidates: Sequence[Candidate],
        enrichments: Sequence[Enrichment] = (),
        conflicts: Mapping[tuple[DecisionKind, str], bool] | None = None,
        round_index: int = 0,
    ) -> tuple[OpenQuestion, ...]:
        """Classify agent candidates + enrichments into ranked OpenQuestions, then
        drop any question already confirmed in the ledger (cross-session dedup).

        Dedup uses two mechanisms:
        1. question_id from ConfirmationRecords (covers answer()-produced confirmations)
        2. (decision_kind, semantic_id) from DecisionRecords (covers auto-record decisions)

        Registry-optional and backend-free: when the project is already loaded,
        blast radius comes from the in-memory dependency graph; before authoring
        or after a failed load, blast radius falls back to zero. Candidate
        generation (which needs a backend) is ``propose_candidates``.
        """
        from marivo.semantic.classifier import classify, to_decision_inputs

        inputs = to_decision_inputs(candidates, enrichments, conflicts=conflicts)
        questions = classify(
            inputs, blast_radius_of=self._open_question_blast_radius_of, round_index=round_index
        )
        confirmed = self._confirmed_question_ids(candidates)
        resolved = self._resolved_decision_keys()
        return tuple(
            q
            for q in questions
            if q.id not in confirmed
            and not any((q.decision_kind, ref) in resolved for ref in q.subject_refs)
        )

    def audit(
        self,
        *,
        inspect_source: Callable[..., TableMetadata],
    ) -> tuple[OpenQuestion, ...]:
        """Re-validate recorded decisions against current metadata. Decisions whose
        structural fingerprint changed are re-surfaced as OpenQuestions through the
        classifier (stale -> low verdict, so dangerous kinds become blockers).

        ``inspect_source`` is a callable with the same signature as
        ``mv.datasources.inspect_source``; the caller injects it so that
        ``marivo.semantic`` does not import ``marivo.analysis``.

        Data-side drift over unchanged schema/comments is not detected (accepted
        residual risk)."""
        from typing import cast

        from marivo.semantic.classifier import DecisionInput, Materiality, classify
        from marivo.semantic.ledger import LedgerStore, is_decision_stale

        store = LedgerStore(self._root)
        stale_inputs: list[DecisionInput] = []
        for obj in store.iter_object_records():
            for decision in obj.decisions:
                if decision.cited_source is None:
                    continue
                datasource_data = decision.cited_source.get("datasource")
                source_data = decision.cited_source.get("source")
                if datasource_data is None:
                    continue
                if not isinstance(source_data, Mapping):
                    continue
                metadata = inspect_source(
                    str(datasource_data),
                    source=source_from_dict(source_data),
                )
                if is_decision_stale(decision, metadata):
                    stale_inputs.append(
                        DecisionInput(
                            decision_kind=cast("DecisionKind", decision.decision_kind),
                            subject_refs=(obj.semantic_id,),
                            candidates=(),
                            agent_materiality=cast("Materiality", decision.materiality),
                            agent_verdict="low",
                            conflict=False,
                        )
                    )
        return classify(tuple(stale_inputs), blast_radius_of=self.blast_radius_of)

    def _confirmed_question_ids(self, candidates: Sequence[Candidate]) -> set[str]:
        from marivo.semantic.ledger import LedgerStore

        store = LedgerStore(self._root)
        models = {c.proposed_id.split(".", 1)[0] for c in candidates if "." in c.proposed_id}
        ids: set[str] = set()
        for model in models:
            for record in store.read_confirmations(model):
                ids.add(record.question_id)
        return ids

    def _resolved_decision_keys(self) -> set[tuple[str, str]]:
        """Return (decision_kind, semantic_id) pairs that already have a
        DecisionRecord in the object ledger. Used by open_questions() to
        dedup questions whose decision_kind is already resolved for a
        given semantic_id, covering auto-record decisions that have no
        corresponding ConfirmationRecord."""
        from marivo.semantic.ledger import LedgerStore

        store = LedgerStore(self._root)
        resolved: set[tuple[str, str]] = set()
        for obj in store.iter_object_records():
            for decision in obj.decisions:
                resolved.add((decision.decision_kind, obj.semantic_id))
        return resolved

    def propose_candidates(
        self,
        *,
        datasource: str,
        sources: Sequence[DatasetSourceIR],
        model: str,
        inspect_source: Callable[..., TableMetadata],
    ) -> ProposalResult:
        """Deterministic structural candidates for the named sources, plus
        residual columns the heuristics did not match.

        The result is a **non-exhaustive structural starting set**.  Callers
        must review ``residual_columns`` for measures, primary keys, dimensions,
        and non-conventional foreign keys that the heuristics omit.

        ``inspect_source`` is a callable with the same signature as
        ``mv.datasources.inspect_source``; the caller injects it so that
        ``marivo.semantic`` does not import ``marivo.analysis``.
        """
        from marivo.semantic.proposal import (
            candidates_from_metadata,
            relationship_candidates,
            residual_columns,
        )

        inspected = [(source, inspect_source(datasource, source=source)) for source in sources]
        cand_out: list[Candidate] = []
        res_out: list[ResidualColumn] = []
        for source, metadata in inspected:
            cands = candidates_from_metadata(metadata, model=model, source=source)
            cand_out.extend(cands)
            res_out.extend(residual_columns(metadata, cands, model=model, source=source))
        metadatas = [metadata for _source, metadata in inspected]
        rel_cands = relationship_candidates(metadatas, model=model)
        cand_out.extend(rel_cands)
        return ProposalResult(
            candidates=tuple(cand_out),
            residual_columns=tuple(res_out),
        )

    # -- describe -----------------------------------------------------------

    def describe(
        self,
        name: str,
        *,
        compile_sql: bool = False,
        format: Literal["object", "text"] = "object",
        backend_factory: Callable[..., Any] | None = None,
    ) -> Description:
        """Describe a semantic object by name.

        Returns a ``Description`` frozen dataclass.  When ``compile_sql``
        is True and the object is a metric, the ``compiled_sql`` field is
        populated.  Requires ``backend_factory`` when ``compile_sql=True``.

        When ``format="text"``, returns ``Description`` whose
        ``to_text()`` method can be called for a human-readable string.
        """
        reg = _require_registry(self._registry, project=self)
        obj = self._find_ir(name, reg)
        if obj is None:
            _raise(
                ErrorKind.METRIC_NOT_FOUND,
                f"Object {name!r} not found.",
                cls=SemanticRuntimeError,
                refs=(name,),
            )

        kind = self._ir_kind(obj)
        compiled_sql: str | None = None
        compile_error: dict[str, Any] | None = None

        if compile_sql and isinstance(obj, MetricIR) and backend_factory is not None:
            try:
                compiled_sql = self.compile_sql(obj.semantic_id, backend_factory=backend_factory)
            except SemanticRuntimeError as exc:
                compile_error = {
                    "kind": exc.kind,
                    "message": exc.message,
                    "refs": list(exc.semantic_refs),
                }

        # Compute dependencies and dependents
        dep_names = self._compute_dependency_ids(name, obj, reg)
        dep_of_names = self._compute_dependent_ids(name, obj, reg)

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
            python_symbol=obj.python_symbol,
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
        )

        return desc

    def _compute_dependency_ids(
        self,
        name: str,
        obj: DatasetIR | DatasourceIR | FieldIR | MetricIR,
        reg: Registry,
    ) -> list[str]:
        """Compute flat list of dependency semantic_ids."""
        deps: list[str] = []
        if isinstance(obj, MetricIR):
            for ds_ref in obj.datasets:
                deps.append(ds_ref)
            for comp_ref in obj.decomposition.components.values():
                deps.append(comp_ref)
        elif isinstance(obj, DatasetIR):
            for f_id, f_ir in reg.fields.items():
                if f_ir.dataset == name:
                    deps.append(f_id)
        elif isinstance(obj, FieldIR):
            deps.append(obj.dataset)
        return deps

    def _compute_dependent_ids(
        self,
        name: str,
        obj: DatasetIR | DatasourceIR | FieldIR | MetricIR,
        reg: Registry,
    ) -> list[str]:
        """Compute flat list of dependent semantic_ids."""
        dep_of: list[str] = []
        if isinstance(obj, DatasetIR):
            for m_id, m_ir in reg.metrics.items():
                if name in m_ir.datasets:
                    dep_of.append(m_id)
            for f_id, f_ir in reg.fields.items():
                if f_ir.dataset == name:
                    dep_of.append(f_id)
        elif isinstance(obj, FieldIR):
            dep_of.append(obj.dataset)
        elif isinstance(obj, MetricIR):
            for m_id, m_ir in reg.metrics.items():
                if m_id == name:
                    continue
                for comp_ref in m_ir.decomposition.components.values():
                    if comp_ref == name:
                        dep_of.append(m_id)
        return dep_of

    # -- compile_sql --------------------------------------------------------

    def compile_sql(
        self,
        metric: str,
        *,
        backend_factory: Callable[[str], Any],
    ) -> str:
        """Compile a metric expression to SQL.

        Materializes the metric ibis expression, then compiles it
        using ``ibis.to_sql()``.

        Raises SemanticRuntimeError (COMPILE_ERROR) if the metric
        is not found or if ibis compilation fails.
        """
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
            mat = Materializer(self, backend_factory)
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
        backend_factory: Callable[[str], Any],
    ) -> ibis.Table:
        """Materialize a dataset by semantic_id using the given backend_factory.

        Each call creates a fresh Materializer instance.
        """
        mat = Materializer(self, backend_factory)
        return mat.dataset(name)

    def materialize_field(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any],
    ) -> ir.Value:
        """Materialize a field by semantic_id using the given backend_factory.

        Each call creates a fresh Materializer instance.
        """
        mat = Materializer(self, backend_factory)
        return mat.field(name)

    def materialize_metric(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any],
    ) -> ir.Value:
        """Materialize a metric by semantic_id using the given backend_factory.

        Each call creates a fresh Materializer instance.
        """
        mat = Materializer(self, backend_factory)
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
        backend_factory: Callable[[str], Any],
        columns: Iterable[str] | None = None,
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Collect a bounded raw preview for a datasource table source.

        The returned preview is the datasource-table preview. A successful call
        records the physical preview ref as raw preview evidence for subsequent
        readiness checks on this project instance.
        """
        validate_preview_limit(limit)
        backend = backend_factory(datasource)
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
            )
        )
        self._record_raw_preview_evidence(preview.ref)
        return preview

    def preview_dataset(
        self,
        name: str,
        *,
        backend_factory: Callable[[str], Any],
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Return a bounded preview of a semantic dataset."""
        limit = validate_preview_limit(limit)
        table = self.materialize_dataset(name, backend_factory=backend_factory)
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
        backend_factory: Callable[[str], Any],
        limit: int = PREVIEW_DEFAULT_LIMIT,
        context_columns: Iterable[str] | None = None,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Return a bounded preview of a semantic field with parent dataset context."""
        limit = validate_preview_limit(limit)
        reg = _require_registry(self._registry, project=self)
        field_ir = reg.fields.get(name)
        if field_ir is None:
            _raise(
                ErrorKind.METRIC_NOT_FOUND,
                f"Field {name!r} not found in registry.",
                cls=SemanticRuntimeError,
                refs=(name,),
            )

        mat = Materializer(self, backend_factory)
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
        backend_factory: Callable[[str], Any],
        limit: int = PREVIEW_DEFAULT_LIMIT,
        include_types: bool = True,
        redact: bool = True,
    ) -> PreviewResult:
        """Return a bounded preview of a semantic metric."""
        limit = validate_preview_limit(limit)
        metric_value = self.materialize_metric(name, backend_factory=backend_factory)
        return preview_ibis_value(
            metric_value,
            kind="semantic_metric",
            ref=name,
            limit=limit,
            column_name="value",
            sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=limit),
            include_types=include_types,
            redact=redact,
        )

    # -- parity -------------------------------------------------------------

    def parity_check(
        self,
        name: str,
        *,
        backend_factory: Callable[..., Any],
        rel_tol: float | None = None,
        abs_tol: float | None = None,
    ) -> ParityResult:
        """Run parity check for a metric against its source SQL.

        See :func:`marivo.semantic.parity.parity_check` for details.
        """
        return parity_check(
            self,
            name,
            backend_factory=backend_factory,
            rel_tol=rel_tol,
            abs_tol=abs_tol,
        )

    # -- readiness ----------------------------------------------------------

    def readiness(
        self,
        *,
        strict_provenance: bool = True,
        require_preview: bool = True,
        require_comments: bool = False,
        require_evidence_ledger: bool = False,
        strict_enrichment: bool = False,
        backend_factory: Callable[[str], Any] | None = None,
        refs: Iterable[str] | None = None,
        required_raw_previews: Iterable[str] | None = None,
        raw_previews: Iterable[str] = (),
        failed_raw_previews: Iterable[str] = (),
        required_semantic_previews: Iterable[str] | None = None,
        knowledge_documents: Iterable[str] = (),
        user_confirmations: Iterable[str] = (),
        confirmed_relationships: Iterable[str] = (),
        primary_keys_sampled: Iterable[str] = (),
        raw_sql_required_refs: Iterable[str] = (),
        supports_federation: bool = False,
        table_metadata: Iterable[TableMetadata] = (),
    ) -> ReadinessReport:
        """Return a structured semantic readiness report.

        ``backend_factory`` is a callable from datasource semantic id to an
        Ibis backend, for example ``lambda name: mv.datasources.build_backend(name)``.
        """
        persisted_raw_previews = self._persisted_raw_preview_evidence()
        collected_raw_previews = tuple(
            dict.fromkeys(
                (*tuple(raw_previews), *persisted_raw_previews, *self._raw_preview_evidence)
            )
        )
        return build_readiness_report(
            self,
            strict_provenance=strict_provenance,
            require_preview=require_preview,
            require_comments=require_comments,
            require_evidence_ledger=require_evidence_ledger,
            strict_enrichment=strict_enrichment,
            backend_factory=backend_factory,
            refs=refs,
            required_raw_previews=required_raw_previews,
            raw_previews=collected_raw_previews,
            failed_raw_previews=failed_raw_previews,
            required_semantic_previews=required_semantic_previews,
            knowledge_documents=knowledge_documents,
            user_confirmations=user_confirmations,
            confirmed_relationships=confirmed_relationships,
            primary_keys_sampled=primary_keys_sampled,
            raw_sql_required_refs=raw_sql_required_refs,
            supports_federation=supports_federation,
            table_metadata=table_metadata,
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

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _find_ir(name: str, reg: Registry) -> DatasetIR | DatasourceIR | FieldIR | MetricIR | None:
        """Look up an IR object by semantic_id across all collections."""
        if name in reg.datasources:
            return reg.datasources[name]
        if name in reg.datasets:
            return reg.datasets[name]
        if name in reg.fields:
            return reg.fields[name]
        if name in reg.metrics:
            return reg.metrics[name]
        return None

    @staticmethod
    def _ir_kind(obj: DatasetIR | DatasourceIR | FieldIR | MetricIR) -> SymbolKind:
        """Return the SymbolKind for an IR object."""
        if isinstance(obj, DatasourceIR):
            return SymbolKind.DATASOURCE
        if isinstance(obj, DatasetIR):
            return SymbolKind.DATASET
        if isinstance(obj, FieldIR):
            return SymbolKind.TIME_FIELD if obj.is_time_field else SymbolKind.FIELD
        if isinstance(obj, MetricIR):
            return SymbolKind.METRIC
        return SymbolKind.MODEL  # fallback, should not happen
