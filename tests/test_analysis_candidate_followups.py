"""Typed FollowupAction / BlockingIssue / ConfidenceScope models for candidate_set."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

import marivo.analysis as mv
from marivo.analysis.errors import FrameMetaInvalidError
from marivo.analysis.followups import (
    BlockingIssue,
    ConfidenceScope,
    FollowupAction,
    _parse_item_followups,
)
from marivo.analysis.frames.candidate import CandidateSetMeta
from marivo.analysis.lineage import Lineage


def test_followup_action_minimal_roundtrip() -> None:
    action = FollowupAction(action_id="a1", kind="submit_step")
    payload = action.model_dump(mode="json")
    restored = FollowupAction.model_validate(payload)
    assert restored == action
    assert restored.input_refs == []
    assert restored.params == {}
    assert restored.preconditions == []
    assert restored.expected_output_family is None


def test_followup_action_full_roundtrip() -> None:
    action = FollowupAction(
        action_id="a1",
        kind="submit_step",
        operator="decompose",
        input_refs=["frame_abc"],
        params={"axis": "country"},
        preconditions=["candidate_set.row_count > 0"],
        expected_output_family="attribution_frame",
    )
    payload = action.model_dump(mode="json")
    restored = FollowupAction.model_validate(payload)
    assert restored == action


def test_followup_action_category_required() -> None:
    action = FollowupAction(
        action_id="a1",
        kind="submit_step",
        category="dag_continuation",
    )
    assert action.category == "dag_continuation"
    assert action.source_issue_id is None


def test_followup_action_quality_remediation_with_source_issue_id() -> None:
    action = FollowupAction(
        action_id="a1",
        kind="submit_step",
        category="quality_remediation",
        source_issue_id="issue_123",
    )
    assert action.source_issue_id == "issue_123"


def test_followup_action_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        FollowupAction(action_id="a1", kind="submit_step", unknown_field=1)  # type: ignore[call-arg]


def test_followup_action_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        FollowupAction(action_id="a1", kind="not_a_kind")  # type: ignore[arg-type]


def test_followup_action_rejects_unknown_expected_output_family() -> None:
    with pytest.raises(ValidationError):
        FollowupAction(
            action_id="a1",
            kind="submit_step",
            expected_output_family="not_a_family",  # type: ignore[arg-type]
        )


def test_blocking_issue_nested_typed_followups() -> None:
    issue = BlockingIssue(
        issue_id="i1",
        kind="quality",
        severity="warning",
        message="too few rows",
        remediation_followups=[
            FollowupAction(action_id="a1", kind="adjust_policy"),
        ],
    )
    payload = issue.model_dump(mode="json")
    restored = BlockingIssue.model_validate(payload)
    assert restored == issue
    assert isinstance(restored.remediation_followups[0], FollowupAction)


def test_blocking_issue_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        BlockingIssue(
            issue_id="i1",
            kind="quality",
            severity="critical",  # type: ignore[arg-type]
            message="x",
        )


def test_confidence_scope_minimal() -> None:
    scope = ConfidenceScope()
    assert scope.metric_ids == []
    assert scope.segment_keys == {}
    assert scope.window is None
    assert scope.assumptions == []


def test_confidence_scope_roundtrip() -> None:
    scope = ConfidenceScope(
        metric_ids=["sales.revenue"],
        segment_keys={"country": "US"},
        window={"kind": "absolute", "start": "2026-01-01", "end": "2026-01-31"},
        assumptions=["fiscal_calendar=US"],
    )
    payload = scope.model_dump(mode="json")
    restored = ConfidenceScope.model_validate(payload)
    assert restored == scope


def test_typed_models_are_frozen() -> None:
    action = FollowupAction(action_id="a1", kind="submit_step")
    with pytest.raises(ValidationError):
        action.action_id = "a2"  # type: ignore[misc]


def test_parse_item_followups_empty_string_returns_empty_list() -> None:
    assert _parse_item_followups("") == []


def test_parse_item_followups_none_returns_empty_list() -> None:
    assert _parse_item_followups(None) == []


def test_parse_item_followups_empty_array_returns_empty_list() -> None:
    assert _parse_item_followups("[]") == []


def test_parse_item_followups_typed_round_trip() -> None:
    raw = json.dumps(
        [
            {
                "action_id": "a1",
                "kind": "submit_step",
                "operator": "decompose",
                "input_refs": ["frame_abc"],
                "params": {"axis": "country"},
                "preconditions": [],
                "expected_output_family": "attribution_frame",
            },
            {"action_id": "a2", "kind": "open_projection"},
        ]
    )
    actions = _parse_item_followups(raw)
    assert len(actions) == 2
    assert actions[0].operator == "decompose"
    assert actions[0].expected_output_family == "attribution_frame"
    assert actions[1].kind == "open_projection"
    assert actions[1].operator is None


def test_parse_item_followups_rejects_object_payload() -> None:
    with pytest.raises(FrameMetaInvalidError) as exc:
        _parse_item_followups('{"action_id": "a1", "kind": "submit_step"}')
    assert exc.value._context.get("kind") == "ItemFollowupShapeInvalid"
    assert exc.value._context.get("actual_type") == "dict"


def test_parse_item_followups_rejects_invalid_followup_entry() -> None:
    raw = json.dumps([{"action_id": "a1"}])  # missing required `kind`
    with pytest.raises(Exception):
        _parse_item_followups(raw)


def test_parse_item_followups_rejects_unknown_kind() -> None:
    raw = json.dumps([{"action_id": "a1", "kind": "bogus"}])
    with pytest.raises(Exception):
        _parse_item_followups(raw)


def _meta_minimal(**overrides: object) -> CandidateSetMeta:
    base: dict[str, object] = {
        "ref": "frame_test",
        "session_id": "sess_1",
        "project_root": "/tmp/proj",
        "produced_by_job": "job_1",
        "created_at": datetime.now(UTC),
        "row_count": 0,
        "byte_size": 0,
        "lineage": Lineage(),
        "shape": "point_anomaly",
        "objective": "point_anomalies",
        "strategy": "zscore",
        "source_ref": "frame_src",
        "source_kind": "metric_frame",
        "metric_ids": ["sales.revenue"],
        "semantic_kind": "time_series",
        "semantic_model": "sales",
        "source_refs": ["frame_src"],
        "params": {"objective": "point_anomalies"},
    }
    base.update(overrides)
    return CandidateSetMeta(**base)  # type: ignore[arg-type]


def test_candidate_set_meta_defaults_typed_followup_fields() -> None:
    meta = _meta_minimal()
    assert meta.affordances == []
    assert meta.blocking_issues == []
    assert meta.confidence_scope is None


def test_candidate_set_meta_round_trip_with_typed_followups() -> None:
    meta = _meta_minimal(
        affordances=[
            mv.ArtifactAffordance(
                capability_id="assess_quality",
                public_entrypoint="session.assess_quality(...)",
                help_target="assess_quality",
                required_inputs=["metric_frame"],
            ),
        ],
        blocking_issues=[
            BlockingIssue(issue_id="i1", kind="quality", severity="warning", message="x"),
        ],
        confidence_scope=ConfidenceScope(metric_ids=["sales.revenue"]),
    )
    payload = meta.model_dump(mode="json")
    restored = CandidateSetMeta.model_validate(payload)
    assert isinstance(restored.affordances[0], mv.ArtifactAffordance)
    assert isinstance(restored.blocking_issues[0], BlockingIssue)
    assert isinstance(restored.confidence_scope, ConfidenceScope)
    assert restored == meta


def test_candidate_set_meta_rejects_unknown_shape() -> None:
    with pytest.raises(ValidationError):
        _meta_minimal(shape="unknown_shape")  # type: ignore[arg-type]


def test_candidate_set_meta_rejects_unknown_source_kind() -> None:
    with pytest.raises(ValidationError):
        _meta_minimal(source_kind="not_a_frame")  # type: ignore[arg-type]


def test_candidate_set_meta_accepts_all_six_shapes() -> None:
    pairings = [
        ("point_anomaly", "point_anomalies", "zscore"),
        ("period_shift", "period_shifts", "delta_window_zscore"),
        ("driver_axis", "driver_axes", "concentration"),
        ("slice", "interesting_slices", "slice_zscore"),
        ("window", "interesting_windows", "global_zscore_runs"),
        ("cross_sectional_outlier", "cross_sectional_outliers", "mad"),
    ]
    for shape, objective, strategy in pairings:
        meta = _meta_minimal(shape=shape, objective=objective, strategy=strategy)
        assert meta.shape == shape
        assert meta.objective == objective
        assert meta.strategy == strategy


def test_candidate_shape_importable_from_submodule() -> None:
    from marivo.analysis.frames.candidate import CandidateShape

    assert CandidateShape is not None
