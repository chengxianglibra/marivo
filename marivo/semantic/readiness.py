"""Semantic readiness report DTOs and report construction."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from marivo.datasource.metadata import TableMetadata
from marivo.preview import (
    METRIC_PREVIEW_SAMPLE_SIZE,
    PreviewResult,
    PreviewSamplePolicy,
    PreviewWarning,
    preview_ibis_table,
    preview_ibis_value,
    validate_preview_limit,
)
from marivo.semantic.ir import ParityStatus, TableSourceIR
from marivo.semantic.materializer import Materializer
from marivo.semantic.parity import propagated_parity_status
from marivo.semantic.richness import DemandSignal, RichnessGap, build_richness_report

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject

ReadinessStatus = Literal["ready", "ready_with_warnings", "blocked"]
ReadinessSeverity = Literal["blocker", "warning"]
ReadinessIssueKind = Literal[
    "load_error",
    "datasource_unreachable",
    "unknown_ref",
    "missing_schema",
    "missing_comments",
    "missing_raw_preview",
    "raw_preview_failed",
    "entity_preview_failed",
    "dimension_preview_failed",
    "missing_knowledge_definition",
    "ambiguous_time_axis",
    "time_dimension_preview_failed",
    "metric_materialize_failed",
    "metric_compile_failed",
    "unverified_metric",
    "parity_drifted",
    "cross_datasource_unfederated",
    "requires_raw_sql",
    "primary_key_unsampled",
    "derived_source_grain_unverified",
    "fragile_string_ref",
    "time_dimension_pushdown_advisory",
    "unresolved_clarification",
    "missing_business_definition",
    "missing_guardrails",
]


@dataclass(frozen=True)
class ReadinessIssue:
    kind: ReadinessIssueKind
    severity: ReadinessSeverity
    refs: tuple[str, ...]
    message: str
    suggested_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "refs": list(self.refs),
            "message": self.message,
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True)
class ReadinessInputSummary:
    datasources: tuple[str, ...]
    refs: tuple[str, ...]
    tables: tuple[str, ...]
    decision_records: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "datasources": list(self.datasources),
            "refs": list(self.refs),
            "tables": list(self.tables),
            "decision_records": list(self.decision_records),
        }


@dataclass(frozen=True)
class PreviewSummary:
    required_previews: tuple[str, ...]
    completed_previews: tuple[str, ...]
    failed_previews: tuple[str, ...]
    warnings: tuple[PreviewWarning, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "required_previews": list(self.required_previews),
            "completed_previews": list(self.completed_previews),
            "failed_previews": list(self.failed_previews),
            "warnings": [
                {
                    "kind": w.kind,
                    "message": w.message,
                    "columns": list(w.columns),
                }
                for w in self.warnings
            ],
        }


@dataclass(frozen=True)
class ParitySummary:
    verified_metrics: tuple[str, ...]
    unverified_metrics: tuple[str, ...]
    drifted_metrics: tuple[str, ...]
    unsupported_metrics: tuple[str, ...]
    skipped_metrics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "verified_metrics": list(self.verified_metrics),
            "unverified_metrics": list(self.unverified_metrics),
            "drifted_metrics": list(self.drifted_metrics),
            "unsupported_metrics": list(self.unsupported_metrics),
            "skipped_metrics": list(self.skipped_metrics),
        }


@dataclass(frozen=True)
class RichnessSummary:
    gaps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class _ReadinessEvidence:
    raw_previews: tuple[str, ...]
    failed_raw_previews: tuple[str, ...]
    required_raw_previews: tuple[str, ...]
    required_semantic_previews: tuple[str, ...]
    primary_keys_sampled: tuple[str, ...]
    raw_sql_required_refs: tuple[str, ...]
    table_metadata: tuple[Any, ...]
    supports_federation: bool


@dataclass(frozen=True)
class ReadinessReport:
    status: ReadinessStatus
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    preview_summary: PreviewSummary
    parity_summary: ParitySummary
    richness_summary: RichnessSummary
    checked_at: str

    def __repr__(self) -> str:
        issues = len(self.blockers) + len(self.warnings)
        return f"<ReadinessReport status={self.status} issues={issues}; call .show() to inspect>"

    def render(self) -> str:
        """Return bounded plain-text inspection card without a trailing newline."""
        lines: list[str] = [
            f"ReadinessReport status={self.status}",
        ]
        if self.blockers:
            lines.append(f"blockers ({len(self.blockers)}):")
            for issue in self.blockers[:3]:
                lines.append(f"  - {issue.kind}: {issue.message}")
            if len(self.blockers) > 3:
                lines.append(f"  ... {len(self.blockers) - 3} more; call .to_dict() for full list")
        if self.warnings:
            lines.append(f"warnings ({len(self.warnings)}):")
            for issue in self.warnings[:3]:
                lines.append(f"  - {issue.kind}: {issue.message}")
            if len(self.warnings) > 3:
                lines.append(f"  ... {len(self.warnings) - 3} more; call .to_dict() for full list")
        ready = list(self.analysis_ready_refs)
        if ready:
            shown = ready[:5]
            lines.append(f"analysis_ready: {', '.join(shown)}")
            if len(ready) > 5:
                lines.append(f"  ... {len(ready) - 5} more")
        lines.append(f"checked_at: {self.checked_at}")
        lines.append("available:")
        for entry in (".render()", ".to_dict()"):
            lines.append(f"- {entry}")
        return "\n".join(lines)

    def show(self) -> None:
        """Print render() output followed by a trailing newline and return None."""
        print(self.render())

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "analysis_ready_refs": list(self.analysis_ready_refs),
            "blockers": [issue.to_dict() for issue in self.blockers],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "input_summary": self.input_summary.to_dict(),
            "preview_summary": self.preview_summary.to_dict(),
            "parity_summary": self.parity_summary.to_dict(),
            "richness_summary": self.richness_summary.to_dict(),
            "checked_at": self.checked_at,
        }


class _SemanticKind(StrEnum):
    ENTITY = "entity"
    DIMENSION = "dimension"
    TIME_DIMENSION = "time_dimension"
    METRIC = "metric"
    RELATIONSHIP = "relationship"


def _checked_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _status(blockers: list[ReadinessIssue], warnings: list[ReadinessIssue]) -> ReadinessStatus:
    if blockers:
        return "blocked"
    if warnings:
        return "ready_with_warnings"
    return "ready"


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)


def _decision_record_summary(project: SemanticProject, refs: Iterable[str]) -> tuple[str, ...]:
    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.semantic_root)
    records: list[str] = []
    for ref in refs:
        record = store.read_object(ref)
        if record is None:
            continue
        for decision in record.decisions:
            records.append(f"{ref}:{decision.decision_kind}")
    return _dedupe(records)


def _issue(
    kind: ReadinessIssueKind,
    severity: ReadinessSeverity,
    refs: Iterable[str],
    message: str,
    suggested_action: str,
) -> ReadinessIssue:
    return ReadinessIssue(
        kind=kind,
        severity=severity,
        refs=_dedupe(refs),
        message=message,
        suggested_action=suggested_action,
    )


def _derive_raw_sql_required_refs(
    kinds: Mapping[str, _SemanticKind],
    objects: Mapping[str, object],
) -> tuple[str, ...]:
    """Metrics/datasets whose logic lives in source_sql, not the semantic API.

    These objects are flagged as blockers because their business logic cannot
    be expressed or drilled into through the semantic layer. Previously this
    blocker was inert (raw_sql_required_refs defaulted to empty and nobody
    passed it); auto-derivation makes it active.
    """
    refs: list[str] = []
    for semantic_id, kind in kinds.items():
        if kind not in {_SemanticKind.METRIC, _SemanticKind.ENTITY}:
            continue
        obj = objects.get(semantic_id)
        if obj is None:
            continue
        provenance = getattr(obj, "provenance", None)
        if (
            provenance is not None
            and getattr(provenance, "source_sql", None) is not None
            and getattr(provenance, "verification_mode", None) != "python_native"
        ):
            refs.append(semantic_id)
    return tuple(refs)


def _object_maps(project: SemanticProject) -> tuple[dict[str, _SemanticKind], dict[str, object]]:
    reg = project._registry
    if reg is None:
        return {}, {}

    kinds: dict[str, _SemanticKind] = {}
    objects: dict[str, object] = {}

    for dataset in reg.datasets.values():
        kinds[dataset.semantic_id] = _SemanticKind.ENTITY
        objects[dataset.semantic_id] = dataset
    for field in reg.fields.values():
        kind = _SemanticKind.TIME_DIMENSION if field.is_time_dimension else _SemanticKind.DIMENSION
        kinds[field.semantic_id] = kind
        objects[field.semantic_id] = field
    for metric in reg.metrics.values():
        kinds[metric.semantic_id] = _SemanticKind.METRIC
        objects[metric.semantic_id] = metric
    for relationship in reg.relationships.values():
        kinds[relationship.semantic_id] = _SemanticKind.RELATIONSHIP
        objects[relationship.semantic_id] = relationship

    return kinds, objects


_REQUIRED_DECISION_BY_KIND = {
    _SemanticKind.TIME_DIMENSION: "time_dimension_identity",
    _SemanticKind.METRIC: "metric_decomposition",
}


def _evidence_ledger_blockers(project: SemanticProject) -> list[ReadinessIssue]:
    """Dangerous-kind authored objects with no backing ledger decision -> blockers.
    Mapping: time_dimension -> time_dimension_identity, metric -> metric_decomposition."""
    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.root)
    kinds, _objects = _object_maps(project)
    issues: list[ReadinessIssue] = []
    for semantic_id, kind in kinds.items():
        required = _REQUIRED_DECISION_BY_KIND.get(kind)
        if required is None:
            continue
        obj = store.read_object(semantic_id)
        has_decision = obj is not None and any(d.decision_kind == required for d in obj.decisions)
        if not has_decision:
            issues.append(
                _issue(
                    "unresolved_clarification",
                    "blocker",
                    (semantic_id,),
                    f"{semantic_id} has no recorded {required} decision; this dangerous decision is unaudited.",
                    f"Reload after the semantic declaration or record an object-level {required} DecisionRecord before handoff.",
                )
            )
    return issues


def _strict_enrichment_issues(
    checked_refs: Iterable[str],
    kinds: Mapping[str, _SemanticKind],
    objects: Mapping[str, object],
) -> tuple[list[ReadinessIssue], list[ReadinessIssue]]:
    """Contracts section 7: analyzable handoff refs must carry a non-empty
    business_definition (blocker) and guardrails (warning). Relationships are out
    of scope, matching semantic-preview scoping."""
    analyzable = {
        _SemanticKind.ENTITY,
        _SemanticKind.DIMENSION,
        _SemanticKind.TIME_DIMENSION,
        _SemanticKind.METRIC,
    }
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    for ref in checked_refs:
        if kinds.get(ref) not in analyzable:
            continue
        obj = objects.get(ref)
        if obj is None:
            continue
        if _missing_business_definition(obj):
            blockers.append(
                _issue(
                    "missing_business_definition",
                    "blocker",
                    (ref,),
                    f"{ref} has no ai_context.business_definition for analysis handoff.",
                    "Add ai_context.business_definition so analysis can match and reuse this ref.",
                )
            )
        if _missing_guardrails(obj):
            warnings.append(
                _issue(
                    "missing_guardrails",
                    "warning",
                    (ref,),
                    f"{ref} has no ai_context.guardrails for analysis handoff.",
                    "Add ai_context.guardrails to record usage constraints before reuse.",
                )
            )
    return blockers, warnings


def _default_checked_refs(kinds: Mapping[str, _SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in kinds if kinds[ref] != _SemanticKind.RELATIONSHIP) + tuple(
        ref for ref in kinds if kinds[ref] == _SemanticKind.RELATIONSHIP
    )


def _semantic_preview_refs(
    refs: Iterable[str],
    kinds: Mapping[str, _SemanticKind],
) -> tuple[str, ...]:
    return tuple(
        ref
        for ref in refs
        if kinds.get(ref)
        in {
            _SemanticKind.ENTITY,
            _SemanticKind.DIMENSION,
            _SemanticKind.TIME_DIMENSION,
            _SemanticKind.METRIC,
        }
    )


def _dataset_refs(refs: Iterable[str], kinds: Mapping[str, _SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in refs if kinds.get(ref) == _SemanticKind.ENTITY)


def _raw_preview_ref(
    datasource: str,
    table: str,
    database: str | tuple[str, ...] | None,
) -> str:
    if database is None:
        return f"{datasource}.{table}"
    namespace = ".".join(database) if isinstance(database, tuple) else database
    return f"{datasource}.{namespace}.{table}"


def _dataset_raw_preview_refs(
    refs: Iterable[str],
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> tuple[str, ...]:
    raw_refs: list[str] = []
    for ref in refs:
        if kinds.get(ref) != _SemanticKind.ENTITY:
            continue
        dataset = objects.get(ref)
        source = getattr(dataset, "source", None)
        datasource = getattr(dataset, "datasource", None)
        if isinstance(source, TableSourceIR) and isinstance(datasource, str):
            raw_refs.append(_raw_preview_ref(datasource, source.table, source.database))
    return tuple(raw_refs)


def _dependencies_for_ref(
    ref: str,
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> tuple[str, ...]:
    kind = kinds.get(ref)
    obj = objects.get(ref)
    if obj is None:
        return ()
    if kind in {_SemanticKind.DIMENSION, _SemanticKind.TIME_DIMENSION}:
        entity = getattr(obj, "entity", None)
        return (entity,) if isinstance(entity, str) else ()
    if kind == _SemanticKind.METRIC:
        deps: list[str] = []
        deps.extend(getattr(obj, "entities", ()))
        decomposition = getattr(obj, "decomposition", None)
        components = getattr(decomposition, "components", {})
        if isinstance(components, Mapping):
            deps.extend(str(value) for value in components.values())
        return tuple(deps)
    if kind == _SemanticKind.RELATIONSHIP:
        relationship_deps = (
            getattr(obj, "from_entity", None),
            getattr(obj, "to_entity", None),
            *getattr(obj, "from_dimensions", ()),
            *getattr(obj, "to_dimensions", ()),
        )
        return tuple(dep for dep in relationship_deps if isinstance(dep, str))
    return ()


def _expand_checked_refs(
    refs: Iterable[str] | None,
    kinds: Mapping[str, _SemanticKind],
    objects: Mapping[str, object],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    seeds = _dedupe(refs if refs is not None else _default_checked_refs(kinds))
    checked: list[str] = []
    unknown: list[str] = []
    queue = list(seeds)
    while queue:
        ref = queue.pop(0)
        if ref in checked:
            continue
        checked.append(ref)
        if ref not in kinds:
            unknown.append(ref)
            continue
        for dep in _dependencies_for_ref(ref, objects, kinds):
            if dep not in checked and dep not in queue:
                queue.append(dep)
    return tuple(checked), tuple(unknown)


def _raw_preview_datasets_by_ref(
    refs: Iterable[str],
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for ref in refs:
        if kinds.get(ref) != _SemanticKind.ENTITY:
            continue
        dataset = objects.get(ref)
        source = getattr(dataset, "source", None)
        datasource = getattr(dataset, "datasource", None)
        if isinstance(source, TableSourceIR) and isinstance(datasource, str):
            raw_ref = _raw_preview_ref(datasource, source.table, source.database)
            out.setdefault(raw_ref, []).append(ref)
    return {key: tuple(values) for key, values in out.items()}


def _raw_preview_specs_by_ref(
    refs: Iterable[str],
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> dict[str, tuple[str, str, str | tuple[str, ...] | None]]:
    specs: dict[str, tuple[str, str, str | tuple[str, ...] | None]] = {}
    for ref in refs:
        if kinds.get(ref) != _SemanticKind.ENTITY:
            continue
        dataset = objects.get(ref)
        source = getattr(dataset, "source", None)
        datasource = getattr(dataset, "datasource", None)
        if isinstance(source, TableSourceIR) and isinstance(datasource, str):
            raw_ref = _raw_preview_ref(datasource, source.table, source.database)
            specs[raw_ref] = (datasource, source.table, source.database)
    return specs


def _datasource_refs_for_checked_refs(
    refs: Iterable[str],
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> tuple[str, ...]:
    datasources: list[str] = []
    for ref in refs:
        if kinds.get(ref) != _SemanticKind.ENTITY:
            continue
        datasource = getattr(objects.get(ref), "datasource", None)
        if isinstance(datasource, str):
            datasources.append(datasource)
    return _dedupe(datasources)


def _refs_with_issue(issues: Iterable[ReadinessIssue]) -> set[str]:
    return {ref for issue in issues for ref in issue.refs}


def _has_definition(obj: object) -> bool:
    description = getattr(obj, "description", None)
    ai_context = getattr(obj, "ai_context", None)
    business_definition = getattr(ai_context, "business_definition", None)
    return bool(description or business_definition)


def _missing_business_definition(obj: object) -> bool:
    ai_context = getattr(obj, "ai_context", None)
    business_definition = getattr(ai_context, "business_definition", None)
    return not (business_definition and business_definition.strip())


def _missing_guardrails(obj: object) -> bool:
    ai_context = getattr(obj, "ai_context", None)
    guardrails = getattr(ai_context, "guardrails", ())
    return not guardrails


def _metadata_by_dataset_ref(
    project: SemanticProject,
    table_metadata: Iterable[TableMetadata],
) -> dict[str, TableMetadata]:
    reg = project._registry
    if reg is None:
        return {}
    by_key = {
        (metadata.datasource, metadata.table, metadata.database): metadata
        for metadata in table_metadata
    }
    out: dict[str, TableMetadata] = {}
    for dataset in reg.datasets.values():
        source = dataset.source
        if not isinstance(source, TableSourceIR):
            continue
        metadata = by_key.get(
            (
                dataset.datasource,
                source.table,
                _dataset_source_database_for_metadata(reg, dataset.datasource, source),
            )
        )
        if metadata is not None:
            out[dataset.semantic_id] = metadata
    return out


def _dataset_source_database_for_metadata(
    registry: object,
    datasource_name: str,
    source: TableSourceIR,
) -> str | tuple[str, ...] | None:
    if source.database is not None:
        return source.database
    datasources = getattr(registry, "datasources", {})
    datasource = datasources.get(datasource_name)
    if getattr(datasource, "backend_type", None) != "clickhouse":
        return None
    database = getattr(datasource, "fields", {}).get("database")
    return str(database) if database is not None else None


def _metadata_has_comment_for_ref(
    ref: str,
    obj: object,
    metadata_by_dataset: Mapping[str, TableMetadata],
) -> bool:
    # For metrics, check if any of their datasets has a metadata comment.
    dataset_refs = getattr(obj, "datasets", None)
    if dataset_refs is not None:
        return any(
            _dataset_has_metadata_comment(dataset_ref, metadata_by_dataset)
            for dataset_ref in dataset_refs
        )
    dataset_ref = getattr(obj, "entity", None)
    if dataset_ref is None:
        dataset_ref = ref
    return _dataset_has_metadata_comment_for_field(str(dataset_ref), obj, metadata_by_dataset)


def _dataset_has_metadata_comment(
    dataset_ref: str,
    metadata_by_dataset: Mapping[str, TableMetadata],
) -> bool:
    metadata = metadata_by_dataset.get(dataset_ref)
    return metadata is not None and bool(metadata.comment)


def _dataset_has_metadata_comment_for_field(
    dataset_ref: str,
    obj: object,
    metadata_by_dataset: Mapping[str, TableMetadata],
) -> bool:
    metadata = metadata_by_dataset.get(dataset_ref)
    if metadata is None:
        return False
    if metadata.comment:
        return True
    field_name = getattr(obj, "name", None)
    if field_name is None:
        return False
    return any(column.name == field_name and column.comment for column in metadata.columns)


def _preview_issue_kind(kind: _SemanticKind) -> ReadinessIssueKind:
    if kind == _SemanticKind.ENTITY:
        return "entity_preview_failed"
    if kind == _SemanticKind.TIME_DIMENSION:
        return "time_dimension_preview_failed"
    if kind == _SemanticKind.DIMENSION:
        return "dimension_preview_failed"
    return "metric_materialize_failed"


def _preview_column_name(semantic_id: str) -> str:
    return f"__marivo_preview_{semantic_id.replace('.', '__')}"


def _run_preview(
    project: SemanticProject,
    ref: str,
    kind: _SemanticKind,
    backend_factory: Callable[[str], Any],
    *,
    limit: int,
) -> PreviewResult:
    if kind == _SemanticKind.ENTITY:
        return project.preview_dataset(ref, backend_factory=backend_factory, limit=limit)
    if kind in {_SemanticKind.DIMENSION, _SemanticKind.TIME_DIMENSION}:
        return project.preview_field(ref, backend_factory=backend_factory, limit=limit)
    if kind == _SemanticKind.METRIC:
        return project.preview_metric(ref, backend_factory=backend_factory, limit=limit)
    raise ValueError(f"cannot preview semantic kind {kind}")


def _run_raw_preview(
    *,
    ref: str,
    datasource: str,
    table: str,
    database: str | tuple[str, ...] | None,
    backend_factory: Callable[[str], Any],
    preview_limit: int,
) -> PreviewResult:
    backend = backend_factory(datasource)
    preview_table = (
        backend.table(table) if database is None else backend.table(table, database=database)
    )
    return preview_ibis_table(
        preview_table,
        kind="datasource_table",
        ref=ref,
        limit=preview_limit,
        sample_policy=PreviewSamplePolicy(method="bounded_limit", limit=preview_limit),
    )


@dataclass(frozen=True)
class _SemanticPreviewRun:
    completed: tuple[str, ...]
    failed: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    preview_warnings: tuple[PreviewWarning, ...]


def _run_semantic_previews(
    project: SemanticProject,
    semantic_required: Iterable[str],
    kinds: Mapping[str, _SemanticKind],
    objects: Mapping[str, object],
    backend_factory: Callable[[str], Any],
    *,
    preview_limit: int,
) -> _SemanticPreviewRun:
    completed: list[str] = []
    failed: list[str] = []
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    preview_warnings: list[PreviewWarning] = []
    required = tuple(semantic_required)
    materializer = Materializer(project, backend_factory)
    metric_materializer = Materializer(
        project, backend_factory, sample_size=METRIC_PREVIEW_SAMPLE_SIZE
    )

    dataset_groups: dict[str, list[str]] = {}
    metric_refs: list[str] = []
    serial_refs: list[str] = []

    for ref in required:
        kind = kinds.get(ref)
        if kind == _SemanticKind.ENTITY:
            dataset_groups.setdefault(ref, []).append(ref)
        elif kind in {_SemanticKind.DIMENSION, _SemanticKind.TIME_DIMENSION}:
            field = objects.get(ref)
            dataset_ref = getattr(field, "entity", None)
            if isinstance(dataset_ref, str):
                dataset_groups.setdefault(dataset_ref, []).append(ref)
            else:
                serial_refs.append(ref)
        elif kind == _SemanticKind.METRIC:
            metric_refs.append(ref)

    for dataset_ref, refs in dataset_groups.items():
        try:
            preview = _run_dataset_group_preview(
                dataset_ref,
                refs,
                kinds,
                materializer,
                preview_limit=preview_limit,
            )
        except Exception:
            fallback = _run_serial_semantic_previews(
                project,
                refs,
                kinds,
                backend_factory,
                preview_limit=preview_limit,
            )
            completed.extend(fallback.completed)
            failed.extend(fallback.failed)
            blockers.extend(fallback.blockers)
            warnings.extend(fallback.warnings)
            preview_warnings.extend(fallback.preview_warnings)
        else:
            completed.extend(refs)
            preview_warnings.extend(preview.warnings)

    for ref in metric_refs:
        kind = kinds.get(ref)
        if kind is None:
            continue
        try:
            preview = _run_metric_preview(ref, metric_materializer, preview_limit=preview_limit)
        except Exception as exc:
            blockers.append(_semantic_preview_blocker(ref, kind, exc))
            failed.append(ref)
        else:
            completed.append(ref)
            preview_warnings.extend(preview.warnings)

    if serial_refs:
        fallback = _run_serial_semantic_previews(
            project,
            serial_refs,
            kinds,
            backend_factory,
            preview_limit=preview_limit,
        )
        completed.extend(fallback.completed)
        failed.extend(fallback.failed)
        blockers.extend(fallback.blockers)
        warnings.extend(fallback.warnings)
        preview_warnings.extend(fallback.preview_warnings)

    return _SemanticPreviewRun(
        completed=tuple(completed),
        failed=tuple(failed),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        preview_warnings=tuple(preview_warnings),
    )


def _run_dataset_group_preview(
    dataset_ref: str,
    refs: Iterable[str],
    kinds: Mapping[str, _SemanticKind],
    materializer: Materializer,
    *,
    preview_limit: int,
) -> PreviewResult:
    parent_table = materializer.entity(dataset_ref)
    projections: list[Any] = []
    ref_tuple = tuple(refs)

    if dataset_ref in ref_tuple:
        projections.extend(parent_table[column] for column in parent_table.columns)

    for ref in ref_tuple:
        if kinds.get(ref) not in {_SemanticKind.DIMENSION, _SemanticKind.TIME_DIMENSION}:
            continue
        field_value = materializer.dimension(ref)
        column_name = _preview_column_name(ref)
        projections.append(field_value.name(column_name))

    if not projections:
        projections.extend(parent_table[column] for column in parent_table.columns)

    preview_table = parent_table.select(*projections)
    return preview_ibis_table(
        preview_table,
        kind="semantic_dataset",
        ref=dataset_ref,
        limit=preview_limit,
        sample_policy=PreviewSamplePolicy(
            method="bounded_limit",
            limit=preview_limit,
        ),
    )


def _run_metric_preview(
    ref: str,
    materializer: Materializer,
    *,
    preview_limit: int,
) -> PreviewResult:
    metric_value = materializer.metric(ref)
    result = preview_ibis_value(
        metric_value,
        kind="semantic_metric",
        ref=ref,
        limit=preview_limit,
        column_name="value",
        sample_policy=PreviewSamplePolicy(
            method="pre_aggregate_limit",
            limit=preview_limit,
        ),
    )
    if materializer._sample_size is not None:
        result = PreviewResult(
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
                    message=f"metric computed on {materializer._sample_size} row sample, result is approximate",
                ),
            ),
            sample_policy=result.sample_policy,
        )
    return result


def _run_serial_semantic_previews(
    project: SemanticProject,
    refs: Iterable[str],
    kinds: Mapping[str, _SemanticKind],
    backend_factory: Callable[[str], Any],
    *,
    preview_limit: int,
) -> _SemanticPreviewRun:
    completed: list[str] = []
    failed: list[str] = []
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    preview_warnings: list[PreviewWarning] = []

    for ref in refs:
        kind = kinds.get(ref)
        if kind is None or kind == _SemanticKind.RELATIONSHIP:
            continue
        try:
            preview = _run_preview(
                project,
                ref,
                kind,
                backend_factory,
                limit=preview_limit,
            )
        except Exception as exc:
            blockers.append(_semantic_preview_blocker(ref, kind, exc))
            failed.append(ref)
        else:
            completed.append(ref)
            preview_warnings.extend(preview.warnings)

    return _SemanticPreviewRun(
        completed=tuple(completed),
        failed=tuple(failed),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        preview_warnings=tuple(preview_warnings),
    )


def _semantic_preview_blocker(
    ref: str,
    kind: _SemanticKind,
    exc: Exception,
) -> ReadinessIssue:
    return _issue(
        _preview_issue_kind(kind),
        "blocker",
        (ref,),
        f"Semantic preview failed for {ref}: {exc}",
        "Fix the semantic object and rerun the bounded preview.",
    )


def _decision_record_refs(project: SemanticProject) -> tuple[str, ...]:
    from marivo.semantic.ledger import LedgerStore

    refs: list[str] = []
    for record in LedgerStore(project.root).iter_object_records():
        refs.extend(
            f"{record.semantic_id}:{decision.decision_kind}" for decision in record.decisions
        )
    return _dedupe(refs)


def _richness_gap_label(gap: RichnessGap) -> str:
    return f"{gap.subkind}:{','.join(gap.refs)}"


def _richness_warning_kind(gap: RichnessGap) -> ReadinessIssueKind:
    if gap.subkind == "missing_business_definition":
        return "missing_business_definition"
    if gap.subkind == "missing_guardrails":
        return "missing_guardrails"
    return "unresolved_clarification"


def _richness_warnings(gaps: Iterable[RichnessGap]) -> tuple[ReadinessIssue, ...]:
    return tuple(
        _issue(
            _richness_warning_kind(gap),
            "warning",
            gap.refs,
            f"Richness gap {gap.subkind} affects {', '.join(gap.refs)}.",
            gap.suggested_action,
        )
        for gap in gaps
    )


def build_readiness_report(
    project: SemanticProject,
    evidence: _ReadinessEvidence,
    *,
    backend_factory: Callable[[str], Any] | None = None,
    refs: Iterable[str] | None = None,
    demand: DemandSignal | None = None,
    preview_limit: int = 20,
    parity_rel_tol: float | None = None,
    parity_abs_tol: float | None = None,
) -> ReadinessReport:
    preview_limit = validate_preview_limit(preview_limit)
    # Unpack evidence into local variables used throughout the function body.
    raw_previews = evidence.raw_previews
    failed_raw_previews = evidence.failed_raw_previews
    required_raw_previews = evidence.required_raw_previews or None
    required_semantic_previews = evidence.required_semantic_previews or None
    primary_keys_sampled = evidence.primary_keys_sampled
    raw_sql_required_refs = evidence.raw_sql_required_refs
    table_metadata = evidence.table_metadata
    supports_federation = evidence.supports_federation

    # Policy flags: strict defaults now that evidence auto-loads.
    require_preview = True
    require_comments = False
    require_evidence_ledger = True

    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    preview_warnings: list[PreviewWarning] = []
    completed_previews: list[str] = []
    failed_previews: list[str] = []

    if not project.is_ready():
        for error in project.errors():
            blockers.append(
                _issue(
                    "load_error",
                    "blocker",
                    error.semantic_refs,
                    error.message,
                    error.hint or "Fix semantic load errors and reload the project.",
                )
            )
        return ReadinessReport(
            status="blocked",
            analysis_ready_refs=(),
            blockers=tuple(blockers),
            warnings=(),
            input_summary=ReadinessInputSummary(
                datasources=(),
                refs=(),
                tables=(),
                decision_records=_decision_record_refs(project),
            ),
            preview_summary=PreviewSummary(
                required_previews=(),
                completed_previews=(),
                failed_previews=_dedupe(failed_raw_previews),
                warnings=(),
            ),
            parity_summary=ParitySummary(
                verified_metrics=(),
                unverified_metrics=(),
                drifted_metrics=(),
                unsupported_metrics=(),
                skipped_metrics=(),
            ),
            richness_summary=RichnessSummary(gaps=()),
            checked_at=_checked_at(),
        )

    kinds, objects = _object_maps(project)
    checked_refs, unknown_refs = _expand_checked_refs(refs, kinds, objects)
    checked_ref_set = set(checked_refs)
    table_metadata_tuple = tuple(table_metadata)
    metadata_by_dataset = _metadata_by_dataset_ref(project, table_metadata_tuple)

    for ref in unknown_refs:
        blockers.append(
            _issue(
                "unknown_ref",
                "blocker",
                (ref,),
                f"Requested semantic ref {ref!r} is not loaded in the project registry.",
                "Reload the project, fix the ref, or remove it from readiness refs.",
            )
        )

    raw_preview_set = set(raw_previews)
    raw_ref_datasets = _raw_preview_datasets_by_ref(checked_refs, objects, kinds)
    raw_preview_specs = _raw_preview_specs_by_ref(checked_refs, objects, kinds)
    scoped_datasources = _datasource_refs_for_checked_refs(checked_refs, objects, kinds)
    if require_preview:
        raw_required = _dedupe(
            required_raw_previews
            if required_raw_previews is not None
            else _dataset_raw_preview_refs(checked_refs, objects, kinds)
        )
        semantic_required = _dedupe(
            required_semantic_previews
            if required_semantic_previews is not None
            else _semantic_preview_refs(checked_refs, kinds)
        )
    else:
        raw_required = _dedupe(required_raw_previews or ())
        semantic_required = _dedupe(required_semantic_previews or ())

    if (raw_required or semantic_required) and backend_factory is None:
        preview_refs = _dedupe(tuple(raw_required) + tuple(semantic_required))
        blockers.append(
            _issue(
                "datasource_unreachable",
                "blocker",
                preview_refs,
                "Required previews need project-bound backend access; register a project datasource via md.register() first.",
                "Register a project datasource via md.register() and rerun readiness.",
            )
        )
        failed_previews.extend(preview_refs)
    elif backend_factory is not None:
        for ref in raw_required:
            spec = raw_preview_specs.get(ref)
            if spec is None:
                if ref in raw_preview_set:
                    completed_previews.append(ref)
                continue
            datasource, table, database = spec
            issue_refs = (ref, *raw_ref_datasets.get(ref, ()))
            try:
                preview = _run_raw_preview(
                    ref=ref,
                    datasource=datasource,
                    table=table,
                    database=database,
                    backend_factory=backend_factory,
                    preview_limit=preview_limit,
                )
            except Exception as exc:
                blockers.append(
                    _issue(
                        "raw_preview_failed",
                        "blocker",
                        issue_refs,
                        f"Raw preview failed for {ref}: {exc}",
                        "Fix the datasource or table reference and rerun readiness.",
                    )
                )
                failed_previews.append(ref)
            else:
                completed_previews.append(ref)
                preview_warnings.extend(preview.warnings)

        semantic_preview_run = _run_semantic_previews(
            project,
            semantic_required,
            kinds,
            objects,
            backend_factory,
            preview_limit=preview_limit,
        )
        completed_previews.extend(semantic_preview_run.completed)
        failed_previews.extend(semantic_preview_run.failed)
        blockers.extend(semantic_preview_run.blockers)
        warnings.extend(semantic_preview_run.warnings)
        preview_warnings.extend(semantic_preview_run.preview_warnings)

    verified_metrics: list[str] = []
    unverified_metrics: list[str] = []
    drifted_metrics: list[str] = []
    unsupported_metrics: list[str] = []
    skipped_metrics: list[str] = []

    reg = project._registry
    metrics = () if reg is None else tuple(reg.metrics.values())
    for metric in metrics:
        if metric.semantic_id not in checked_ref_set:
            skipped_metrics.append(metric.semantic_id)
            continue
        if (
            backend_factory is not None
            and not metric.is_derived
            and metric.provenance.verification_mode == "sql_parity"
        ):
            try:
                parity_result = project.parity_check(
                    metric.semantic_id,
                    backend_factory=backend_factory,
                    rel_tol=parity_rel_tol,
                    abs_tol=parity_abs_tol,
                    force=True,
                )
            except Exception as exc:
                unsupported_metrics.append(metric.semantic_id)
                warnings.append(
                    _issue(
                        "metric_compile_failed",
                        "warning",
                        (metric.semantic_id,),
                        f"Metric {metric.semantic_id} parity check could not run: {exc}",
                        "Fix the source_sql, datasource access, or metric definition and rerun readiness.",
                    )
                )
            else:
                if parity_result.error is not None:
                    unsupported_metrics.append(metric.semantic_id)
                    warnings.append(
                        _issue(
                            "metric_compile_failed",
                            "warning",
                            (metric.semantic_id,),
                            f"Metric {metric.semantic_id} parity check could not run: {parity_result.error.message}",
                            "Fix the source_sql, datasource access, or metric definition and rerun readiness.",
                        )
                    )
        parity_status = propagated_parity_status(project, metric.semantic_id)
        if parity_status == ParityStatus.VERIFIED:
            verified_metrics.append(metric.semantic_id)
        elif parity_status == ParityStatus.UNVERIFIED:
            unverified_metrics.append(metric.semantic_id)
            warnings.append(
                _issue(
                    "unverified_metric",
                    "warning",
                    (metric.semantic_id,),
                    f"Metric {metric.semantic_id} is unverified.",
                    "Run project.parity_check(...) or set verification_mode='python_native' when no SQL oracle exists.",
                )
            )
        elif parity_status == ParityStatus.DRIFTED:
            drifted_metrics.append(metric.semantic_id)
            warnings.append(
                _issue(
                    "parity_drifted",
                    "warning",
                    (metric.semantic_id,),
                    f"Metric {metric.semantic_id} has drifted from source SQL parity.",
                    "Compare the metric body with source_sql and fix the semantic definition or provenance.",
                )
            )

    if reg is not None:
        for datasource_ref in scoped_datasources:
            if backend_factory is None:
                continue
            try:
                backend_factory(datasource_ref)
            except Exception as exc:
                blockers.append(
                    _issue(
                        "datasource_unreachable",
                        "blocker",
                        (datasource_ref,),
                        f"Datasource {datasource_ref} is unreachable: {exc}",
                        "Fix datasource configuration or credentials and rerun readiness.",
                    )
                )

        for ref in checked_refs:
            obj = objects.get(ref)
            if obj is None:
                continue
            if (
                require_comments
                and not _has_definition(obj)
                and not _metadata_has_comment_for_ref(ref, obj, metadata_by_dataset)
            ):
                blockers.append(
                    _issue(
                        "missing_comments",
                        "blocker",
                        (ref,),
                        f"{ref} lacks description or ai_context.business_definition.",
                        "Add description or ai_context.business_definition before analysis handoff.",
                    )
                )

        for dataset in reg.datasets.values():
            if (
                dataset.semantic_id in checked_ref_set
                and dataset.primary_key
                and dataset.semantic_id not in set(primary_keys_sampled)
            ):
                warnings.append(
                    _issue(
                        "primary_key_unsampled",
                        "warning",
                        (dataset.semantic_id,),
                        f"Primary key uniqueness was not sampled for {dataset.semantic_id}.",
                        "Sample primary key uniqueness or note why uniqueness is trusted from upstream constraints.",
                    )
                )
            metadata = metadata_by_dataset.get(dataset.semantic_id)
            if dataset.semantic_id in checked_ref_set and metadata is not None and metadata.is_view:
                warnings.append(
                    _issue(
                        "derived_source_grain_unverified",
                        "warning",
                        (dataset.semantic_id,),
                        f"Entity {dataset.semantic_id} is backed by a database view; "
                        "its grain, primary key, and additivity are author-asserted and "
                        "cannot be physically verified, and the view may prevent partition "
                        "pruning on the time axis.",
                        "Confirm the row grain and additivity, verify the time dimension still "
                        "prunes (see time_dimension_pushdown_advisory), and set primary_key "
                        "deliberately rather than inheriting base-table assumptions.",
                    )
                )

        datasource_by_dataset = {
            dataset.semantic_id: dataset.datasource for dataset in reg.datasets.values()
        }
        for metric in reg.metrics.values():
            if metric.semantic_id not in checked_ref_set:
                continue
            datasources = {
                datasource_by_dataset[dataset_ref]
                for dataset_ref in metric.entities
                if dataset_ref in datasource_by_dataset
            }
            if len(datasources) > 1 and not supports_federation:
                blockers.append(
                    _issue(
                        "cross_datasource_unfederated",
                        "blocker",
                        (metric.semantic_id,),
                        f"Metric {metric.semantic_id} spans multiple datasources without federation support.",
                        "Move integration upstream, enable a federated backend, or split the metric.",
                    )
                )

    scoped_raw_sql_required_refs = _dedupe(
        ref
        for ref in (*raw_sql_required_refs, *_derive_raw_sql_required_refs(kinds, objects))
        if ref in checked_ref_set
    )

    for ref in scoped_raw_sql_required_refs:
        blockers.append(
            _issue(
                "requires_raw_sql",
                "blocker",
                (ref,),
                f"{ref} requires raw SQL to express the business logic.",
                "Represent the logic upstream or extend the semantic API before handoff.",
            )
        )

    for sw in project.warnings():
        if sw.kind in {"string_ref", "potentially_fragile_reference"}:
            warnings.append(
                _issue(
                    "fragile_string_ref",
                    "warning",
                    sw.refs,
                    sw.message,
                    "Replace fragile string refs with stable object refs where possible.",
                )
            )
        if sw.kind == "time_dimension_pushdown_advisory":
            warnings.append(
                _issue(
                    "time_dimension_pushdown_advisory",
                    "warning",
                    sw.refs,
                    sw.message,
                    "If the business axis matches the partition field, keep the raw string/integer column and declare date_format; keep the expression when business semantics require it.",
                )
            )

    if require_evidence_ledger:
        blockers.extend(_evidence_ledger_blockers(project))

    richness_report = build_richness_report(project, demand=demand)
    scoped_richness_gaps = tuple(
        gap for gap in richness_report.gaps if set(gap.refs) & checked_ref_set
    )
    warnings.extend(_richness_warnings(scoped_richness_gaps))

    blocked_refs = _refs_with_issue(blockers)
    analysis_ready_refs = tuple(ref for ref in checked_refs if ref not in blocked_refs)

    preview_summary = PreviewSummary(
        required_previews=_dedupe(tuple(raw_required) + tuple(semantic_required)),
        completed_previews=_dedupe(completed_previews),
        failed_previews=_dedupe(failed_previews),
        warnings=tuple(preview_warnings),
    )
    parity_summary = ParitySummary(
        verified_metrics=_dedupe(verified_metrics),
        unverified_metrics=_dedupe(unverified_metrics),
        drifted_metrics=_dedupe(drifted_metrics),
        unsupported_metrics=_dedupe(unsupported_metrics),
        skipped_metrics=_dedupe(skipped_metrics),
    )
    datasources_checked: tuple[str, ...] = (
        scoped_datasources if reg is not None and backend_factory is not None else ()
    )

    return ReadinessReport(
        status=_status(blockers, warnings),
        analysis_ready_refs=analysis_ready_refs,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        input_summary=ReadinessInputSummary(
            datasources=datasources_checked,
            refs=checked_refs,
            tables=_dedupe(tuple(metadata_by_dataset.keys()) + _dataset_refs(checked_refs, kinds)),
            decision_records=_decision_record_summary(project, checked_refs),
        ),
        preview_summary=preview_summary,
        parity_summary=parity_summary,
        richness_summary=RichnessSummary(
            gaps=tuple(_richness_gap_label(gap) for gap in scoped_richness_gaps)
        ),
        checked_at=_checked_at(),
    )
