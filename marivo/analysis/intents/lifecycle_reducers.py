"""Pure persisted-history reducers exposed through ``session.lifecycle``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal, TypeAlias, TypedDict, cast

import pandas as pd

from marivo._compat import UTC
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AnalysisRepair,
    InvalidDistributionInstantsError,
    SubjectSetMismatchError,
)
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
from marivo.analysis.evidence.store import EvidenceStore
from marivo.analysis.frames.lifecycle import (
    LifecycleDistributionFrameMeta,
    LifecycleDwellFrameMeta,
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
    LifecycleStateBinding,
    LifecycleStatePair,
    LifecycleTransitionsFrameMeta,
    LifecycleViolationsFrameMeta,
)
from marivo.analysis.intents._derived import compose_lineage, gen_ref, params_digest
from marivo.analysis.intents._lifecycle_distribution import (
    lifecycle_state_membership,
    reduce_lifecycle_distribution,
)
from marivo.analysis.intents._lifecycle_dwell import reduce_lifecycle_dwell
from marivo.analysis.intents._lifecycle_subject_axes import (
    materialize_lifecycle_subject_axes,
    resolve_lifecycle_subject_axes,
)
from marivo.analysis.intents._lifecycle_transitions import (
    reduce_lifecycle_transitions,
)
from marivo.analysis.intents._lifecycle_violations import (
    reduce_lifecycle_violations,
)
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
    require_current_session,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.introspection.live.model import LiveHelpTarget
from marivo.refs import RefPayloadV1
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import StateModelEntry
from marivo.semantic.errors import SemanticRuntimeError

LifecycleReducerHelpTarget: TypeAlias = Literal[
    "lifecycle.distribution",
    "lifecycle.transitions",
    "lifecycle.dwell",
    "lifecycle.violations",
]


def _repair(
    *,
    help_target: LifecycleReducerHelpTarget,
    kind: Literal["inspect", "user_choice"],
    action: str,
    candidates: tuple[str, ...] = (),
) -> AnalysisRepair:
    return AnalysisRepair(
        kind=kind,
        action=action,
        help_target=LiveHelpTarget(
            surface="analysis",
            canonical_id=help_target,
        ),
        candidates=candidates,
    )


def _source_error(
    *,
    help_target: LifecycleReducerHelpTarget,
    message: str,
    expected: str,
    received: str,
    action: str,
) -> SubjectSetMismatchError:
    return SubjectSetMismatchError(
        message=message,
        expected=expected,
        received=received,
        location=f"session.{help_target}.history",
        repair=_repair(
            help_target=help_target,
            kind="inspect",
            action=action,
        ),
    )


def _require_history_source(
    *,
    session: Session,
    history: LifecycleFrame,
    help_target: LifecycleReducerHelpTarget,
) -> tuple[LifecycleFrame, LifecycleHistoryFrameMeta, str, str]:
    if type(history) is not LifecycleFrame:
        raise _source_error(
            help_target=help_target,
            message=f"{help_target} requires an exact LifecycleFrame.",
            expected="LifecycleFrame[history]",
            received=type(history).__name__,
            action="Pass the canonical history returned by session.lifecycle.replay(...).",
        )
    if history.meta.session_id != session.id:
        raise _source_error(
            help_target=help_target,
            message="The Lifecycle history belongs to a different analysis session.",
            expected="a LifecycleFrame owned by the current session",
            received="different_session",
            action="Load or rebuild the history in the current session.",
        )
    if Path(history.meta.project_root).resolve() != session.project_root.resolve():
        raise _source_error(
            help_target=help_target,
            message="The Lifecycle history belongs to a different Marivo project.",
            expected="a LifecycleFrame owned by the current project",
            received="different_project",
            action="Replay the StateModel from the current project catalog.",
        )
    if history.meta.semantic_kind != "history":
        raise _source_error(
            help_target=help_target,
            message=f"{help_target} accepts only canonical replay history.",
            expected="LifecycleFrame semantic_kind='history'",
            received=f"semantic_kind={history.meta.semantic_kind!r}",
            action="Use the source LifecycleFrame[history], not a reducer output.",
        )
    history_meta = history.meta
    if history_meta.catalog_definition_fingerprint != session.catalog.definition_fingerprint:
        raise _source_error(
            help_target=help_target,
            message="The Lifecycle history was built from a stale semantic catalog.",
            expected="the current catalog definition fingerprint",
            received=history_meta.catalog_definition_fingerprint,
            action="Replay the StateModel against the current catalog before reducing it.",
        )
    try:
        model_entry = session.catalog.require(
            ref_factory.state_model(history_meta.state_model_ref.path)
        )
    except SemanticRuntimeError as exc:
        raise _source_error(
            help_target=help_target,
            message="The history StateModel is absent from the current catalog.",
            expected="the exact retained StateModel in the current catalog",
            received=history_meta.state_model_ref.path,
            action="Inspect current StateModels and replay a current definition.",
        ) from exc
    if not isinstance(model_entry, StateModelEntry):
        raise AssertionError(f"StateModel ref resolved to {type(model_entry).__name__}")
    if model_entry.details().definition_fingerprint != history_meta.state_model_fingerprint:
        raise _source_error(
            help_target=help_target,
            message="The history StateModel definition has changed.",
            expected="the retained StateModel definition fingerprint",
            received=model_entry.details().definition_fingerprint,
            action="Replay the current StateModel before applying Lifecycle reducers.",
        )

    source_ref = history_meta.artifact_id or history_meta.ref
    source_fingerprint = history_meta.content_hash
    registered = session._store.get_artifact(session.id, source_ref)
    if (
        source_fingerprint is None
        or registered is None
        or registered["content_hash"] != source_fingerprint
    ):
        raise _source_error(
            help_target=help_target,
            message="The Lifecycle history artifact is missing or stale in this session.",
            expected="a registered source artifact with the retained content fingerprint",
            received="source=unavailable_or_changed",
            action="Inspect the source history and replay it before reducing it.",
        )
    committed = session.get_frame(source_ref)
    if (
        type(committed) is not LifecycleFrame
        or committed.meta.semantic_kind != "history"
        or committed.meta.content_hash != source_fingerprint
    ):
        raise _source_error(
            help_target=help_target,
            message="The committed source no longer matches the retained Lifecycle history.",
            expected="the exact committed LifecycleFrame[history]",
            received="source=wrong_kind_or_fingerprint",
            action="Inspect the committed source artifact and replay it if necessary.",
        )
    return (
        committed,
        committed.meta,
        source_ref,
        source_fingerprint,
    )


def _rollback_reducer_commit(
    *,
    session: Session,
    evidence_store: EvidenceStore | None,
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
    frame: LifecycleFrame,
    source: LifecycleFrame,
    intent: LifecycleReducerHelpTarget,
    job_ref: str,
    started_at: datetime,
    started: float,
    finished_at: datetime,
    analysis_purpose: str | None,
    params: dict[str, object],
    queries: list[dict[str, object]],
) -> LifecycleFrame:
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
            "LifecycleFrame",
            commit_result(
                store=evidence_store,
                frames_dir=session._layout.frames_dir,
                frame=frame,
                step_type=intent,
                inputs=commit_inputs,
                params=commit_params,
                semantic_anchors=commit_anchors,
                subject=lifecycle_subject_for_frame(frame),
                extractor_family="lifecycle_frame",
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


def _canonical_instants(
    at: Sequence[str],
    *,
    history_meta: LifecycleHistoryFrameMeta,
) -> tuple[tuple[str, pd.Timestamp], ...]:
    if isinstance(at, (str, bytes)) or not at:
        raise InvalidDistributionInstantsError(
            message="Lifecycle distribution requires at least one explicit instant.",
            expected="a non-empty Sequence[str] of timezone-aware instants",
            received=repr(at),
            location="session.lifecycle.distribution.at",
            repair=_repair(
                help_target="lifecycle.distribution",
                kind="user_choice",
                action="Choose one or more instants inside the replay window.",
            ),
        )
    window_start = pd.Timestamp(history_meta.window.start)
    window_end = pd.Timestamp(history_meta.window.end)
    normalized: list[tuple[str, pd.Timestamp]] = []
    for index, value in enumerate(at):
        if type(value) is not str or not value.strip():
            raise InvalidDistributionInstantsError(
                message="Lifecycle distribution instants must be non-empty strings.",
                expected="a timezone-aware ISO-8601 instant",
                received=repr(value),
                location=f"session.lifecycle.distribution.at[{index}]",
                repair=_repair(
                    help_target="lifecycle.distribution",
                    kind="user_choice",
                    action="Choose a timezone-aware instant inside the replay window.",
                ),
            )
        try:
            instant = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise InvalidDistributionInstantsError(
                message="Lifecycle distribution received an invalid instant.",
                expected="a timezone-aware ISO-8601 instant",
                received=repr(value),
                location=f"session.lifecycle.distribution.at[{index}]",
                repair=_repair(
                    help_target="lifecycle.distribution",
                    kind="user_choice",
                    action="Choose a timezone-aware instant inside the replay window.",
                ),
            ) from exc
        if instant.tzinfo is None:
            raise InvalidDistributionInstantsError(
                message="Lifecycle distribution instants must be timezone-aware.",
                expected="a timezone-aware ISO-8601 instant",
                received=repr(value),
                location=f"session.lifecycle.distribution.at[{index}]",
                repair=_repair(
                    help_target="lifecycle.distribution",
                    kind="user_choice",
                    action="Add an explicit timezone offset to the distribution instant.",
                ),
            )
        instant = instant.tz_convert(UTC)
        canonical = instant.isoformat().replace("+00:00", "Z")
        normalized.append((canonical, instant))
    normalized.sort(key=lambda item: item[1])
    canonical_values = tuple(item[0] for item in normalized)
    if len(set(canonical_values)) != len(canonical_values):
        raise InvalidDistributionInstantsError(
            message="Lifecycle distribution instants must be unique after normalization.",
            expected="unique normalized instants",
            received=repr(canonical_values),
            location="session.lifecycle.distribution.at",
            repair=_repair(
                help_target="lifecycle.distribution",
                kind="user_choice",
                action="Remove duplicate instants and retry distribution.",
            ),
        )
    outside = tuple(
        canonical
        for canonical, instant in normalized
        if instant < window_start or instant >= window_end
    )
    if outside:
        raise InvalidDistributionInstantsError(
            message="Lifecycle distribution instants must lie inside the replay window.",
            expected=f"{history_meta.window.start} <= at < {history_meta.window.end}",
            received=repr(outside),
            location="session.lifecycle.distribution.at",
            repair=_repair(
                help_target="lifecycle.distribution",
                kind="user_choice",
                action="Choose instants inside the retained half-open replay window.",
            ),
        )
    return tuple(normalized)


class _LifecycleReducerMetaKwargs(TypedDict):
    catalog_definition_fingerprint: str
    state_model_ref: RefPayloadV1
    state_model_fingerprint: str
    subject_entity_ref: RefPayloadV1
    subject_identity: tuple[str, ...]
    states: tuple[LifecycleStateBinding, ...]
    source_history_ref: str
    source_history_fingerprint: str


def _common_meta(
    *,
    source_meta: LifecycleHistoryFrameMeta,
    source_ref: str,
    source_fingerprint: str,
) -> _LifecycleReducerMetaKwargs:
    return {
        "catalog_definition_fingerprint": (source_meta.catalog_definition_fingerprint),
        "state_model_ref": source_meta.state_model_ref,
        "state_model_fingerprint": source_meta.state_model_fingerprint,
        "subject_entity_ref": source_meta.subject_entity_ref,
        "subject_identity": source_meta.subject_identity,
        "states": source_meta.states,
        "source_history_ref": source_ref,
        "source_history_fingerprint": source_fingerprint,
    }


def distribution(
    history: LifecycleFrame,
    *,
    at: Sequence[str],
    axes: Sequence[object] = (),
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> LifecycleFrame:
    """Reduce committed replay history into dense point-in-time distributions."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_can_execute(resolved_session)
    source, source_meta, source_ref, source_fingerprint = _require_history_source(
        session=resolved_session,
        history=history,
        help_target="lifecycle.distribution",
    )
    instants = _canonical_instants(at, history_meta=source_meta)
    started_at = datetime.now(UTC)
    started = monotonic()
    resolved_axes = resolve_lifecycle_subject_axes(
        resolved_session,
        subject_entity=ref_factory.entity(source_meta.subject_entity_ref.path),
        axes=axes,
    )
    source_rows = source._dataframe_copy()
    membership = lifecycle_state_membership(source_rows, instants=instants)
    axis_materialization = materialize_lifecycle_subject_axes(
        resolved_session,
        membership=membership,
        instants=tuple(item[0] for item in instants),
        subject_entity=ref_factory.entity(source_meta.subject_entity_ref.path),
        subject_identity=source_meta.subject_identity,
        axes=resolved_axes,
    )
    axis_columns = tuple(item.output_column for item in axis_materialization.bindings)
    state_order = tuple(item.state.name for item in source_meta.states)
    reduction = reduce_lifecycle_distribution(
        source_rows,
        instants=instants,
        state_order=state_order,
        population_count=source_meta.population_count,
        axis_values=axis_materialization.values if axis_columns else None,
        axis_columns=axis_columns,
    )
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params: dict[str, object] = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "at": [item[0] for item in instants],
        "axes": [item.model_dump(mode="json") for item in axis_materialization.bindings],
        "known_subject_counts": reduction.known_subject_counts,
        "coverage_censored_subject_counts": (reduction.coverage_censored_subject_counts),
        "grouped_reconciliation_hash": reduction.grouped_reconciliation_hash,
    }
    step = LineageStep(
        intent="lifecycle.distribution",
        job_ref=job_ref,
        inputs=[source_ref],
        params_digest=params_digest(params),
        params={
            "source_artifact_ref": source_ref,
            "at": [item[0] for item in instants],
            "axis_refs": [axis.ref.key for axis in resolved_axes],
        },
        analysis_purpose=analysis_purpose,
    )
    frame = LifecycleFrame(
        _df=reduction.rows,
        meta=LifecycleDistributionFrameMeta(
            **_common_meta(
                source_meta=source_meta,
                source_ref=source_ref,
                source_fingerprint=source_fingerprint,
            ),
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(reduction.rows),
            byte_size=0,
            lineage=compose_lineage((source,), step=step),
            at=tuple(item[0] for item in instants),
            axes=axis_materialization.bindings,
            known_subject_counts=reduction.known_subject_counts,
            coverage_censored_subject_counts=(reduction.coverage_censored_subject_counts),
            grouped_reconciliation_hash=reduction.grouped_reconciliation_hash,
        ),
    )
    return _commit_reducer(
        session=resolved_session,
        frame=frame,
        source=source,
        intent="lifecycle.distribution",
        job_ref=job_ref,
        started_at=started_at,
        started=started,
        finished_at=finished_at,
        analysis_purpose=analysis_purpose,
        params=params,
        queries=list(axis_materialization.lineage),
    )


