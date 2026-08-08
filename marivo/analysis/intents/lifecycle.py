"""Replay one exact StateModel into canonical clipped Lifecycle history."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any, cast

import pandas as pd

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AnalysisRepair,
    InvalidCompletenessDeclarationError,
    InvalidLifecycleSeedError,
    RepairKind,
    SemanticKindMismatchError,
    WindowInvalidError,
)
from marivo.analysis.event import CompletenessDeclaration
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
    lifecycle_subject_for_frame,
    rollback_committed_result,
)
from marivo.analysis.frames.lifecycle import (
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
    LifecycleStateBinding,
    LifecycleTraceManifest,
    LifecycleTriggerBinding,
    PersistedModelStateHandle,
)
from marivo.analysis.frames.subject import SubjectSet
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.intents._event_occurrences import (
    EventCoverageInput,
    EventOccurrenceInput,
    materialize_event_occurrences,
    occurrence_snapshot_fingerprint,
    resolve_event_coverage,
)
from marivo.analysis.intents._lifecycle_replay import replay_state_model
from marivo.analysis.intents._subject_cohort import (
    ResolvedSubjectCohort,
    apply_event_subject_membership,
    resolve_subject_cohort,
)
from marivo.analysis.lifecycle import FromInception
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.analysis.windows.spec import TimeScope
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import EventKind, Ref, RefPayloadV1, SemanticKind, StateModelKind
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import (
    CatalogEntry,
    EventEntry,
    StateModelEntry,
    _normalize_semantic_input,
    _SemanticInput,
)
from marivo.semantic.errors import SemanticRuntimeError
from marivo.semantic.ir import StateModelIR, StateTriggerIR

_HELP_TARGET = "lifecycle.replay"


def _repair(
    *,
    kind: RepairKind,
    action: str,
    candidates: tuple[str, ...] = (),
) -> AnalysisRepair:
    return AnalysisRepair(
        kind=kind,
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id=_HELP_TARGET),
        candidates=candidates,
    )


def _model_candidates(session: Session) -> tuple[str, ...]:
    return tuple(item.ref.key for item in session.catalog.state_models.items[:5])


def _resolve_model(
    *,
    session: Session,
    model: _SemanticInput[StateModelKind],
) -> tuple[Ref[StateModelKind], StateModelIR, str]:
    """Normalize one exact current-catalog StateModel entry or ref."""
    catalog = session.catalog
    location = "session.lifecycle.replay.model"
    candidates = _model_candidates(session)
    try:
        normalized = _normalize_semantic_input(
            catalog,
            cast("Any", model),
            allowed_kinds=frozenset({SemanticKind.STATE_MODEL}),
            location=location,
        )
    except SemanticRuntimeError as exc:
        received = model.ref if isinstance(model, CatalogEntry) else model
        raise SemanticKindMismatchError(
            message="lifecycle.replay requires one exact current-catalog StateModel",
            expected="StateModelEntry | Ref[state_model]",
            received=(received.key if type(received) is Ref else type(model).__name__),
            location=location,
            repair=_repair(
                kind="inspect",
                action="Inspect current catalog StateModels and choose an exact model.",
                candidates=candidates,
            ),
        ) from exc
    model_ref = cast("Ref[StateModelKind]", normalized)
    entry = session.catalog.require(model_ref)
    if not isinstance(entry, StateModelEntry):
        raise AssertionError(f"StateModel ref resolved to {type(entry).__name__}")
    registry = catalog._require_index().registry
    model_ir = registry.state_models[model_ref.path]
    if not model_ir.inceptions:
        raise InvalidLifecycleSeedError(
            message="the StateModel declares no inception trigger for from-inception replay",
            expected="a StateModel with at least one ms.inception(on=...) trigger",
            received=model_ref.key,
            location=location,
            repair=_repair(
                kind="semantic_authoring",
                action="Author at least one inception trigger before replaying this StateModel.",
                candidates=candidates,
            ),
        )
    return model_ref, model_ir, entry.details().definition_fingerprint


def _parse_instant(value: object, *, label: str, location: str) -> pd.Timestamp:
    action = (
        "Pass mv.time_scope(start=<aware instant>, end=<later aware instant>) "
        "with explicit timezone offsets."
    )
    if type(value) is not str or not value.strip():
        raise WindowInvalidError(
            message=f"lifecycle.replay {label} must be a non-empty ISO-8601 instant",
            expected="a timezone-aware ISO-8601 instant",
            received=repr(value),
            location=location,
            repair=_repair(kind="user_choice", action=action),
        )
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise WindowInvalidError(
            message=f"lifecycle.replay {label} is not a valid ISO-8601 instant",
            expected="a timezone-aware ISO-8601 instant",
            received=repr(value),
            location=location,
            repair=_repair(kind="user_choice", action=action),
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WindowInvalidError(
            message=f"lifecycle.replay {label} must carry an explicit timezone offset",
            expected="a timezone-aware ISO-8601 instant",
            received=repr(value),
            location=location,
            repair=_repair(kind="user_choice", action=action),
        )
    return pd.Timestamp(parsed.astimezone(UTC))


def _resolve_window(window: TimeScope) -> tuple[pd.Timestamp, pd.Timestamp]:
    if type(window) is not TimeScope:
        raise WindowInvalidError(
            message="lifecycle.replay window must be an exact TimeScope from mv.time_scope(...)",
            expected="mv.time_scope(start=<inclusive>, end=<exclusive>)",
            received=type(window).__name__,
            location="session.lifecycle.replay.window",
            repair=_repair(
                kind="user_choice",
                action="Construct the replay window with mv.time_scope(start=..., end=...).",
            ),
        )
    start = _parse_instant(
        window.start,
        label="window.start",
        location="session.lifecycle.replay.window.start",
    )
    end = _parse_instant(
        window.end,
        label="window.end",
        location="session.lifecycle.replay.window.end",
    )
    if start >= end:
        raise WindowInvalidError(
            message="lifecycle.replay window must be a non-empty half-open interval",
            expected="window.start < window.end",
            received=f"{window.start!r} .. {window.end!r}",
            location="session.lifecycle.replay.window",
            repair=_repair(
                kind="user_choice",
                action="Choose a replay start strictly before the exclusive end.",
            ),
        )
    return start, end


def _require_seed(seed: FromInception) -> FromInception:
    if type(seed) is not FromInception:
        raise InvalidLifecycleSeedError(
            message="lifecycle.replay requires the exact explicit from-inception seed",
            expected="mv.from_inception()",
            received=type(seed).__name__,
            location="session.lifecycle.replay.seed",
            repair=_repair(
                kind="user_choice",
                action="Pass seed=mv.from_inception() explicitly; replay has no default seed.",
                candidates=("mv.from_inception()",),
            ),
        )
    return seed


def _model_triggers(model_ir: StateModelIR) -> tuple[StateTriggerIR, ...]:
    """Return declared inception and transition triggers in declaration order."""
    return (
        *(item.trigger for item in model_ir.inceptions),
        *(item.trigger for item in model_ir.transitions),
    )


def _resolve_completeness(
    *,
    completeness: tuple[CompletenessDeclaration, ...],
    model_events: frozenset[str],
    window_end: pd.Timestamp,
    window: TimeScope,
) -> dict[Ref[EventKind], CompletenessDeclaration]:
    location = "session.lifecycle.replay.completeness"
    if type(completeness) is not tuple or any(
        not isinstance(item, CompletenessDeclaration) for item in completeness
    ):
        raise InvalidCompletenessDeclarationError(
            message="lifecycle.replay completeness must be a tuple of typed declarations",
            expected="tuple[mv.CompletenessDeclaration, ...]",
            received=type(completeness).__name__,
            location=location,
            repair=_repair(
                kind="user_choice",
                action="Pass a tuple of governed completeness declarations, or omit it.",
            ),
        )
    candidates = tuple(sorted(f"event:{path}" for path in model_events))[:5]
    declaration_by_event: dict[Ref[EventKind], CompletenessDeclaration] = {}
    for declaration in completeness:
        through = _parse_declaration_through(declaration)
        if through < window_end:
            raise InvalidCompletenessDeclarationError(
                message="completeness declaration does not cover the replay window end",
                expected=f"through >= {window.end!r}",
                received=repr(declaration.through),
                location=f"{location}.through",
                repair=_repair(
                    kind="user_choice",
                    action="Extend the declaration through window.end or remove it.",
                ),
            )
        for event_ref in declaration.inputs:
            if event_ref.path not in model_events:
                raise InvalidCompletenessDeclarationError(
                    message="completeness declaration references an Event outside the StateModel",
                    expected="only EventRefs used by the current StateModel triggers",
                    received=event_ref.key,
                    location=f"{location}.inputs",
                    repair=_repair(
                        kind="user_choice",
                        action="Choose only EventRefs used by the current StateModel triggers.",
                        candidates=candidates,
                    ),
                )
            if event_ref in declaration_by_event:
                raise InvalidCompletenessDeclarationError(
                    message="one StateModel Event is covered by multiple declarations",
                    expected="at most one declaration per EventRef",
                    received=event_ref.key,
                    location=f"{location}.inputs",
                    repair=_repair(
                        kind="user_choice",
                        action="Merge or remove overlapping completeness declarations.",
                        candidates=(event_ref.key,),
                    ),
                )
            declaration_by_event[event_ref] = declaration
    return declaration_by_event


def _parse_declaration_through(declaration: CompletenessDeclaration) -> pd.Timestamp:
    try:
        return _parse_instant(
            declaration.through,
            label="completeness.through",
            location="session.lifecycle.replay.completeness.through",
        )
    except WindowInvalidError as exc:
        raise InvalidCompletenessDeclarationError(
            message="completeness declaration has an invalid through bound",
            expected="a timezone-aware ISO-8601 instant",
            received=repr(declaration.through),
            location="session.lifecycle.replay.completeness.through",
            repair=_repair(
                kind="user_choice",
                action="Rebuild the declaration with a valid governed through bound.",
            ),
        ) from exc


def _subject_identity(*, session: Session, entity_path: str) -> tuple[str, ...]:
    registry = session.catalog._require_index().registry
    entity = registry.entities.get(entity_path)
    if entity is None or not entity.primary_key:
        raise SemanticKindMismatchError(
            message="the StateModel subject Entity has no usable primary key",
            expected="a subject Entity with a non-empty primary_key",
            received=entity_path,
            location="session.lifecycle.replay.model.subject",
            repair=_repair(
                kind="semantic_authoring",
                action="Define the StateModel subject Entity primary_key before replaying.",
            ),
        )
    return tuple(
        (
            component
            if component in registry.dimensions
            else (
                f"{entity_path}.{component}"
                if f"{entity_path}.{component}" in registry.dimensions
                else component
            )
        )
        for component in entity.primary_key
    )


class _EventPlan:
    """Distinct-Event query plan carrying every required participant role."""

    __slots__ = (
        "coverage_inputs",
        "fingerprints",
        "identity_components",
        "occurrence_inputs",
    )

    def __init__(
        self,
        *,
        occurrence_inputs: tuple[EventOccurrenceInput, ...],
        coverage_inputs: tuple[EventCoverageInput, ...],
        fingerprints: dict[str, str],
        identity_components: dict[str, tuple[RefPayloadV1, ...]],
    ) -> None:
        self.occurrence_inputs = occurrence_inputs
        self.coverage_inputs = coverage_inputs
        self.fingerprints = fingerprints
        self.identity_components = identity_components


def _event_plan(
    *,
    session: Session,
    model_ir: StateModelIR,
    subject_identity: tuple[str, ...],
) -> _EventPlan:
    """Group StateModel triggers into exactly one query plan per distinct Event."""
    registry = session.catalog._require_index().registry
    roles_by_event: dict[str, list[str]] = {}
    for trigger in _model_triggers(model_ir):
        roles = roles_by_event.setdefault(trigger.event_ref, [])
        if trigger.participant_role not in roles:
            roles.append(trigger.participant_role)

    occurrence_inputs: list[EventOccurrenceInput] = []
    coverage_inputs: list[EventCoverageInput] = []
    fingerprints: dict[str, str] = {}
    identity_components: dict[str, tuple[RefPayloadV1, ...]] = {}
    for event_path, role_names in roles_by_event.items():
        event_ref = ref_factory.event(event_path)
        entry = session.catalog.require(event_ref)
        if not isinstance(entry, EventEntry):
            raise AssertionError(f"Event ref resolved to {type(entry).__name__}")
        details = entry.details()
        event_ir = registry.events[event_path]
        source_ir = registry.entities[event_ir.source_entity]
        occurrence_inputs.append(
            EventOccurrenceInput(
                event_ref=event_ref,
                participant_names=tuple(role_names),
                subject_identity_counts=tuple(
                    (role_name, len(subject_identity)) for role_name in role_names
                ),
                datasource_name=source_ir.datasource,
                event_fingerprint=details.definition_fingerprint,
            )
        )
        coverage_inputs.append(
            EventCoverageInput(
                event_ref=event_ref,
                event_fingerprint=details.definition_fingerprint,
                datasource_name=source_ir.datasource,
                source_entity_ref=event_ir.source_entity,
                occurred_at_ref=event_ir.occurred_at,
            )
        )
        fingerprints[event_path] = details.definition_fingerprint
        identity_components[event_path] = tuple(
            RefPayloadV1.from_ref(component) for component in details.identity
        )
    return _EventPlan(
        occurrence_inputs=tuple(occurrence_inputs),
        coverage_inputs=tuple(coverage_inputs),
        fingerprints=fingerprints,
        identity_components=identity_components,
    )


def _state_bindings(
    *,
    model_ir: StateModelIR,
    model_ref: RefPayloadV1,
) -> tuple[LifecycleStateBinding, ...]:
    return tuple(
        LifecycleStateBinding(
            state=PersistedModelStateHandle(model=model_ref, name=state.name),
            initial=state.initial,
            terminal=state.terminal,
        )
        for state in model_ir.states
    )


def _trigger_bindings(model_ir: StateModelIR) -> tuple[LifecycleTriggerBinding, ...]:
    initial_state = next(state.name for state in model_ir.states if state.initial)
    bindings = [
        LifecycleTriggerBinding(
            kind="inception",
            event_ref=RefPayloadV1.from_ref(ref_factory.event(item.trigger.event_ref)),
            participant_role=item.trigger.participant_role,
            to_state=initial_state,
        )
        for item in model_ir.inceptions
    ]
    bindings.extend(
        LifecycleTriggerBinding(
            kind="transition",
            event_ref=RefPayloadV1.from_ref(ref_factory.event(item.trigger.event_ref)),
            participant_role=item.trigger.participant_role,
            from_state=item.from_state,
            to_state=item.to_state,
        )
        for item in model_ir.transitions
    )
    # Exact duplicate transition declarations stay legal in the semantic layer
    # but a Lifecycle artifact retains each canonical binding exactly once.
    return tuple(dict.fromkeys(bindings))


def _rollback_replay_commit(
    *,
    session: Session,
    evidence_store: Any,
    artifact_id: str,
    job_ref: str,
    preserve_artifact: bool,
) -> None:
    """Best-effort rollback for one Lifecycle replay persistence transaction."""
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


def replay(
    model: _SemanticInput[StateModelKind],
    *,
    window: TimeScope,
    seed: FromInception,
    completeness: tuple[CompletenessDeclaration, ...] = (),
    cohort: SubjectSet | None = None,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> LifecycleFrame:
    """Replay one StateModel from its explicit inception seed into clipped history."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_can_execute(resolved_session)

    model_ref, model_ir, model_fingerprint = _resolve_model(
        session=resolved_session,
        model=model,
    )
    window_start, window_end = _resolve_window(window)
    resolved_seed = _require_seed(seed)
    model_events = frozenset(trigger.event_ref for trigger in _model_triggers(model_ir))
    declaration_by_event = _resolve_completeness(
        completeness=completeness,
        model_events=model_events,
        window_end=window_end,
        window=window,
    )
    subject_entity = ref_factory.entity(model_ir.subject)
    subject_identity = _subject_identity(
        session=resolved_session,
        entity_path=model_ir.subject,
    )
    resolved_cohort: ResolvedSubjectCohort | None = resolve_subject_cohort(
        session=resolved_session,
        cohort=cohort,
        consumer="lifecycle.replay",
        expected_subject_entity=RefPayloadV1.from_ref(subject_entity),
        expected_subject_identity=subject_identity,
    )
    plan = _event_plan(
        session=resolved_session,
        model_ir=model_ir,
        subject_identity=subject_identity,
    )

    started_at = datetime.now(UTC)
    started = monotonic()
    occurrence_sets, queries = materialize_event_occurrences(
        session=resolved_session,
        inputs=plan.occurrence_inputs,
        start=None,
        end=window.end,
        normalized_end=window_end,
        end_inclusive=False,
        cohort=resolved_cohort,
        help_target=_HELP_TARGET,
        membership_lowerer=apply_event_subject_membership,
        occurred_at_location="session.lifecycle.replay StateModel trigger Event occurred_at",
    )
    input_coverage, coverage_basis = resolve_event_coverage(
        session=resolved_session,
        inputs=plan.coverage_inputs,
        required_through=window.end,
        required_instant=window_end,
        declaration_by_event=declaration_by_event,
    )
    result = replay_state_model(
        model=model_ir,
        occurrence_sets=occurrence_sets,
        window_start=window_start,
        window_end=window_end,
        coverage_complete=coverage_basis != "unknown",
        population=resolved_cohort.identities if resolved_cohort is not None else None,
    )

    history = result.history.copy()
    history["valid_from"] = pd.to_datetime(history["valid_from"], utc=True)
    history["valid_to"] = pd.to_datetime(history["valid_to"], utc=True)
    trace = result.violations.copy()
    trace["occurred_at"] = pd.to_datetime(trace["occurred_at"], utc=True)

    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    model_payload = RefPayloadV1.from_ref(model_ref)
    states = _state_bindings(model_ir=model_ir, model_ref=model_payload)
    triggers = _trigger_bindings(model_ir)
    query_refs = tuple(
        query.query_id for query in queries if isinstance(getattr(query, "query_id", None), str)
    )
    input_refs = sorted(f"event:{path}" for path in model_events)
    if resolved_cohort is not None:
        input_refs.append(resolved_cohort.binding.artifact_ref)
    params: dict[str, object] = {
        "state_model_ref": model_payload.to_dict(),
        "state_model_fingerprint": model_fingerprint,
        "seed": resolved_seed.model_dump(mode="json"),
        "seed_fingerprint": resolved_seed.fingerprint,
        "window": window.model_dump(mode="json"),
        "states": [item.model_dump(mode="json") for item in states],
        "triggers": [item.model_dump(mode="json") for item in triggers],
        "completeness": [declaration.model_dump(mode="json") for declaration in completeness],
        "input_coverage": [item.model_dump(mode="json") for item in input_coverage],
        "coverage_basis": coverage_basis,
        "event_fingerprints": dict(sorted(plan.fingerprints.items())),
        "event_identity_components": {
            key: [component.to_dict() for component in components]
            for key, components in sorted(plan.identity_components.items())
        },
        "cohort": (
            resolved_cohort.binding.model_dump(mode="json") if resolved_cohort is not None else None
        ),
        "population_count": result.population_count,
        "seeded_subject_count": result.seeded_subject_count,
        "coverage_censored_subject_count": result.coverage_censored_subject_count,
        "interval_count": result.interval_count,
        "violation_count": result.violation_count,
        "pre_inception_ignored_counts": dict(sorted(result.pre_inception_ignored_counts.items())),
        "snapshot_fingerprint": occurrence_snapshot_fingerprint(occurrence_sets),
    }
    frame = LifecycleFrame(
        _df=history,
        meta=LifecycleHistoryFrameMeta(
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(history),
            byte_size=0,
            lineage=Lineage(
                steps=[
                    LineageStep(
                        intent="lifecycle.replay",
                        job_ref=job_ref,
                        inputs=list(input_refs),
                        params_digest=params_digest(params),
                        params={
                            "state_model_ref": model_ref.key,
                            "state_model_fingerprint": model_fingerprint,
                            "coverage_basis": coverage_basis,
                        },
                        analysis_purpose=analysis_purpose,
                    )
                ],
                external_inputs=sorted(input_refs),
            ),
            catalog_definition_fingerprint=(resolved_session.catalog.definition_fingerprint),
            state_model_ref=model_payload,
            state_model_fingerprint=model_fingerprint,
            subject_entity_ref=RefPayloadV1.from_ref(subject_entity),
            subject_identity=subject_identity,
            states=states,
            seed=resolved_seed,
            window=window,
            cohort=resolved_cohort.binding if resolved_cohort is not None else None,
            triggers=triggers,
            completeness=completeness,
            input_coverage=input_coverage,
            coverage_basis=coverage_basis,
            event_fingerprints=plan.fingerprints,
            event_identity_components=plan.identity_components,
            query_refs=query_refs,
            population_count=result.population_count,
            seeded_subject_count=result.seeded_subject_count,
            coverage_censored_subject_count=result.coverage_censored_subject_count,
            interval_count=result.interval_count,
            violation_count=result.violation_count,
            pre_inception_ignored_counts=result.pre_inception_ignored_counts,
            violation_trace=LifecycleTraceManifest(row_count=len(trace)),
        ),
        _auxiliary_frames={"violations.parquet": trace},
    )

    commit_inputs = CommitInputs(input_refs=list(input_refs))
    commit_params = CommitParams(values=params)
    commit_anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=(resolved_session.catalog.definition_fingerprint),
    )
    prospective_id = compute_prospective_artifact_id(
        step_type="lifecycle.replay",
        inputs=commit_inputs,
        params=commit_params,
        semantic_anchors=commit_anchors,
    )
    artifact_preexisting = resolved_session._store.get_artifact(
        resolved_session.id,
        prospective_id,
    ) is not None or frame_exists_on_disk(
        resolved_session._layout.frames_dir,
        prospective_id,
    )
    evidence_store = resolved_session._evidence_store()
    try:
        frame = cast(
            "LifecycleFrame",
            commit_result(
                store=evidence_store,
                frames_dir=resolved_session._layout.frames_dir,
                frame=frame,
                step_type="lifecycle.replay",
                inputs=commit_inputs,
                params=commit_params,
                semantic_anchors=commit_anchors,
                subject=lifecycle_subject_for_frame(frame),
                extractor_family="lifecycle_frame",
            ),
        )
        register_frame_artifact(resolved_session, frame)
        persist_job_record(
            resolved_session,
            {
                "id": job_ref,
                "session_id": resolved_session.id,
                "intent": "lifecycle.replay",
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
        _rollback_replay_commit(
            session=resolved_session,
            evidence_store=evidence_store,
            artifact_id=prospective_id,
            job_ref=job_ref,
            preserve_artifact=artifact_preexisting,
        )
        raise
    return frame


__all__ = ["replay"]
