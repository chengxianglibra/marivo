"""Materialize typed semantic Events into dense subject journeys."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AmbiguousEventOrderError,
    AnalysisRepair,
    EventIdentityError,
    EventParticipantCardinalityError,
    InvalidCompletenessDeclarationError,
    InvalidEventMatchingPolicyError,
    InvalidEventPatternError,
    PatternStepMismatchError,
)
from marivo.analysis.event import (
    CompletenessDeclaration,
    EventMatchingPolicy,
    EventPattern,
    EventWatermarkReceipt,
    EventWatermarkRequest,
    EveryStart,
    FirstPerSubject,
    PatternStep,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    event_subject_for_frame,
    frame_exists_on_disk,
    rollback_committed_result,
)
from marivo.analysis.executor.runner import ExecutionResult, execute
from marivo.analysis.executor.windowing import (
    _window_bound_predicates,
    datasource_engine_profile,
    datasource_read_timezone,
    effective_time_context,
)
from marivo.analysis.frames.event import EventFrame, EventFrameMeta, EventInputCoverage
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.intents._observe_catalog import (
    _build_entity_adapter,
    _entity_details,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.windows.spec import AbsoluteWindow, TimeScope
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EntityKind, EventKind, Ref, RefPayloadV1, SemanticKind
from marivo.semantic.catalog import EventDetails, EventEntry

_ROW_COLUMNS = (
    "journey_id",
    "completion_status",
    "subject_identity",
    "step_key",
    "event_identity",
    "occurred_at",
    "elapsed_from_start",
    "elapsed_from_previous",
)


@dataclass(frozen=True)
class _ResolvedStep:
    step: PatternStep
    details: EventDetails
    endpoint: Ref[EntityKind]
    subject_identity: tuple[str, ...]
    datasource_name: str
    event_fingerprint: str


@dataclass(frozen=True)
class _Occurrence:
    event_ref: Ref[EventKind]
    participant_name: str
    event_identity: tuple[object, ...]
    subject_identity: tuple[object, ...]
    occurred_at: pd.Timestamp

    @property
    def occurrence_key(self) -> tuple[str, tuple[object, ...]]:
        return (self.event_ref.key, self.event_identity)


def _repair(action: str, *, snippet: str | None = None) -> AnalysisRepair:
    return AnalysisRepair(
        kind="retry",
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="events.match"),
        snippet=snippet,
    )


def _canonical_scalar(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        converted = item()
        if converted is not value:
            return _canonical_scalar(converted)
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return repr(value)


def _identity_component_sort_key(value: object) -> tuple[int, Decimal | str]:
    item = getattr(value, "item", None)
    if callable(item):
        converted = item()
        if converted is not value:
            return _identity_component_sort_key(converted)
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, Decimal(int(value)))
    if isinstance(value, (int, float, Decimal)):
        return (2, Decimal(str(value)))
    if isinstance(value, pd.Timestamp):
        timestamp = value
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC")
        return (3, timestamp.isoformat())
    if isinstance(value, datetime):
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC")
        return (3, timestamp.isoformat())
    if isinstance(value, date):
        return (3, value.isoformat())
    if isinstance(value, str):
        return (4, value)
    if isinstance(value, bytes):
        return (5, value.hex())
    value_type = type(value)
    return (6, f"{value_type.__module__}.{value_type.__qualname__}:{value!r}")


def _identity_sort_key(
    identity: tuple[object, ...],
) -> tuple[tuple[int, Decimal | str], ...]:
    return tuple(_identity_component_sort_key(component) for component in identity)


def _stable_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _journey_id(
    *,
    subject_identity: tuple[object, ...],
    pattern_fingerprint: str,
    matching: EventMatchingPolicy,
    anchor: _Occurrence,
) -> str:
    digest = _stable_digest(
        {
            "operator_version": "events.match/v1",
            "subject_identity": [_canonical_scalar(item) for item in subject_identity],
            "pattern_fingerprint": pattern_fingerprint,
            "matching": matching.model_dump(mode="json"),
            "anchor_event_ref": anchor.event_ref.key,
            "anchor_event_identity": [_canonical_scalar(item) for item in anchor.event_identity],
        }
    )
    return f"journey_{digest.removeprefix('sha256:')}"


def _parse_bound(value: object, *, report_tz: ZoneInfo, label: str) -> pd.Timestamp:
    if type(value) is not str or not value.strip():
        raise InvalidEventPatternError(
            message=f"events.match {label} must be a non-empty ISO-8601 string",
            expected="an ISO-8601 date or datetime string",
            received=repr(value),
            repair=_repair("Pass an explicit ISO-8601 cohort window and completion_through bound."),
        )
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        if len(raw) == 10 and "T" not in raw:
            parsed = datetime.combine(date.fromisoformat(raw), time.min, tzinfo=report_tz)
        else:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=report_tz)
    except (TypeError, ValueError) as exc:
        raise InvalidEventPatternError(
            message=f"events.match {label} is not a valid ISO-8601 bound",
            expected="an ISO-8601 date or datetime string",
            received=repr(value),
            repair=_repair("Pass an explicit ISO-8601 cohort window and completion_through bound."),
        ) from exc
    return pd.Timestamp(parsed.astimezone(UTC))


def _inclusive_successor(
    value: str,
    *,
    report_tz: ZoneInfo,
    granularity: str,
) -> str:
    bound = _parse_bound(value, report_tz=report_tz, label="completion_through")
    local_bound = bound.tz_convert(report_tz)
    if granularity == "year":
        successor = local_bound + pd.DateOffset(years=1)
    elif granularity == "quarter":
        successor = local_bound + pd.DateOffset(months=3)
    elif granularity == "month":
        successor = local_bound + pd.DateOffset(months=1)
    elif granularity == "week":
        successor = local_bound + pd.DateOffset(weeks=1)
    elif granularity == "day":
        successor = local_bound + pd.DateOffset(days=1)
    elif granularity == "hour":
        successor = local_bound + pd.Timedelta(hours=1)
    elif granularity == "minute":
        successor = local_bound + pd.Timedelta(minutes=1)
    elif granularity == "second":
        successor = local_bound + pd.Timedelta(seconds=1)
    else:
        raise InvalidEventPatternError(
            message="Event occurred_at has an unsupported granularity",
            expected="year | quarter | month | week | day | hour | minute | second",
            received=repr(granularity),
            repair=_repair("Repair the Event occurred_at TimeDimension granularity."),
        )
    return successor.isoformat()


def _resolve_pattern(
    *,
    session: Session,
    pattern: EventPattern,
    matching: EventMatchingPolicy,
    cohort_window: TimeScope,
    completion_through: str,
    completeness: tuple[CompletenessDeclaration, ...],
) -> tuple[
    tuple[_ResolvedStep, ...],
    pd.Timestamp,
    pd.Timestamp,
    pd.Timestamp,
    dict[Ref[EventKind], CompletenessDeclaration],
]:
    if not isinstance(pattern, EventPattern) or not pattern.steps:
        raise InvalidEventPatternError(
            message="events.match requires a non-empty typed EventPattern",
            expected="mv.sequence(mv.step(...), ...)",
            received=type(pattern).__name__,
            repair=_repair("Build the pattern with mv.step(...) and mv.sequence(...)."),
        )
    if not isinstance(matching, (FirstPerSubject, EveryStart)):
        raise InvalidEventMatchingPolicyError(
            message="events.match requires a typed matching policy",
            expected="mv.first_per_subject() or mv.every_start(...)",
            received=type(matching).__name__,
            repair=_repair("Choose mv.first_per_subject() or mv.every_start(...)."),
        )
    if not isinstance(cohort_window, TimeScope):
        raise InvalidEventPatternError(
            message="events.match cohort_window must be mv.TimeScope",
            expected="mv.TimeScope(start=<inclusive>, end=<exclusive>)",
            received=type(cohort_window).__name__,
            repair=_repair("Construct cohort_window with mv.TimeScope(start=..., end=...)."),
        )
    if type(completeness) is not tuple or any(
        not isinstance(item, CompletenessDeclaration) for item in completeness
    ):
        raise InvalidCompletenessDeclarationError(
            message="events.match completeness must be a tuple of typed declarations",
            expected="tuple[mv.CompletenessDeclaration, ...]",
            received=type(completeness).__name__,
            repair=_repair("Pass completeness=(mv.declared_complete_through(...),), or omit it."),
        )

    report_tz = cast("ZoneInfo", session.report_tz)
    cohort_start = _parse_bound(
        cohort_window.start,
        report_tz=report_tz,
        label="cohort_window.start",
    )
    cohort_end = _parse_bound(
        cohort_window.end,
        report_tz=report_tz,
        label="cohort_window.end",
    )
    completion = _parse_bound(
        completion_through,
        report_tz=report_tz,
        label="completion_through",
    )
    if cohort_start >= cohort_end:
        raise InvalidEventPatternError(
            message="events.match cohort_window must be a non-empty half-open interval",
            expected="cohort_window.start < cohort_window.end",
            received=f"{cohort_window.start!r} .. {cohort_window.end!r}",
            repair=_repair("Choose a cohort start strictly before the exclusive end."),
        )
    if completion < cohort_end:
        raise InvalidEventPatternError(
            message="events.match completion_through cannot precede the cohort end",
            expected="completion_through >= cohort_window.end",
            received=f"{completion_through!r} < {cohort_window.end!r}",
            repair=_repair("Extend completion_through to the cohort end or later."),
        )

    registry = session.catalog._require_index().registry
    resolved: list[_ResolvedStep] = []
    endpoint_ref: Ref[EntityKind] | None = None
    subject_identity: tuple[str, ...] | None = None
    step_keys: set[str] = set()
    for raw_step in pattern.steps:
        if not isinstance(raw_step, PatternStep):
            raise InvalidEventPatternError(
                message="EventPattern contains a non-PatternStep value",
                expected="only mv.step(...) values",
                received=type(raw_step).__name__,
                repair=_repair("Rebuild the pattern using only mv.step(...) values."),
            )
        if raw_step.key in step_keys:
            raise InvalidEventPatternError(
                message=f"EventPattern repeats step key {raw_step.key!r}",
                expected="unique snake_case step keys",
                received=raw_step.key,
                repair=_repair("Give every pattern step a unique snake_case key."),
            )
        step_keys.add(raw_step.key)
        if type(raw_step.event) is not Ref or raw_step.event.kind is not SemanticKind.EVENT:
            raise InvalidEventPatternError(
                message=f"step {raw_step.key!r} does not reference an exact Event",
                expected="a ParticipantRoleHandle created by ms.participant_role(...)",
                received=repr(raw_step.event),
                repair=_repair(
                    "Create the participant with ms.participant_role(event=..., name=...)."
                ),
            )
        try:
            entry = session.catalog.require(raw_step.event)
        except Exception as exc:
            raise PatternStepMismatchError(
                message=f"step {raw_step.key!r} Event is not loaded in this catalog",
                expected="an exact EventRef from session.catalog.events",
                received=raw_step.event.key,
                repair=_repair("Load the Event from this session catalog and rebuild the step."),
            ) from exc
        if not isinstance(entry, EventEntry):
            raise PatternStepMismatchError(
                message=f"step {raw_step.key!r} did not resolve to an Event",
                expected="EventEntry",
                received=type(entry).__name__,
                repair=_repair("Use an Event participant role from the active catalog."),
            )
        details = entry.details()
        role = next(
            (
                (name, endpoint, cardinality, path)
                for name, endpoint, cardinality, path in details.participants
                if name == raw_step.participant.name
            ),
            None,
        )
        if role is None:
            available = tuple(item[0] for item in details.participants)
            raise PatternStepMismatchError(
                message=(
                    f"step {raw_step.key!r} participant {raw_step.participant.name!r} "
                    "is not declared on the Event"
                ),
                expected=f"one of {available!r}",
                received=raw_step.participant.name,
                repair=_repair("Choose a declared participant name and rebuild the role handle."),
            )
        _name, endpoint, cardinality, _path = role
        if cardinality != "one":
            raise EventParticipantCardinalityError(
                message=(
                    f"step {raw_step.key!r} participant cannot be an analysis subject "
                    f"because its cardinality is {cardinality!r}"
                ),
                expected="participant cardinality='one'",
                received=cardinality,
                repair=_repair(
                    "Choose a cardinality='one' participant, or repair the Event role definition."
                ),
            )
        endpoint_ir = registry.entities.get(endpoint.path)
        if endpoint_ir is None or not endpoint_ir.primary_key:
            raise PatternStepMismatchError(
                message=f"step {raw_step.key!r} participant endpoint has no usable primary key",
                expected="an endpoint Entity with a non-empty primary_key",
                received=endpoint.key,
                repair=_repair("Define the participant endpoint Entity primary_key."),
            )
        normalized_subject_identity = tuple(
            (
                component
                if component in registry.dimensions
                else (
                    f"{endpoint.path}.{component}"
                    if f"{endpoint.path}.{component}" in registry.dimensions
                    else component
                )
            )
            for component in endpoint_ir.primary_key
        )
        if endpoint_ref is None:
            endpoint_ref = endpoint
            subject_identity = normalized_subject_identity
        elif endpoint != endpoint_ref:
            raise PatternStepMismatchError(
                message="all EventPattern participant roles must resolve to one subject Entity",
                expected=endpoint_ref.key,
                received=endpoint.key,
                repair=_repair("Choose participant roles whose endpoints are the same Entity."),
            )
        elif normalized_subject_identity != subject_identity:
            raise PatternStepMismatchError(
                message="EventPattern participant roles disagree on subject identity",
                expected=repr(subject_identity),
                received=repr(normalized_subject_identity),
                repair=_repair("Reload one coherent catalog and rebuild the pattern."),
            )
        event_ir = registry.events[raw_step.event.path]
        source_ir = registry.entities[event_ir.source_entity]
        resolved.append(
            _ResolvedStep(
                step=raw_step,
                details=details,
                endpoint=endpoint,
                subject_identity=normalized_subject_identity,
                datasource_name=source_ir.datasource,
                event_fingerprint=details.definition_fingerprint,
            )
        )

    pattern_events = {item.step.event for item in resolved}
    declaration_by_event: dict[Ref[EventKind], CompletenessDeclaration] = {}
    for declaration in completeness:
        declaration_through = _parse_declaration_through(
            declaration,
            report_tz=report_tz,
        )
        if declaration_through < completion:
            raise InvalidCompletenessDeclarationError(
                message="completeness declaration does not cover completion_through",
                expected=f"through >= {completion_through!r}",
                received=repr(declaration.through),
                repair=_repair("Extend the declaration through completion_through or remove it."),
            )
        for event_ref in declaration.inputs:
            if event_ref not in pattern_events:
                raise InvalidCompletenessDeclarationError(
                    message="completeness declaration references an Event outside the pattern",
                    expected="only EventRefs used by the current EventPattern",
                    received=event_ref.key,
                    repair=_repair("Remove the unrelated EventRef from the declaration."),
                )
            previous = declaration_by_event.get(event_ref)
            if previous is not None:
                raise InvalidCompletenessDeclarationError(
                    message="one pattern Event is covered by multiple declarations",
                    expected="at most one declaration per EventRef",
                    received=event_ref.key,
                    repair=_repair("Merge the overlapping completeness declarations."),
                )
            declaration_by_event[event_ref] = declaration

    return (
        tuple(resolved),
        cohort_start,
        cohort_end,
        completion,
        declaration_by_event,
    )


def _parse_declaration_through(
    declaration: CompletenessDeclaration,
    *,
    report_tz: ZoneInfo,
) -> pd.Timestamp:
    try:
        return _parse_bound(
            declaration.through,
            report_tz=report_tz,
            label="completeness.through",
        )
    except InvalidEventPatternError as exc:
        raise InvalidCompletenessDeclarationError(
            message="completeness declaration has an invalid through bound",
            expected="an ISO-8601 date or datetime string",
            received=repr(declaration.through),
            repair=_repair("Rebuild the declaration with a valid through bound."),
        ) from exc


def _event_groups(
    resolved: tuple[_ResolvedStep, ...],
) -> tuple[tuple[Ref[EventKind], tuple[str, ...], _ResolvedStep], ...]:
    grouped: dict[Ref[EventKind], tuple[list[str], _ResolvedStep]] = {}
    for item in resolved:
        current = grouped.get(item.step.event)
        if current is None:
            current = ([], item)
            grouped[item.step.event] = current
        if item.step.participant.name not in current[0]:
            current[0].append(item.step.participant.name)
    return tuple(
        (event_ref, tuple(role_names), representative)
        for event_ref, (role_names, representative) in grouped.items()
    )


def _time_adapter(
    *,
    session: Session,
    resolver: Any,
    source_entity: str,
    occurred_at: str,
) -> Any:
    entity = _entity_details(session.catalog, source_entity)
    adapter = _build_entity_adapter(session.catalog, resolver, entity)
    for field in adapter.fields.values():
        if field.semantic_id == occurred_at:
            if field.time_meta is None:
                raise InvalidEventPatternError(
                    message=f"Event occurred_at {occurred_at!r} has no time metadata",
                    expected="a governed TimeDimension",
                    received=occurred_at,
                    repair=_repair("Repair the Event occurred_at TimeDimension definition."),
                )
            return adapter, field
    raise InvalidEventPatternError(
        message=f"Event occurred_at {occurred_at!r} is not on its source Entity",
        expected=f"a TimeDimension owned by {source_entity!r}",
        received=occurred_at,
        repair=_repair("Repair the Event occurred_at reference."),
    )


def _query_events(
    *,
    session: Session,
    resolved: tuple[_ResolvedStep, ...],
    cohort_window: TimeScope,
    completion_through: str,
    completion: pd.Timestamp,
) -> tuple[
    dict[tuple[Ref[EventKind], str], tuple[_Occurrence, ...]],
    tuple[Any, ...],
]:
    registry = session.catalog._require_index().registry
    resolver = session.catalog._semantic_resolver(connections=session._connection_runtime)
    occurrence_sets: dict[tuple[Ref[EventKind], str], tuple[_Occurrence, ...]] = {}
    report_tz = cast("ZoneInfo", session.report_tz)
    session._connection_runtime.begin_query_capture()
    try:
        for event_ref, role_names, representative in _event_groups(resolved):
            event_ir = registry.events[event_ref.path]
            table = resolver.event(event_ref, participants=role_names)
            entity_adapter, occurred_adapter = _time_adapter(
                session=session,
                resolver=resolver,
                source_entity=event_ir.source_entity,
                occurred_at=event_ir.occurred_at,
            )
            datasource_name = representative.datasource_name
            read_tz = datasource_read_timezone(
                session._connection_runtime,
                datasource_name,
            )
            profile = datasource_engine_profile(
                session._connection_runtime,
                datasource_name,
            )
            query_window = AbsoluteWindow(
                start=cohort_window.start,
                end=_inclusive_successor(
                    completion_through,
                    report_tz=report_tz,
                    granularity=occurred_adapter.time_meta.granularity,
                ),
            )
            lower, upper = _window_bound_predicates(
                table["__occurred_at"],
                query_window,
                occurred_adapter.time_meta,
                report_tz=report_tz,
                datasource_read_tz=read_tz,
                profile=profile,
            )
            result = execute(
                table.filter(lower, upper),
                datasource_name=datasource_name,
                cache=session._connection_runtime,
                session_id=session.id,
            )
            normalized = _normalize_event_rows(
                result=result,
                event_ref=event_ref,
                role_names=role_names,
                identity_count=len(event_ir.identity),
                subject_identity_count={
                    item.step.participant.name: len(item.subject_identity)
                    for item in resolved
                    if item.step.event == event_ref
                },
                time_meta=occurred_adapter.time_meta,
                dataset_adapter=entity_adapter,
                report_tz=report_tz,
                datasource_read_tz=read_tz,
            )
            occurrence_sets.update(
                {
                    key: tuple(
                        occurrence
                        for occurrence in occurrences
                        if occurrence.occurred_at <= completion
                    )
                    for key, occurrences in normalized.items()
                }
            )
    except BaseException:
        session._connection_runtime.take_captured_queries()
        raise
    return occurrence_sets, tuple(session._connection_runtime.take_captured_queries())


def _is_empty_component(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value:
        return True
    missing = pd.isna(cast("Any", value))
    return bool(missing) if isinstance(missing, bool) else False


def _normalize_timestamp(
    value: object,
    *,
    column_tz: ZoneInfo,
    decode_policy: str,
) -> pd.Timestamp | None:
    if _is_empty_component(value):
        return None
    try:
        timestamp = pd.Timestamp(cast("Any", value))
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timezone = ZoneInfo("UTC") if decode_policy == "utc_naive_instant" else column_tz
        timestamp = timestamp.tz_localize(timezone)
    return timestamp.tz_convert("UTC")


def _normalize_event_rows(
    *,
    result: ExecutionResult,
    event_ref: Ref[EventKind],
    role_names: tuple[str, ...],
    identity_count: int,
    subject_identity_count: dict[str, int],
    time_meta: Any,
    dataset_adapter: Any,
    report_tz: ZoneInfo,
    datasource_read_tz: ZoneInfo,
) -> dict[tuple[Ref[EventKind], str], tuple[_Occurrence, ...]]:
    frame = result.df.copy()
    identity_columns = tuple(f"__event_identity_{index}" for index in range(identity_count))
    required = {
        *identity_columns,
        "__occurred_at",
        "__source_identity_count",
    }
    for role_name in role_names:
        required.update(
            f"__subject_{role_name}_identity_{index}"
            for index in range(subject_identity_count[role_name])
        )
    missing = required - set(frame.columns)
    if missing:
        raise EventIdentityError(
            message=f"materialized Event {event_ref.key!r} is missing identity columns",
            expected=repr(sorted(required)),
            received=repr(sorted(frame.columns)),
            repair=_repair("Inspect the Event identity and participant path definitions."),
        )

    context = effective_time_context(
        time_meta,
        report_tz=report_tz,
        datasource_read_tz=datasource_read_tz,
        field_expr=None,
        backend_policy=cast("Any", result.backend_datetime_decode_policy),
    )
    column_tz = context.effective_column_tz or datasource_read_tz
    by_role: dict[tuple[Ref[EventKind], str], list[_Occurrence]] = {
        (event_ref, name): [] for name in role_names
    }
    seen_event_ids: set[tuple[object, ...]] = set()
    for row in frame.to_dict("records"):
        event_identity = tuple(row[column] for column in identity_columns)
        try:
            source_identity_count = int(row["__source_identity_count"])
        except (TypeError, ValueError) as exc:
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} produced an invalid source identity count",
                expected="exactly one source occurrence per declared Event identity",
                received=repr(row["__source_identity_count"]),
                repair=_repair("Repair the Event identity dimensions or source data."),
            ) from exc
        if source_identity_count != 1:
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} declared identity is not unique",
                expected="one source occurrence per declared Event identity",
                received=(
                    f"event_identity={event_identity!r}, "
                    f"source_identity_count={source_identity_count}"
                ),
                repair=_repair("Repair the Event identity dimensions or source data."),
            )
        if any(_is_empty_component(component) for component in event_identity):
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} produced an empty identity component",
                expected="a non-null, non-empty declared Event identity tuple",
                received=repr(event_identity),
                repair=_repair("Repair the Event identity dimensions or source data."),
            )
        if event_identity in seen_event_ids:
            raise EventParticipantCardinalityError(
                message=(
                    f"Event {event_ref.key!r} participant join produced more than "
                    "one row for an occurrence"
                ),
                expected="exactly one endpoint for every cardinality='one' role",
                received=repr(event_identity),
                repair=_repair("Repair participant relationship keys or role cardinality."),
            )
        seen_event_ids.add(event_identity)
        occurred_at = _normalize_timestamp(
            row["__occurred_at"],
            column_tz=column_tz,
            decode_policy=result.backend_datetime_decode_policy,
        )
        if occurred_at is None:
            raise EventIdentityError(
                message=f"Event {event_ref.key!r} produced an invalid occurred_at value",
                expected="a non-null governed timestamp",
                received=repr(row["__occurred_at"]),
                repair=_repair("Repair the Event occurred_at TimeDimension or source data."),
            )
        for role_name in role_names:
            columns = tuple(
                f"__subject_{role_name}_identity_{index}"
                for index in range(subject_identity_count[role_name])
            )
            subject_identity = tuple(row[column] for column in columns)
            if any(_is_empty_component(component) for component in subject_identity):
                raise EventParticipantCardinalityError(
                    message=(
                        f"Event {event_ref.key!r} participant {role_name!r} "
                        "did not resolve to exactly one subject"
                    ),
                    expected="one non-null participant endpoint identity",
                    received=repr(subject_identity),
                    repair=_repair("Repair missing participant join keys or choose another role."),
                )
            by_role[(event_ref, role_name)].append(
                _Occurrence(
                    event_ref=event_ref,
                    participant_name=role_name,
                    event_identity=event_identity,
                    subject_identity=subject_identity,
                    occurred_at=occurred_at,
                )
            )
    return {
        key: tuple(
            sorted(
                values,
                key=lambda item: (
                    item.occurred_at,
                    _identity_sort_key(item.event_identity),
                ),
            )
        )
        for key, values in by_role.items()
    }


def _candidate_after(
    *,
    previous: _Occurrence,
    candidates: tuple[_Occurrence, ...],
    used: set[tuple[str, tuple[object, ...]]],
    excluded: set[tuple[str, tuple[object, ...]]],
) -> _Occurrence | None:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.subject_identity == previous.subject_identity
        and candidate.occurrence_key not in used
        and candidate.occurrence_key not in excluded
        and candidate.occurred_at >= previous.occurred_at
    ]
    for candidate in eligible:
        if candidate.occurred_at > previous.occurred_at:
            return candidate
        if candidate.event_ref != previous.event_ref:
            raise AmbiguousEventOrderError(
                message="different EventRefs occur at the same time where order changes matching",
                expected="distinct timestamps for cross-Event ordering",
                received=(
                    f"{previous.event_ref.key} and {candidate.event_ref.key} "
                    f"at {candidate.occurred_at.isoformat()}"
                ),
                repair=_repair(
                    "Model a more precise occurred_at timestamp or remove the ambiguous ordering."
                ),
            )
        if _identity_sort_key(candidate.event_identity) > _identity_sort_key(
            previous.event_identity
        ):
            return candidate
    return None


def _attempt(
    *,
    anchor: _Occurrence,
    step_occurrences: tuple[tuple[_Occurrence, ...], ...],
    exclusive_final: set[tuple[str, tuple[object, ...]]],
) -> tuple[_Occurrence | None, ...]:
    matched: list[_Occurrence | None] = [anchor]
    used = {anchor.occurrence_key}
    previous = anchor
    final_index = len(step_occurrences) - 1
    for index, candidates in enumerate(step_occurrences[1:], start=1):
        candidate = _candidate_after(
            previous=previous,
            candidates=candidates,
            used=used,
            excluded=exclusive_final if index == final_index else set(),
        )
        if candidate is None:
            matched.extend([None] * (len(step_occurrences) - len(matched)))
            break
        matched.append(candidate)
        used.add(candidate.occurrence_key)
        previous = candidate
    return tuple(matched)


def _match_rows(
    *,
    pattern: EventPattern,
    matching: EventMatchingPolicy,
    resolved: tuple[_ResolvedStep, ...],
    occurrence_sets: dict[tuple[Ref[EventKind], str], tuple[_Occurrence, ...]],
    cohort_start: pd.Timestamp,
    cohort_end: pd.Timestamp,
    coverage_complete: bool,
) -> tuple[pd.DataFrame, int]:
    materialized_pattern_fingerprint = _stable_digest(
        {
            "pattern": pattern.fingerprint,
            "event_fingerprints": [
                {
                    "step_key": item.step.key,
                    "event": item.step.event.key,
                    "fingerprint": item.event_fingerprint,
                }
                for item in resolved
            ],
        }
    )
    step_occurrences = tuple(
        occurrence_sets[(item.step.event, item.step.participant.name)] for item in resolved
    )
    anchors = tuple(
        item for item in step_occurrences[0] if cohort_start <= item.occurred_at < cohort_end
    )
    anchors_by_subject: dict[tuple[object, ...], list[_Occurrence]] = {}
    for anchor in anchors:
        anchors_by_subject.setdefault(anchor.subject_identity, []).append(anchor)
    selected_anchors: list[_Occurrence] = []
    for subject in sorted(anchors_by_subject, key=_identity_sort_key):
        subject_anchors = sorted(
            anchors_by_subject[subject],
            key=lambda item: (
                item.occurred_at,
                _identity_sort_key(item.event_identity),
            ),
        )
        if isinstance(matching, FirstPerSubject):
            selected_anchors.append(subject_anchors[0])
        else:
            selected_anchors.extend(subject_anchors)

    exclusive_final: set[tuple[str, tuple[object, ...]]] = set()
    used_occurrences: set[tuple[str, tuple[object, ...]]] = set()
    rows: list[dict[str, object]] = []
    for anchor in selected_anchors:
        matched = _attempt(
            anchor=anchor,
            step_occurrences=step_occurrences,
            exclusive_final=exclusive_final,
        )
        final_occurrence = matched[-1]
        if (
            isinstance(matching, EveryStart)
            and matching.completion_assignment == "exclusive"
            and final_occurrence is not None
        ):
            exclusive_final.add(final_occurrence.occurrence_key)
        complete = all(item is not None for item in matched)
        status: Literal["complete", "incomplete", "coverage_censored"]
        status = (
            "complete" if complete else "incomplete" if coverage_complete else "coverage_censored"
        )
        journey_id = _journey_id(
            subject_identity=anchor.subject_identity,
            pattern_fingerprint=materialized_pattern_fingerprint,
            matching=matching,
            anchor=anchor,
        )
        previous_present: _Occurrence | None = None
        for item, resolved_step in zip(matched, resolved, strict=True):
            if item is None:
                event_identity: tuple[object, ...] | None = None
                occurred_at: pd.Timestamp | None = None
                elapsed_start: pd.Timedelta | None = None
                elapsed_previous: pd.Timedelta | None = None
            else:
                used_occurrences.add(item.occurrence_key)
                event_identity = item.event_identity
                occurred_at = item.occurred_at
                elapsed_start = item.occurred_at - anchor.occurred_at
                elapsed_previous = (
                    item.occurred_at - previous_present.occurred_at
                    if previous_present is not None
                    else pd.Timedelta(0)
                )
                previous_present = item
            rows.append(
                {
                    "journey_id": journey_id,
                    "completion_status": status,
                    "subject_identity": anchor.subject_identity,
                    "step_key": resolved_step.step.key,
                    "event_identity": event_identity,
                    "occurred_at": occurred_at,
                    "elapsed_from_start": elapsed_start,
                    "elapsed_from_previous": elapsed_previous,
                }
            )

    all_occurrences = {
        occurrence.occurrence_key
        for occurrences in occurrence_sets.values()
        for occurrence in occurrences
    }
    unused_count = len(all_occurrences - used_occurrences)
    frame = pd.DataFrame(rows, columns=_ROW_COLUMNS)
    if not frame.empty:
        frame["occurred_at"] = pd.to_datetime(frame["occurred_at"], utc=True)
        frame["elapsed_from_start"] = pd.to_timedelta(frame["elapsed_from_start"])
        frame["elapsed_from_previous"] = pd.to_timedelta(frame["elapsed_from_previous"])
    return frame, unused_count


def _coverage(
    *,
    session: Session,
    resolved: tuple[_ResolvedStep, ...],
    completion: pd.Timestamp,
    completion_through: str,
    declaration_by_event: dict[Ref[EventKind], CompletenessDeclaration],
) -> tuple[
    tuple[EventInputCoverage, ...],
    Literal["observed_watermark", "declared_complete", "mixed", "unknown"],
]:
    registry = session.catalog._require_index().registry
    report_tz = cast("ZoneInfo", session.report_tz)
    items: list[EventInputCoverage] = []
    seen: set[Ref[EventKind]] = set()
    for resolved_step in resolved:
        event_ref = resolved_step.step.event
        if event_ref in seen:
            continue
        seen.add(event_ref)
        event_ir = registry.events[event_ref.path]
        request = EventWatermarkRequest(
            event_ref=event_ref,
            event_fingerprint=resolved_step.event_fingerprint,
            source_entity_ref=event_ir.source_entity,
            occurred_at_ref=event_ir.occurred_at,
            required_through=completion_through,
        )
        raw_receipt = session._connection_runtime.event_watermark(
            resolved_step.datasource_name,
            request,
        )
        receipt: EventWatermarkReceipt | None = None
        valid_receipt: EventWatermarkReceipt | None = None
        if raw_receipt is not None:
            try:
                candidate = EventWatermarkReceipt.model_validate(raw_receipt)
                receipt_complete = _parse_bound(
                    candidate.complete_through,
                    report_tz=report_tz,
                    label="watermark.complete_through",
                )
                _parse_bound(
                    candidate.observed_at,
                    report_tz=report_tz,
                    label="watermark.observed_at",
                )
            except (TypeError, ValueError, InvalidEventPatternError):
                candidate = None
            if candidate is not None:
                valid_receipt = candidate
                if receipt_complete >= completion:
                    receipt = candidate
        if receipt is not None:
            items.append(
                EventInputCoverage(
                    event_ref=RefPayloadV1.from_ref(event_ref),
                    basis="observed_watermark",
                    receipt=receipt,
                    observed_complete_through=receipt.complete_through,
                )
            )
            continue
        declaration = declaration_by_event.get(event_ref)
        if (
            declaration is not None
            and _parse_declaration_through(
                declaration,
                report_tz=report_tz,
            )
            >= completion
        ):
            items.append(
                EventInputCoverage(
                    event_ref=RefPayloadV1.from_ref(event_ref),
                    basis="declared_complete",
                    receipt=valid_receipt,
                    declaration_fingerprint=declaration.fingerprint,
                    declaration_rationale=declaration.rationale,
                    observed_complete_through=(
                        valid_receipt.complete_through if valid_receipt is not None else None
                    ),
                )
            )
            continue
        items.append(
            EventInputCoverage(
                event_ref=RefPayloadV1.from_ref(event_ref),
                basis="unknown",
                receipt=valid_receipt,
                observed_complete_through=(
                    valid_receipt.complete_through if valid_receipt is not None else None
                ),
            )
        )

    bases = {item.basis for item in items}
    if "unknown" in bases:
        aggregate: Literal["observed_watermark", "declared_complete", "mixed", "unknown"] = (
            "unknown"
        )
    elif bases == {"observed_watermark"}:
        aggregate = "observed_watermark"
    elif bases == {"declared_complete"}:
        aggregate = "declared_complete"
    else:
        aggregate = "mixed"
    return tuple(items), aggregate


def _snapshot_fingerprint(
    occurrence_sets: dict[tuple[Ref[EventKind], str], tuple[_Occurrence, ...]],
) -> str:
    payload = [
        {
            "event": event_ref.key,
            "participant": role,
            "event_identity": [
                [_canonical_scalar(component) for component in item.event_identity]
                for item in occurrences
            ],
            "subject_identity": [
                [_canonical_scalar(component) for component in item.subject_identity]
                for item in occurrences
            ],
            "occurred_at": [item.occurred_at.isoformat() for item in occurrences],
        }
        for (event_ref, role), occurrences in sorted(
            occurrence_sets.items(),
            key=lambda item: (item[0][0].key, item[0][1]),
        )
    ]
    return _stable_digest(payload)


def _rollback_event_commit(
    *,
    session: Session,
    evidence_store: Any,
    artifact_id: str,
    job_ref: str,
    preserve_artifact: bool,
) -> None:
    """Best-effort rollback for one Event Journey persistence transaction."""
    cleanup_actions: list[Callable[[], object]] = [
        lambda: session._store.delete_job(session.id, job_ref),
        lambda: (session._layout.jobs_dir / f"{job_ref}.json").unlink(missing_ok=True),
    ]
    if not preserve_artifact:
        cleanup_actions.extend(
            [
                lambda: session._store.delete_artifact(session.id, artifact_id),
                lambda: rollback_committed_result(
                    store=evidence_store,
                    frames_dir=session._layout.frames_dir,
                    artifact_id=artifact_id,
                ),
            ]
        )
    for cleanup in cleanup_actions:
        try:
            cleanup()
        except BaseException:
            continue


def match(
    *,
    pattern: EventPattern,
    cohort_window: TimeScope,
    completion_through: str,
    matching: EventMatchingPolicy,
    completeness: tuple[CompletenessDeclaration, ...] = (),
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> EventFrame:
    """Match a typed EventPattern into one dense EventFrame[journey]."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_writable(resolved_session)
    (
        resolved,
        cohort_start,
        cohort_end,
        completion,
        declaration_by_event,
    ) = _resolve_pattern(
        session=resolved_session,
        pattern=pattern,
        matching=matching,
        cohort_window=cohort_window,
        completion_through=completion_through,
        completeness=completeness,
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    occurrence_sets, queries = _query_events(
        session=resolved_session,
        resolved=resolved,
        cohort_window=cohort_window,
        completion_through=completion_through,
        completion=completion,
    )
    input_coverage, coverage_basis = _coverage(
        session=resolved_session,
        resolved=resolved,
        completion=completion,
        completion_through=completion_through,
        declaration_by_event=declaration_by_event,
    )
    output, unused_count = _match_rows(
        pattern=pattern,
        matching=matching,
        resolved=resolved,
        occurrence_sets=occurrence_sets,
        cohort_start=cohort_start,
        cohort_end=cohort_end,
        coverage_complete=coverage_basis != "unknown",
    )
    job_ref = gen_ref("job")
    initial_ref = gen_ref("frame")
    finished_at = datetime.now(UTC)
    event_fingerprints = {item.step.event.path: item.event_fingerprint for item in resolved}
    event_identity_components = {
        item.step.event.path: tuple(
            RefPayloadV1.from_ref(component) for component in item.details.identity
        )
        for item in resolved
    }
    role_endpoints = {item.step.key: RefPayloadV1.from_ref(item.endpoint) for item in resolved}
    query_refs = tuple(
        query.query_id for query in queries if isinstance(getattr(query, "query_id", None), str)
    )
    params = {
        "pattern": pattern.model_dump(mode="json"),
        "matching": matching.model_dump(mode="json"),
        "cohort_window": cohort_window.model_dump(mode="json"),
        "completion_through": completion_through,
        "completeness": [declaration.model_dump(mode="json") for declaration in completeness],
        "input_coverage": [item.model_dump(mode="json") for item in input_coverage],
        "coverage_basis": coverage_basis,
        "event_fingerprints": dict(sorted(event_fingerprints.items())),
        "event_identity_components": {
            key: [component.to_dict() for component in components]
            for key, components in sorted(event_identity_components.items())
        },
        "role_endpoints": {key: value.to_dict() for key, value in sorted(role_endpoints.items())},
        "snapshot_fingerprint": _snapshot_fingerprint(occurrence_sets),
    }
    subject_identity = resolved[0].subject_identity
    subject_entity_ref = RefPayloadV1.from_ref(resolved[0].endpoint)
    frame = EventFrame(
        _df=output,
        meta=EventFrameMeta(
            ref=initial_ref,
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(output),
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="events.match",
                        job_ref=job_ref,
                        inputs=[
                            item.key for item in dict.fromkeys(step.event for step in pattern.steps)
                        ],
                        params_digest=params_digest(params),
                        params={
                            "pattern_fingerprint": pattern.fingerprint,
                            "coverage_basis": coverage_basis,
                        },
                        analysis_purpose=analysis_purpose,
                    )
                ],
                external_inputs=sorted(
                    item.key for item in dict.fromkeys(step.event for step in pattern.steps)
                ),
            ),
            catalog_definition_fingerprint=resolved_session.catalog.definition_fingerprint,
            subject_entity_ref=subject_entity_ref,
            subject_identity=subject_identity,
            pattern=pattern,
            matching=matching,
            cohort_window=cohort_window,
            completion_through=completion_through,
            completeness=completeness,
            input_coverage=input_coverage,
            coverage_basis=coverage_basis,
            event_fingerprints=event_fingerprints,
            event_identity_components=event_identity_components,
            role_endpoints=role_endpoints,
            query_refs=query_refs,
            unused_event_count=unused_count,
        ),
    )
    commit_inputs = CommitInputs(
        input_refs=[item.key for item in dict.fromkeys(step.event for step in pattern.steps)]
    )
    commit_params = CommitParams(values=params)
    commit_anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=resolved_session.catalog.definition_fingerprint,
    )
    prospective_id = compute_prospective_artifact_id(
        step_type="events.match",
        inputs=commit_inputs,
        params=commit_params,
        semantic_anchors=commit_anchors,
    )
    artifact_preexisting = resolved_session._store.get_artifact(
        resolved_session.id, prospective_id
    ) is not None or frame_exists_on_disk(resolved_session._layout.frames_dir, prospective_id)
    evidence_store = resolved_session._evidence_store()
    try:
        frame = cast(
            "EventFrame",
            commit_result(
                store=evidence_store,
                frames_dir=resolved_session._layout.frames_dir,
                frame=frame,
                step_type="events.match",
                inputs=commit_inputs,
                params=commit_params,
                semantic_anchors=commit_anchors,
                subject=event_subject_for_frame(frame),
                extractor_family="event_frame",
            ),
        )
        register_frame_artifact(resolved_session, frame)
        persist_job_record(
            resolved_session,
            {
                "id": job_ref,
                "session_id": resolved_session.id,
                "intent": "events.match",
                **job_semantics_from_frames(frame),
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": [],
                "output_frame_ref": frame.meta.artifact_id or frame.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": str(resolved_session.catalog.semantic_root),
                "queries": [query.to_dict() for query in queries],
            },
        )
    except BaseException:
        _rollback_event_commit(
            session=resolved_session,
            evidence_store=evidence_store,
            artifact_id=prospective_id,
            job_ref=job_ref,
            preserve_artifact=artifact_preexisting,
        )
        raise
    return frame


__all__ = ["match"]
