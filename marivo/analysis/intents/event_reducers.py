"""Persisted Event Journey reducers exposed through ``session.events``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Literal, cast

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    InvalidEventMatchingPolicyError,
    PatternStepMismatchError,
    SubjectSetMismatchError,
)
from marivo.analysis.event import FirstPerSubject, PatternStep, _event_repair
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
from marivo.analysis.frames.event import (
    EventFrame,
    EventFrameMeta,
    EventFunnelFrameMeta,
    EventTimeToEventFrameMeta,
    GroupedFunnelReconciliationReceipt,
)
from marivo.analysis.intents._derived import compose_lineage, gen_ref, params_digest
from marivo.analysis.intents._event_funnel import reduce_event_funnel
from marivo.analysis.intents._event_subject_axes import (
    materialize_subject_axes,
    resolve_subject_axes,
)
from marivo.analysis.intents._event_time_to_event import reduce_event_time_to_event
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.refs import ref as ref_factory

EventReducerHelpTarget = Literal["events.funnel", "events.time_to_event"]


def _source_error(
    *,
    message: str,
    expected: str,
    received: str,
    location: str,
    help_target: EventReducerHelpTarget,
    action: str,
) -> SubjectSetMismatchError:
    return SubjectSetMismatchError(
        message=message,
        expected=expected,
        received=received,
        location=location,
        repair=_event_repair(
            kind="inspect",
            action=action,
            help_target=help_target,
        ),
    )


def _require_journey_source(
    *,
    session: Session,
    journeys: EventFrame,
    help_target: EventReducerHelpTarget,
) -> tuple[str, str]:
    location = f"session.{help_target}.journeys"
    if type(journeys) is not EventFrame:
        raise _source_error(
            message=f"{help_target} requires an exact EventFrame.",
            expected="EventFrame[journey]",
            received=type(journeys).__name__,
            location=location,
            help_target=help_target,
            action="Pass the canonical journey returned by session.events.match(...).",
        )
    if journeys.meta.session_id != session.id:
        raise _source_error(
            message="The source journey belongs to a different analysis session.",
            expected="an EventFrame owned by the current session",
            received="different_session",
            location=location,
            help_target=help_target,
            action="Load or rebuild the journey in the current session.",
        )
    if Path(journeys.meta.project_root).resolve() != session.project_root.resolve():
        raise _source_error(
            message="The source journey belongs to a different Marivo project.",
            expected="an EventFrame owned by the current project",
            received="different_project",
            location=location,
            help_target=help_target,
            action="Rebuild the journey from the current project catalog.",
        )
    if journeys.meta.semantic_kind != "journey":
        raise _source_error(
            message=f"{help_target} accepts only canonical journey rows.",
            expected="EventFrame semantic_kind='journey'",
            received=f"semantic_kind={journeys.meta.semantic_kind!r}",
            location=location,
            help_target=help_target,
            action="Use the source EventFrame[journey], not a reducer output.",
        )
    source_ref = journeys.meta.artifact_id or journeys.meta.ref
    source_fingerprint = journeys.meta.content_hash
    registered = session._store.get_artifact(session.id, source_ref)
    if (
        source_fingerprint is None
        or registered is None
        or registered["content_hash"] != source_fingerprint
    ):
        raise _source_error(
            message="The source journey artifact is missing or stale in this session.",
            expected="a registered source artifact with the retained content fingerprint",
            received="source=unavailable_or_changed",
            location=location,
            help_target=help_target,
            action="Inspect the source journey and rebuild it before reducing it.",
        )
    return source_ref, source_fingerprint


def _rollback_reducer_commit(
    *,
    session: Session,
    evidence_store: Any,
    artifact_id: str,
    job_ref: str,
    preserve_artifact: bool,
) -> None:
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


def _commit_reducer(
    *,
    session: Session,
    frame: EventFrame,
    source: EventFrame,
    intent: str,
    job_ref: str,
    started_at: datetime,
    started: float,
    finished_at: datetime,
    analysis_purpose: str | None,
    params: dict[str, Any],
    queries: list[dict[str, object]],
) -> EventFrame:
    source_ref = source.meta.artifact_id or source.meta.ref
    commit_inputs = CommitInputs(input_refs=[source_ref])
    commit_params = CommitParams(values=params)
    commit_anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=session.catalog.definition_fingerprint,
    )
    prospective_id = compute_prospective_artifact_id(
        step_type=intent,
        inputs=commit_inputs,
        params=commit_params,
        semantic_anchors=commit_anchors,
    )
    artifact_preexisting = session._store.get_artifact(
        session.id,
        prospective_id,
    ) is not None or frame_exists_on_disk(session._layout.frames_dir, prospective_id)
    evidence_store = session._evidence_store()
    try:
        committed = cast(
            "EventFrame",
            commit_result(
                store=evidence_store,
                frames_dir=session._layout.frames_dir,
                frame=frame,
                step_type=intent,
                inputs=commit_inputs,
                params=commit_params,
                semantic_anchors=commit_anchors,
                subject=event_subject_for_frame(frame),
                extractor_family="event_frame",
            ),
        )
        register_frame_artifact(session, committed)
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": intent,
                **job_semantics_from_frames(committed),
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": [source_ref],
                "output_frame_ref": committed.meta.artifact_id or committed.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": str(session.catalog.semantic_root),
                "queries": queries,
            },
        )
    except BaseException:
        _rollback_reducer_commit(
            session=session,
            evidence_store=evidence_store,
            artifact_id=prospective_id,
            job_ref=job_ref,
            preserve_artifact=artifact_preexisting,
        )
        raise
    return committed


def funnel(
    journeys: EventFrame,
    *,
    axes: Sequence[object] = (),
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> EventFrame:
    """Reduce canonical first-per-subject journeys into a reconciled funnel."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_writable(resolved_session)
    source_ref, source_fingerprint = _require_journey_source(
        session=resolved_session,
        journeys=journeys,
        help_target="events.funnel",
    )
    journey_meta = cast("EventFrameMeta", journeys.meta)
    started_at = datetime.now(UTC)
    started = monotonic()
    if type(journeys.meta.matching) is not FirstPerSubject:
        raise InvalidEventMatchingPolicyError(
            message="events.funnel requires first_per_subject matching.",
            expected="source.meta.matching.kind='first_per_subject'",
            received=f"matching={journeys.meta.matching.kind!r}",
            location="session.events.funnel.journeys.matching",
            repair=_event_repair(
                kind="user_choice",
                action="Match the source pattern with mv.first_per_subject().",
                help_target="events.funnel",
                candidates=("first_per_subject",),
            ),
        )

    subject_entity = ref_factory.entity(journeys.meta.subject_entity_ref.path)
    resolved_axes = resolve_subject_axes(
        resolved_session,
        subject_entity=subject_entity,
        axes=axes,
    )
    source_rows = journeys._dataframe_copy()
    axis_materialization = materialize_subject_axes(
        resolved_session,
        journey_rows=source_rows,
        first_step_key=journeys.meta.pattern.steps[0].key,
        subject_entity=subject_entity,
        subject_identity=journeys.meta.subject_identity,
        axes=resolved_axes,
    )
    axis_columns = tuple(item.output_column for item in axis_materialization.bindings)
    reduction = reduce_event_funnel(
        source_rows,
        pattern=journeys.meta.pattern,
        event_coverage_complete={
            item.event_ref.path: item.basis != "unknown" for item in journeys.meta.input_coverage
        },
        axis_values=axis_materialization.values if axis_columns else None,
        axis_columns=axis_columns,
    )

    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "axes": [item.model_dump(mode="json") for item in axis_materialization.bindings],
        "grouped_reconciliation": {
            "additive_columns": list(reduction.additive_columns),
            "ungrouped_hash": reduction.ungrouped_hash,
            "grouped_hash": reduction.grouped_hash,
            "status": "pass",
        },
        "source_unused_event_count": journey_meta.unused_event_count,
    }
    step = LineageStep(
        intent="events.funnel",
        job_ref=job_ref,
        inputs=[source_ref],
        params_digest=params_digest(params),
        params={
            "source_artifact_ref": source_ref,
            "axis_refs": [axis.ref.key for axis in resolved_axes],
        },
        analysis_purpose=analysis_purpose,
    )
    frame = EventFrame(
        _df=reduction.rows,
        meta=EventFunnelFrameMeta(
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(reduction.rows),
            byte_size=0,
            lineage=compose_lineage((journeys,), step=step),
            catalog_definition_fingerprint=journeys.meta.catalog_definition_fingerprint,
            subject_entity_ref=journeys.meta.subject_entity_ref,
            subject_identity=journeys.meta.subject_identity,
            pattern=journeys.meta.pattern,
            matching=journeys.meta.matching,
            cohort_window=journeys.meta.cohort_window,
            completion_through=journeys.meta.completion_through,
            completeness=journeys.meta.completeness,
            input_coverage=journeys.meta.input_coverage,
            coverage_basis=journeys.meta.coverage_basis,
            event_fingerprints=journeys.meta.event_fingerprints,
            event_identity_components=journeys.meta.event_identity_components,
            role_endpoints=journeys.meta.role_endpoints,
            cohort=journeys.meta.cohort,
            source_journey_ref=source_ref,
            source_journey_fingerprint=source_fingerprint,
            source_unused_event_count=journey_meta.unused_event_count,
            axes=axis_materialization.bindings,
            grouped_reconciliation=GroupedFunnelReconciliationReceipt(
                additive_columns=reduction.additive_columns,
                ungrouped_hash=reduction.ungrouped_hash,
                grouped_hash=reduction.grouped_hash,
            ),
        ),
    )
    return _commit_reducer(
        session=resolved_session,
        frame=frame,
        source=journeys,
        intent="events.funnel",
        job_ref=job_ref,
        started_at=started_at,
        started=started,
        finished_at=finished_at,
        analysis_purpose=analysis_purpose,
        params=params,
        queries=list(axis_materialization.lineage),
    )


