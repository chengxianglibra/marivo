"""Materialize typed subject selections from journeys or replayed state."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

import pandas as pd

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    InvalidEventMatchingPolicyError,
    ModelStateMismatchError,
    PatternStepMismatchError,
    SubjectSetMismatchError,
    WindowInvalidError,
)
from marivo.analysis.event import FirstPerSubject, _event_repair
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    event_subject_for_frame,
    frame_exists_on_disk,
    lifecycle_subject_for_frame,
    rollback_committed_result,
)
from marivo.analysis.evidence.types import EventSubject, LifecycleSubject
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.frames.lifecycle import LifecycleFrame, LifecycleHistoryFrameMeta
from marivo.analysis.frames.subject import (
    SubjectSet,
    SubjectSetMeta,
    SubjectSetSourceBinding,
)
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.lifecycle import InState
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.subject import DroppedBefore, SubjectSelection
from marivo.refs import RefPayloadV1
from marivo.semantic.catalog import StateModelEntry


def _subject_error(
    *,
    message: str,
    expected: str,
    received: str,
    location: str,
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
            help_target="select_subjects",
        ),
    )


def _require_ownership(*, session: Session, artifact: EventFrame | LifecycleFrame) -> None:
    """Reject any source artifact not owned by the current session and catalog."""
    label = "journey" if type(artifact) is EventFrame else "replay history"
    kind = "an EventFrame" if type(artifact) is EventFrame else "a LifecycleFrame"
    if artifact.meta.session_id != session.id:
        raise _subject_error(
            message=f"The source {label} belongs to a different analysis session.",
            expected=f"{kind} owned by the current session",
            received="different_session",
            location="session.select_subjects.artifact",
            action=f"Load or rebuild the {label} in the current session.",
        )
    if Path(artifact.meta.project_root).resolve() != session.project_root.resolve():
        raise _subject_error(
            message=f"The source {label} belongs to a different Marivo project.",
            expected=f"{kind} owned by the current project",
            received="different_project",
            location="session.select_subjects.artifact",
            action=f"Rebuild the {label} from the current project catalog.",
        )
    if artifact.meta.catalog_definition_fingerprint != session.catalog.definition_fingerprint:
        raise _subject_error(
            message=f"The source {label} was produced from a different catalog definition.",
            expected=f"{kind} built from the active catalog fingerprint",
            received="catalog_definition_changed",
            location="session.select_subjects.artifact",
            action=f"Reload the active catalog and rebuild the source {label}.",
        )


def _require_registered_source(
    *,
    session: Session,
    artifact: EventFrame | LifecycleFrame,
    label: str,
) -> tuple[str, str]:
    """Return the exact registered source artifact ref and content fingerprint."""
    artifact_ref = artifact.meta.artifact_id or artifact.meta.ref
    source_fingerprint = artifact.meta.content_hash
    row = session._store.get_artifact(session.id, artifact_ref)
    if source_fingerprint is None or row is None or row["content_hash"] != source_fingerprint:
        raise _subject_error(
            message=f"The source {label} artifact is missing or stale in this session.",
            expected="a registered source artifact with the retained content fingerprint",
            received="source=unavailable_or_changed",
            location="session.select_subjects.artifact",
            action=f"Inspect the source {label} and rebuild it before selecting subjects.",
        )
    return artifact_ref, source_fingerprint


def _require_source(
    *,
    session: Session,
    artifact: EventFrame,
    selection: SubjectSelection,
) -> tuple[DroppedBefore, int]:
    _require_ownership(session=session, artifact=artifact)
    if artifact.meta.semantic_kind != "journey":
        raise _subject_error(
            message="select_subjects accepts only canonical journey rows.",
            expected="EventFrame semantic_kind='journey'",
            received=f"semantic_kind={artifact.meta.semantic_kind!r}",
            location="session.select_subjects.artifact",
            action="Use the source EventFrame[journey], not a reducer output.",
        )
    if type(artifact.meta.matching) is not FirstPerSubject:
        raise InvalidEventMatchingPolicyError(
            message="dropped_before selection requires first_per_subject matching.",
            expected="source.meta.matching.kind='first_per_subject'",
            received=f"matching={artifact.meta.matching.kind!r}",
            location="session.select_subjects.artifact.matching",
            repair=_event_repair(
                kind="user_choice",
                action="Match the source pattern with mv.first_per_subject() before selection.",
                help_target="select_subjects",
            ),
        )
    if type(selection) is not DroppedBefore:
        raise PatternStepMismatchError(
            message="select_subjects requires a typed dropped_before selection.",
            expected="mv.dropped_before(step=<PatternStep>)",
            received=type(selection).__name__,
            location="session.select_subjects.selection",
            repair=_event_repair(
                kind="user_choice",
                action="Build the selection with mv.dropped_before(step=<exact source step>).",
                help_target="select_subjects",
            ),
        )

    step_fingerprints = tuple(step.fingerprint for step in artifact.meta.pattern.steps)
    matching_indexes = [
        index
        for index, fingerprint in enumerate(step_fingerprints)
        if fingerprint == selection.step.fingerprint
    ]
    if len(matching_indexes) != 1:
        raise PatternStepMismatchError(
            message="The selection step is not the exact step retained by the source pattern.",
            expected="one exact non-initial PatternStep from source.meta.pattern",
            received=selection.step.key,
            location="session.select_subjects.selection.step",
            repair=_event_repair(
                kind="user_choice",
                action="Choose the target from the source EventPattern steps.",
                help_target="select_subjects",
                candidates=tuple(step.key for step in artifact.meta.pattern.steps[:5]),
            ),
        )
    target_index = matching_indexes[0]
    if target_index == 0:
        raise PatternStepMismatchError(
            message="dropped_before cannot target the initial EventPattern step.",
            expected="a non-initial PatternStep",
            received=selection.step.key,
            location="session.select_subjects.selection.step",
            repair=_event_repair(
                kind="user_choice",
                action="Choose a step after the initial cohort-entry step.",
                help_target="select_subjects",
                candidates=tuple(step.key for step in artifact.meta.pattern.steps[1:6]),
            ),
        )

    _require_registered_source(session=session, artifact=artifact, label="journey")
    return selection, target_index


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        missing = pd.isna(cast("Any", value))
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, bool) else False


def _identity_sort_key(identity: tuple[object, ...]) -> str:
    return json.dumps(identity, ensure_ascii=True, separators=(",", ":"), default=str)


def _select_rows(
    artifact: EventFrame,
    *,
    target_index: int,
) -> tuple[pd.DataFrame, int]:
    rows = artifact._dataframe_copy()
    target_key = artifact.meta.pattern.steps[target_index].key
    target_event_path = artifact.meta.pattern.steps[target_index].event.path
    preceding_key = artifact.meta.pattern.steps[target_index - 1].key
    target_coverage = tuple(
        item for item in artifact.meta.input_coverage if item.event_ref.path == target_event_path
    )
    if len(target_coverage) != 1:
        raise _subject_error(
            message="The source journey does not retain exact target Event coverage.",
            expected="exactly one coverage entry for the selected PatternStep Event",
            received=f"coverage_entries={len(target_coverage)}",
            location="session.select_subjects.artifact.meta.input_coverage",
            action="Inspect and rebuild the source EventFrame[journey].",
        )
    target_coverage_complete = target_coverage[0].basis != "unknown"
    selected: set[tuple[object, ...]] = set()
    censored: set[tuple[object, ...]] = set()

    for _journey_id, journey in rows.groupby("journey_id", sort=False, dropna=False):
        preceding = journey.loc[journey["step_key"] == preceding_key]
        target = journey.loc[journey["step_key"] == target_key]
        if len(preceding) != 1 or len(target) != 1:
            raise _subject_error(
                message="The source journey does not satisfy the dense step-row contract.",
                expected="exactly one row per journey and PatternStep",
                received="dense_step_rows_invalid",
                location="session.select_subjects.artifact.rows",
                action="Inspect and rebuild the source EventFrame[journey].",
            )
        preceding_row = preceding.iloc[0]
        target_row = target.iloc[0]
        if _is_missing(preceding_row["event_identity"]) or not _is_missing(
            target_row["event_identity"]
        ):
            continue
        identity_value = target_row["subject_identity"]
        identity = (
            identity_value
            if isinstance(identity_value, tuple)
            else tuple(cast("Any", identity_value))
        )
        if not target_coverage_complete:
            censored.add(identity)
        else:
            selected.add(identity)

    selected.difference_update(censored)
    ordered = sorted(selected, key=_identity_sort_key)
    return pd.DataFrame({"subject_identity": ordered}), len(censored)


def _state_error(
    *,
    message: str,
    expected: str,
    received: str,
    location: str,
    action: str,
    candidates: tuple[str, ...] = (),
) -> ModelStateMismatchError:
    return ModelStateMismatchError(
        message=message,
        expected=expected,
        received=received,
        location=location,
        repair=_event_repair(
            kind="user_choice",
            action=action,
            help_target="select_subjects",
            candidates=candidates,
        ),
    )


def _parse_as_of(value: str) -> pd.Timestamp:
    location = "session.select_subjects.selection.as_of"
    action = "Pass as_of as a timezone-aware ISO-8601 instant inside the replay window."
    raw = value.strip()
    normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise WindowInvalidError(
            message="in_state as_of is not a valid ISO-8601 instant",
            expected="a timezone-aware ISO-8601 instant",
            received=repr(value),
            location=location,
            repair=_event_repair(
                kind="user_choice",
                action=action,
                help_target="select_subjects",
            ),
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WindowInvalidError(
            message="in_state as_of must carry an explicit timezone offset",
            expected="a timezone-aware ISO-8601 instant",
            received=repr(value),
            location=location,
            repair=_event_repair(
                kind="user_choice",
                action=action,
                help_target="select_subjects",
            ),
        )
    return pd.Timestamp(parsed.astimezone(UTC))


def _require_lifecycle_source(
    *,
    session: Session,
    artifact: LifecycleFrame,
    selection: SubjectSelection,
) -> tuple[InState, pd.Timestamp]:
    """Validate one history artifact and its exact current-catalog state handle."""
    _require_ownership(session=session, artifact=artifact)
    if artifact.meta.semantic_kind != "history":
        raise _subject_error(
            message="select_subjects accepts only canonical replay history rows.",
            expected="LifecycleFrame semantic_kind='history'",
            received=f"semantic_kind={artifact.meta.semantic_kind!r}",
            location="session.select_subjects.artifact",
            action="Use the source LifecycleFrame[history], not a reducer output.",
        )
    meta = artifact.meta
    if type(selection) is not InState:
        raise _state_error(
            message="LifecycleFrame[history] selection requires a typed in_state value.",
            expected="mv.in_state(state=<ModelStateHandle>, as_of=<instant>)",
            received=type(selection).__name__,
            location="session.select_subjects.selection",
            action="Build the selection with mv.in_state(ms.model_state(...), as_of=...).",
        )

    state_names = tuple(item.state.name for item in meta.states)
    entry = session.catalog.state_models.get(meta.state_model_ref.path)
    if not isinstance(entry, StateModelEntry):
        raise _state_error(
            message="The retained StateModel is no longer in the current catalog.",
            expected=f"state_model:{meta.state_model_ref.path} in the active catalog",
            received="state_model=absent",
            location="session.select_subjects.artifact",
            action="Reload the active catalog and replay the StateModel again.",
            candidates=tuple(item.ref.key for item in session.catalog.state_models.items[:5]),
        )
    if entry.details().definition_fingerprint != meta.state_model_fingerprint:
        raise _state_error(
            message="The StateModel definition changed after the source replay.",
            expected="the retained StateModel definition fingerprint",
            received="state_model_definition_changed",
            location="session.select_subjects.artifact",
            action="Replay the current StateModel before selecting subjects.",
        )
    if RefPayloadV1.from_ref(selection.state.model) != meta.state_model_ref:
        raise _state_error(
            message="The in_state handle belongs to a different StateModel.",
            expected=f"a state of state_model:{meta.state_model_ref.path}",
            received=selection.state.key,
            location="session.select_subjects.selection.state",
            action="Choose a state of the StateModel that produced this history.",
            candidates=(f"state_model:{meta.state_model_ref.path}",),
        )
    if selection.state.name not in state_names:
        raise _state_error(
            message="The in_state handle names a state outside the replayed StateModel.",
            expected="one exact state retained by the source history",
            received=selection.state.key,
            location="session.select_subjects.selection.state",
            action="Choose one exact state retained by the source replay history.",
            candidates=state_names[:5],
        )

    as_of = _parse_as_of(selection.as_of)
    window_start = _parse_as_of(meta.window.start)
    window_end = _parse_as_of(meta.window.end)
    if not window_start <= as_of <= window_end:
        raise WindowInvalidError(
            message="in_state as_of is outside the source replay window.",
            expected=f"{meta.window.start!r} <= as_of <= {meta.window.end!r}",
            received=selection.as_of,
            location="session.select_subjects.selection.as_of",
            repair=_event_repair(
                kind="user_choice",
                action="Choose an instant inside the closed source replay window.",
                help_target="select_subjects",
                candidates=(meta.window.start, meta.window.end),
            ),
        )
    _require_registered_source(session=session, artifact=artifact, label="replay history")
    return selection, as_of


def _select_state_rows(
    artifact: LifecycleFrame,
    *,
    selection: InState,
    as_of: pd.Timestamp,
) -> tuple[pd.DataFrame, int]:
    """Select subjects whose proven replayed state covers ``as_of``."""
    meta = cast("LifecycleHistoryFrameMeta", artifact.meta)
    window_end = _parse_as_of(meta.window.end)
    rows = artifact._dataframe_copy()
    selected: set[tuple[object, ...]] = set()
    censored: set[tuple[object, ...]] = set()
    matched: set[tuple[object, ...]] = set()

    for row in rows.itertuples(index=False):
        valid_from = cast("pd.Timestamp", row.valid_from)
        valid_to = cast("pd.Timestamp", row.valid_to)
        # Half-open membership, with the exclusive replay end accepted as the
        # observation boundary of the one final open interval per subject.
        covers = valid_from <= as_of < valid_to or (as_of == window_end and valid_to == window_end)
        if not covers:
            continue
        identity_value = row.subject_identity
        identity = (
            identity_value
            if isinstance(identity_value, tuple)
            else tuple(cast("Any", identity_value))
        )
        if identity in matched:
            raise _subject_error(
                message="The source history does not satisfy the disjoint interval contract.",
                expected="at most one state interval per subject and instant",
                received="overlapping_intervals",
                location="session.select_subjects.artifact.rows",
                action="Inspect and rebuild the source LifecycleFrame[history].",
            )
        matched.add(identity)
        if row.interval_status == "coverage_censored":
            censored.add(identity)
        elif row.model_state == selection.state.name:
            selected.add(identity)

    ordered = sorted(selected, key=_identity_sort_key)
    excluded = len(censored) + meta.coverage_censored_subject_count
    return pd.DataFrame({"subject_identity": ordered}), excluded


def _rollback_subject_commit(
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


def select_subjects(
    artifact: EventFrame | LifecycleFrame,
    *,
    selection: SubjectSelection,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> SubjectSet:
    """Materialize one closed subject selection from a journey or replay history."""

    resolved_session = session if session is not None else require_current_session()
    ensure_session_writable(resolved_session)
    normalized_selection: SubjectSelection
    evidence_subject: EventSubject | LifecycleSubject
    if type(artifact) is EventFrame:
        normalized_selection, target_index = _require_source(
            session=resolved_session,
            artifact=artifact,
            selection=selection,
        )
        output, excluded_censored_count = _select_rows(artifact, target_index=target_index)
        evidence_subject = event_subject_for_frame(artifact)
    elif type(artifact) is LifecycleFrame:
        normalized_selection, as_of = _require_lifecycle_source(
            session=resolved_session,
            artifact=artifact,
            selection=selection,
        )
        output, excluded_censored_count = _select_state_rows(
            artifact,
            selection=normalized_selection,
            as_of=as_of,
        )
        evidence_subject = lifecycle_subject_for_frame(artifact)
    else:
        raise _subject_error(
            message="select_subjects requires an exact source analysis artifact.",
            expected="EventFrame[journey] | LifecycleFrame[history]",
            received=type(artifact).__name__,
            location="session.select_subjects.artifact",
            action=(
                "Pass the canonical journey from session.events.match(...) or the "
                "replay history from session.lifecycle.replay(...)."
            ),
        )

    started_at = datetime.now(UTC)
    started = monotonic()
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    source_ref = artifact.meta.artifact_id or artifact.meta.ref
    source_fingerprint = cast("str", artifact.meta.content_hash)
    params = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "selection": normalized_selection.model_dump(mode="json"),
        "selection_fingerprint": normalized_selection.fingerprint,
        "selected_count": len(output),
        "excluded_coverage_censored_count": excluded_censored_count,
        "coverage_status": ("coverage_censored" if excluded_censored_count else "ready"),
    }
    frame = SubjectSet(
        _df=output,
        meta=SubjectSetMeta(
            ref=gen_ref("subjects"),
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
                        intent="select_subjects",
                        job_ref=job_ref,
                        inputs=[source_ref],
                        params_digest=params_digest(params),
                        params=params,
                        analysis_purpose=analysis_purpose,
                    )
                ],
                external_inputs=[source_ref],
            ),
            catalog_definition_fingerprint=(resolved_session.catalog.definition_fingerprint),
            subject_entity_ref=artifact.meta.subject_entity_ref,
            subject_identity=artifact.meta.subject_identity,
            source=SubjectSetSourceBinding(
                artifact_ref=source_ref,
                artifact_fingerprint=source_fingerprint,
            ),
            selection=normalized_selection,
            selection_fingerprint=normalized_selection.fingerprint,
            selected_count=len(output),
            excluded_coverage_censored_count=excluded_censored_count,
            coverage_status=("coverage_censored" if excluded_censored_count else "ready"),
        ),
    )

    commit_inputs = CommitInputs(input_refs=[source_ref])
    commit_params = CommitParams(values=params)
    commit_anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=(resolved_session.catalog.definition_fingerprint),
    )
    prospective_id = compute_prospective_artifact_id(
        step_type="select_subjects",
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
            "SubjectSet",
            commit_result(
                store=evidence_store,
                frames_dir=resolved_session._layout.frames_dir,
                frame=frame,
                step_type="select_subjects",
                inputs=commit_inputs,
                params=commit_params,
                semantic_anchors=commit_anchors,
                subject=evidence_subject,
                extractor_family="subject_set",
            ),
        )
        register_frame_artifact(resolved_session, frame)
        persist_job_record(
            resolved_session,
            {
                "id": job_ref,
                "session_id": resolved_session.id,
                "intent": "select_subjects",
                **job_semantics_from_frames(frame),
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": [source_ref],
                "output_frame_ref": frame.meta.artifact_id or frame.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": str(resolved_session.catalog.semantic_root),
                "queries": [],
            },
        )
    except BaseException:
        _rollback_subject_commit(
            session=resolved_session,
            evidence_store=evidence_store,
            artifact_id=prospective_id,
            job_ref=job_ref,
            preserve_artifact=artifact_preexisting,
        )
        raise
    return frame


__all__ = ["select_subjects"]
