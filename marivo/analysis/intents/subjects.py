"""Materialize typed subject selections from canonical Event journeys."""

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
    PatternStepMismatchError,
    SubjectSetMismatchError,
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
    rollback_committed_result,
)
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.frames.subject import (
    SubjectSet,
    SubjectSetMeta,
    SubjectSetSourceBinding,
)
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.subject import DroppedBefore, SubjectSelection


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


def _require_source(
    *,
    session: Session,
    artifact: EventFrame,
    selection: SubjectSelection,
) -> tuple[DroppedBefore, int]:
    if type(artifact) is not EventFrame:
        raise _subject_error(
            message="select_subjects requires an exact EventFrame.",
            expected="EventFrame[journey]",
            received=type(artifact).__name__,
            location="session.select_subjects.artifact",
            action="Pass the canonical journey returned by session.events.match(...).",
        )
    if artifact.meta.session_id != session.id:
        raise _subject_error(
            message="The source journey belongs to a different analysis session.",
            expected="an EventFrame owned by the current session",
            received="different_session",
            location="session.select_subjects.artifact",
            action="Load or rebuild the journey in the current session.",
        )
    if Path(artifact.meta.project_root).resolve() != session.project_root.resolve():
        raise _subject_error(
            message="The source journey belongs to a different Marivo project.",
            expected="an EventFrame owned by the current project",
            received="different_project",
            location="session.select_subjects.artifact",
            action="Rebuild the journey from the current project catalog.",
        )
    if artifact.meta.catalog_definition_fingerprint != session.catalog.definition_fingerprint:
        raise _subject_error(
            message="The source journey was produced from a different catalog definition.",
            expected="an EventFrame built from the active catalog fingerprint",
            received="catalog_definition_changed",
            location="session.select_subjects.artifact",
            action="Reload the active catalog and rebuild the source journey.",
        )
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

    artifact_ref = artifact.meta.artifact_id or artifact.meta.ref
    source_fingerprint = artifact.meta.content_hash
    row = session._store.get_artifact(session.id, artifact_ref)
    if source_fingerprint is None or row is None or row["content_hash"] != source_fingerprint:
        raise _subject_error(
            message="The source journey artifact is missing or stale in this session.",
            expected="a registered source artifact with the retained content fingerprint",
            received="source=unavailable_or_changed",
            location="session.select_subjects.artifact",
            action="Inspect the source journey and rebuild it before selecting subjects.",
        )
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
    artifact: EventFrame,
    *,
    selection: SubjectSelection,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> SubjectSet:
    """Materialize one closed subject selection from an EventFrame[journey]."""

    resolved_session = session if session is not None else require_current_session()
    ensure_session_writable(resolved_session)
    normalized_selection, target_index = _require_source(
        session=resolved_session,
        artifact=artifact,
        selection=selection,
    )
    output, excluded_censored_count = _select_rows(
        artifact,
        target_index=target_index,
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
                subject=event_subject_for_frame(artifact),
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