def _require_step_pair(
    journeys: EventFrame,
    *,
    start_step: PatternStep,
    end_step: PatternStep,
) -> None:
    candidates = tuple(step.key for step in journeys.meta.pattern.steps[:6])
    for parameter, step in (("start_step", start_step), ("end_step", end_step)):
        if type(step) is not PatternStep:
            raise PatternStepMismatchError(
                message=f"{parameter} must be an exact PatternStep.",
                expected="PatternStep retained by source.meta.pattern",
                received=type(step).__name__,
                location=f"session.events.time_to_event.{parameter}",
                repair=_event_repair(
                    kind="user_choice",
                    action="Choose the exact step retained by the source EventPattern.",
                    help_target="events.time_to_event",
                    candidates=candidates,
                ),
            )
        if (
            sum(
                candidate.fingerprint == step.fingerprint
                for candidate in journeys.meta.pattern.steps
            )
            != 1
        ):
            raise PatternStepMismatchError(
                message=f"{parameter} is not the exact step retained by the source pattern.",
                expected="one exact PatternStep from source.meta.pattern",
                received=step.key,
                location=f"session.events.time_to_event.{parameter}",
                repair=_event_repair(
                    kind="user_choice",
                    action="Choose the exact step retained by the source EventPattern.",
                    help_target="events.time_to_event",
                    candidates=candidates,
                ),
            )
    step_fingerprints = tuple(step.fingerprint for step in journeys.meta.pattern.steps)
    if step_fingerprints.index(start_step.fingerprint) >= step_fingerprints.index(
        end_step.fingerprint
    ):
        raise PatternStepMismatchError(
            message="start_step must precede end_step in the persisted EventPattern.",
            expected="start_step before end_step",
            received=f"{start_step.key} -> {end_step.key}",
            location="session.events.time_to_event",
            repair=_event_repair(
                kind="user_choice",
                action="Choose an ordered start/end step pair from the source pattern.",
                help_target="events.time_to_event",
                candidates=candidates,
            ),
        )


