"""Assess frame quality and return a QualityReport."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, cast

import pandas as pd

from marivo.analysis.errors import QualityShapeUnsupportedError
from marivo.analysis.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis.evidence.types import Subject, TriggeredByFollowup
from marivo.analysis.followups import BlockingIssue
from marivo.analysis.frames._meta_defaults import compute_quality_summary
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    resolve_session,
)
from marivo.analysis.intents._quality_checks import run_metric_checks
from marivo.analysis.lineage import LineageStep
from marivo.analysis.session._runtime import (
    persist_frame,
    persist_job_record,
    register_frame_artifact,
)
from marivo.analysis.session.core import Session, ensure_session_writable


def assess_quality(
    frame: BaseFrame,
    *,
    session: Session | None = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> QualityReport:
    session = resolve_session(session)
    ensure_session_writable(session)
    if not isinstance(frame, MetricFrame):
        raise QualityShapeUnsupportedError(
            message="assess_quality v1 only supports MetricFrame inputs",
            details={"frame_kind": frame.meta.kind},
        )
    ensure_frame_in_session(frame, session=session, label="assess_quality frame")

    started_at = datetime.now(UTC)
    started = monotonic()
    rows = run_metric_checks(frame, tz=session.report_tz_name if session.report_tz else None)
    output = pd.DataFrame(rows)
    checks_run = output["check_id"].astype(str).tolist()
    blocking_issues = _blocking_issues(frame, output)
    overall = _overall_status(output)
    params = {
        "source_ref": frame.ref,
        "report_shape": "metric",
        "frame_kind": frame.meta.kind,
        "checks_run": checks_run,
    }
    frame_ref = gen_ref("frame")
    job_ref = gen_ref("job")
    finished_at = datetime.now(UTC)
    meta = QualityReportMeta(
        kind="quality_report",
        ref=frame_ref,
        session_id=session.id,
        project_root=str(session.project_root),
        produced_by_job=job_ref,
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
            ),
        ),
        source_refs=[frame.ref],
        report_shape="metric",
        target_kind="metric_frame",
        target_metric_id=frame.meta.metric_id,
        target_semantic_model=frame.meta.semantic_model,
        target_semantic_kind=frame.meta.semantic_kind,
        checks_run=checks_run,
        overall_status=overall,
        blocking_issue_count=int((output["severity"] == "blocking").sum()),
        warning_count=int((output["severity"] == "warning").sum()),
        blocking_issues=blocking_issues,
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
            semantic_anchors=CommitSemanticAnchors(values={"metric_id": frame.meta.metric_id}),
            subject=Subject(
                metric=frame.meta.metric_id,
                grain=getattr(frame.meta, "grain", None),
                analysis_axis="scalar",
            ),
            extractor_family="quality_report",
            triggered_by_followup=_triggered_by,
        ),
    )
    register_frame_artifact(session, result)

    # Attach a lightweight quality summary to the source frame and persist it;
    # the full QualityReport is a separate artifact (cheap summary vs explicit report).
    frame.meta = frame.meta.model_copy(update={"quality_summary": compute_quality_summary(frame)})
    frame.meta = cast("MetricFrameMeta", persist_frame(session, frame))

    persist_job_record(
        session,
        {
            "id": job_ref,
            "session_id": session.id,
            "intent": "assess_quality",
            "params": params,
            "input_frame_refs": [frame.ref],
            "output_frame_ref": result.meta.artifact_id or result.ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": str(session.catalog._project.semantic_root),
            "semantic_model": frame.meta.semantic_model,
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


def _blocking_issues(frame: MetricFrame, output: pd.DataFrame) -> list[BlockingIssue]:
    issues: list[BlockingIssue] = []
    for row in output.to_dict("records"):
        if row["severity"] != "blocking":
            continue
        if row["check_kind"] == "duplicate_keys":
            issues.append(
                BlockingIssue(
                    issue_id=f"issue_{len(issues) + 1}",
                    kind="quality",
                    severity="blocking",
                    source_refs=[frame.ref],
                    message="duplicate key tuples in metric frame",
                    remediation_followups=[],
                )
            )
        if row["check_kind"] == "row_count":
            details = json.loads(str(row["details_json"]))
            if details.get("row_count") == 0:
                issues.append(
                    BlockingIssue(
                        issue_id=f"issue_{len(issues) + 1}",
                        kind="sample_size",
                        severity="blocking",
                        source_refs=[frame.ref],
                        message="metric frame has zero rows",
                        remediation_followups=[],
                    )
                )
    return issues
