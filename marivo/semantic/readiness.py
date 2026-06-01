"""Semantic readiness report DTOs and report construction."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from marivo.preview import PreviewResult, PreviewWarning
from marivo.semantic.ir import ParityStatus, TableSourceIR
from marivo.semantic.parity import propagated_parity_status

if TYPE_CHECKING:
    from marivo.analysis.datasources.metadata import TableMetadata
    from marivo.semantic.reader import SemanticProject

ReadinessStatus = Literal["ready", "ready_with_warnings", "blocked"]
ReadinessSeverity = Literal["blocker", "warning"]
ReadinessIssueKind = Literal[
    "load_error",
    "datasource_unreachable",
    "missing_schema",
    "missing_comments",
    "missing_raw_preview",
    "raw_preview_failed",
    "dataset_preview_failed",
    "field_preview_failed",
    "missing_knowledge_definition",
    "ambiguous_time_axis",
    "time_field_preview_failed",
    "metric_materialize_failed",
    "metric_compile_failed",
    "unverified_metric",
    "parity_drifted",
    "relationship_unconfirmed",
    "sensitive_preview_column",
    "cross_datasource_unfederated",
    "requires_raw_sql",
    "primary_key_unsampled",
    "fragile_string_ref",
    "time_field_pushdown_advisory",
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
class EvidenceSummary:
    datasources_checked: tuple[str, ...]
    tables_inspected: tuple[str, ...]
    raw_previews: tuple[str, ...]
    knowledge_documents: tuple[str, ...]
    user_confirmations: tuple[str, ...]
    semantic_objects_changed: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "datasources_checked": list(self.datasources_checked),
            "tables_inspected": list(self.tables_inspected),
            "raw_previews": list(self.raw_previews),
            "knowledge_documents": list(self.knowledge_documents),
            "user_confirmations": list(self.user_confirmations),
            "semantic_objects_changed": list(self.semantic_objects_changed),
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
    python_native_metrics: tuple[str, ...]
    unverified_metrics: tuple[str, ...]
    drifted_metrics: tuple[str, ...]
    skipped_metrics: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "verified_metrics": list(self.verified_metrics),
            "python_native_metrics": list(self.python_native_metrics),
            "unverified_metrics": list(self.unverified_metrics),
            "drifted_metrics": list(self.drifted_metrics),
            "skipped_metrics": list(self.skipped_metrics),
        }


@dataclass(frozen=True)
class ReadinessReport:
    status: ReadinessStatus
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    evidence_summary: EvidenceSummary
    preview_summary: PreviewSummary
    parity_summary: ParitySummary
    checked_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "analysis_ready_refs": list(self.analysis_ready_refs),
            "blockers": [issue.to_dict() for issue in self.blockers],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "evidence_summary": self.evidence_summary.to_dict(),
            "preview_summary": self.preview_summary.to_dict(),
            "parity_summary": self.parity_summary.to_dict(),
            "checked_at": self.checked_at,
        }


class _SemanticKind(StrEnum):
    DATASET = "dataset"
    FIELD = "field"
    TIME_FIELD = "time_field"
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


def _object_maps(project: SemanticProject) -> tuple[dict[str, _SemanticKind], dict[str, object]]:
    reg = project.registry()
    if reg is None:
        return {}, {}

    kinds: dict[str, _SemanticKind] = {}
    objects: dict[str, object] = {}

    for dataset in reg.datasets.values():
        kinds[dataset.semantic_id] = _SemanticKind.DATASET
        objects[dataset.semantic_id] = dataset
    for field in reg.fields.values():
        kind = _SemanticKind.TIME_FIELD if field.is_time_field else _SemanticKind.FIELD
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
    _SemanticKind.TIME_FIELD: "time_field_identity",
    _SemanticKind.METRIC: "metric_decomposition",
}


def _evidence_ledger_blockers(project: SemanticProject) -> list[ReadinessIssue]:
    """Dangerous-kind authored objects with no backing ledger decision -> blockers.
    Mapping: time_field -> time_field_identity, metric -> metric_decomposition."""
    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.root_path)
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
                    f"Record a {required} decision via the authoring loop (open_questions + answer) before handoff.",
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
        _SemanticKind.DATASET,
        _SemanticKind.FIELD,
        _SemanticKind.TIME_FIELD,
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
            _SemanticKind.DATASET,
            _SemanticKind.FIELD,
            _SemanticKind.TIME_FIELD,
            _SemanticKind.METRIC,
        }
    )


def _dataset_refs(refs: Iterable[str], kinds: Mapping[str, _SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in refs if kinds.get(ref) == _SemanticKind.DATASET)


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
        if kinds.get(ref) != _SemanticKind.DATASET:
            continue
        dataset = objects.get(ref)
        source = getattr(dataset, "source", None)
        datasource = getattr(dataset, "datasource", None)
        if isinstance(source, TableSourceIR) and isinstance(datasource, str):
            raw_refs.append(_raw_preview_ref(datasource, source.table, source.database))
        else:
            raw_refs.append(ref)
    return tuple(raw_refs)


def _raw_preview_datasets_by_ref(
    refs: Iterable[str],
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {}
    for ref in refs:
        if kinds.get(ref) != _SemanticKind.DATASET:
            continue
        dataset = objects.get(ref)
        source = getattr(dataset, "source", None)
        datasource = getattr(dataset, "datasource", None)
        if isinstance(source, TableSourceIR) and isinstance(datasource, str):
            raw_ref = _raw_preview_ref(datasource, source.table, source.database)
            out.setdefault(raw_ref, []).append(ref)
    return {key: tuple(values) for key, values in out.items()}


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
    reg = project.registry()
    if reg is None:
        return {}
    by_key = {(metadata.datasource, metadata.table): metadata for metadata in table_metadata}
    out: dict[str, TableMetadata] = {}
    for dataset in reg.datasets.values():
        metadata = by_key.get((dataset.datasource, dataset.name))
        if metadata is not None:
            out[dataset.semantic_id] = metadata
    return out


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
    dataset_ref = getattr(obj, "dataset", None)
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
    if kind == _SemanticKind.DATASET:
        return "dataset_preview_failed"
    if kind == _SemanticKind.TIME_FIELD:
        return "time_field_preview_failed"
    if kind == _SemanticKind.FIELD:
        return "field_preview_failed"
    return "metric_materialize_failed"


def _run_preview(
    project: SemanticProject, ref: str, kind: _SemanticKind, backend_factory: Callable[[str], Any]
) -> PreviewResult:
    if kind == _SemanticKind.DATASET:
        return project.preview_dataset(ref, backend_factory=backend_factory)
    if kind in {_SemanticKind.FIELD, _SemanticKind.TIME_FIELD}:
        return project.preview_field(ref, backend_factory=backend_factory)
    if kind == _SemanticKind.METRIC:
        return project.preview_metric(ref, backend_factory=backend_factory)
    raise ValueError(f"cannot preview semantic kind {kind}")


def build_readiness_report(
    project: SemanticProject,
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
            evidence_summary=EvidenceSummary(
                datasources_checked=(),
                tables_inspected=(),
                raw_previews=_dedupe(raw_previews),
                knowledge_documents=_dedupe(knowledge_documents),
                user_confirmations=_dedupe(user_confirmations),
                semantic_objects_changed=(),
            ),
            preview_summary=PreviewSummary(
                required_previews=(),
                completed_previews=(),
                failed_previews=_dedupe(failed_raw_previews),
                warnings=(),
            ),
            parity_summary=ParitySummary(
                verified_metrics=(),
                python_native_metrics=(),
                unverified_metrics=(),
                drifted_metrics=(),
                skipped_metrics=(),
            ),
            checked_at=_checked_at(),
        )

    kinds, objects = _object_maps(project)
    checked_refs = _dedupe(refs if refs is not None else _default_checked_refs(kinds))
    checked_ref_set = set(checked_refs)
    table_metadata_tuple = tuple(table_metadata)
    metadata_by_dataset = _metadata_by_dataset_ref(project, table_metadata_tuple)

    raw_preview_set = set(raw_previews)
    failed_raw_preview_set = set(failed_raw_previews)
    raw_ref_datasets = _raw_preview_datasets_by_ref(checked_refs, objects, kinds)
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

    for ref in raw_required:
        issue_refs = (ref, *raw_ref_datasets.get(ref, ()))
        if ref in failed_raw_preview_set:
            blockers.append(
                _issue(
                    "raw_preview_failed",
                    "blocker",
                    issue_refs,
                    f"Raw preview failed for {ref}.",
                    "Run project.collect_source_preview(...) with a bounded limit and fix the datasource or table reference.",
                )
            )
            failed_previews.append(ref)
        elif ref in raw_preview_set:
            completed_previews.append(ref)
        else:
            blockers.append(
                _issue(
                    "missing_raw_preview",
                    "blocker",
                    issue_refs,
                    f"Raw preview evidence is missing for {ref}.",
                    "Collect a bounded raw table preview with project.collect_source_preview(...).",
                )
            )

    if semantic_required and backend_factory is None:
        blockers.append(
            _issue(
                "datasource_unreachable",
                "blocker",
                semantic_required,
                "Semantic preview requires backend_factory but none was provided.",
                "Pass backend_factory=lambda name: mv.datasources.build_backend(name).",
            )
        )
        failed_previews.extend(semantic_required)
    elif backend_factory is not None:
        for ref in semantic_required:
            kind = kinds.get(ref)
            if kind is None or kind == _SemanticKind.RELATIONSHIP:
                continue
            try:
                preview = _run_preview(project, ref, kind, backend_factory)
            except Exception as exc:
                blockers.append(
                    _issue(
                        _preview_issue_kind(kind),
                        "blocker",
                        (ref,),
                        f"Semantic preview failed for {ref}: {exc}",
                        "Fix the semantic object and rerun the bounded preview.",
                    )
                )
                failed_previews.append(ref)
            else:
                completed_previews.append(ref)
                preview_warnings.extend(preview.warnings)
                for warning in preview.warnings:
                    if warning.kind == "redacted_column":
                        warnings.append(
                            _issue(
                                "sensitive_preview_column",
                                "warning",
                                (ref,),
                                f"Preview for {ref} redacted sensitive columns: {', '.join(warning.columns)}.",
                                "Keep preview rows out of semantic definitions and avoid exposing sensitive values in handoff notes.",
                            )
                        )

    verified_metrics: list[str] = []
    python_native_metrics: list[str] = []
    unverified_metrics: list[str] = []
    drifted_metrics: list[str] = []
    skipped_metrics: list[str] = []

    reg = project.registry()
    metrics = () if reg is None else tuple(reg.metrics.values())
    for metric in metrics:
        if metric.semantic_id not in checked_ref_set:
            skipped_metrics.append(metric.semantic_id)
            continue
        parity_status = propagated_parity_status(project, metric.semantic_id)
        if parity_status == ParityStatus.VERIFIED:
            verified_metrics.append(metric.semantic_id)
        elif parity_status == ParityStatus.PYTHON_NATIVE:
            python_native_metrics.append(metric.semantic_id)
            warnings.append(
                _issue(
                    "unverified_metric",
                    "warning",
                    (metric.semantic_id,),
                    f"Metric {metric.semantic_id} is declared python_native and has no SQL parity oracle.",
                    "Keep declared_status='python_native' only when the user accepts Python-native provenance.",
                )
            )
        elif parity_status == ParityStatus.UNVERIFIED:
            unverified_metrics.append(metric.semantic_id)
            severity: ReadinessSeverity = "blocker" if strict_provenance else "warning"
            issue_target = blockers if strict_provenance else warnings
            issue_target.append(
                _issue(
                    "unverified_metric",
                    severity,
                    (metric.semantic_id,),
                    f"Metric {metric.semantic_id} is unverified.",
                    "Run project.parity_check(...) or explicitly declare python_native when no SQL oracle exists.",
                )
            )
        elif parity_status == ParityStatus.DRIFTED:
            drifted_metrics.append(metric.semantic_id)
            blockers.append(
                _issue(
                    "parity_drifted",
                    "blocker",
                    (metric.semantic_id,),
                    f"Metric {metric.semantic_id} has drifted from source SQL parity.",
                    "Compare the metric body with source_sql and fix the semantic definition or provenance.",
                )
            )

    if reg is not None:
        for datasource in reg.datasources.values():
            if backend_factory is None:
                continue
            try:
                backend_factory(datasource.semantic_id)
            except Exception as exc:
                blockers.append(
                    _issue(
                        "datasource_unreachable",
                        "blocker",
                        (datasource.semantic_id,),
                        f"Datasource {datasource.semantic_id} is unreachable: {exc}",
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

        for relationship in reg.relationships.values():
            if relationship.semantic_id in checked_ref_set and relationship.semantic_id not in set(
                confirmed_relationships
            ):
                blockers.append(
                    _issue(
                        "relationship_unconfirmed",
                        "blocker",
                        (relationship.semantic_id,),
                        f"Relationship {relationship.semantic_id} has not been confirmed.",
                        "Confirm join keys with metadata, preview evidence, or the user.",
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
                for dataset_ref in metric.datasets
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

    for ref in raw_sql_required_refs:
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
        if sw.kind == "time_field_pushdown_advisory":
            warnings.append(
                _issue(
                    "time_field_pushdown_advisory",
                    "warning",
                    sw.refs,
                    sw.message,
                    "If the business axis matches the partition field, keep the raw string/integer column and declare date_format; keep the expression when business semantics require it.",
                )
            )

    if require_evidence_ledger:
        blockers.extend(_evidence_ledger_blockers(project))

    if strict_enrichment:
        se_blockers, se_warnings = _strict_enrichment_issues(checked_refs, kinds, objects)
        blockers.extend(se_blockers)
        warnings.extend(se_warnings)

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
        python_native_metrics=_dedupe(python_native_metrics),
        unverified_metrics=_dedupe(unverified_metrics),
        drifted_metrics=_dedupe(drifted_metrics),
        skipped_metrics=_dedupe(skipped_metrics),
    )
    datasources_checked: tuple[str, ...] = ()
    if reg is not None:
        datasources_checked = tuple(
            datasource.semantic_id
            for datasource in reg.datasources.values()
            if backend_factory is not None
        )

    return ReadinessReport(
        status=_status(blockers, warnings),
        analysis_ready_refs=analysis_ready_refs,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        evidence_summary=EvidenceSummary(
            datasources_checked=datasources_checked,
            tables_inspected=_dedupe(
                tuple(metadata_by_dataset.keys()) + _dataset_refs(checked_refs, kinds)
            ),
            raw_previews=_dedupe(raw_previews),
            knowledge_documents=_dedupe(knowledge_documents),
            user_confirmations=_dedupe(user_confirmations),
            semantic_objects_changed=checked_refs,
        ),
        preview_summary=preview_summary,
        parity_summary=parity_summary,
        checked_at=_checked_at(),
    )
