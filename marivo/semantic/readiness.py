"""Semantic readiness report DTOs and query-free evidence gating."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from marivo._authoring.model import AuthoringRepair
from marivo._compat import UTC
from marivo.refs import Ref, RefPayloadV1, SemanticKind, SemanticKindTag
from marivo.refs import ref as ref_factory
from marivo.render import Card, RenderableResult
from marivo.semantic.errors import repair
from marivo.semantic.runtime_metric import RuntimeMetricExpr, replay_payload

if TYPE_CHECKING:
    from marivo._authoring.model import AuthoringContract
    from marivo.semantic.preview_checks import PreviewEvidenceRequirement
    from marivo.semantic.reader import SemanticProject

ReadinessStatus = Literal["ready", "ready_with_warnings", "blocked"]
ReadinessSeverity = Literal["blocker", "warning", "advisory"]
ReadinessIssueKind = Literal[
    "load_error",
    "unknown_ref",
    "cross_datasource_unfederated",
    "sql_parity_unverified",
    "fragile_string_ref",
    "time_dimension_pushdown_advisory",
    "snapshot_missing",
    "runtime_preview_missing",
    "missing_business_definition",
    "missing_guardrails",
    "undeclared_naive_time_axis",
    "metric_graph_invalid",
    "snapshot_fold_unobservable",
    "state_model_seed_missing",
    "period_calendar_snapshot_missing",
    "period_calendar_snapshot_stale",
    "period_calendar_snapshot_invalid",
    "temporal_set_snapshot_missing",
    "temporal_set_snapshot_stale",
    "temporal_set_snapshot_invalid",
    "work_schedule_snapshot_missing",
    "work_schedule_snapshot_stale",
    "work_schedule_snapshot_invalid",
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


@dataclass(frozen=True, repr=False)
class ReadinessReport(RenderableResult):
    scope: ClassVar[Literal["semantic_static"]] = "semantic_static"
    status: ReadinessStatus
    analysis_ready_refs: tuple[Ref[SemanticKindTag], ...]
    blockers: tuple[ReadinessIssue, ...]
    warnings: tuple[ReadinessIssue, ...]
    input_summary: ReadinessInputSummary
    checked_at: str
    preview_required_refs: tuple[Ref[SemanticKindTag], ...] = ()
    catalog_definition_fingerprint: str | None = None
    analysis_ready_inputs: tuple[Ref[SemanticKindTag] | RuntimeMetricExpr, ...] = ()

    def __post_init__(self) -> None:
        if not self.analysis_ready_inputs and self.analysis_ready_refs:
            object.__setattr__(self, "analysis_ready_inputs", self.analysis_ready_refs)

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
                ".contract()",
                ".preview_required_refs",
                ".analysis_ready_inputs",
            ),
        )
        card = card.field(label="scope", value=self.scope)
        if self.blockers:
            blocker_items = [
                f"{i.kind}: {i.message} -> fix: {i.repair.action if i.repair else ''}"
                for i in self.blockers
            ]
            card = card.listing(
                label=f"blockers ({len(self.blockers)})",
                items=tuple(blocker_items),
            )
        actual_warnings = tuple(issue for issue in self.warnings if issue.severity == "warning")
        advisories = tuple(issue for issue in self.warnings if issue.severity == "advisory")
        if actual_warnings:
            warning_items = [
                f"{i.kind}: {i.message} -> fix: {i.repair.action if i.repair else ''}"
                for i in actual_warnings
            ]
            card = card.listing(
                label=f"warnings ({len(actual_warnings)})",
                items=tuple(warning_items),
            )
        if advisories:
            card = card.listing(
                label=f"advisories ({len(advisories)})",
                items=tuple(
                    f"{i.kind}: {i.message} -> optional fix: {i.repair.action if i.repair else ''}"
                    for i in advisories
                ),
            )
        if self.analysis_ready_refs:
            card = card.field(
                label="analysis_ready",
                value=", ".join(ref.key for ref in self.analysis_ready_refs),
            )
        runtime_inputs = tuple(item for item in self.analysis_ready_inputs if type(item) is not Ref)
        if runtime_inputs:
            card = card.field(
                label="analysis_ready_runtime",
                value=", ".join(item.label for item in runtime_inputs),
            )
        return card.field(label="checked_at", value=self.checked_at)

    def to_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "status": self.status,
            "analysis_ready_refs": [
                RefPayloadV1.from_ref(ref).to_dict() for ref in self.analysis_ready_refs
            ],
            "analysis_ready_inputs": [
                RefPayloadV1.from_ref(item).to_dict() if type(item) is Ref else replay_payload(item)
                for item in self.analysis_ready_inputs
            ],
            "blockers": [issue.to_dict() for issue in self.blockers],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "input_summary": self.input_summary.to_dict(),
            "checked_at": self.checked_at,
            "catalog_definition_fingerprint": self.catalog_definition_fingerprint,
            "preview_required_refs": [
                RefPayloadV1.from_ref(ref).to_dict() for ref in self.preview_required_refs
            ],
        }

    def contract(self) -> AuthoringContract:
        """Return the mechanical continuation contract for this readiness report.

        Exposes only mechanically valid semantic repair transitions. Ready refs
        remain available through ``analysis_ready_refs`` for explicit analysis.
        """
        from marivo.semantic._capabilities.contracts import (
            contract_for_readiness_report,
        )

        return contract_for_readiness_report(
            tuple(ref.path for ref in self.analysis_ready_refs),
            self.blockers + self.warnings,
        )


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
        SemanticKind.EVENT: ref_factory.event,
        SemanticKind.STATE_MODEL: ref_factory.state_model,
        SemanticKind.PERIOD_CALENDAR: ref_factory.period_calendar,
        SemanticKind.TEMPORAL_SET: ref_factory.temporal_set,
        SemanticKind.WORK_SCHEDULE: ref_factory.work_schedule,
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
        SemanticKind.METRIC,
        SemanticKind.RELATIONSHIP,
        SemanticKind.EVENT,
        SemanticKind.STATE_MODEL,
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


def _parity_passed(project: SemanticProject, ref: str) -> bool:
    """Check whether a metric with SQL provenance has passed parity verification."""
    parity_result = project._parity_results.get(ref)
    return parity_result is not None and parity_result.ok


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
    for metric in reg.metrics.values():
        key = _exact_key(metric.semantic_id, SemanticKind.METRIC)
        kinds[key] = SemanticKind.METRIC
        objects[key] = metric
    for relationship in reg.relationships.values():
        key = _exact_key(relationship.semantic_id, SemanticKind.RELATIONSHIP)
        kinds[key] = SemanticKind.RELATIONSHIP
        objects[key] = relationship
    for event in reg.events.values():
        key = _exact_key(event.semantic_id, SemanticKind.EVENT)
        kinds[key] = SemanticKind.EVENT
        objects[key] = event
    for state_model in reg.state_models.values():
        key = _exact_key(state_model.semantic_id, SemanticKind.STATE_MODEL)
        kinds[key] = SemanticKind.STATE_MODEL
        objects[key] = state_model
    for calendar in reg.period_calendars.values():
        key = _exact_key(calendar.semantic_id, SemanticKind.PERIOD_CALENDAR)
        kinds[key] = SemanticKind.PERIOD_CALENDAR
        objects[key] = calendar
    for temporal_set in reg.temporal_sets.values():
        key = _exact_key(temporal_set.semantic_id, SemanticKind.TEMPORAL_SET)
        kinds[key] = SemanticKind.TEMPORAL_SET
        objects[key] = temporal_set
    for work_schedule in reg.work_schedules.values():
        key = _exact_key(work_schedule.semantic_id, SemanticKind.WORK_SCHEDULE)
        kinds[key] = SemanticKind.WORK_SCHEDULE
        objects[key] = work_schedule
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


def _strict_enrichment_issues(
    checked_refs: Iterable[str],
    kinds: Mapping[str, SemanticKind],
    objects: Mapping[str, object],
) -> tuple[list[ReadinessIssue], list[ReadinessIssue]]:
    """Contracts section 7: analyzable handoff refs must carry a non-empty
    business_definition (blocker) and guardrails (warning for all analyzable
    refs). Richness owns optional enrichment suggestions.
    Relationships are out of scope, matching semantic-preview scoping."""
    analyzable = {
        SemanticKind.ENTITY,
        SemanticKind.DIMENSION,
        SemanticKind.MEASURE,
        SemanticKind.TIME_DIMENSION,
        SemanticKind.METRIC,
        SemanticKind.EVENT,
        SemanticKind.STATE_MODEL,
    }
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    guardrails_missing: list[str] = []
    for ref in checked_refs:
        kind = kinds.get(ref)
        if kind not in analyzable:
            continue
        obj = objects.get(ref)
        if obj is None:
            continue
        path = _display_path(ref)
        if _missing_business_definition(obj):
            blockers.append(
                _issue(
                    "missing_business_definition",
                    "blocker",
                    (path,),
                    f"{path} has no ai_context.business_definition for semantic certification.",
                    repair(
                        kind="reauthor",
                        canonical_id="metric",
                        action="Add ai_context=ms.ai_context(business_definition=...) so analysis can match and reuse this ref.",
                    ),
                )
            )
            # business_definition missing implies guardrails missing too; report
            # the single most fundamental issue rather than stacking findings.
            continue
        if _missing_guardrails(obj):
            guardrails_missing.append(path)
    if guardrails_missing:
        missing_paths = _dedupe(guardrails_missing)
        noun = "ref" if len(missing_paths) == 1 else "refs"
        verb = "has" if len(missing_paths) == 1 else "have"
        warnings.append(
            _issue(
                "missing_guardrails",
                "warning",
                missing_paths,
                f"{len(missing_paths)} analyzable {noun} {verb} no ai_context.guardrails; "
                "analysis may proceed but the agent lacks usage constraints.",
                repair(
                    kind="reauthor",
                    canonical_id="metric",
                    action="Add ai_context=ms.ai_context(guardrails=[...]) to make safe usage explicit.",
                ),
                details={"missing_refs": list(missing_paths)},
            )
        )
    return blockers, warnings


_CONTAINER_KINDS = frozenset(
    {SemanticKind.RELATIONSHIP, SemanticKind.DOMAIN, SemanticKind.DATASOURCE}
)


def _default_checked_refs(kinds: Mapping[str, SemanticKind]) -> tuple[str, ...]:
    return tuple(ref for ref in kinds if kinds[ref] not in _CONTAINER_KINDS) + tuple(
        ref for ref in kinds if kinds[ref] in _CONTAINER_KINDS
    )


def _dependencies_for_ref(
    ref: str,
    objects: Mapping[str, object],
    kinds: Mapping[str, SemanticKind],
    *,
    event_predicate_dependencies: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    kind = kinds.get(ref)
    obj = objects.get(ref)
    if obj is None:
        return ()
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
    if kind in {SemanticKind.DIMENSION, SemanticKind.TIME_DIMENSION}:
        entity = getattr(obj, "entity", None)
        return (_exact_key(entity, SemanticKind.ENTITY),) if isinstance(entity, str) else ()
    if kind == SemanticKind.MEASURE:
        entity = getattr(obj, "entity", None)
        return (_exact_key(entity, SemanticKind.ENTITY),) if isinstance(entity, str) else ()
    if kind == SemanticKind.PERIOD_CALENDAR:
        date_field = getattr(obj, "date", None)
        levels = tuple(field for _level, field in getattr(obj, "levels", ()))
        correspondence_fields = tuple(
            field for _name, _level, field in getattr(obj, "correspondences", ())
        )
        return tuple(
            _exact_key(
                field,
                SemanticKind.TIME_DIMENSION if field == date_field else SemanticKind.DIMENSION,
            )
            for field in (date_field, *levels, *correspondence_fields)
            if isinstance(field, str)
        )
    if kind == SemanticKind.TEMPORAL_SET:
        fields = (
            getattr(obj, "occurrence_id", None),
            getattr(obj, "start", None),
            getattr(obj, "end", None),
            getattr(obj, "category", None),
        )
        return tuple(
            _exact_key(
                field,
                SemanticKind.TIME_DIMENSION
                if field in {getattr(obj, "start", None), getattr(obj, "end", None)}
                else SemanticKind.DIMENSION,
            )
            for field in fields
            if isinstance(field, str)
        )
    if kind == SemanticKind.WORK_SCHEDULE:
        date_field = getattr(obj, "date", None)
        status_field = getattr(obj, "is_working", None)
        return tuple(
            _exact_key(
                field,
                SemanticKind.TIME_DIMENSION if field == date_field else SemanticKind.DIMENSION,
            )
            for field in (date_field, status_field)
            if isinstance(field, str)
        )
    if kind == SemanticKind.METRIC:
        deps: list[str] = []
        deps.extend(
            _exact_key(entity, SemanticKind.ENTITY) for entity in getattr(obj, "entities", ())
        )
        composition = getattr(obj, "composition", None)
        if composition is not None:
            from marivo.semantic.ir import composition_components

            components = composition_components(composition)
            deps.extend(
                _exact_key(str(value), SemanticKind.METRIC) for value in components.values()
            )
        return tuple(deps)
    if kind == SemanticKind.RELATIONSHIP:
        keys = getattr(obj, "keys", ())
        key_refs = tuple(ref for key in keys for ref in key.to_tuple())
        entity_deps = (getattr(obj, "from_entity", None), getattr(obj, "to_entity", None))
        return tuple(
            _exact_key(dep, SemanticKind.ENTITY) for dep in entity_deps if isinstance(dep, str)
        ) + tuple(
            _exact_key(dep, SemanticKind.DIMENSION)
            for dep in (*key_refs, *getattr(obj, "from_keys", ()), *getattr(obj, "to_keys", ()))
            if isinstance(dep, str)
        )
    if kind == SemanticKind.EVENT:
        from marivo.semantic.ir import EventIR

        event = cast("EventIR", obj)
        deps = [
            _exact_key(event.source_entity, SemanticKind.ENTITY),
            _exact_key(event.occurred_at, SemanticKind.TIME_DIMENSION),
        ]
        deps.extend(_exact_key(path, SemanticKind.DIMENSION) for path in event.identity)
        for participant in event.participants:
            deps.extend(
                _exact_key(path, SemanticKind.RELATIONSHIP) for path in (participant.path or ())
            )
        if event_predicate_dependencies is not None:
            deps.extend(event_predicate_dependencies.get(ref, ()))
        return tuple(deps)
    if kind == SemanticKind.STATE_MODEL:
        from marivo.semantic.ir import StateModelIR

        model = cast("StateModelIR", obj)
        event_refs = {item.trigger.event_ref for item in model.inceptions} | {
            item.trigger.event_ref for item in model.transitions
        }
        return (
            _exact_key(model.subject, SemanticKind.ENTITY),
            *tuple(_exact_key(event_ref, SemanticKind.EVENT) for event_ref in sorted(event_refs)),
        )
    return ()


def _expand_checked_refs(
    refs: Iterable[str] | None,
    kinds: Mapping[str, SemanticKind],
    objects: Mapping[str, object],
    *,
    event_predicate_dependencies: Mapping[str, tuple[str, ...]] | None = None,
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
        for dep in _dependencies_for_ref(
            ref,
            objects,
            kinds,
            event_predicate_dependencies=event_predicate_dependencies,
        ):
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


def _missing_business_definition(obj: object) -> bool:
    ai_context = getattr(obj, "ai_context", None)
    business_definition = getattr(ai_context, "business_definition", None)
    return not (business_definition and business_definition.strip())


def _missing_guardrails(obj: object) -> bool:
    ai_context = getattr(obj, "ai_context", None)
    guardrails = getattr(ai_context, "guardrails", None)
    return not guardrails


def _undeclared_naive_time_axis_issues(
    checked_refs: Iterable[str],
    kinds: Mapping[str, SemanticKind],
    objects: Mapping[str, object],
    preview_types: Mapping[str, str],
) -> tuple[list[ReadinessIssue], list[ReadinessIssue]]:
    """Return blockers/warnings for native temporal axes without a source timezone.

    An explicit ``parse=ms.datetime()``/``parse=ms.timestamp()`` with no timezone is
    certainly a naive datetime/timestamp, so it blocks.  A time dimension with no
    ``parse`` at all is classified only from a matching persisted preview type;
    readiness remains query-free and does not turn an unknown physical type into a
    timezone warning.
    """
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    for ref in checked_refs:
        if kinds.get(ref) != SemanticKind.TIME_DIMENSION:
            continue
        path = _display_path(ref)
        time_dimension = objects.get(ref)
        parse = getattr(time_dimension, "parse", None)
        data_type = getattr(parse, "kind", None)
        declared_timezone = getattr(parse, "timezone", None)

        entity_ref = getattr(time_dimension, "entity", None)
        entity = (
            objects.get(_exact_key(entity_ref, SemanticKind.ENTITY))
            if isinstance(entity_ref, str)
            else None
        )
        datasource = getattr(entity, "datasource", None)

        if data_type in {"datetime", "timestamp"} and declared_timezone is None:
            parse_call = f'ms.{data_type}(timezone="Region/City")'
            blockers.append(
                _issue(
                    "undeclared_naive_time_axis",
                    "blocker",
                    (path,),
                    f"{path} is a native {data_type} time axis with no declared source timezone; "
                    "analysis will otherwise interpret naive values using the datasource read timezone.",
                    repair(
                        kind="reauthor",
                        canonical_id="time_dimension_column",
                        action=f"Declare the source timezone on this time dimension with parse={parse_call}.",
                    ),
                    details={
                        "data_type": data_type,
                        "declared_timezone": None,
                        "datasource": datasource,
                        "datasource_read_timezone": "resolved at runtime",
                        "report_timezone": "resolved by the analysis session",
                        "window_alignment_risk": "Report-local windows may shift at day or hour boundaries.",
                    },
                )
            )
        elif parse is None and _preview_type_is_naive_timestamp(preview_types.get(ref)):
            observed_type = preview_types[ref]
            warnings.append(
                _issue(
                    "undeclared_naive_time_axis",
                    "warning",
                    (path,),
                    f"{path} declares no parse/timezone, and matching preview evidence reports "
                    f"the native naive type {observed_type!r}; analysis will interpret values using "
                    "the datasource read timezone, risking silent misalignment across "
                    "time-zone boundaries.",
                    repair(
                        kind="reauthor",
                        canonical_id="time_dimension_column",
                        action=(
                            "Declare the source timezone by adding "
                            'parse=ms.datetime(timezone="Region/City") (or '
                            'ms.timestamp(timezone="Region/City")) to the @ms.time_dimension '
                            "declaration."
                        ),
                    ),
                    details={
                        "data_type": observed_type,
                        "data_type_evidence": "matching_preview",
                        "declared_timezone": None,
                        "datasource": datasource,
                        "datasource_read_timezone": "resolved at runtime",
                        "report_timezone": "resolved by the analysis session",
                        "window_alignment_risk": "Report-local windows may shift at day or hour boundaries.",
                    },
                )
            )
    return blockers, warnings


def _preview_type_is_naive_timestamp(data_type: str | None) -> bool:
    """Whether a persisted ibis preview type is timestamp-like and timezone-naive."""
    if data_type is None:
        return False
    normalized = data_type.strip().lower().replace(" ", "")
    return normalized.startswith("timestamp") and "'" not in normalized and '"' not in normalized


def _snapshot_fold_unobservable_issues(
    checked_refs: Iterable[str],
    kinds: Mapping[str, SemanticKind],
    objects: Mapping[str, object],
) -> list[ReadinessIssue]:
    """Return blockers for metrics whose time fold has no legal observe path.

    A semi-additive metric with a time_fold is observable only when either
    (a) its status time dimension declares a ``sample_interval`` (sampled fold),
    or (b) the fold is first/last and the root entity binds snapshot versioning
    to the same status time dimension (snapshot selection). Any other
    combination deadlocks the observe planner (reported as a single
    ``snapshot-fold-deadlock`` error) and must be surfaced at readiness time
    rather than reported analysis_ready.
    """
    from marivo.semantic.ir import SnapshotVersioningIR

    blockers: list[ReadinessIssue] = []
    for ref in checked_refs:
        if kinds.get(ref) != SemanticKind.METRIC:
            continue
        metric = objects.get(ref)
        if metric is None:
            continue
        time_fold = getattr(metric, "time_fold", None)
        if time_fold is None:
            continue
        status_time_dimension = getattr(metric, "status_time_dimension", None)
        if not isinstance(status_time_dimension, str) or not status_time_dimension:
            continue

        dim_ir = objects.get(_exact_key(status_time_dimension, SemanticKind.TIME_DIMENSION))
        if dim_ir is None:
            continue
        parse = getattr(dim_ir, "parse", None)
        sample_interval = getattr(parse, "sample_interval", None)
        if sample_interval is not None:
            continue  # a sampled status fold is always legal

        fold_kind = getattr(time_fold, "kind", None)
        root_entity = getattr(metric, "root_entity", None)
        if not isinstance(root_entity, str) or not root_entity:
            entities = tuple(getattr(metric, "entities", ()))
            if len(entities) == 1:
                root_entity = entities[0]
            else:
                continue

        partition_field: str | None = None
        entity_ir = objects.get(_exact_key(root_entity, SemanticKind.ENTITY))
        if entity_ir is not None:
            versioning = getattr(entity_ir, "versioning", None)
            if isinstance(versioning, SnapshotVersioningIR):
                partition_field = versioning.partition_field

        if fold_kind in {"first", "last"} and partition_field == status_time_dimension:
            continue  # snapshot selection is legal

        path = _display_path(ref)
        if fold_kind in {"first", "last"}:
            reason = "snapshot_versioning_missing"
        else:
            reason = "unsampled_non_selection_fold"
        blockers.append(
            _issue(
                "snapshot_fold_unobservable",
                "blocker",
                (path,),
                (
                    f"{path} has no legal observe path: time_fold={fold_kind} over "
                    f"{status_time_dimension} requires a sample_interval on the status "
                    "time dimension or snapshot versioning bound to it; neither is declared."
                ),
                repair(
                    kind="reauthor",
                    canonical_id="metric",
                    action=(
                        "Declare sample_interval=(1, 'hour') (or another minute/hour interval) "
                        "on the status time dimension via parse=ms.timestamp(...), or bind "
                        "snapshot versioning with versioning=ms.snapshot(partition_field="
                        "<status_time_dimension>, grain='day') on the root entity for "
                        "first/last folds."
                    ),
                ),
                details={
                    "time_fold": fold_kind,
                    "status_time_dimension": status_time_dimension,
                    "root_entity": root_entity,
                    "snapshot_partition_field": partition_field,
                    "reason": reason,
                },
            )
        )
    return blockers


def build_readiness_report(
    project: SemanticProject,
    *,
    refs: Iterable[Ref[SemanticKindTag] | str] | None = None,
) -> ReadinessReport:
    """Build a readiness report from loaded state and persisted row-free evidence.

    Performs pure in-memory checks: load errors, unknown refs,
    cross-datasource unfederated metrics, raw SQL requirements,
    strict enrichment issues, load warnings, and matching preview checks.
    It never acquires snapshots, refreshes state, or executes a datasource query.

    Args:
        project: A loaded SemanticProject instance.
        refs: Semantic refs to scope the check. None checks all loaded objects.

    Returns:
        ReadinessReport indicating whether the selected refs satisfy the
        current certification contract.
    """
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    preview_required_keys: list[str] = []

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
            analysis_ready_refs=(),
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
    preview_requirements: dict[str, PreviewEvidenceRequirement] = {}

    def preview_requirement_for(path: str) -> PreviewEvidenceRequirement | None:
        if project._registry is None or project._expression_sidecar is None:
            return None
        cached = preview_requirements.get(path)
        if cached is not None:
            return cached
        from marivo.semantic.preview_checks import preview_evidence_requirement

        requirement = preview_evidence_requirement(
            path,
            registry=project._registry,
            sidecar=project._expression_sidecar,
            project_root=project._workspace_dir,
            catalog_definition_fingerprint=catalog_definition_fingerprint,
        )
        preview_requirements[path] = requirement
        return requirement

    event_predicate_dependencies = {
        ref.key: tuple(binding.to_ref().key for binding in body.bindings)
        for ref, body in compiled_state.sidecar.bodies.items()
        if ref.kind is SemanticKind.EVENT
    }

    kinds, objects = _object_maps(project)
    scoped_keys = _scope_keys(refs, kinds)
    direct_refs = _dedupe(scoped_keys if scoped_keys is not None else _default_checked_refs(kinds))
    checked_refs, unknown_refs = _expand_checked_refs(
        scoped_keys,
        kinds,
        objects,
        event_predicate_dependencies=event_predicate_dependencies,
    )
    scoped_datasources = _datasource_refs_for_checked_refs(checked_refs, objects, kinds)
    reg = project._registry
    cross_datasource_refs: list[str] = []
    graph_invalid_refs: set[str] = set()
    if reg is not None:
        from marivo.semantic.preview_checks import preview_dependency_entities

        for ref in direct_refs:
            if kinds.get(ref) not in _EXECUTABLE_KINDS:
                continue
            entity_ids = preview_dependency_entities(_display_path(ref), registry=reg)
            datasource_ids = {
                reg.entities[entity_id].datasource
                for entity_id in entity_ids
                if entity_id in reg.entities
            }
            if len(entity_ids) > 1 and len(datasource_ids) > 1:
                cross_datasource_refs.append(ref)

    if reg is not None:
        from marivo.semantic.metric_graph_canonical import MetricGraphContractError
        from marivo.semantic.metric_graph_lowering import (
            lower_catalog_metric,
        )

        for ref in direct_refs:
            if kinds.get(ref) != SemanticKind.METRIC:
                continue
            path = _display_path(ref)
            try:
                lower_catalog_metric(reg, path, sidecar=project._expression_sidecar)
            except (MetricGraphContractError, TypeError, ValueError) as exc:
                graph_invalid_refs.add(ref)
                blockers.append(
                    _issue(
                        "metric_graph_invalid",
                        "blocker",
                        (path,),
                        f"{path} cannot lower to the bounded metric expression graph: {exc}",
                        repair(
                            kind="reauthor",
                            canonical_id="metric",
                            action=(
                                "Reduce the metric's recursive composition to depth at most 10 "
                                "and 256 pre-CSE occurrences, or repair the dependency reported "
                                "at the failing occurrence path."
                            ),
                        ),
                        details={
                            "max_depth": 10,
                            "max_occurrences": 256,
                            "lowering_error_kind": getattr(exc, "kind", "graph_contract"),
                            "observed_count": getattr(exc, "observed_count", None),
                            "limit": getattr(exc, "limit", None),
                            "dependency_path": getattr(exc, "path", None),
                            "occurrence_path": getattr(exc, "path", None),
                        },
                    )
                )

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
                    action="Browse loaded refs with catalog.domains.show() or catalog.metrics.show(), then inspect a known identity with catalog.require(ms.ref.<kind>(path)).details().show().",
                ),
            )
        )

    preview_time_axis_types: dict[str, str] = {}
    for ref in checked_refs:
        if kinds.get(ref) is not SemanticKind.TIME_DIMENSION:
            continue
        time_dimension = objects.get(ref)
        if getattr(time_dimension, "parse", None) is not None:
            continue
        requirement = preview_requirement_for(_display_path(ref))
        if requirement is None or requirement.status != "matched":
            continue
        field_name = getattr(time_dimension, "name", None)
        if not isinstance(field_name, str):
            continue
        observed_type = dict(requirement.types).get(field_name)
        if isinstance(observed_type, str):
            preview_time_axis_types[ref] = observed_type

    naive_time_axis_blockers, naive_time_axis_warnings = _undeclared_naive_time_axis_issues(
        checked_refs,
        kinds,
        objects,
        preview_time_axis_types,
    )
    blockers.extend(naive_time_axis_blockers)
    warnings.extend(naive_time_axis_warnings)
    blockers.extend(_snapshot_fold_unobservable_issues(checked_refs, kinds, objects))

    # Period calendars are executable semantic dependencies. Unlike ordinary
    # preview evidence, a missing/stale certified snapshot is a hard blocker
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
                correspondences=calendar.correspondences,
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
                    f"period_calendar_snapshot_{status}",
                )
                if status == "missing":
                    issue_kind = "period_calendar_snapshot_missing"
                blockers.append(
                    _issue(
                        issue_kind,
                        "blocker",
                        (path,),
                        (
                            f"{path} has no current certified period-calendar snapshot for its "
                            f"declaration (state={status})."
                        ),
                        repair(
                            kind="reverify",
                            canonical_id="preview",
                            action=(
                                "Acquire one fresh exhaustive DiscoverySnapshot with "
                                "persist_values=True, then preview this period calendar using "
                                "that exact snapshot."
                            ),
                        ),
                        details={"snapshot_status": status},
                    )
                )

        from marivo._temporal import WorkScheduleSnapshotStore, work_schedule_definition_digest
        from marivo.semantic.ir import WorkScheduleIR

        work_schedule_store = WorkScheduleSnapshotStore(project._workspace_dir)
        for ref in checked_refs:
            if kinds.get(ref) is not SemanticKind.WORK_SCHEDULE:
                continue
            work_schedule = objects.get(ref)
            if not isinstance(work_schedule, WorkScheduleIR):
                continue
            work_schedule_ref = ref_factory.work_schedule(_display_path(ref))
            definition_digest = work_schedule_definition_digest(
                work_schedule_ref=work_schedule_ref,
                boundary_timezone=work_schedule.boundary_timezone,
                coverage=work_schedule.coverage,
                date=work_schedule.date,
                is_working=work_schedule.is_working,
                dependency_digest=scoped_definition_fingerprint(
                    root=work_schedule_ref,
                    definitions=compiled_state.definitions,
                    dependencies=compiled_state.dependencies,
                    sidecar=compiled_state.sidecar,
                ),
            )
            status, _schedule_snapshot = work_schedule_store.inspect_current(
                work_schedule_ref,
                definition_digest=definition_digest,
            )
            if status != "current":
                path = _display_path(ref)
                issue_kind = cast("ReadinessIssueKind", f"work_schedule_snapshot_{status}")
                if status == "missing":
                    issue_kind = "work_schedule_snapshot_missing"
                blockers.append(
                    _issue(
                        issue_kind,
                        "blocker",
                        (path,),
                        f"{path} has no current certified work-schedule snapshot for its declaration (state={status}).",
                        repair(
                            kind="reverify",
                            canonical_id="preview",
                            action=(
                                "Acquire one fresh exhaustive DiscoverySnapshot with persist_values=True, "
                                "then preview this work schedule using that exact snapshot."
                            ),
                        ),
                        details={"snapshot_status": status},
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
                issue_kind = cast("ReadinessIssueKind", f"temporal_set_snapshot_{status}")
                if status == "missing":
                    issue_kind = "temporal_set_snapshot_missing"
                blockers.append(
                    _issue(
                        issue_kind,
                        "blocker",
                        (path,),
                        f"{path} has no current certified temporal-set snapshot for its declaration (state={status}).",
                        repair(
                            kind="reverify",
                            canonical_id="preview",
                            action=(
                                "Acquire one fresh exhaustive DiscoverySnapshot with persist_values=True, "
                                "then preview this temporal set using that exact snapshot."
                            ),
                        ),
                        details={"snapshot_status": status},
                    )
                )

    for ref in direct_refs:
        if kinds.get(ref) is not SemanticKind.STATE_MODEL:
            continue
        model = objects.get(ref)
        if model is not None and not getattr(model, "inceptions", ()):
            path = _display_path(ref)
            blockers.append(
                _issue(
                    "state_model_seed_missing",
                    "blocker",
                    (path,),
                    f"{path} has no inception trigger and is not ready for Phase 3 replay.",
                    repair(
                        kind="reauthor",
                        canonical_id="state_model",
                        action="Add at least one ms.inception(on=...) trigger before replay.",
                    ),
                )
            )

    # Strict enrichment: missing business_definition is a blocker;
    # missing guardrails is a warning for every analyzable object.
    enrichment_blockers, enrichment_warnings = _strict_enrichment_issues(
        checked_refs,
        kinds,
        objects,
    )
    blockers.extend(enrichment_blockers)
    warnings.extend(enrichment_warnings)

    if project._registry is not None and project._expression_sidecar is not None:
        evidence_issue_refs: dict[
            tuple[Literal["snapshot_missing", "runtime_preview_missing"], tuple[str, ...]],
            list[str],
        ] = {}
        evidence_issue_repairs: dict[
            tuple[Literal["snapshot_missing", "runtime_preview_missing"], tuple[str, ...]],
            AuthoringRepair,
        ] = {}
        for ref in direct_refs:
            if kinds.get(ref) not in _EXECUTABLE_KINDS or ref in cross_datasource_refs:
                continue
            if ref in graph_invalid_refs:
                continue
            path = _display_path(ref)
            requirement = preview_requirement_for(path)
            assert requirement is not None
            if requirement.status == "matched":
                continue
            key = (requirement.status, requirement.evidence_roots)
            evidence_issue_refs.setdefault(key, []).append(path)
            evidence_issue_repairs.setdefault(key, requirement.repair)
            if requirement.status == "snapshot_missing":
                continue
            preview_required_keys.append(ref)

        for (issue_kind, evidence_roots), paths in evidence_issue_refs.items():
            unique_paths = _dedupe(paths)
            if issue_kind == "snapshot_missing":
                message = (
                    f"{len(unique_paths)} semantic refs share an evidence root with no matching "
                    "datasource snapshot metadata; analysis may proceed."
                )
            else:
                message = (
                    f"{len(unique_paths)} semantic refs share an evidence root without a current "
                    "runtime preview; analysis may proceed, but preview remains the optional "
                    "certification step for authoring changes."
                )
            issue_repair = evidence_issue_repairs[(issue_kind, evidence_roots)]
            if issue_kind == "runtime_preview_missing":
                issue_repair = repair(
                    kind="repreview",
                    canonical_id="preview_many",
                    action=(
                        "Run catalog.preview_many(report.preview_required_refs, using=...) "
                        "to repair the grouped runtime-preview evidence."
                    ),
                    snippet="catalog.preview_many(report.preview_required_refs, using=...)",
                    preserves_evidence=False,
                )
            warnings.append(
                _issue(
                    issue_kind,
                    "advisory",
                    unique_paths,
                    message,
                    repair=issue_repair,
                    details={"evidence_roots": list(evidence_roots)},
                )
            )

    # Cross-datasource unfederated metrics.
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
                        canonical_id="metric",
                        action="Move integration upstream, enable a federated backend, or split the metric.",
                    ),
                )
            )

    # SQL parity unverified warnings.
    for ref in checked_refs:
        if kinds.get(ref) != SemanticKind.METRIC:
            continue
        path = _display_path(ref)
        obj = objects.get(ref)
        if obj is None:
            continue
        prov = getattr(obj, "provenance", None)
        if prov is None:
            continue
        provenance_sql = prov.sql
        if provenance_sql is None:
            continue
        if not _parity_passed(project, path):
            warnings.append(
                _issue(
                    "sql_parity_unverified",
                    "warning",
                    (path,),
                    f"{path} has provenance SQL but parity has not been confirmed.",
                    repair(
                        kind="reverify",
                        canonical_id="parity_check",
                        action=f"Run ms.parity_check({path!r}) when parity matters, or report the warning as non-blocking when the certification policy allows it.",
                    ),
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
                event_predicate_dependencies=event_predicate_dependencies,
            )[0]
        )
    )
    analysis_ready_refs = tuple(
        _exact_ref(_display_path(ref), kinds[ref]) for ref in analysis_ready_ids if ref in kinds
    )
    preview_required_refs = tuple(
        _exact_ref(_display_path(ref), kinds[ref])
        for ref in _dedupe(preview_required_keys)
        if ref in kinds
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
        analysis_ready_refs=analysis_ready_refs,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        input_summary=ReadinessInputSummary(
            datasources=datasources_checked,
            refs=_dedupe(_display_path(ref) for ref in checked_refs),
            tables=_dataset_refs(checked_refs, kinds),
        ),
        checked_at=_checked_at(),
        preview_required_refs=preview_required_refs,
        catalog_definition_fingerprint=catalog_definition_fingerprint,
    )
