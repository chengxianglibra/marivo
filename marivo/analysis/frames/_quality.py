"""Construction-time quality evaluation for supported analysis frames."""

from __future__ import annotations

# mypy: disable-error-code=import-untyped
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

import pandas as pd

from marivo.analysis.errors import (
    AnalysisRepair,
    ArtifactQualityError,
    FrameMetaInvalidError,
)
from marivo.analysis.evidence.identity import make_issue_id
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactIssue,
    DataQualityIssue,
    EvidenceScope,
    QualitySummary,
)
from marivo.analysis.frames._meta_defaults import compute_analysis_scope, compute_quality_summary
from marivo.analysis.frames._quality_checks import (
    _VALUE_DENSITY_WARNING_THRESHOLD,
    run_attribution_checks,
    run_delta_checks,
    run_event_checks,
    run_funnel_attribution_checks,
    run_funnel_delta_checks,
    run_lifecycle_checks,
    run_metric_checks,
)
from marivo.analysis.frames.attribution import AttributionFrame
from marivo.analysis.frames.base import BaseFrame
from marivo.analysis.frames.delta import DeltaFrame
from marivo.analysis.frames.event import EventFrame
from marivo.analysis.frames.lifecycle import LifecycleFrame
from marivo.analysis.frames.metric import MetricFrame
from marivo.introspection.live.model import LiveHelpTarget

QUALITY_CHECK_COLUMNS = (
    "check_id",
    "check_kind",
    "status",
    "severity",
    "message",
    "details_json",
    "metric_id",
)

QualityCheckedFrame = MetricFrame | EventFrame | LifecycleFrame | DeltaFrame | AttributionFrame


@dataclass(frozen=True, slots=True)
class FrameQualityEvaluation:
    """One deterministic pre-publication quality evaluation."""

    dataframe: pd.DataFrame
    overall_status: Literal["ok", "warning", "blocking"]
    blocking_issue_count: int
    warning_count: int
    issues: tuple[ArtifactIssue, ...]
    summary: QualitySummary


def _is_supported(frame: BaseFrame) -> bool:
    return isinstance(
        frame,
        (MetricFrame, EventFrame, LifecycleFrame, DeltaFrame, AttributionFrame),
    )


def _validate_metric_quality_bindings(frame: MetricFrame) -> None:
    identities = frame.meta.metric_identities
    if len(identities) <= 1:
        return
    bindings = frame.meta.measure_bindings
    identities_match = len(bindings) == len(identities) and all(
        binding.identity == identity for binding, identity in zip(bindings, identities, strict=True)
    )
    value_columns = tuple(binding.value_column for binding in bindings)
    materialized_columns = frozenset(str(column) for column in frame._df.columns)
    missing_value_columns = tuple(
        column for column in value_columns if column not in materialized_columns
    )
    duplicate_value_columns = tuple(
        dict.fromkeys(column for column in value_columns if value_columns.count(column) > 1)
    )
    if identities_match and not missing_value_columns and not duplicate_value_columns:
        return
    raise FrameMetaInvalidError(
        message="multi-metric construction quality requires authoritative measure bindings",
        expected=(
            f"{len(identities)} ordered, unique, materialized measure binding(s) "
            "matching metric_identities"
        ),
        received=(
            f"measure_bindings={len(bindings)}, metric_identities={len(identities)}, "
            f"identities_match={identities_match}, "
            f"missing_value_columns={list(missing_value_columns)!r}, "
            f"duplicate_value_columns={list(duplicate_value_columns)!r}"
        ),
        location="MetricFrame construction quality bindings",
        repair=AnalysisRepair(
            kind="retry",
            action="Re-run session.observe(metrics=[...]) to rebuild typed measure bindings.",
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
            snippet="frame = session.observe(metrics=[metric_a, metric_b], ...)",
        ),
        context={
            "frame_ref": frame.ref,
            "metric_ids": list(AnalysisScope(metric_identities=identities).metric_ids),
        },
    )


