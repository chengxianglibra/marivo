import pytest
from pydantic import ValidationError

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._temporal import (
    AlignmentEvidenceV1,
    ComparisonTemporalContractV1,
    FrameTemporalContractV1,
)
from marivo.analysis.errors import AlignmentPolicyValidationError
from marivo.analysis.policies import (
    AlignmentKind,
    AlignmentPolicy,
    day_of_week,
    occurrence_progress,
    period_correspondence,
    period_progress,
    window_bucket,
    working_day_progress,
)
from marivo.analysis.refs import ArtifactRef


def test_semantic_refs_stay_on_the_semantic_surface():
    assert mv.AlignmentKind is AlignmentKind
    assert ms.ref.metric("sales.revenue").path == "sales.revenue"
    assert ms.ref.dimension("sales.orders.region").path == "sales.orders.region"
    assert not hasattr(mv, "SemanticRef")
    assert not hasattr(mv, "Ref")
    assert not hasattr(mv, "SemanticKind")
    assert not hasattr(mv, "CatalogObject")
    assert not hasattr(mv, "CalendarRef")


def test_artifact_ref_is_exported_and_preserves_ref():
    assert mv.ArtifactRef is ArtifactRef
    assert ArtifactRef("frame_abc123").ref == "frame_abc123"
    assert str(ArtifactRef("frame_abc123")) == "frame_abc123"


def test_refs_reject_empty_refs():
    with pytest.raises(ValidationError):
        ArtifactRef(" ")


def test_alignment_policy_is_closed_and_direct_constructor_is_rejected():
    with pytest.raises(AlignmentPolicyValidationError) as exc_info:
        AlignmentPolicy(kind="window_bucket")
    assert exc_info.value._context["case"] == "direct_constructor"
    assert "mv.window_bucket()" in str(exc_info.value)


def test_alignment_helpers_have_exact_closed_payloads():
    policies = [
        window_bucket(),
        window_bucket(mode="calendar_bucket", strict_lengths=True),
        day_of_week(),
        period_progress(unmatched="drop"),
        period_correspondence(correspondence="prior_year_shifted"),
        occurrence_progress(anchor="end", unmatched="drop"),
        working_day_progress(schedule=ms.ref.work_schedule("sales.cn_schedule"), unmatched="drop"),
    ]
    assert [policy.kind for policy in policies] == [
        "window_bucket",
        "window_bucket",
        "day_of_week",
        "period_progress",
        "period_correspondence",
        "occurrence_progress",
        "working_day_progress",
    ]
    assert policies[0].model_dump(mode="json") == {
        "kind": "window_bucket",
        "mode": "ordinal_bucket",
        "strict_lengths": False,
    }
    assert policies[2].model_dump(mode="json")["within"]["unit"] == "month"
    assert policies[3].model_dump(mode="json")["unmatched"] == "drop"
    assert policies[4].model_dump(mode="json")["correspondence"] == "prior_year_shifted"
    assert policies[5].model_dump(mode="json") == {
        "kind": "occurrence_progress",
        "anchor": "end",
        "unmatched": "drop",
    }
    assert policies[6].model_dump(mode="json") == {
        "kind": "working_day_progress",
        "schedule_ref": "sales.cn_schedule",
        "unmatched": "drop",
    }


@pytest.mark.parametrize(
    "factory, kwargs",
    [
        (window_bucket, {"mode": "bad"}),
        (day_of_week, {"unmatched": "bad"}),
        (period_progress, {"unmatched": "bad"}),
        (period_correspondence, {"correspondence": "", "unmatched": "fail"}),
        (occurrence_progress, {"anchor": "middle"}),
        (occurrence_progress, {"anchor": []}),
        (occurrence_progress, {"unmatched": []}),
        (working_day_progress, {"schedule": {}}),
        (working_day_progress, {"schedule": ms.ref.metric("sales.revenue")}),
    ],
)
def test_alignment_helpers_reject_invalid_arguments(factory, kwargs):
    with pytest.raises(AlignmentPolicyValidationError):
        factory(**kwargs)


def test_comparison_temporal_contract_rejects_unknown_policy_and_inconsistent_evidence():
    current = FrameTemporalContractV1(display_timezone="UTC")
    baseline = FrameTemporalContractV1(display_timezone="UTC")
    valid_evidence = AlignmentEvidenceV1(
        candidate_current_points=1,
        candidate_baseline_points=1,
        paired_points=1,
        current_only_points=0,
        baseline_only_points=0,
        unmatched_points=0,
        dropped_points=0,
        execution_path="local",
    )
    with pytest.raises(ValidationError):
        ComparisonTemporalContractV1(
            current=current,
            baseline=baseline,
            alignment_policy={"kind": "unsupported"},
            alignment_evidence=valid_evidence,
        )
    with pytest.raises(ValidationError):
        AlignmentEvidenceV1(
            candidate_current_points=1,
            candidate_baseline_points=1,
            paired_points=2,
            current_only_points=0,
            baseline_only_points=0,
            unmatched_points=0,
            dropped_points=0,
            execution_path="local",
        )


def test_lag_policy_is_not_public_policy():
    import marivo.analysis.policies as policies

    assert not hasattr(policies, "LagPolicy")


def test_sampling_policy_defaults_and_forbids_extra():
    from marivo.analysis import SamplingPolicy

    policy = SamplingPolicy()
    assert policy.unit == "bucket"
    assert policy.method == "paired_numeric_summary"
    assert policy.pairing == "window_bucket"
    assert policy.null_handling == "drop_pair"
    assert policy.min_n == 3

    with pytest.raises(ValidationError):
        SamplingPolicy(extra_field=True)  # type: ignore[call-arg]
