"""C1 dag_continuation + C2 quality_remediation followup generator (slice-1 whitelist)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.analysis_py.errors import FollowupGenerationRuleViolatedError
from marivo.analysis_py.evidence.identity import make_action_id
from marivo.analysis_py.followups import BlockingIssue, FollowupAction

_OutputFamily = Literal[
    "metric_frame",
    "delta_frame",
    "attribution_frame",
    "candidate_set",
    "association_result",
    "hypothesis_test_result",
    "forecast_frame",
    "forecast_evaluation_result",
    "quality_report",
    "diagnosis_result",
]


@dataclass(frozen=True)
class GenerationContext:
    source_artifact_id: str
    source_family: str
    source_semantic_kind: str
    blocking_issues: list[BlockingIssue] = field(default_factory=list)


_C1_METRIC_BY_KIND: dict[str, list[tuple[str, dict[str, Any], _OutputFamily]]] = {
    "scalar": [("assess_quality", {}, "quality_report")],
    "time_series": [
        ("assess_quality", {}, "quality_report"),
        ("discover", {"objective": "point_anomalies"}, "candidate_set"),
        ("discover", {"objective": "interesting_windows"}, "candidate_set"),
        ("forecast", {"horizon": "default"}, "forecast_frame"),
    ],
    "segmented": [
        ("assess_quality", {}, "quality_report"),
        ("discover", {"objective": "interesting_slices"}, "candidate_set"),
        ("discover", {"objective": "cross_sectional_outliers"}, "candidate_set"),
    ],
    "panel": [
        ("assess_quality", {}, "quality_report"),
        ("discover", {"objective": "point_anomalies"}, "candidate_set"),
        ("discover", {"objective": "cross_sectional_outliers"}, "candidate_set"),
        ("discover", {"objective": "interesting_windows"}, "candidate_set"),
        ("forecast", {"horizon": "default"}, "forecast_frame"),
    ],
}

_C1_DELTA_COMMON: list[tuple[str, dict[str, Any], _OutputFamily]] = [
    ("assess_quality", {}, "quality_report"),
    ("discover", {"objective": "driver_axes"}, "candidate_set"),
    ("discover", {"objective": "interesting_slices"}, "candidate_set"),
]
_C1_DELTA_TIMEY: list[tuple[str, dict[str, Any], _OutputFamily]] = _C1_DELTA_COMMON + [
    ("discover", {"objective": "period_shifts"}, "candidate_set"),
]


def _c1_for_source(ctx: GenerationContext) -> list[tuple[str, dict[str, Any], _OutputFamily]]:
    if ctx.source_family == "metric_frame":
        rules = _C1_METRIC_BY_KIND.get(ctx.source_semantic_kind)
        if rules is None:
            raise FollowupGenerationRuleViolatedError(
                message=f"no C1 rule for metric_frame[{ctx.source_semantic_kind}]",
                details={"family": ctx.source_family, "semantic_kind": ctx.source_semantic_kind},
            )
        return rules
    if ctx.source_family == "delta_frame":
        if ctx.source_semantic_kind in ("time_series", "panel"):
            return _C1_DELTA_TIMEY
        return _C1_DELTA_COMMON
    raise FollowupGenerationRuleViolatedError(
        message=f"family {ctx.source_family!r} not in slice-1 C1 whitelist",
        details={"family": ctx.source_family},
    )


def _c2_for_issue(
    issue: BlockingIssue, source_artifact_id: str
) -> list[FollowupAction]:
    if issue.kind == "null_rate_high":
        return [
            FollowupAction(
                action_id=make_action_id(
                    source_artifact_id=source_artifact_id,
                    category="quality_remediation",
                    operator="transform",
                    input_refs=[source_artifact_id],
                    params={"op": "impute_nulls", "issue_id": issue.issue_id},
                ),
                kind="submit_step",
                operator="transform",
                input_refs=[source_artifact_id],
                params={"op": "impute_nulls"},
                category="quality_remediation",
                source_issue_id=issue.issue_id,
                expected_output_family="metric_frame",
            )
        ]
    if issue.kind == "comparability_incompatible":
        return [
            FollowupAction(
                action_id=make_action_id(
                    source_artifact_id=source_artifact_id,
                    category="quality_remediation",
                    operator="compare",
                    input_refs=[source_artifact_id],
                    params={
                        "alignment": "calendar_bucket",
                        "issue_id": issue.issue_id,
                    },
                ),
                kind="submit_step",
                operator="compare",
                input_refs=[source_artifact_id],
                params={"alignment": "calendar_bucket"},
                category="quality_remediation",
                source_issue_id=issue.issue_id,
                expected_output_family="delta_frame",
            )
        ]
    if issue.kind == "evidence_partial":
        return [
            FollowupAction(
                action_id=make_action_id(
                    source_artifact_id=source_artifact_id,
                    category="quality_remediation",
                    operator=None,
                    input_refs=[source_artifact_id],
                    params={"action": "retry_evidence_pipeline", "issue_id": issue.issue_id},
                ),
                kind="adjust_policy",
                operator=None,
                input_refs=[source_artifact_id],
                params={"action": "retry_evidence_pipeline"},
                category="quality_remediation",
                source_issue_id=issue.issue_id,
            )
        ]
    return []


def generate_followups(ctx: GenerationContext) -> list[FollowupAction]:
    """Generate C1 dag_continuation and C2 quality_remediation followup actions."""
    actions: list[FollowupAction] = []
    for operator, params, expected_family in _c1_for_source(ctx):
        action_id = make_action_id(
            source_artifact_id=ctx.source_artifact_id,
            category="dag_continuation",
            operator=operator,
            input_refs=[ctx.source_artifact_id],
            params=params,
        )
        actions.append(
            FollowupAction(
                action_id=action_id,
                kind="submit_step",
                operator=operator,
                input_refs=[ctx.source_artifact_id],
                params=params,
                category="dag_continuation",
                source_issue_id=None,
                expected_output_family=expected_family,
            )
        )
    for issue in ctx.blocking_issues:
        actions.extend(_c2_for_issue(issue, ctx.source_artifact_id))
    # Conformance guard
    for action in actions:
        if action.category is None:
            raise FollowupGenerationRuleViolatedError(
                message="generated FollowupAction without category",
                details={"action_id": action.action_id, "operator": action.operator},
            )
        if action.category == "quality_remediation" and action.source_issue_id is None:
            raise FollowupGenerationRuleViolatedError(
                message="quality_remediation followup must have source_issue_id",
                details={"action_id": action.action_id},
            )
        if action.category == "dag_continuation" and action.source_issue_id is not None:
            raise FollowupGenerationRuleViolatedError(
                message="dag_continuation followup must not have source_issue_id",
                details={"action_id": action.action_id},
            )
    return actions


__all__ = ["GenerationContext", "generate_followups"]
