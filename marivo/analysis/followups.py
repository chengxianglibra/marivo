"""Typed FollowupAction / BlockingIssue / ConfidenceScope for candidate_set."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from marivo.analysis.errors import FrameMetaInvalidError


class FollowupAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    kind: Literal[
        "submit_step",
        "open_projection",
        "adjust_policy",
        "request_semantic_input",
    ]
    operator: str | None = None
    input_refs: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)
    expected_output_family: (
        Literal[
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
        | None
    ) = None
    category: Literal["dag_continuation", "quality_remediation"] | None = None
    source_issue_id: str | None = None


class BlockingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str
    kind: Literal[
        "null_rate_high",
        "sample_size_low",
        "comparability_incompatible",
        "definition_drift_detected",
        "evidence_partial",
        "evidence_store_unavailable",
        "cross_session_window_mismatch",
        "outlier_winsorize_recommended",
        # legacy kinds kept for candidate_set row-level usage
        "quality",
        "sample_size",
        "comparability",
        "definition_drift",
        "missing_semantic_ref",
        "cost",
        "permission",
    ]
    severity: Literal["warning", "blocking"]
    source_refs: list[str] = Field(default_factory=list)
    message: str
    payload: dict[str, Any] | None = None
    remediation_followups: list[FollowupAction] = Field(default_factory=list)


class ConfidenceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_ids: list[str] = Field(default_factory=list)
    segment_keys: dict[str, Any] = Field(default_factory=dict)
    window: dict[str, Any] | None = None
    assumptions: list[str] = Field(default_factory=list)


def _parse_item_followups(raw: str | None) -> list[FollowupAction]:
    """Decode a `recommended_followups_json` cell into typed FollowupAction list.

    Used by select(field="recommended_followups") and validate_shape_columns.
    """

    payload = json.loads(raw) if raw else []
    if not isinstance(payload, list):
        raise FrameMetaInvalidError(
            message="recommended_followups_json must encode a JSON array",
            details={
                "kind": "ItemFollowupShapeInvalid",
                "actual_type": type(payload).__name__,
            },
        )
    return [FollowupAction.model_validate(entry) for entry in payload]