def transitions(
    history: LifecycleFrame,
    *,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> LifecycleFrame:
    """Count dense modeled state pairs from committed replay history."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_can_execute(resolved_session)
    source, source_meta, source_ref, source_fingerprint = _require_history_source(
        session=resolved_session,
        history=history,
        help_target="lifecycle.transitions",
    )
    started_at = datetime.now(UTC)
    started = monotonic()
    reduction = reduce_lifecycle_transitions(
        source._dataframe_copy(),
        triggers=source_meta.triggers,
    )
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params: dict[str, object] = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "modeled_pairs": [
            {"from_state": source_state, "to_state": target_state}
            for source_state, target_state in reduction.modeled_pairs
        ],
        "modeled_transition_count": reduction.modeled_transition_count,
    }
    step = LineageStep(
        intent="lifecycle.transitions",
        job_ref=job_ref,
        inputs=[source_ref],
        params_digest=params_digest(params),
        params={"source_artifact_ref": source_ref},
        analysis_purpose=analysis_purpose,
    )
    frame = LifecycleFrame(
        _df=reduction.rows,
        meta=LifecycleTransitionsFrameMeta(
            **_common_meta(
                source_meta=source_meta,
                source_ref=source_ref,
                source_fingerprint=source_fingerprint,
            ),
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(reduction.rows),
            byte_size=0,
            lineage=compose_lineage((source,), step=step),
            modeled_pairs=tuple(
                LifecycleStatePair(from_state=source_state, to_state=target_state)
                for source_state, target_state in reduction.modeled_pairs
            ),
            modeled_transition_count=reduction.modeled_transition_count,
        ),
    )
    return _commit_reducer(
        session=resolved_session,
        frame=frame,
        source=source,
        intent="lifecycle.transitions",
        job_ref=job_ref,
        started_at=started_at,
        started=started,
        finished_at=finished_at,
        analysis_purpose=analysis_purpose,
        params=params,
        queries=[],
    )


def dwell(
    history: LifecycleFrame,
    *,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> LifecycleFrame:
    """Summarize completed and censored dwell from committed replay history."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_can_execute(resolved_session)
    source, source_meta, source_ref, source_fingerprint = _require_history_source(
        session=resolved_session,
        history=history,
        help_target="lifecycle.dwell",
    )
    started_at = datetime.now(UTC)
    started = monotonic()
    reduction = reduce_lifecycle_dwell(
        source._dataframe_copy(),
        state_order=tuple(item.state.name for item in source_meta.states),
    )
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params: dict[str, object] = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "source_interval_count": reduction.source_interval_count,
    }
    step = LineageStep(
        intent="lifecycle.dwell",
        job_ref=job_ref,
        inputs=[source_ref],
        params_digest=params_digest(params),
        params={"source_artifact_ref": source_ref},
        analysis_purpose=analysis_purpose,
    )
    frame = LifecycleFrame(
        _df=reduction.rows,
        meta=LifecycleDwellFrameMeta(
            **_common_meta(
                source_meta=source_meta,
                source_ref=source_ref,
                source_fingerprint=source_fingerprint,
            ),
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(reduction.rows),
            byte_size=0,
            lineage=compose_lineage((source,), step=step),
            source_interval_count=reduction.source_interval_count,
        ),
    )
    return _commit_reducer(
        session=resolved_session,
        frame=frame,
        source=source,
        intent="lifecycle.dwell",
        job_ref=job_ref,
        started_at=started_at,
        started=started,
        finished_at=finished_at,
        analysis_purpose=analysis_purpose,
        params=params,
        queries=[],
    )