def _overall_status(output: pd.DataFrame) -> Literal["ok", "warning", "blocking"]:
    severities = set(output["severity"].astype(str))
    if "blocking" in severities:
        return "blocking"
    if "warning" in severities:
        return "warning"
    return "ok"


def _quality_repair(kind: str, *, metric_id: str | None = None) -> AnalysisRepair | None:
    target = f" for metric {metric_id!r}" if metric_id is not None else ""
    if kind == "null_rate_high":
        return AnalysisRepair(
            kind="retry",
            action=(
                f"Widen the observed window or slice{target} to reduce the null ratio, "
                "or disclose the missingness in the result."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        )
    if kind == "value_density_low":
        return AnalysisRepair(
            kind="inspect",
            action=(
                f"Confirm the sparse values{target} reflect business reality rather than "
                "a metric-authoring or join defect."
            ),
            help_target=LiveHelpTarget(surface="analysis", canonical_id="observe"),
        )
    return None


def _warning_issue_fields(row: dict[str, object]) -> tuple[str, object, str] | None:
    details = json.loads(str(row["details_json"]))
    kind = str(row["check_kind"])
    if kind == "row_count":
        return (
            "sample_size_low",
            int(details["row_count"]),
            f"row_count >= {int(details['threshold_warning'])}",
        )
    if kind == "null_ratio":
        return (
            "null_rate_high",
            float(details["null_ratio"]),
            f"null_ratio <= {float(details['threshold_warning'])}",
        )
    if kind == "time_coverage":
        return (
            "time_coverage_incomplete",
            float(details["coverage_ratio"]),
            "coverage_ratio == 1.0",
        )
    if kind == "value_density":
        return (
            "value_density_low",
            float(details["value_density"]),
            f"value_density >= {_VALUE_DENSITY_WARNING_THRESHOLD}",
        )
    if kind == "cumulative_pairing":
        return (
            "cumulative_alignment_caveat_present",
            int(details["caveat_count"]),
            "matched_null_rows, unpaired rows, and fallback_rows are all zero",
        )
    if kind in {"event_coverage", "funnel_delta_coverage"}:
        observed = int(details.get("unknown_count", details.get("invalid_count", 0)))
        return "event_coverage_unknown", observed, "coverage evidence is known"
    if kind == "declared_completeness_used":
        return (
            "declared_completeness_used",
            int(details["declared_input_count"]),
            "caller declaration disclosed",
        )
    if kind == "event_censoring":
        return (
            "event_censoring_present",
            int(details["coverage_censored_count"]),
            "coverage_censored_count == 0",
        )
    if kind == "lifecycle_coverage":
        return (
            "lifecycle_coverage_unknown",
            int(details.get("unknown_count", 0)),
            "coverage evidence is known",
        )
    if kind == "lifecycle_censoring":
        observed = int(details["coverage_censored_interval_count"]) + int(
            details["coverage_censored_subject_count"]
        )
        return "lifecycle_censoring_present", observed, "coverage_censored_count == 0"
    return None


def _warning_issues(
    frame: QualityCheckedFrame,
    output: pd.DataFrame,
    *,
    artifact_id: str,
) -> tuple[ArtifactIssue, ...]:
    scope = frame.meta.analysis_scope or compute_analysis_scope(frame)
    metric_scopes: dict[str, AnalysisScope] = {}
    if isinstance(frame, MetricFrame) and isinstance(scope, AnalysisScope):
        metric_scopes = {
            metric_id: scope.model_copy(update={"metric_identities": (identity,)})
            for metric_id, identity in zip(
                frame.metrics,
                frame.meta.metric_identities,
                strict=True,
            )
        }
    issues: list[ArtifactIssue] = []
    for raw_row in output.loc[output["severity"] == "warning"].to_dict("records"):
        row = {str(key): value for key, value in raw_row.items()}
        fields = _warning_issue_fields(row)
        if fields is None:
            continue
        kind, observed, expectation = fields
        raw_metric_id = row.get("metric_id")
        metric_id = raw_metric_id if isinstance(raw_metric_id, str) and raw_metric_id else None
        evaluated_scope: EvidenceScope = scope
        if metric_id is not None and metric_id in metric_scopes:
            evaluated_scope = metric_scopes[metric_id]
        issues.append(
            DataQualityIssue(
                issue_id=make_issue_id(
                    artifact_id=artifact_id,
                    kind=kind,
                    source_refs=(artifact_id, str(row["check_id"])),
                ),
                kind=cast("Any", kind),
                severity="warning",
                source_refs=(artifact_id,),
                check_id=str(row["check_id"]),
                observed_value=cast("Any", observed),
                expectation=expectation,
                evaluated_scope=evaluated_scope,
                repair=_quality_repair(kind, metric_id=metric_id),
            )
        )
    return tuple(issues)


def _summary(frame: QualityCheckedFrame, output: pd.DataFrame) -> QualitySummary:
    base = compute_quality_summary(frame)
    return base.model_copy(
        update={
            "evaluated_check_count": len(output),
            "failed_check_count": int((output["severity"] == "blocking").sum()),
            "warning_check_count": int((output["severity"] == "warning").sum()),
        }
    )


def evaluate_frame_quality(
    frame: BaseFrame,
    *,
    artifact_id: str,
    source_history: LifecycleFrame | None = None,
) -> FrameQualityEvaluation | None:
    """Evaluate fixed checks once before an Artifact is published."""
    if not _is_supported(frame):
        return None
    checked = cast("QualityCheckedFrame", frame)
    if isinstance(checked, MetricFrame):
        _validate_metric_quality_bindings(checked)
        rows = run_metric_checks(checked, tz=checked.meta.report_tz)
    elif isinstance(checked, EventFrame):
        rows = run_event_checks(checked)
    elif isinstance(checked, LifecycleFrame):
        rows = run_lifecycle_checks(checked, source_history=source_history)
    elif isinstance(checked, DeltaFrame) and checked.meta.semantic_kind == "funnel":
        rows = run_funnel_delta_checks(checked)
    elif isinstance(checked, DeltaFrame):
        rows = run_delta_checks(checked)
    elif isinstance(checked, AttributionFrame) and checked.meta.semantic_kind == "funnel_loss_rate":
        rows = run_funnel_attribution_checks(checked)
    else:
        rows = run_attribution_checks(checked)
    output = pd.DataFrame(rows, columns=QUALITY_CHECK_COLUMNS)
    output["metric_id"] = output["metric_id"].replace("", None)
    status = _overall_status(output)
    blocking_issue_count = int((output["severity"] == "blocking").sum())
    evaluation = FrameQualityEvaluation(
        dataframe=output,
        overall_status=status,
        blocking_issue_count=blocking_issue_count,
        warning_count=int((output["severity"] == "warning").sum()),
        issues=_warning_issues(checked, output, artifact_id=artifact_id),
        summary=_summary(checked, output),
    )
    if blocking_issue_count:
        blocking = output.loc[output["severity"] == "blocking"]
        raise ArtifactQualityError(
            message=(
                f"{frame.meta.kind} failed {blocking_issue_count} "
                "construction-time quality check(s)"
            ),
            expected="all blocking construction checks to pass before publication",
            received="; ".join(
                f"{row.check_id}: {row.details_json}" for row in blocking.itertuples(index=False)
            ),
            location=f"{frame.meta.kind} pre-publication quality",
            repair=AnalysisRepair(
                kind="inspect",
                action="Inspect failed_checks and repair the producer input before retrying.",
                help_target=LiveHelpTarget(
                    surface="analysis",
                    canonical_id="artifacts.reading",
                ),
            ),
            context={
                "artifact_id": artifact_id,
                "frame_kind": frame.meta.kind,
                "failed_checks": blocking.to_dict("records"),
            },
        )
    return evaluation


__all__ = [
    "FrameQualityEvaluation",
    "evaluate_frame_quality",
]
