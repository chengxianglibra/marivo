"""Semantic readiness report DTOs and structural readiness construction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from marivo.datasource.authoring import DatasourceRef
from marivo.render import Card, RenderableResult

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

    def to_dict(self) -> dict[str, object]:
        return {
            "datasources": list(self.datasources),
            "refs": list(self.refs),
            "tables": list(self.tables),
        }


@dataclass(frozen=True, repr=False)
class ReadinessReport(RenderableResult):
    status: ReadinessStatus
    analysis_ready_refs: tuple[str, ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    checked_at: str

    def _repr_identity(self) -> str:
        return (
            f"ReadinessReport status={self.status} issues={len(self.blockers) + len(self.warnings)}"
        )

    def _card(self) -> Card:
        card = Card(identity=self._repr_identity(), available=(".render()", ".to_dict()"))
        if self.blockers:
            card = card.listing(
                label=f"blockers ({len(self.blockers)})",
                items=tuple(
                    f"{i.kind}: {i.message} -> fix: {i.suggested_action}" for i in self.blockers
                ),
            )
        if self.warnings:
            card = card.listing(
                label=f"warnings ({len(self.warnings)})",
                items=tuple(
                    f"{i.kind}: {i.message} -> fix: {i.suggested_action}" for i in self.warnings
                ),
            )
        if self.analysis_ready_refs:
            card = card.field(label="analysis_ready", value=", ".join(self.analysis_ready_refs))
        if self.status == "ready_with_warnings":
            card = card.field(
                label="handoff",
                value="ready_with_warnings: warnings are non-blocking; proceed to marivo.analysis only if accepted",
            )
        return card.field(label="checked_at", value=self.checked_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "analysis_ready_refs": list(self.analysis_ready_refs),
            "blockers": [issue.to_dict() for issue in self.blockers],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "input_summary": self.input_summary.to_dict(),
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
        datasource_ref = DatasourceRef.from_id(ds_ir.semantic_id).id
        kinds[datasource_ref] = _SemanticKind.DATASOURCE
        objects[datasource_ref] = ds_ir

    return kinds, objects


def _strict_enrichment_issues(
    checked_refs: Iterable[str],
    kinds: Mapping[str, _SemanticKind],
    objects: Mapping[str, object],
) -> tuple[list[ReadinessIssue], list[ReadinessIssue]]:
    """Contracts section 7: analyzable handoff refs must carry a non-empty
    business_definition (blocker) and guardrails (blocker for metrics, warning
    for other analyzable refs). Richness owns optional enrichment suggestions.
    Relationships are out of scope, matching semantic-preview scoping."""
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
        kind = kinds.get(ref)
        if kind not in analyzable:
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
                    "Add ai_context=ms.ai_context(business_definition=...) so analysis can match and reuse this ref.",
                )
            )
            # business_definition missing implies guardrails missing too; report
            # the single most fundamental issue rather than stacking findings.
            continue
        if _missing_guardrails(obj):
            if kind == _SemanticKind.METRIC:
                blockers.append(
                    _issue(
                        "missing_guardrails",
                        "blocker",
                        (ref,),
                        f"{ref} has no ai_context.guardrails; metrics are the central analysis unit and must declare usage constraints.",
                        "Add ai_context=ms.ai_context(guardrails=[...]) describing how this metric may and may not be used.",
                    )
                )
            else:
                warnings.append(
                    _issue(
                        "missing_guardrails",
                        "warning",
                        (ref,),
                        f"{ref} has no ai_context.guardrails; analysis may proceed but the agent lacks usage constraints.",
                        "Add ai_context=ms.ai_context(guardrails=[...]) to make safe usage explicit.",
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
        key_refs = tuple(ref for key in keys for ref in key.to_tuple())
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
    guardrails = getattr(ai_context, "guardrails", None)
    return not guardrails


def build_structural_readiness_report(
    project: SemanticProject,
    *,
    refs: Iterable[str] | None = None,
) -> ReadinessReport:
    """Build a structural readiness report without backend access.

    Performs pure in-memory checks: load errors, unknown refs,
    cross-datasource unfederated metrics, raw SQL requirements,
    strict enrichment issues, and load warnings forwarding.
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
            ),
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
                "Browse loaded refs with catalog.domains.show(), catalog.metrics.show(), etc., inspect a known ref with catalog.get(...).details().show(), then fix or remove the ref from readiness refs.",
            )
        )

    # Strict enrichment: missing business_definition is a blocker;
    # missing guardrails is a blocker for metrics, a warning otherwise.
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
                    f"Run ms.parity_check({ref!r}) when parity matters, or report the warning as non-blocking when analysis handoff allows it.",
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
        ),
        checked_at=_checked_at(),
    )
