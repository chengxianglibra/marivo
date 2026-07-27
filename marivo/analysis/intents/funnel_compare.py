"""Exact comparison orchestration for two persisted Event funnels."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AnalysisRepair,
    EventCoverageUnknownError,
    FunnelComparisonMismatchError,
)
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    compute_prospective_artifact_id,
    frame_exists_on_disk,
    rollback_committed_result,
)
from marivo.analysis.evidence.types import EventSubject
from marivo.analysis.frames.delta import DeltaFrame, FunnelDeltaFrameMeta
from marivo.analysis.frames.event import EventFrame, EventFunnelFrameMeta
from marivo.analysis.intents._derived import gen_ref, params_digest
from marivo.analysis.intents._funnel_delta import build_funnel_delta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.policies import AlignmentPolicy
from marivo.analysis.session._load import load_frame
from marivo.analysis.session._runtime import persist_job_record, register_frame_artifact
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.introspection.live.model import LiveHelpTarget


def _repair(*, action: str, kind: str = "user_choice") -> AnalysisRepair:
    return AnalysisRepair(
        kind=cast("Any", kind),
        action=action,
        help_target=LiveHelpTarget(surface="analysis", canonical_id="compare"),
    )


def _require_funnel_pair(
    current: object,
    baseline: object,
    *,
    session: Session,
) -> tuple[EventFrame, EventFrame]:
    for name, value in (("current", current), ("baseline", baseline)):
        if type(value) is not EventFrame or value.meta.semantic_kind != "funnel":
            raise FunnelComparisonMismatchError(
                message="funnel comparison requires two exact EventFrame[funnel] artifacts",
                expected="current and baseline are EventFrame[funnel]",
                received=f"{name}={type(value).__name__}",
                location=f"session.compare.{name}",
                repair=_repair(
                    action="Reduce two first-per-subject journeys with session.events.funnel(...)."
                ),
            )
        if (
            value.meta.session_id != session.id
            or Path(value.meta.project_root).resolve() != session.project_root.resolve()
        ):
            raise FunnelComparisonMismatchError(
                message=f"{name} funnel is not owned by this session",
                expected="same-session EventFrame[funnel]",
                received=f"session_id={value.meta.session_id!r}",
                location=f"session.compare.{name}",
                repair=_repair(action=f"Load or rebuild the {name} funnel in this session."),
            )
    return cast("EventFrame", current), cast("EventFrame", baseline)


def _reject_alignment(alignment: AlignmentPolicy | None) -> None:
    if alignment is None:
        return
    raise FunnelComparisonMismatchError(
        message="funnel comparison does not accept a caller-supplied alignment",
        expected="alignment=None",
        received=f"alignment={alignment.kind!r}",
        location="session.compare.alignment",
        repair=_repair(
            action=(
                "Omit alignment. Funnel compare derives its alignment from persisted "
                "PatternStep identity plus the exact axis tuple."
            )
        ),
    )


_COMPATIBILITY_FACETS: tuple[tuple[str, Callable[[EventFunnelFrameMeta], object]], ...] = (
    ("catalog_definition_fingerprint", lambda meta: meta.catalog_definition_fingerprint),
    ("subject_entity", lambda meta: meta.subject_entity_ref.path),
    ("subject_identity", lambda meta: meta.subject_identity),
    ("pattern", lambda meta: meta.pattern.fingerprint),
    ("pattern_step_keys", lambda meta: tuple(step.key for step in meta.pattern.steps)),
    (
        "pattern_step_definitions",
        lambda meta: tuple(step.fingerprint for step in meta.pattern.steps),
    ),
    ("matching", lambda meta: meta.matching.kind),
    ("completion_through", lambda meta: meta.completion_through),
    ("axis_dimensions", lambda meta: tuple(axis.dimension_ref.path for axis in meta.axes)),
    ("axis_columns", lambda meta: tuple(axis.output_column for axis in meta.axes)),
    ("axis_anchors", lambda meta: tuple(axis.anchor for axis in meta.axes)),
)


def _require_compatible(current: EventFrame, baseline: EventFrame) -> None:
    current_meta = cast("EventFunnelFrameMeta", current.meta)
    baseline_meta = cast("EventFunnelFrameMeta", baseline.meta)
    for facet, read in _COMPATIBILITY_FACETS:
        left, right = read(current_meta), read(baseline_meta)
        if left == right:
            continue
        raise FunnelComparisonMismatchError(
            message=f"compared funnels differ in {facet}",
            expected=f"two EventFrame[funnel] artifacts with identical {facet}",
            received=f"{facet}: current={left!r} baseline={right!r}",
            location="session.compare(current, baseline)",
            repair=_repair(
                action=(
                    f"Rebuild one side so both funnels share the same {facet}, then "
                    "compare them again. Comparison never aligns by position."
                )
            ),
        )


def _require_resolved_coverage(current: EventFrame, baseline: EventFrame) -> None:
    for label, frame in (("current", current), ("baseline", baseline)):
        censored = int(frame._dataframe_copy()["coverage_censored_count"].sum())
        if censored:
            raise EventCoverageUnknownError(
                message="funnel comparison requires fully classified populations",
                expected="both funnels resolved at every aligned PatternStep",
                received=f"{label} funnel has {censored} coverage-censored subjects",
                location="session.compare(current, baseline)",
                repair=_repair(
                    kind="inspect",
                    action=(
                        "Extend observed coverage past completion_through, or declare "
                        f"the named Event inputs complete, then rebuild the {label} funnel."
                    ),
                ),
            )


def _rollback(
    *,
    session: Session,
    evidence_store: Any,
    artifact_id: str,
    job_ref: str,
    preserve_artifact: bool,
) -> None:
    cleanup: list[Callable[[], object]] = [
        lambda: session._store.delete_job(session.id, job_ref),
        lambda: (session._layout.jobs_dir / f"{job_ref}.json").unlink(missing_ok=True),
    ]
    if not preserve_artifact:
        cleanup.extend(
            [
                lambda: session._store.delete_artifact(session.id, artifact_id),
                lambda: rollback_committed_result(
                    store=evidence_store,
                    frames_dir=session._layout.frames_dir,
                    artifact_id=artifact_id,
                ),
            ]
        )
    for action in cleanup:
        try:
            action()
        except BaseException:
            continue


def compare_funnels(
    current: object,
    baseline: object,
    *,
    alignment: AlignmentPolicy | None,
    analysis_purpose: str | None,
    session: Session,
) -> DeltaFrame:
    """Compare two exact, structurally compatible Event funnels."""
    ensure_session_writable(session)
    started_at = datetime.now(UTC)
    started = monotonic()
    current_frame, baseline_frame = _require_funnel_pair(current, baseline, session=session)
    _reject_alignment(alignment)
    _require_compatible(current_frame, baseline_frame)
    _require_resolved_coverage(current_frame, baseline_frame)
    current_meta = cast("EventFunnelFrameMeta", current_frame.meta)
    baseline_meta = cast("EventFunnelFrameMeta", baseline_frame.meta)
    axis_columns = tuple(axis.output_column for axis in current_meta.axes)
    delta = build_funnel_delta(
        current=current_frame._dataframe_copy(),
        baseline=baseline_frame._dataframe_copy(),
        axis_columns=axis_columns,
        step_order=tuple(step.key for step in current_meta.pattern.steps),
    )
    finished_at = datetime.now(UTC)
    job_ref = gen_ref("job")
    params: dict[str, Any] = {
        "alignment_kind": delta.alignment_kind,
        "axes": [axis.model_dump(mode="json") for axis in current_meta.axes],
        "aligned_step_keys": list(delta.aligned_step_keys),
        "zero_filled_tuple_count": delta.zero_filled_tuple_count,
        "source_current_ref": current_frame.meta.artifact_id or current_frame.ref,
        "source_baseline_ref": baseline_frame.meta.artifact_id or baseline_frame.ref,
    }
    inputs = CommitInputs(
        input_refs=[
            current_frame.meta.artifact_id or current_frame.ref,
            baseline_frame.meta.artifact_id or baseline_frame.ref,
        ]
    )
    commit_params = CommitParams(values=params)
    anchors = CommitSemanticAnchors(
        catalog_definition_fingerprint=current_meta.catalog_definition_fingerprint,
    )
    prospective_id = compute_prospective_artifact_id(
        step_type="compare.funnel",
        inputs=inputs,
        params=commit_params,
        semantic_anchors=anchors,
    )
    if frame_exists_on_disk(session._layout.frames_dir, prospective_id):
        return cast("DeltaFrame", load_frame(prospective_id, session=session))

    source_current_fingerprint = current_meta.content_hash
    source_baseline_fingerprint = baseline_meta.content_hash
    if not source_current_fingerprint or not source_baseline_fingerprint:
        raise FunnelComparisonMismatchError(
            message="funnel comparison sources must be persisted",
            expected="both source funnels retain content_hash",
            received="one or both content hashes are missing",
            location="session.compare(current, baseline)",
            repair=_repair(action="Rebuild both funnels in the current session."),
        )
    step = LineageStep(
        intent="compare.funnel",
        job_ref=job_ref,
        inputs=list(inputs.input_refs),
        params_digest=params_digest(params),
        analysis_purpose=analysis_purpose,
    )
    frame = DeltaFrame(
        _df=delta.rows,
        meta=FunnelDeltaFrameMeta(
            ref=gen_ref("frame"),
            session_id=session.id,
            project_root=str(session.project_root),
            produced_by_job=job_ref,
            analysis_purpose=analysis_purpose,
            created_at=finished_at,
            row_count=len(delta.rows),
            byte_size=0,
            lineage=Lineage.compose(
                current_frame.lineage,
                baseline_frame.lineage,
                new_step=step,
            ),
            catalog_definition_fingerprint=current_meta.catalog_definition_fingerprint,
            subject_entity_ref=current_meta.subject_entity_ref,
            subject_identity=current_meta.subject_identity,
            pattern=current_meta.pattern,
            matching=current_meta.matching,
            completion_through=current_meta.completion_through,
            axes=current_meta.axes,
            aligned_step_keys=delta.aligned_step_keys,
            zero_filled_tuple_count=delta.zero_filled_tuple_count,
            source_current_ref=inputs.input_refs[0],
            source_baseline_ref=inputs.input_refs[1],
            source_current_fingerprint=source_current_fingerprint,
            source_baseline_fingerprint=source_baseline_fingerprint,
            source_current_journey_ref=current_meta.source_journey_ref,
            source_baseline_journey_ref=baseline_meta.source_journey_ref,
            current_cohort_window=current_meta.cohort_window,
            baseline_cohort_window=baseline_meta.cohort_window,
            current_coverage_basis=current_meta.coverage_basis,
            baseline_coverage_basis=baseline_meta.coverage_basis,
            current_completeness=current_meta.completeness,
            baseline_completeness=baseline_meta.completeness,
        ),
    )
    evidence_store = session._evidence_store()
    preexisting = session._store.get_artifact(session.id, prospective_id) is not None
    try:
        committed = cast(
            "DeltaFrame",
            commit_result(
                store=evidence_store,
                frames_dir=session._layout.frames_dir,
                frame=frame,
                step_type="compare.funnel",
                inputs=inputs,
                params=commit_params,
                semantic_anchors=anchors,
                subject=EventSubject(
                    subject_entity_ref=current_meta.subject_entity_ref,
                    subject_identity_signature=current_meta.subject_identity,
                    analysis_axis="funnel_delta",
                ),
                extractor_family="delta_frame",
            ),
        )
        register_frame_artifact(session, committed)
        persist_job_record(
            session,
            {
                "id": job_ref,
                "session_id": session.id,
                "intent": "compare.funnel",
                **job_semantics_from_frames(committed),
                "analysis_purpose": analysis_purpose,
                "params": params,
                "input_frame_refs": list(inputs.input_refs),
                "output_frame_ref": committed.meta.artifact_id or committed.ref,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_ms": int((monotonic() - started) * 1000),
                "status": "succeeded",
                "error": None,
                "semantic_project_root": str(session.catalog.semantic_root),
            },
        )
    except BaseException:
        _rollback(
            session=session,
            evidence_store=evidence_store,
            artifact_id=prospective_id,
            job_ref=job_ref,
            preserve_artifact=preexisting,
        )
        raise
    return committed


__all__ = ["compare_funnels"]
