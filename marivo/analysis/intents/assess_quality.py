"""Assess frame quality and return a QualityReport."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from datetime import datetime
from time import monotonic
from typing import Any, Literal, cast

import pandas as pd

from marivo._compat import UTC
from marivo.analysis._semantic_persistence import job_semantics_from_frames
from marivo.analysis.errors import (
    AnalysisRepair,
    FrameMetaInvalidError,
    QualityShapeUnsupportedError,
)
from marivo.analysis.evidence.identity import make_issue_id
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
    event_subject_for_frame,
    lifecycle_subject_for_frame,
)
from marivo.analysis.evidence.types import (
    ArtifactIssue,
    DataQualityIssue,
    EventSubject,
    EvidenceSubject,
    Subject,
)
from marivo.analysis.frames._meta_defaults import compute_analysis_scope
from marivo.analysis.frames.attribution import AttributionFrame, FunnelAttributionFrameMeta
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.delta import DeltaFrame, FunnelDeltaFrameMeta
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.frames.lifecycle import (
    LifecycleFrame,
    LifecycleHistoryFrameMeta,
)
from marivo.analysis.frames.metric import MetricFrame
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.intents._derived import (
    compose_candidate_origins,
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    resolve_session,
)
from marivo.analysis.intents._quality_checks import (
    _VALUE_DENSITY_WARNING_THRESHOLD,
    run_attribution_checks,
    run_delta_checks,
    run_event_checks,
    run_funnel_attribution_checks,
    run_funnel_delta_checks,
    run_lifecycle_checks,
    run_metric_checks,
)
from marivo.analysis.intents._validate import require_single_metric
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import (
    persist_job_record,
    register_frame_artifact,
)
from marivo.analysis.session.core import Session, ensure_session_can_execute
from marivo.introspection.live.model import LiveHelpTarget


def assess_quality(
    frame: BaseFrame,
    *,
    analysis_purpose: str | None = None,
    session: Session | None = None,
) -> QualityReport:
    session = resolve_session(session)
    ensure_session_can_execute(session)
    if not isinstance(
        frame,
        (MetricFrame, EventFrame, LifecycleFrame, DeltaFrame, AttributionFrame),
    ):
        raise QualityShapeUnsupportedError(
            message=(
                "assess_quality supports MetricFrame, registered EventFrame "
                "journey/funnel/time-to-event shapes, and registered LifecycleFrame "
                "history/distribution/transitions/dwell/violations shapes, "
                "metric DeltaFrame shapes, DeltaFrame[funnel], and "
                "registered metric/funnel AttributionFrame shapes"
            ),
            context={"frame_kind": frame.meta.kind},
        )
    if isinstance(frame, MetricFrame):
        require_single_metric(frame, intent="assess_quality")
    ensure_frame_in_session(frame, session=session, label="assess_quality frame")

    attribution_metric_id: str | None = None
    if isinstance(frame, AttributionFrame) and not isinstance(
        frame.meta, FunnelAttributionFrameMeta
    ):
        if len(frame.meta.metric_ids) != 1:
            raise FrameMetaInvalidError(
                message="metric attribution quality requires exactly one target metric",
                expected="one metric id",
                received=f"metric_ids={frame.meta.metric_ids!r}",
                location="session.assess_quality frame.meta.metric_ids",
                repair=AnalysisRepair(
                    kind="retry",
                    action=(
                        "Re-run session.attribute(...) from a canonical single-metric DeltaFrame."
                    ),
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                    snippet="drivers = session.attribute(delta, axes=[axis])",
                ),
            )
        attribution_metric_id = frame.meta.metric_ids[0]
        if frame.meta.produced_by_job is None:
            raise FrameMetaInvalidError(
                message="metric attribution quality requires its producing job",
                expected="a persisted producer job ref",
                received="produced_by_job=None",
                location="session.assess_quality frame.meta.produced_by_job",
                repair=AnalysisRepair(
                    kind="retry",
                    action="Re-run session.attribute(...) from the source DeltaFrame.",
                    help_target=LiveHelpTarget(surface="analysis", canonical_id="attribute"),
                    snippet="drivers = session.attribute(delta, axes=[axis])",
                ),
            )
        producer_job = session.job(frame.meta.produced_by_job)
        semantic_keys = (
            "catalog_definition_fingerprint",
            "dimension_refs",
            "slice_predicates",
            "time_dimension_ref",
            "semantic_dependency_digest",
            "semantic_dependency_digests",
            "subject",
            "subjects",
            "cohort",
        )
        job_semantics = {key: producer_job[key] for key in semantic_keys if key in producer_job}
    else:
        job_semantics = job_semantics_from_frames(frame)

    started_at = datetime.now(UTC)
    started = monotonic()
    if isinstance(frame, MetricFrame):
        rows = run_metric_checks(
            frame,
            tz=session.report_tz_name if session.report_tz else None,
        )
    elif isinstance(frame, EventFrame):
        rows = run_event_checks(frame)
    elif isinstance(frame, LifecycleFrame):
        rows = run_lifecycle_checks(frame)
    elif isinstance(frame, DeltaFrame) and frame.meta.semantic_kind == "funnel":
        rows = run_funnel_delta_checks(frame)
    elif isinstance(frame, DeltaFrame):
        rows = run_delta_checks(frame)
    elif isinstance(frame, AttributionFrame) and frame.meta.semantic_kind == "funnel_loss_rate":
        rows = run_funnel_attribution_checks(frame)
    elif isinstance(frame, AttributionFrame):
        rows = run_attribution_checks(frame)
    else:
        raise QualityShapeUnsupportedError(
            message="assess_quality received an unregistered family/shape",
            context={
                "frame_kind": frame.meta.kind,
                "semantic_kind": getattr(frame.meta, "semantic_kind", None),
            },
        )
    output = pd.DataFrame(rows)
    checks_run = output["check_id"].astype(str).tolist()
    issues = _quality_issues(frame, output)
    overall = _overall_status(output)
    if isinstance(frame, MetricFrame):
        report_shape = "metric"
    elif isinstance(frame, EventFrame):
        report_shape = f"event_{frame.meta.semantic_kind}"
    elif isinstance(frame, LifecycleFrame):
        report_shape = f"lifecycle_{frame.meta.semantic_kind}"
    elif isinstance(frame, DeltaFrame):
        report_shape = "funnel_delta" if frame.meta.semantic_kind == "funnel" else "delta"
    elif isinstance(frame, AttributionFrame):
        report_shape = (
            "funnel_attribution"
            if isinstance(frame.meta, FunnelAttributionFrameMeta)
            else "attribution"
        )
    else:
        raise AssertionError("quality report shape dispatch is not exhaustive")
    params = {
        "source_ref": frame.ref,
        "report_shape": report_shape,
        "frame_kind": frame.meta.kind,
        "checks_run": checks_run,
    }
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    finished_at = datetime.now(UTC)
    target_coverage_basis = (
        frame.meta.coverage_basis
        if isinstance(frame, EventFrame)
        else frame.meta.coverage_basis
        if isinstance(frame, LifecycleFrame) and isinstance(frame.meta, LifecycleHistoryFrameMeta)
        else None
    )
    target_pattern_fingerprint = (
        frame.meta.pattern.fingerprint
        if isinstance(frame, EventFrame)
        else frame.meta.pattern.fingerprint
        if isinstance(frame, DeltaFrame) and isinstance(frame.meta, FunnelDeltaFrameMeta)
        else None
    )
    meta = QualityReportMeta(
        kind="quality_report",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
        analysis_purpose=analysis_purpose,
        created_at=finished_at,
        row_count=len(output),
        byte_size=0,
        lineage=compose_lineage(
            [frame],
            step=LineageStep(
                intent="assess_quality",
                job_ref=job_ref,
                inputs=[frame.ref],
                params_digest=params_digest(params),
                analysis_purpose=analysis_purpose,
            ),
        ),
        candidate_origins=compose_candidate_origins((frame,)),
        source_refs=[frame.ref],
        report_shape=cast("Any", report_shape),
        target_kind=(
            "metric_frame"
            if isinstance(frame, MetricFrame)
            else "event_frame"
            if isinstance(frame, EventFrame)
            else "lifecycle_frame"
            if isinstance(frame, LifecycleFrame)
            else "delta_frame"
            if isinstance(frame, DeltaFrame)
            else "attribution_frame"
        ),
        target_metric_id=(
            frame.meta.metric_id
            if isinstance(frame, MetricFrame)
            else frame.meta.metric_id
            if isinstance(frame, DeltaFrame) and not isinstance(frame.meta, FunnelDeltaFrameMeta)
            else attribution_metric_id
            if isinstance(frame, AttributionFrame)
            and not isinstance(frame.meta, FunnelAttributionFrameMeta)
            else None
        ),
        target_semantic_model=(
            frame.meta.semantic_model
            if isinstance(frame, MetricFrame)
            else frame.meta.semantic_model
            if isinstance(frame, DeltaFrame) and not isinstance(frame.meta, FunnelDeltaFrameMeta)
            else frame.meta.semantic_model
            if isinstance(frame, AttributionFrame)
            and not isinstance(frame.meta, FunnelAttributionFrameMeta)
            else None
        ),
        target_semantic_kind=cast("Any", frame.meta.semantic_kind),
        target_event_pattern_fingerprint=target_pattern_fingerprint,
        target_state_model_ref=(
            frame.meta.state_model_ref if isinstance(frame, LifecycleFrame) else None
        ),
        target_state_model_fingerprint=(
            frame.meta.state_model_fingerprint if isinstance(frame, LifecycleFrame) else None
        ),
        target_coverage_basis=target_coverage_basis,
        checks_run=checks_run,
        overall_status=overall,
        blocking_issue_count=int((output["severity"] == "blocking").sum()),
        warning_count=int((output["severity"] == "warning").sum()),
        analysis_scope=frame.meta.analysis_scope or compute_analysis_scope(frame),
        issues=tuple(issues),
    )
    result = QualityReport(_df=output, meta=meta)
    result = cast(
        "QualityReport",
        commit_result(
            store=session._evidence_store(),
            frames_dir=session._layout.frames_dir,
            frame=result,
            step_type="assess_quality",
            inputs=CommitInputs(input_refs=[frame.meta.artifact_id or frame.ref]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors.from_frame(frame),
            subject=_quality_subject(frame),
            extractor_family="quality_report",
        ),
    )
    register_frame_artifact(session, result)

    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "assess_quality",
            **job_semantics,
            "analysis_purpose": analysis_purpose,
            "params": params,
            "input_frame_refs": [frame.ref],
            "output_frame_ref": result.meta.artifact_id or result.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
        },
    )
    return result


def _overall_status(output: pd.DataFrame) -> Literal["ok", "warning", "blocking"]:
    severities = set(output["severity"].astype(str))
    if "blocking" in severities:
        return "blocking"
    if "warning" in severities:
        return "warning"
    return "ok"


def _quality_issues(
    frame: MetricFrame | EventFrame | LifecycleFrame | DeltaFrame | AttributionFrame,
    output: pd.DataFrame,
) -> list[ArtifactIssue]:
    issues: list[ArtifactIssue] = []
    scope = frame.meta.analysis_scope or compute_analysis_scope(frame)
    for row in output.to_dict("records"):
        severity = str(row["severity"])
        # MetricFrame quality surfaces a typed DataQualityIssue for the
        # near-constant-empty signal even though its severity is "warning":
        # this is the only non-blocking metric check whose whole point is to
        # diagnose a suspicious-but-not-dead metric (issue #104).
        metric_density_warning = (
            isinstance(frame, MetricFrame)
            and severity == "warning"
            and row["check_kind"] == "value_density"
        )
        if (
            severity != "blocking"
            and not (
                isinstance(frame, (EventFrame, LifecycleFrame, DeltaFrame))
                and severity == "warning"
            )
            and not metric_density_warning
        ):
            continue
        details = json.loads(str(row["details_json"]))
        kind: str | None = None
        observed: str | int | float | bool | None = None
        expectation: str | None = None
        if row["check_kind"] == "duplicate_keys":
            kind = "duplicate_keys_detected"
            observed = int(details["duplicate_count"])
            expectation = "duplicate_count == 0"
        elif row["check_kind"] == "time_coverage":
            kind = "time_coverage_incomplete"
            observed = float(details["coverage_ratio"])
            expectation = "coverage_ratio >= 0.8"
        elif row["check_kind"] == "row_count" and details.get("row_count") == 0:
            kind = "sample_size_low"
            observed = int(details["row_count"])
            expectation = "row_count > 0"
        elif row["check_kind"] == "null_ratio":
            kind = "null_rate_high"
            observed = float(details["null_ratio"])
            expectation = "null_ratio <= 0.5"
        elif row["check_kind"] == "value_density":
            kind = "value_density_low"
            observed = float(details["value_density"])
            expectation = f"value_density >= {_VALUE_DENSITY_WARNING_THRESHOLD}"
        elif row["check_kind"] == "event_row_contract":
            kind = "event_row_contract_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "event_identity":
            kind = "event_identity_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "event_participant":
            kind = "event_participant_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "event_ordering":
            kind = "event_order_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "event_coverage":
            kind = "event_coverage_unknown"
            observed = int(details["unknown_count"])
            expectation = "unknown_count == 0"
        elif row["check_kind"] == "declared_completeness_used":
            kind = "declared_completeness_used"
            observed = int(details["declared_input_count"])
            expectation = "caller declaration disclosed"
        elif row["check_kind"] == "event_censoring":
            kind = "event_censoring_present"
            observed = int(details["coverage_censored_count"])
            expectation = "coverage_censored_count == 0"
        elif row["check_kind"] == "delta_row_contract":
            kind = "delta_row_contract_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "attribution_row_contract":
            kind = "attribution_row_contract_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "attribution_contribution_values":
            kind = "attribution_contribution_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "attribution_reconciliation":
            kind = "attribution_reconciliation_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] in {
            "event_funnel_row_contract",
            "event_funnel_math",
            "event_funnel_axes",
            "event_funnel_reconciliation",
            "event_time_to_event_row_contract",
            "event_time_to_event_identity",
            "event_time_to_event_duration",
            "event_time_to_event_axes",
            "funnel_delta_alignment",
            "funnel_delta_components",
            "funnel_delta_coverage",
            "funnel_delta_row_contract",
            "funnel_attribution_components",
            "funnel_attribution_pools",
            "funnel_attribution_residual",
            "funnel_attribution_reconciliation",
        }:
            kind = "event_row_contract_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "cumulative_pairing":
            kind = "cumulative_alignment_caveat_present"
            observed = int(details["caveat_count"])
            expectation = "matched_null_rows, unpaired rows, and fallback_rows are all zero"
        elif row["check_kind"] in {
            "lifecycle_history_row_contract",
            "lifecycle_history_state",
            "lifecycle_history_intervals",
            "lifecycle_history_counts",
            "lifecycle_distribution_row_contract",
            "lifecycle_distribution_math",
            "lifecycle_distribution_reconciliation",
            "lifecycle_transitions_row_contract",
            "lifecycle_transitions_math",
            "lifecycle_dwell_row_contract",
            "lifecycle_dwell_math",
            "lifecycle_violations_row_contract",
            "lifecycle_violations_math",
        }:
            kind = "lifecycle_row_contract_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "lifecycle_source_history":
            kind = "lifecycle_source_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "lifecycle_trace":
            kind = "lifecycle_trace_invalid"
            observed = int(details["invalid_count"])
            expectation = "invalid_count == 0"
        elif row["check_kind"] == "lifecycle_coverage":
            kind = "lifecycle_coverage_unknown"
            observed = int(details.get("unknown_count", 0))
            expectation = "coverage evidence is valid and authoritative basis is disclosed"
        elif row["check_kind"] == "lifecycle_censoring":
            kind = "lifecycle_censoring_present"
            observed = int(details["coverage_censored_interval_count"]) + int(
                details["coverage_censored_subject_count"]
            )
            expectation = "coverage_censored_count == 0"
        if kind is None or expectation is None:
            continue
        issues.append(
            DataQualityIssue(
                issue_id=make_issue_id(
                    artifact_id=frame.ref,
                    kind=kind,
                    source_refs=(frame.ref, str(row["check_id"])),
                ),
                kind=kind,  # type: ignore[arg-type]
                severity=cast("Literal['warning', 'blocking']", severity),
                source_refs=(frame.ref,),
                check_id=str(row["check_id"]),
                observed_value=observed,
                expectation=expectation,
                evaluated_scope=scope,
                repair=_quality_repair(kind),
            )
        )
    return issues


def _quality_repair(kind: str) -> AnalysisRepair | None:
    """Return a concrete next step for blocking quality issues, if one exists."""
    if kind == "null_rate_high":
        return AnalysisRepair(
            kind="retry",
            action=(
                "Widen the observed window or slice to bring the null ratio "
                "under 0.5 before continuing."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        )
    if kind == "value_density_low":
        return AnalysisRepair(
            kind="inspect",
            action=(
                "Confirm the near-constant-empty values reflect business reality "
                "rather than an authoring or join defect: widen the window, or "
                "review the metric's association/gating definition and the "
                "underlying join conditions."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        )
    return None


def _quality_subject(
    frame: MetricFrame | EventFrame | LifecycleFrame | DeltaFrame | AttributionFrame,
) -> EvidenceSubject:
    if isinstance(frame, EventFrame):
        return event_subject_for_frame(frame)
    if isinstance(frame, LifecycleFrame):
        return lifecycle_subject_for_frame(frame)
    if isinstance(frame, DeltaFrame) and isinstance(frame.meta, FunnelDeltaFrameMeta):
        return EventSubject(
            subject_entity_ref=frame.meta.subject_entity_ref,
            subject_identity_signature=frame.meta.subject_identity,
            analysis_axis="funnel_delta",
        )
    if isinstance(frame, AttributionFrame) and isinstance(frame.meta, FunnelAttributionFrameMeta):
        return EventSubject(
            subject_entity_ref=frame.meta.subject_entity_ref,
            subject_identity_signature=frame.meta.subject_identity,
            analysis_axis="funnel_loss_rate",
        )
    return Subject(
        grain=getattr(frame.meta, "grain", None),
        analysis_axis="quality",
    )
