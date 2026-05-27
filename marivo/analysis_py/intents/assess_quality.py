"""Assess frame quality and return a QualityReport."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, cast

import pandas as pd

from marivo.analysis_py.errors import QualityShapeUnsupportedError
from marivo.analysis_py.evidence.pipeline import (
    CommitInputs,
    CommitParams,
    CommitSemanticAnchors,
    commit_result,
)
from marivo.analysis_py.evidence.types import Subject, TriggeredByFollowup
from marivo.analysis_py.followups import BlockingIssue, FollowupAction
from marivo.analysis_py.frames.base import BaseFrame
from marivo.analysis_py.frames.metric import MetricFrame
from marivo.analysis_py.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis_py.intents._derived import (
    compose_lineage,
    ensure_frame_in_session,
    gen_ref,
    params_digest,
    resolve_session,
)
from marivo.analysis_py.intents._quality_checks import run_metric_checks
from marivo.analysis_py.lineage import LineageStep
from marivo.analysis_py.session.core import Session, ensure_session_writable
from marivo.analysis_py.session.persistence import write_job_record


def assess_quality(
    target: BaseFrame,
    *,
    session: Session | None = None,
    _triggered_by: TriggeredByFollowup | None = None,
) -> QualityReport:
    session = resolve_session(session)
    ensure_session_writable(session)
    if getattr(getattr(target, "meta", None), "kind", None) != "metric_frame":
        raise QualityShapeUnsupportedError(
            message="assess_quality v1 only supports MetricFrame targets",
            details={"target_kind": target.meta.kind},
        )
    target = cast("MetricFrame", target)
    ensure_frame_in_session(target, session=session, label="assess_quality target")

    started_at = datetime.now(UTC)
    started = monotonic()
    rows = run_metric_checks(target)
    output = pd.DataFrame(rows)
    checks_run = output["check_id"].astype(str).tolist()
    blocking_issues = _blocking_issues(target, output)
    followups = _recommended_followups(target, output)
    overall = _overall_status(output)
    params = {
        "source_ref": target.ref,
        "report_shape": "metric",
        "target_kind": target.meta.kind,
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
            [target],
            step=LineageStep(
                intent="assess_quality",
                job_ref=job_ref,
                inputs=[target.ref],
                params_digest=params_digest(params),
            ),
        ),
        source_refs=[target.ref],
        report_shape="metric",
        target_kind="metric_frame",
        target_metric_id=target.meta.metric_id,
        target_semantic_model=target.meta.semantic_model,
        target_semantic_kind=target.meta.semantic_kind,
        checks_run=checks_run,
        overall_status=overall,
        blocking_issue_count=int((output["severity"] == "blocking").sum()),
        warning_count=int((output["severity"] == "warning").sum()),
        recommended_followups=followups,
        blocking_issues=blocking_issues,
    )
    frame = QualityReport(_df=output, meta=meta)
    frame = cast(
        "QualityReport",
        commit_result(
            store=session.evidence_store(),
            frames_dir=session.layout.frames_dir,
            frame=frame,
            step_type="assess_quality",
            inputs=CommitInputs(input_refs=[target.meta.artifact_id or target.ref]),
            params=CommitParams(values=params),
            semantic_anchors=CommitSemanticAnchors(values={"metric_id": target.meta.metric_id}),
            subject=Subject(
                metric=target.meta.metric_id,
                grain=getattr(target.meta, "grain", None),
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
            "input_frame_refs": [target.ref],
            "output_frame_ref": frame.meta.artifact_id or frame_ref,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": int((monotonic() - started) * 1000),
            "status": "succeeded",
            "error": None,
            "semantic_project_root": session.semantic_project.root,
            "semantic_model": target.meta.semantic_model,
        },
    )
    return frame


def _overall_status(output: pd.DataFrame) -> Literal["ok", "warning", "blocking"]:
    severities = set(output["severity"].astype(str))
    if "blocking" in severities:
        return "blocking"
    if "warning" in severities:
        return "warning"
    return "ok"


def _recommended_followups(target: MetricFrame, output: pd.DataFrame) -> list[FollowupAction]:
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
                    input_refs=[target.ref],
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
                    input_refs=[target.ref],
                    params={"narrow_window": True},
                    preconditions=[],
                    expected_output_family="metric_frame",
                )
            )
    return followups


def _blocking_issues(target: MetricFrame, output: pd.DataFrame) -> list[BlockingIssue]:
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
                    source_refs=[target.ref],
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
                        source_refs=[target.ref],
                        message="metric frame has zero rows",
                        remediation_followups=[],
                    )
                )
    return issues