def violations(
    history: LifecycleFrame,
    *,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> LifecycleFrame:
    """Expose a typed copy of the committed replay violation trace."""
    resolved_session = session if session is not None else require_current_session()
    ensure_session_can_execute(resolved_session)
    source, source_meta, source_ref, source_fingerprint = _require_history_source(
        session=resolved_session,
        history=history,
        help_target="lifecycle.violations",
    )
    started_at = datetime.now(UTC)
    started = monotonic()
    trace_hash = source_meta.violation_trace.content_hash
    if trace_hash is None:
        raise _source_error(
            help_target="lifecycle.violations",
            message="The committed Lifecycle violation trace lacks its content receipt.",
            expected="a validated sha256 trace content hash",
            received="trace.content_hash=None",
            action="Reload or replay the Lifecycle history before reading violations.",
        )
    reduction = reduce_lifecycle_violations(
        source._auxiliary_frames[source_meta.violation_trace.filename]
    )
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params: dict[str, object] = {
        "source_artifact_ref": source_ref,
        "source_artifact_fingerprint": source_fingerprint,
        "source_trace_content_hash": trace_hash,
        "violation_count": reduction.violation_count,
    }
    step = LineageStep(
        intent="lifecycle.violations",
        job_ref=job_ref,
        inputs=[source_ref],
        params_digest=params_digest(params),
        params={"source_artifact_ref": source_ref},
        analysis_purpose=analysis_purpose,
    )
    frame = LifecycleFrame(
        _df=reduction.rows,
        meta=LifecycleViolationsFrameMeta(
            **_common_meta(
                source_meta=source_meta,
                source_ref=source_ref,
                source_fingerprint=source_fingerprint,
            ),
            ref=gen_ref("frame"),
            session_id=resolved_session.id,
            project_root=str(resolved_session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(reduction.rows),
            byte_size=0,
            lineage=compose_lineage((source,), step=step),
            violation_count=reduction.violation_count,
            source_trace_content_hash=trace_hash,
        ),
    )
    return _commit_reducer(
        session=resolved_session,
        frame=frame,
        source=source,
        intent="lifecycle.violations",
        job_ref=job_ref,
        started_at=started_at,
        started=started,
        finished_at=finished_at,
        analysis_purpose=analysis_purpose,
        params=params,
        queries=[],
    )


__all__ = ["distribution", "dwell", "transitions", "violations"]
