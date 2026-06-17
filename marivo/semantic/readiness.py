"""Semantic readiness report DTOs and structural readiness construction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject

ReadinessStatus = Literal["ready", "ready_with_warnings", "blocked"]
ReadinessSeverity = Literal["blocker", "warning"]
ReadinessIssueKind = Literal[
    "load_error",
    "unknown_ref",
    "cross_datasource_unfederated",
    "sql_parity_unverified",
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
class ReadinessReport:
    status: ReadinessStatus
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    checked_at: str
    abandoned: tuple[Any, ...] = ()

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
        if self.abandoned:
            lines.append(f"abandoned ({len(self.abandoned)}):")
            for candidate in self.abandoned[:3]:
                lines.append(f"  - {candidate.candidate}")
            if len(self.abandoned) > 3:
                lines.append(f"  ... {len(self.abandoned) - 3} more; call .to_dict() for full list")
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
            "abandoned": [c.to_dict() for c in self.abandoned],
            "checked_at": self.checked_at,
        }


class _SemanticKind(StrEnum):
    DOMAIN = "domain"
    DATASOURCE = "datasource"
    ENTITY = "entity"
    DIMENSION = "dimension"
    MEASURE = "measure"
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

    store = LedgerStore(project.state_root)
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


def _parity_passed(project: SemanticProject, ref: str) -> bool:
    """Check whether a metric with SQL provenance has passed parity verification."""
    parity_result = project._parity_results.get(ref)
    return parity_result is not None and parity_result.ok


def _object_maps(project: SemanticProject) -> tuple[dict[str, _SemanticKind], dict[str, object]]:
    reg = project._registry
    if reg is None:
        return {}, {}

    kinds: dict[str, _SemanticKind] = {}
    objects: dict[str, object] = {}

    for entity in reg.entities.values():
        kinds[entity.semantic_id] = _SemanticKind.ENTITY
        objects[entity.semantic_id] = entity
    for dim in reg.dimensions.values():
        kind = _SemanticKind.TIME_DIMENSION if dim.is_time_dimension else _SemanticKind.DIMENSION
        kinds[dim.semantic_id] = kind
        objects[dim.semantic_id] = dim
    for measure in reg.measures.values():
        kinds[measure.semantic_id] = _SemanticKind.MEASURE
        objects[measure.semantic_id] = measure
    for metric in reg.metrics.values():
        kinds[metric.semantic_id] = _SemanticKind.METRIC
        objects[metric.semantic_id] = metric
    for relationship in reg.relationships.values():
        kinds[relationship.semantic_id] = _SemanticKind.RELATIONSHIP
        objects[relationship.semantic_id] = relationship
    for domain_ir in reg.domains.values():
        kinds[domain_ir.name] = _SemanticKind.DOMAIN
        objects[domain_ir.name] = domain_ir
    for ds_ir in project._datasource_irs or reg.datasources.values():
        kinds[ds_ir.semantic_id] = _SemanticKind.DATASOURCE
        objects[ds_ir.semantic_id] = ds_ir

    return kinds, objects


_REQUIRED_DECISION_BY_KIND = {
    _SemanticKind.TIME_DIMENSION: "time_dimension_identity",
    _SemanticKind.METRIC: "metric_composition",
}


def _evidence_ledger_blockers(project: SemanticProject) -> list[ReadinessIssue]:
    """Dangerous-kind authored objects with no backing ledger decision -> blockers.
    Mapping: time_dimension -> time_dimension_identity, metric -> metric_composition."""
    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.state_root)
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
        _SemanticKind.MEASURE,
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


_CONTAINER_KINDS = frozenset(
    {_SemanticKind.RELATIONSHIP, _SemanticKind.DOMAIN, _SemanticKind.DATASOURCE}
)


def _default_checked_refs(kinds: Mapping[str, _SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in kinds if kinds[ref] not in _CONTAINER_KINDS) + tuple(
        ref for ref in kinds if kinds[ref] in _CONTAINER_KINDS
    )


def _dependencies_for_ref(
    ref: str,
    objects: Mapping[str, object],
    kinds: Mapping[str, _SemanticKind],
) -> tuple[str, ...]:
    kind = kinds.get(ref)
    obj = objects.get(ref)
    if obj is None:
        return ()
    if kind == _SemanticKind.DOMAIN:
        return tuple(
            obj_id
            for obj_id, other in objects.items()
            if kinds.get(obj_id) == _SemanticKind.ENTITY and getattr(other, "domain", None) == ref
        )
    if kind == _SemanticKind.DATASOURCE:
        return tuple(
            obj_id
            for obj_id, other in objects.items()
            if kinds.get(obj_id) == _SemanticKind.ENTITY
            and getattr(other, "datasource", None) == ref
        )
    if kind in {_SemanticKind.DIMENSION, _SemanticKind.TIME_DIMENSION}:
        entity = getattr(obj, "entity", None)
        return (entity,) if isinstance(entity, str) else ()
    if kind == _SemanticKind.MEASURE:
        entity = getattr(obj, "entity", None)
        return (entity,) if isinstance(entity, str) else ()
    if kind == _SemanticKind.METRIC:
        deps: list[str] = []
        deps.extend(getattr(obj, "entities", ()))
        composition = getattr(obj, "composition", None)
        if composition is not None:
            from marivo.semantic.ir import composition_components

            components = composition_components(composition)
            deps.extend(str(value) for value in components.values())
        return tuple(deps)
    if kind == _SemanticKind.RELATIONSHIP:
        keys = getattr(obj, "keys", ())
        key_refs = (*(k.from_key for k in keys), *(k.to_key for k in keys)) if keys else ()
        relationship_deps = (
            getattr(obj, "from_entity", None),
            getattr(obj, "to_entity", None),
            *key_refs,
            *getattr(obj, "from_keys", ()),
            *getattr(obj, "to_keys", ()),
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


def _dataset_refs(refs: Iterable[str], kinds: Mapping[str, _SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in refs if kinds.get(ref) == _SemanticKind.ENTITY)


def _refs_with_issue(issues: Iterable[ReadinessIssue]) -> set[str]:
    return {ref for issue in issues for ref in issue.refs}


def _missing_business_definition(obj: object) -> bool:
    ai_context = getattr(obj, "ai_context", None)
    business_definition = getattr(ai_context, "business_definition", None)
    return not (business_definition and business_definition.strip())


def _missing_guardrails(obj: object) -> bool:
    ai_context = getattr(obj, "ai_context", None)
    guardrails = getattr(ai_context, "guardrails", ())
    return not guardrails


def _decision_record_refs(project: SemanticProject) -> tuple[str, ...]:
    from marivo.semantic.ledger import LedgerStore

    refs: list[str] = []
    for record in LedgerStore(project.state_root).iter_object_records():
        refs.extend(
            f"{record.semantic_id}:{decision.decision_kind}" for decision in record.decisions
        )
    return _dedupe(refs)


def _abandoned_candidates(project: SemanticProject) -> tuple[Any, ...]:
    """Return authoring-abandoned rejected candidates from the project ledger."""
    from marivo.semantic.ledger import LedgerStore

    store = LedgerStore(project.state_root)
    return tuple(
        c for c in store.list_rejected_candidates() if c.decision_kind == "authoring_abandoned"
    )


def build_structural_readiness_report(
    project: SemanticProject,
    *,
    refs: Iterable[str] | None = None,
) -> ReadinessReport:
    """Build a structural readiness report without backend access.

    Performs pure in-memory checks: load errors, unknown refs, evidence
    ledger blockers, cross-datasource unfederated metrics, raw SQL
    requirements, strict enrichment issues, and load warnings forwarding.
    Does not require or use any datasource connection.

    Args:
        project: A loaded SemanticProject instance.
        refs: Semantic refs to scope the check. None checks all loaded objects.

    Returns:
        ReadinessReport indicating structural readiness for analysis handoff.
    """
    # Defensive normalization: ensure all refs are plain strings so that
    # downstream code (dict key lookups, .split() calls) works correctly
    # even if callers pass SemanticRef objects.
    if refs is not None:
        refs = [str(r) for r in refs]

    require_evidence_ledger = True

    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []

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
            abandoned=_abandoned_candidates(project),
            checked_at=_checked_at(),
        )

    kinds, objects = _object_maps(project)
    checked_refs, unknown_refs = _expand_checked_refs(refs, kinds, objects)
    checked_ref_set = set(checked_refs)
    scoped_datasources = _datasource_refs_for_checked_refs(checked_refs, objects, kinds)

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

    # Strict enrichment: missing business_definition is a blocker, missing guardrails is a warning.
    enrichment_blockers, enrichment_warnings = _strict_enrichment_issues(
        checked_refs,
        kinds,
        objects,
    )
    blockers.extend(enrichment_blockers)
    warnings.extend(enrichment_warnings)

    # Cross-datasource unfederated metrics.
    reg = project._registry
    if reg is not None:
        datasource_by_dataset = {
            dataset.semantic_id: dataset.datasource for dataset in reg.entities.values()
        }
        for metric in reg.metrics.values():
            if metric.semantic_id not in checked_ref_set:
                continue
            metric_datasources = {
                datasource_by_dataset[dataset_ref]
                for dataset_ref in metric.entities
                if dataset_ref in datasource_by_dataset
            }
            if len(metric_datasources) > 1:
                blockers.append(
                    _issue(
                        "cross_datasource_unfederated",
                        "blocker",
                        (metric.semantic_id,),
                        f"Metric {metric.semantic_id} spans multiple datasources without federation support.",
                        "Move integration upstream, enable a federated backend, or split the metric.",
                    )
                )

    # SQL parity unverified warnings.
    for ref in checked_refs:
        if kinds.get(ref) != _SemanticKind.METRIC:
            continue
        obj = objects.get(ref)
        if obj is None:
            continue
        prov = getattr(obj, "provenance", None)
        if prov is None:
            continue
        provenance_sql = prov.sql
        if provenance_sql is None:
            continue
        if not _parity_passed(project, ref):
            warnings.append(
                _issue(
                    "sql_parity_unverified",
                    "warning",
                    (ref,),
                    f"{ref} has provenance SQL but parity has not been confirmed.",
                    f"Run ms.parity_check({ref!r}) to verify.",
                )
            )

    # Forward load warnings as readiness warnings.
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

    # Evidence ledger blockers.
    if require_evidence_ledger:
        blockers.extend(_evidence_ledger_blockers(project))

    blocked_refs = _refs_with_issue(blockers)
    analysis_ready_refs = tuple(ref for ref in checked_refs if ref not in blocked_refs)

    datasources_checked: tuple[str, ...] = scoped_datasources if reg is not None else ()

    return ReadinessReport(
        status=_status(blockers, warnings),
        analysis_ready_refs=analysis_ready_refs,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        input_summary=ReadinessInputSummary(
            datasources=datasources_checked,
            refs=checked_refs,
            tables=_dataset_refs(checked_refs, kinds),
            decision_records=_decision_record_summary(project, checked_refs),
        ),
        abandoned=_abandoned_candidates(project),
        checked_at=_checked_at(),
    )
