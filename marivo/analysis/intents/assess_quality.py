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
from marivo.analysis.followups import BlockingIssue, FollowupAction
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.metric import MetricFrame
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
from marivo.analysis.session.core import Session, ensure_session_writable
from marivo.analysis.session.persistence import write_job_record


def assess_quality(
    frame: BaseFrame,
    *,
    session: Session | None = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> QualityReport:
    """Run quality checks over a MetricFrame and return a structured report.

    When to use: check data quality (nulls, outliers, coverage) before analysis.

    v1 accepts only MetricFrames. Reports for DeltaFrame / CandidateSet /
    ForecastFrame / AttributionFrame are planned for later releases. The
    returned QualityReport carries per-check rows, blocking issues, and a list
    of recommended follow-up intents.

    Args:
        frame: A MetricFrame to inspect.
        session: Defaults to the currently-attached session.

    Raises:
        QualityShapeUnsupportedError: ``frame`` is not a MetricFrame.
        CrossSessionFrameError: ``frame`` belongs to a different session.

    Example:
        >>> report = session.assess_quality(frame)
        >>> for issue in report.blocking_issues:
        ...     print(issue)
    """
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
    rows = run_metric_checks(frame)
    output = pd.DataFrame(rows)
    checks_run = output["check_id"].astype(str).tolist()
    blocking_issues = _blocking_issues(frame, output)
    followups = _recommended_followups(frame, output)
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
        recommended_followups=followups,
        blocking_issues=blocking_issues,
    )
    result = QualityReport(_df=output, meta=meta)
    result = cast(
        "QualityReport",
        commit_result(
            store=session.evidence_store(),
            frames_dir=session.layout.frames_dir,
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
    write_job_record(
        session.layout,
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
            "semantic_project_root": session.semantic_project.root,
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


def _recommended_followups(frame: MetricFrame, output: pd.DataFrame) -> list[FollowupAction]:
    followups: list[FollowupAction] = []
    for row in output.to_dict("records"):
        if row["severity"] != "blocking":
            continue
        if row["check_kind"] == "null_ratio":
            followups.append(
                FollowupAction(
                    action_id=f"followup_{len(followups) + 1}",
                    kind="adjust_policy",
                    operator="transform",
                    input_refs=[frame.ref],
                    params={"op": "impute_nulls"},
                    preconditions=[],
                    expected_output_family="metric_frame",
                )
            )
        if row["check_kind"] == "time_coverage":
            followups.append(
                FollowupAction(
                    action_id=f"followup_{len(followups) + 1}",
                    kind="adjust_policy",
                    operator="observe",
                    input_refs=[frame.ref],
                    params={"narrow_window": True},
                    preconditions=[],
                    expected_output_family="metric_frame",
                )
            )
    return followups


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