def time_to_event(
    journeys: EventFrame,
    *,
    start_step: PatternStep,
    end_step: PatternStep,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> EventFrame:
    """Project persisted journey assignments into exact time-to-event rows."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_writable(resolved_session)
    source_ref, source_fingerprint = _require_journey_source(
        session=resolved_session,
        journeys=journeys,
        help_target="events.time_to_event",
    )
    journey_meta = cast("EventFrameMeta", journeys.meta)
    started_at = datetime.now(UTC)
    started = monotonic()
    _require_step_pair(
        journeys,
        start_step=start_step,
        end_step=end_step,
    )
    output = reduce_event_time_to_event(
        journeys._dataframe_copy(),
        pattern=journeys.meta.pattern,
        start_step=start_step,
        end_step=end_step,
    )

    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "start_step": start_step.model_dump(mode="json"),
        "end_step": end_step.model_dump(mode="json"),
        "source_unused_end_count": journey_meta.unused_event_counts_by_step[end_step.key],
    }
    step = LineageStep(
        intent="events.time_to_event",
        job_ref=job_ref,
        inputs=[source_ref],
        params_digest=params_digest(params),
        params={
            "source_artifact_ref": source_ref,
            "start_step": start_step.key,
            "end_step": end_step.key,
        },
        analysis_purpose=analysis_purpose,
    )
    frame = EventFrame(
        _df=output,
        meta=EventTimeToEventFrameMeta(
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(output),
            byte_size=0,
            lineage=compose_lineage((journeys,), step=step),
            catalog_definition_fingerprint=journeys.meta.catalog_definition_fingerprint,
            subject_entity_ref=journeys.meta.subject_entity_ref,
            subject_identity=journeys.meta.subject_identity,
            pattern=journeys.meta.pattern,
            matching=journeys.meta.matching,
            cohort_window=journeys.meta.cohort_window,
            completion_through=journeys.meta.completion_through,
            completeness=journeys.meta.completeness,
            input_coverage=journeys.meta.input_coverage,
            coverage_basis=journeys.meta.coverage_basis,
            event_fingerprints=journeys.meta.event_fingerprints,
            event_identity_components=journeys.meta.event_identity_components,
            role_endpoints=journeys.meta.role_endpoints,
            cohort=journeys.meta.cohort,
            source_journey_ref=source_ref,
            source_journey_fingerprint=source_fingerprint,
            source_unused_end_count=journey_meta.unused_event_counts_by_step[end_step.key],
            start_step=start_step,
            end_step=end_step,
        ),
    )
    return _commit_reducer(
        session=resolved_session,
        frame=frame,
        source=journeys,
        intent="events.time_to_event",
        job_ref=job_ref,
        started_at=started_at,
        started=started,
        finished_at=finished_at,
        analysis_purpose=analysis_purpose,
        params=params,
        queries=[],
    )


__all__ = ["funnel", "time_to_event"]
