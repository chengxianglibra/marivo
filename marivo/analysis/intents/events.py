"""Materialize typed semantic Events into dense subject journeys."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from time import monotonic
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

import pandas as pd

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AmbiguousEventOrderError,
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
    EveryStart,
    FirstPerSubject,
    PatternStep,
)
from marivo.analysis.event import (
    _event_repair as _repair,
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
from marivo.analysis.frames.event import EventFrame, EventFrameMeta, EventInputCoverage
from marivo.analysis.frames.subject import SubjectSet
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.intents._event_occurrences import (
    EventCoverageInput,
    EventOccurrenceInput,
    materialize_event_occurrences,
    resolve_event_coverage,
)
from marivo.analysis.intents._event_occurrences import (
    EventOccurrence as _Occurrence,
)
from marivo.analysis.intents._event_occurrences import (
    canonical_scalar as _canonical_scalar,
)
from marivo.analysis.intents._event_occurrences import (
    identity_sort_key as _identity_sort_key,
)
from marivo.analysis.intents._event_occurrences import (
    occurrence_snapshot_fingerprint as _snapshot_fingerprint,
)
from marivo.analysis.intents._event_occurrences import (
    stable_digest as _stable_digest,
)
from marivo.analysis.intents._subject_cohort import (
    ResolvedSubjectCohort,
    apply_event_subject_membership,
    resolve_subject_cohort,
)
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.analysis.windows.spec import TimeScope
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
            location=f"session.events.match.{label}",
            repair=_repair(
                kind="user_choice",
                action="Pass an explicit ISO-8601 cohort window and completion_through bound.",
            ),
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
            location=f"session.events.match.{label}",
            repair=_repair(
                kind="user_choice",
                action="Pass an explicit ISO-8601 cohort window and completion_through bound.",
            ),
        ) from exc
    return pd.Timestamp(parsed.astimezone(UTC))


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
            location="session.events.match.pattern",
            repair=_repair(
                kind="user_choice",
                action="Build the pattern with mv.step(...) and mv.sequence(...).",
            ),
        )
    if not isinstance(matching, (FirstPerSubject, EveryStart)):
        raise InvalidEventMatchingPolicyError(
            message="events.match requires a typed matching policy",
            expected="mv.first_per_subject() or mv.every_start(...)",
            received=type(matching).__name__,
            location="session.events.match.matching",
            repair=_repair(
                kind="user_choice",
                action="Choose one matching policy that fits the business attempt semantics.",
                candidates=(
                    "mv.first_per_subject()",
                    'mv.every_start(completion_assignment="exclusive")',
                    'mv.every_start(completion_assignment="shared")',
                ),
            ),
        )
    if not isinstance(cohort_window, TimeScope):
        raise InvalidEventPatternError(
            message="events.match cohort_window must be mv.TimeScope",
            expected="mv.TimeScope(start=<inclusive>, end=<exclusive>)",
            received=type(cohort_window).__name__,
            location="session.events.match.cohort_window",
            repair=_repair(
                kind="user_choice",
                action="Construct cohort_window with explicit inclusive start and exclusive end.",
            ),
        )
    if type(completeness) is not tuple or any(
        not isinstance(item, CompletenessDeclaration) for item in completeness
    ):
        raise InvalidCompletenessDeclarationError(
            message="events.match completeness must be a tuple of typed declarations",
            expected="tuple[mv.CompletenessDeclaration, ...]",
            received=type(completeness).__name__,
            location="session.events.match.completeness",
            repair=_repair(
                kind="user_choice",
                action="Pass a tuple of governed completeness declarations, or omit it.",
            ),
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
            location="session.events.match.cohort_window",
            repair=_repair(
                kind="user_choice",
                action="Choose a cohort start strictly before the exclusive end.",
            ),
        )
    if completion < cohort_end:
        raise InvalidEventPatternError(
            message="events.match completion_through cannot precede the cohort end",
            expected="completion_through >= cohort_window.end",
            received=f"{completion_through!r} < {cohort_window.end!r}",
            location="session.events.match.completion_through",
            repair=_repair(
                kind="user_choice",
                action="Choose a follow-up bound at or after the cohort end.",
            ),
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
                location="session.events.match.pattern.steps",
                repair=_repair(
                    kind="user_choice",
                    action="Rebuild the pattern using only mv.step(...) values.",
                ),
            )
        if raw_step.key in step_keys:
            raise InvalidEventPatternError(
                message=f"EventPattern repeats step key {raw_step.key!r}",
                expected="unique snake_case step keys",
                received=raw_step.key,
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].key",
                repair=_repair(
                    kind="user_choice",
                    action="Give every pattern step a unique snake_case key.",
                ),
            )
        step_keys.add(raw_step.key)
        if type(raw_step.event) is not Ref or raw_step.event.kind is not SemanticKind.EVENT:
            raise InvalidEventPatternError(
                message=f"step {raw_step.key!r} does not reference an exact Event",
                expected="a ParticipantRoleHandle created by ms.participant_role(...)",
                received=repr(raw_step.event),
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].participant",
                repair=_repair(
                    kind="user_choice",
                    action=(
                        "Create the participant with ms.participant_role(event=..., name=...)."
                    ),
                ),
            )
        try:
            entry = session.catalog.require(raw_step.event)
        except Exception as exc:
            raise PatternStepMismatchError(
                message=f"step {raw_step.key!r} Event is not loaded in this catalog",
                expected="an exact EventRef from session.catalog.events",
                received=raw_step.event.key,
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].event",
                repair=_repair(
                    kind="inspect",
                    action="Inspect current catalog Events and rebuild the step from an exact role.",
                    candidates=tuple(item.ref.key for item in session.catalog.events.items[:5]),
                ),
            ) from exc
        if not isinstance(entry, EventEntry):
            raise PatternStepMismatchError(
                message=f"step {raw_step.key!r} did not resolve to an Event",
                expected="EventEntry",
                received=type(entry).__name__,
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].event",
                repair=_repair(
                    kind="inspect",
                    action="Inspect current catalog Events and use one of their participant roles.",
                    candidates=tuple(item.ref.key for item in session.catalog.events.items[:5]),
                ),
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
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].participant",
                repair=_repair(
                    kind="user_choice",
                    action="Choose a declared participant name and rebuild the role handle.",
                    candidates=available[:5],
                ),
            )
        _name, endpoint, cardinality, _path = role
        if cardinality != "one":
            cardinality_one_roles = tuple(
                name
                for name, _candidate_endpoint, candidate_cardinality, _candidate_path in (
                    details.participants
                )
                if candidate_cardinality == "one"
            )
            if cardinality_one_roles:
                cardinality_repair = _repair(
                    kind="user_choice",
                    action="Choose a cardinality='one' participant from the current Event.",
                    candidates=cardinality_one_roles[:5],
                )
            else:
                cardinality_repair = _repair(
                    kind="semantic_authoring",
                    action="Author a cardinality='one' participant for this analysis subject.",
                )
            raise EventParticipantCardinalityError(
                message=(
                    f"step {raw_step.key!r} participant cannot be an analysis subject "
                    f"because its cardinality is {cardinality!r}"
                ),
                expected="participant cardinality='one'",
                received=cardinality,
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].participant",
                repair=cardinality_repair,
            )
        endpoint_ir = registry.entities.get(endpoint.path)
        if endpoint_ir is None or not endpoint_ir.primary_key:
            raise PatternStepMismatchError(
                message=f"step {raw_step.key!r} participant endpoint has no usable primary key",
                expected="an endpoint Entity with a non-empty primary_key",
                received=endpoint.key,
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].participant.endpoint",
                repair=_repair(
                    kind="semantic_authoring",
                    action="Define the participant endpoint Entity primary_key.",
                ),
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
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].participant.endpoint",
                repair=_repair(
                    kind="user_choice",
                    action="Choose participant roles whose endpoints are the same Entity.",
                ),
            )
        elif normalized_subject_identity != subject_identity:
            raise PatternStepMismatchError(
                message="EventPattern participant roles disagree on subject identity",
                expected=repr(subject_identity),
                received=repr(normalized_subject_identity),
                location=f"session.events.match.pattern.steps[{raw_step.key!r}].subject_identity",
                repair=_repair(
                    kind="inspect",
                    action="Inspect the active catalog identity definitions, then rebuild the pattern.",
                ),
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
                location="session.events.match.completeness.through",
                repair=_repair(
                    kind="user_choice",
                    action="Extend the declaration through completion_through or remove it.",
                ),
            )
        for event_ref in declaration.inputs:
            if event_ref not in pattern_events:
                raise InvalidCompletenessDeclarationError(
                    message="completeness declaration references an Event outside the pattern",
                    expected="only EventRefs used by the current EventPattern",
                    received=event_ref.key,
                    location="session.events.match.completeness.inputs",
                    repair=_repair(
                        kind="user_choice",
                        action="Choose only EventRefs used by the current EventPattern.",
                        candidates=tuple(sorted(item.key for item in pattern_events))[:5],
                    ),
                )
            previous = declaration_by_event.get(event_ref)
            if previous is not None:
                raise InvalidCompletenessDeclarationError(
                    message="one pattern Event is covered by multiple declarations",
                    expected="at most one declaration per EventRef",
                    received=event_ref.key,
                    location="session.events.match.completeness.inputs",
                    repair=_repair(
                        kind="user_choice",
                        action="Merge or remove overlapping completeness declarations.",
                        candidates=(event_ref.key,),
                    ),
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
            location="session.events.match.completeness.through",
            repair=_repair(
                kind="user_choice",
                action="Rebuild the declaration with a valid governed through bound.",
            ),
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


def _query_events(
    *,
    session: Session,
    resolved: tuple[_ResolvedStep, ...],
    cohort_window: TimeScope,
    completion_through: str,
    completion: pd.Timestamp,
    cohort: ResolvedSubjectCohort | None,
) -> tuple[
    dict[tuple[Ref[EventKind], str], tuple[_Occurrence, ...]],
    tuple[Any, ...],
]:
    inputs = tuple(
        EventOccurrenceInput(
            event_ref=event_ref,
            participant_names=role_names,
            subject_identity_counts=tuple(
                (
                    role_name,
                    next(
                        len(item.subject_identity)
                        for item in resolved
                        if item.step.event == event_ref and item.step.participant.name == role_name
                    ),
                )
                for role_name in role_names
            ),
            datasource_name=representative.datasource_name,
            event_fingerprint=representative.event_fingerprint,
        )
        for event_ref, role_names, representative in _event_groups(resolved)
    )
    return materialize_event_occurrences(
        session=session,
        inputs=inputs,
        start=cohort_window.start,
        end=completion_through,
        normalized_end=completion,
        end_inclusive=True,
        cohort=cohort,
        help_target="events.match",
        membership_lowerer=apply_event_subject_membership,
        occurred_at_location="session.events.match.pattern Event occurred_at",
    )


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
                location="session.events.match.occurrence_order",
                repair=_repair(
                    kind="inspect",
                    action=(
                        "Inspect source timestamps, then model more precise occurrence time "
                        "or remove the ambiguous ordering."
                    ),
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
) -> tuple[pd.DataFrame, int, dict[str, int]]:
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
    used_occurrences_by_step: dict[str, set[tuple[str, tuple[object, ...]]]] = {
        item.step.key: set() for item in resolved
    }
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
                used_occurrences_by_step[resolved_step.step.key].add(item.occurrence_key)
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
    unused_counts_by_step = {
        resolved_step.step.key: len(
            {occurrence.occurrence_key for occurrence in step_occurrences[index]}
            - used_occurrences_by_step[resolved_step.step.key]
        )
        for index, resolved_step in enumerate(resolved)
    }
    frame = pd.DataFrame(rows, columns=_ROW_COLUMNS)
    if not frame.empty:
        frame["occurred_at"] = pd.to_datetime(frame["occurred_at"], utc=True)
        frame["elapsed_from_start"] = pd.to_timedelta(frame["elapsed_from_start"])
        frame["elapsed_from_previous"] = pd.to_timedelta(frame["elapsed_from_previous"])
    return frame, unused_count, unused_counts_by_step


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
    seen: set[Ref[EventKind]] = set()
    inputs: list[EventCoverageInput] = []
    for resolved_step in resolved:
        event_ref = resolved_step.step.event
        if event_ref in seen:
            continue
        seen.add(event_ref)
        event_ir = registry.events[event_ref.path]
        inputs.append(
            EventCoverageInput(
                event_ref=event_ref,
                event_fingerprint=resolved_step.event_fingerprint,
                datasource_name=resolved_step.datasource_name,
                source_entity_ref=event_ir.source_entity,
                occurred_at_ref=event_ir.occurred_at,
            )
        )
    return resolve_event_coverage(
        session=session,
        inputs=tuple(inputs),
        required_through=completion_through,
        required_instant=completion,
        declaration_by_event=declaration_by_event,
    )


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
    cohort: SubjectSet | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> EventFrame:
    """Match a typed EventPattern into one dense EventFrame[journey]."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_can_execute(resolved_session)
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
    resolved_cohort = resolve_subject_cohort(
        session=resolved_session,
        cohort=cohort,
        consumer="events.match",
        expected_subject_entity=RefPayloadV1.from_ref(resolved[0].endpoint),
        expected_subject_identity=resolved[0].subject_identity,
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    occurrence_sets, queries = _query_events(
        session=resolved_session,
        resolved=resolved,
        cohort_window=cohort_window,
        completion_through=completion_through,
        completion=completion,
        cohort=resolved_cohort,
    )
    input_coverage, coverage_basis = _coverage(
        session=resolved_session,
        resolved=resolved,
        completion=completion,
        completion_through=completion_through,
        declaration_by_event=declaration_by_event,
    )
    output, unused_count, unused_counts_by_step = _match_rows(
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
        "cohort": (
            resolved_cohort.binding.model_dump(mode="json") if resolved_cohort is not None else None
        ),
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
                            *[
                                item.key
                                for item in dict.fromkeys(step.event for step in pattern.steps)
                            ],
                            *(
                                [resolved_cohort.binding.artifact_ref]
                                if resolved_cohort is not None
                                else []
                            ),
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
                    [
                        *[item.key for item in dict.fromkeys(step.event for step in pattern.steps)],
                        *(
                            [resolved_cohort.binding.artifact_ref]
                            if resolved_cohort is not None
                            else []
                        ),
                    ]
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
            unused_event_counts_by_step=unused_counts_by_step,
            cohort=resolved_cohort.binding if resolved_cohort is not None else None,
        ),
    )
    input_refs = [item.key for item in dict.fromkeys(step.event for step in pattern.steps)]
    if resolved_cohort is not None:
        input_refs.append(resolved_cohort.binding.artifact_ref)
    commit_inputs = CommitInputs(input_refs=input_refs)
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
                "input_frame_refs": (
                    [resolved_cohort.binding.artifact_ref] if resolved_cohort is not None else []
                ),
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
