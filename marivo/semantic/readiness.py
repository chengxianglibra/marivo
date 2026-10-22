"""Semantic readiness report DTOs and query-free evidence gating."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from marivo._authoring.model import AuthoringRepair
from marivo._authoring.render import _repair_summary
from marivo._compat import UTC
from marivo._symbolic import LookupSymbol, symbol_from_ref
from marivo.refs import Ref, RefPayloadV1, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.render import Card, RenderableResult
from marivo.semantic.errors import repair

if TYPE_CHECKING:
    from marivo.semantic.reader import SemanticProject

ReadinessStatus = Literal["ready", "ready_with_warnings", "blocked"]
ReadinessSeverity = Literal["blocker", "warning", "advisory"]
ReadinessIssueKind = Literal[
    "load_error",
    "unknown_ref",
    "cross_datasource_unfederated",
    "fragile_string_ref",
    "time_dimension_pushdown_advisory",
    "period_calendar_artifact_missing",
    "period_calendar_artifact_stale",
    "period_calendar_artifact_invalid",
    "temporal_set_artifact_missing",
    "temporal_set_artifact_stale",
    "temporal_set_artifact_invalid",
]


@dataclass(frozen=True)
class ReadinessIssue:
    kind: ReadinessIssueKind
    severity: ReadinessSeverity
    refs: tuple[str, ...]
    message: str
    repair: AuthoringRepair | None = None
    details: Mapping[str, object] = field(default_factory=dict)
    catalog_definition_fingerprint: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "severity": self.severity,
            "refs": list(self.refs),
            "message": self.message,
            "repair": self.repair.model_dump() if self.repair is not None else None,
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


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


def _issue_summary(issue: ReadinessIssue) -> str:
    summary = f"{issue.kind}: {issue.message}"
    if issue.refs:
        summary += f"; affected refs: {', '.join(issue.refs)}"
    if issue.repair is not None:
        summary += " -> " + _repair_summary(issue.repair, optional=issue.severity == "advisory")
    return summary


@dataclass(frozen=True, repr=False)
class ReadinessReport(RenderableResult):
    scope: ClassVar[Literal["semantic_static"]] = "semantic_static"
    status: ReadinessStatus
    _analysis_ready_inputs: tuple[Ref[SemanticKindTag], ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    checked_at: str
    catalog_definition_fingerprint: str | None = None

    @property
    def analysis_ready_inputs(self) -> tuple[LookupSymbol, ...]:
        """Native lookup selectors for definitions ready for analysis."""
        return tuple(symbol_from_ref(ref) for ref in self._analysis_ready_inputs)

    def _repr_identity(self) -> str:
        return (
            f"ReadinessReport scope={self.scope} status={self.status} "
            f"issues={len(self.blockers) + len(self.warnings)}"
        )

    def _card(self) -> Card:
        card = Card(
            identity=self._repr_identity(),
            available=(
                ".show()",
                ".to_dict()",
                ".analysis_ready_inputs",
                ".input_summary",
                ".blockers",
                ".warnings",
            ),
        )
        card = card.field(label="scope", value=self.scope)
        if self.blockers:
            card = card.listing(
                label=f"blockers ({len(self.blockers)})",
                items=(_issue_summary(issue) for issue in self.blockers),
            )
        actual_warnings = tuple(issue for issue in self.warnings if issue.severity == "warning")
        advisories = tuple(issue for issue in self.warnings if issue.severity == "advisory")
        if actual_warnings:
            card = card.listing(
                label=f"warnings ({len(actual_warnings)})",
                items=(_issue_summary(issue) for issue in actual_warnings),
            )
        if advisories:
            card = card.listing(
                label=f"advisories ({len(advisories)})",
                items=(_issue_summary(issue) for issue in advisories),
            )
        ready_refs = self._analysis_ready_inputs
        if ready_refs:
            card = card.field(
                label="analysis_ready",
                value=", ".join(ref.key for ref in ready_refs),
            )
        card = card.listing("checked refs", self.input_summary.refs)
        if self.input_summary.datasources:
            card = card.listing("checked datasources", self.input_summary.datasources)
        if self.input_summary.tables:
            card = card.listing("checked tables", self.input_summary.tables)
        return card.field(label="checked_at", value=self.checked_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "status": self.status,
            "analysis_ready_inputs": [
                RefPayloadV1.from_ref(item).to_dict() for item in self._analysis_ready_inputs
            ],
            "blockers": [issue.to_dict() for issue in self.blockers],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "input_summary": self.input_summary.to_dict(),
            "checked_at": self.checked_at,
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
        }


def _exact_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    factory = {
        SemanticKind.DOMAIN: ref_factory.domain,
        SemanticKind.DATASOURCE: ref_factory.datasource,
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
        SemanticKind.PERIOD_CALENDAR: ref_factory.period_calendar,
        SemanticKind.TEMPORAL_SET: ref_factory.temporal_set,
    }[kind]
    return factory(path)


def _exact_key(path: str, kind: SemanticKind) -> str:
    return _exact_ref(path, kind).key


def _display_path(ref_key: str) -> str:
    prefix, separator, path = ref_key.partition(":")
    if separator and prefix in {kind.value for kind in SemanticKind}:
        return path
    return ref_key


_EXECUTABLE_KINDS = frozenset(
    {
        SemanticKind.ENTITY,
        SemanticKind.DIMENSION,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.MEASURE,
        SemanticKind.RELATIONSHIP,
    }
)


def _checked_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _status(blockers: list[ReadinessIssue], warnings: list[ReadinessIssue]) -> ReadinessStatus:
    if blockers:
        return "blocked"
    if any(issue.severity == "warning" for issue in warnings):
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
    repair: AuthoringRepair | None = None,
    *,
    details: Mapping[str, object] | None = None,
) -> ReadinessIssue:
    return ReadinessIssue(
        kind=kind,
        severity=severity,
        refs=_dedupe(refs),
        message=message,
        repair=repair,
        details={} if details is None else details,
    )


def _object_maps(project: SemanticProject) -> tuple[dict[str, SemanticKind], dict[str, object]]:
    reg = project._registry
    if reg is None:
        return {}, {}

    kinds: dict[str, SemanticKind] = {}
    objects: dict[str, object] = {}

    for entity in reg.entities.values():
        key = _exact_key(entity.semantic_id, SemanticKind.ENTITY)
        kinds[key] = SemanticKind.ENTITY
        objects[key] = entity
    for dim in reg.dimensions.values():
        kind = SemanticKind.TIME_DIMENSION if dim.is_time_dimension else SemanticKind.DIMENSION
        key = _exact_key(dim.semantic_id, kind)
        kinds[key] = kind
        objects[key] = dim
    for measure in reg.measures.values():
        key = _exact_key(measure.semantic_id, SemanticKind.MEASURE)
        kinds[key] = SemanticKind.MEASURE
        objects[key] = measure
    for relationship in reg.relationships.values():
        key = _exact_key(relationship.semantic_id, SemanticKind.RELATIONSHIP)
        kinds[key] = SemanticKind.RELATIONSHIP
        objects[key] = relationship
    for calendar in reg.period_calendars.values():
        key = _exact_key(calendar.semantic_id, SemanticKind.PERIOD_CALENDAR)
        kinds[key] = SemanticKind.PERIOD_CALENDAR
        objects[key] = calendar
    for temporal_set in reg.temporal_sets.values():
        key = _exact_key(temporal_set.semantic_id, SemanticKind.TEMPORAL_SET)
        kinds[key] = SemanticKind.TEMPORAL_SET
        objects[key] = temporal_set
    for domain_ir in reg.domains.values():
        key = _exact_key(domain_ir.name, SemanticKind.DOMAIN)
        kinds[key] = SemanticKind.DOMAIN
        objects[key] = domain_ir
    for ds_ir in project._datasource_irs or reg.datasources.values():
        key = _exact_key(ds_ir.semantic_id, SemanticKind.DATASOURCE)
        kinds[key] = SemanticKind.DATASOURCE
        objects[key] = ds_ir

    return kinds, objects


def _scope_keys(
    refs: Iterable[Ref[SemanticKindTag] | str] | None,
    kinds: Mapping[str, SemanticKind],
) -> tuple[str, ...] | None:
    if refs is None:
        return None
    keys: list[str] = []
    for ref in refs:
        if type(ref) is Ref:
            keys.append(ref.key)
            continue
        candidates = tuple(key for key in kinds if _display_path(key) == ref)
        keys.append(candidates[0] if len(candidates) == 1 else ref)
    return tuple(keys)


_CONTAINER_KINDS = frozenset(
    {SemanticKind.RELATIONSHIP, SemanticKind.DOMAIN, SemanticKind.DATASOURCE}
)


def _default_checked_refs(kinds: Mapping[str, SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in kinds if kinds[ref] not in _CONTAINER_KINDS) + tuple(
        ref for ref in kinds if kinds[ref] in _CONTAINER_KINDS
    )


def _container_scope_children(
    ref: str,
    objects: Mapping[str, object],
    kinds: Mapping[str, SemanticKind],
) -> tuple[str, ...]:
    kind = kinds.get(ref)
    path = _display_path(ref)
    if kind == SemanticKind.DOMAIN:
        return tuple(
            obj_id
            for obj_id, other in objects.items()
            if kinds.get(obj_id) == SemanticKind.ENTITY and getattr(other, "domain", None) == path
        )
    if kind == SemanticKind.DATASOURCE:
        return tuple(
            obj_id
            for obj_id, other in objects.items()
            if kinds.get(obj_id) == SemanticKind.ENTITY
            and getattr(other, "datasource", None) == path
        )
    return ()


def _expand_checked_refs(
    refs: Iterable[str] | None,
    kinds: Mapping[str, SemanticKind],
    objects: Mapping[str, object],
    *,
    dependencies: Mapping[str, tuple[str, ...]],
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
        for dep in (*dependencies.get(ref, ()), *_container_scope_children(ref, objects, kinds)):
            if dep not in checked and dep not in queue:
                queue.append(dep)
    return tuple(checked), tuple(unknown)


def _datasource_refs_for_checked_refs(
    refs: Iterable[str],
    objects: Mapping[str, object],
    kinds: Mapping[str, SemanticKind],
) -> tuple[str, ...]:
    datasources: list[str] = []
    for ref in refs:
        if kinds.get(ref) != SemanticKind.ENTITY:
            continue
        datasource = getattr(objects.get(ref), "datasource", None)
        if isinstance(datasource, str):
            datasources.append(datasource)
    return _dedupe(datasources)


def _dataset_refs(refs: Iterable[str], kinds: Mapping[str, SemanticKind]) -> tuple[str, ...]:
    return tuple(_display_path(ref) for ref in refs if kinds.get(ref) == SemanticKind.ENTITY)


def _refs_with_issue(issues: Iterable[ReadinessIssue]) -> set[str]:
    return {ref for issue in issues for ref in issue.refs}


def build_readiness_report(
    project: SemanticProject,
    *,
    refs: Iterable[Ref[SemanticKindTag] | str] | None = None,
) -> ReadinessReport:
    """Build a semantic-static readiness report from current loaded state.

    Uses the compiled definition graph for dependency closure, then performs
    static checks: load errors, unknown physical refs, cross-datasource dependencies,
    certified temporal artifact
    integrity, and load warnings. It never reads ordinary
    discovery or preview history and never executes a datasource query.
    Load warnings outside an explicit requested closure are excluded.

    Args:
        project: A loaded SemanticProject instance.
        refs: Semantic refs to scope the check. None checks all loaded objects.

    Returns:
        ReadinessReport indicating whether the selected refs satisfy the
        current certification contract.
    """
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
                    repair(
                        kind="reload",
                        canonical_id="load",
                        action=error.hint or "Fix semantic load errors and reload the project.",
                    ),
                )
            )
        return ReadinessReport(
            status="blocked",
            _analysis_ready_inputs=(),
            blockers=tuple(blockers),
            warnings=(),
            input_summary=ReadinessInputSummary(
                datasources=(),
                refs=(),
                tables=(),
            ),
            checked_at=_checked_at(),
            catalog_definition_fingerprint=None,
        )

    compiled_state = project._compiled_state
    if compiled_state is None:
        raise RuntimeError("ready semantic project has no compiled state")
    catalog_definition_fingerprint = compiled_state.definition_fingerprint
    kinds, objects = _object_maps(project)
    dependencies = {
        ref.key: tuple(dependency.key for dependency in values)
        for ref, values in compiled_state.dependencies.items()
    }
    scoped_keys = _scope_keys(refs, kinds)
    direct_refs = _dedupe(scoped_keys if scoped_keys is not None else _default_checked_refs(kinds))
    checked_refs, unknown_refs = _expand_checked_refs(
        scoped_keys,
        kinds,
        objects,
        dependencies=dependencies,
    )
    scoped_datasources = _datasource_refs_for_checked_refs(checked_refs, objects, kinds)
    reg = project._registry
    cross_datasource_refs: list[str] = []
    if reg is not None:
        from marivo.semantic.preview_scope import dependency_entities_for_ref

        for ref in direct_refs:
            if kinds.get(ref) not in _EXECUTABLE_KINDS:
                continue
            entity_ids = dependency_entities_for_ref(_display_path(ref), registry=reg)
            datasource_ids = {
                reg.entities[entity_id].datasource
                for entity_id in entity_ids
                if entity_id in reg.entities
            }
            if len(entity_ids) > 1 and len(datasource_ids) > 1:
                cross_datasource_refs.append(ref)

    for ref in unknown_refs:
        path = _display_path(ref)
        blockers.append(
            _issue(
                "unknown_ref",
                "blocker",
                (path,),
                f"Requested semantic ref {path!r} is not loaded in the project registry.",
                repair(
                    kind="inspect",
                    canonical_id="load",
                    action="Browse loaded refs with catalog.domains.show() or catalog.fields.show(), then select a displayed path with the corresponding collection.get(path).",
                ),
            )
        )

    # Period calendars are executable semantic dependencies. Unlike ordinary
    # preview output, a missing/stale certified artifact is a hard blocker
    # and is checked entirely from project-local state.
    if reg is not None:
        from marivo._temporal import TemporalSnapshotStore, period_calendar_definition_digest
        from marivo.refs import ref as ref_factory
        from marivo.semantic._definition_identity import scoped_definition_fingerprint
        from marivo.semantic.ir import PeriodCalendarIR

        snapshot_store = TemporalSnapshotStore(project._workspace_dir)
        for ref in checked_refs:
            if kinds.get(ref) is not SemanticKind.PERIOD_CALENDAR:
                continue
            calendar = objects.get(ref)
            if not isinstance(calendar, PeriodCalendarIR):
                continue
            calendar_ref = ref_factory.period_calendar(_display_path(ref))
            definition_digest = period_calendar_definition_digest(
                calendar_ref=calendar_ref,
                boundary_timezone=calendar.boundary_timezone,
                coverage=calendar.coverage,
                levels=calendar.levels,
                dependency_digest=scoped_definition_fingerprint(
                    root=calendar_ref,
                    definitions=compiled_state.definitions,
                    dependencies=compiled_state.dependencies,
                    sidecar=compiled_state.sidecar,
                ),
            )
            status, _snapshot = snapshot_store.inspect_current(
                calendar_ref,
                definition_digest=definition_digest,
            )
            if status != "current":
                path = _display_path(ref)
                issue_kind = cast(
                    "ReadinessIssueKind",
                    f"period_calendar_artifact_{status}",
                )
                if status == "missing":
                    issue_kind = "period_calendar_artifact_missing"
                blockers.append(
                    _issue(
                        issue_kind,
                        "blocker",
                        (path,),
                        (
                            f"{path} has no current certified period-calendar artifact for its "
                            f"declaration (state={status})."
                        ),
                        repair(
                            kind="rescope",
                            canonical_id="preview",
                            action=(
                                "Run catalog.preview(ref, scope=...) with one exhaustive explicit "
                                "scope covering the period-calendar declaration."
                            ),
                        ),
                        details={"artifact_status": status},
                    )
                )

        from marivo._temporal import TemporalSetSnapshotStore, temporal_set_definition_digest
        from marivo.semantic.ir import TemporalSetIR

        temporal_set_store = TemporalSetSnapshotStore(project._workspace_dir)
        for ref in checked_refs:
            if kinds.get(ref) is not SemanticKind.TEMPORAL_SET:
                continue
            temporal_set = objects.get(ref)
            if not isinstance(temporal_set, TemporalSetIR):
                continue
            temporal_set_ref = ref_factory.temporal_set(_display_path(ref))
            definition_digest = temporal_set_definition_digest(
                temporal_set_ref=temporal_set_ref,
                boundary_timezone=temporal_set.boundary_timezone,
                coverage=temporal_set.coverage,
                occurrence_id=temporal_set.occurrence_id,
                start=temporal_set.start,
                end=temporal_set.end,
                category=temporal_set.category,
                dependency_digest=scoped_definition_fingerprint(
                    root=temporal_set_ref,
                    definitions=compiled_state.definitions,
                    dependencies=compiled_state.dependencies,
                    sidecar=compiled_state.sidecar,
                ),
            )
            status, _temporal_snapshot = temporal_set_store.inspect_current(
                temporal_set_ref,
                definition_digest=definition_digest,
            )
            if status != "current":
                path = _display_path(ref)
                issue_kind = cast("ReadinessIssueKind", f"temporal_set_artifact_{status}")
                if status == "missing":
                    issue_kind = "temporal_set_artifact_missing"
                blockers.append(
                    _issue(
                        issue_kind,
                        "blocker",
                        (path,),
                        f"{path} has no current certified temporal-set artifact for its declaration (state={status}).",
                        repair(
                            kind="rescope",
                            canonical_id="preview",
                            action=(
                                "Run catalog.preview(ref, scope=...) with one exhaustive explicit "
                                "scope covering the temporal-set declaration."
                            ),
                        ),
                        details={"artifact_status": status},
                    )
                )

    # Cross-datasource physical dependencies.
    if reg is not None:
        for ref in cross_datasource_refs:
            path = _display_path(ref)
            blockers.append(
                _issue(
                    "cross_datasource_unfederated",
                    "blocker",
                    (path,),
                    f"Semantic object {path} spans multiple datasources without federation support.",
                    repair(
                        kind="reauthor",
                        canonical_id="relationship",
                        action="Move integration upstream or select physical inputs from one datasource.",
                    ),
                )
            )

    # Forward load warnings as readiness warnings.
    checked_paths = {_display_path(ref) for ref in checked_refs}
    for sw in project.warnings():
        if scoped_keys is not None and sw.refs and checked_paths.isdisjoint(sw.refs):
            continue
        if sw.kind in {"string_ref", "potentially_fragile_reference"}:
            warnings.append(
                _issue(
                    "fragile_string_ref",
                    "warning",
                    sw.refs,
                    sw.message,
                    repair(
                        kind="reauthor",
                        canonical_id="ref",
                        action="Replace fragile string refs with stable object refs where possible.",
                    ),
                )
            )
        if sw.kind == "time_dimension_pushdown_advisory":
            warnings.append(
                _issue(
                    "time_dimension_pushdown_advisory",
                    "warning",
                    sw.refs,
                    sw.message,
                    repair(
                        kind="reauthor",
                        canonical_id="time_dimension_column",
                        action="If the business axis matches the partition field, keep the raw string/integer column and declare date_format; keep the expression when business semantics require it.",
                    ),
                )
            )
    blocked_refs = _refs_with_issue(blockers)
    analysis_ready_ids = tuple(
        ref
        for ref in direct_refs
        if blocked_refs.isdisjoint(
            _display_path(dependency)
            for dependency in _expand_checked_refs(
                (ref,),
                kinds,
                objects,
                dependencies=dependencies,
            )[0]
        )
    )
    analysis_ready_inputs = tuple(
        _exact_ref(_display_path(ref), kinds[ref]) for ref in analysis_ready_ids if ref in kinds
    )

    datasources_checked: tuple[str, ...] = scoped_datasources if reg is not None else ()

    blockers = [
        replace(issue, catalog_definition_fingerprint=catalog_definition_fingerprint)
        for issue in blockers
    ]
    warnings = [
        replace(issue, catalog_definition_fingerprint=catalog_definition_fingerprint)
        for issue in warnings
    ]

    return ReadinessReport(
        status=_status(blockers, warnings),
        _analysis_ready_inputs=analysis_ready_inputs,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        input_summary=ReadinessInputSummary(
            datasources=datasources_checked,
            refs=_dedupe(_display_path(ref) for ref in checked_refs),
            tables=_dataset_refs(checked_refs, kinds),
        ),
        checked_at=_checked_at(),
        catalog_definition_fingerprint=catalog_definition_fingerprint,
    )
