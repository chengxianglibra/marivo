"""Base frame protocol after the typed-digest cutover."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from marivo.analysis.errors import FrameMutationError
from marivo.analysis.evidence.types import (
    ArtifactDigest,
    ChangeFact,
    DerivationRule,
    EvidenceAvailabilityIssue,
    InferenceBoundary,
    OmissionSummary,
    OperatorSemantics,
    RawFallback,
)
from marivo.analysis.frames._content_hash import stable_meta_payload
from marivo.analysis.frames.base import (
    ArtifactAffordance,
    ArtifactContract,
    ArtifactInputRequirement,
    BaseFrame,
    BaseFrameMeta,
)
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.lineage import Lineage
from tests.shared_fixtures import (
    make_test_analysis_scope,
    make_test_metric_meta_contract,
    make_test_subject,
)


def _meta(**overrides) -> BaseFrameMeta:
    values = {
        "kind": "metric_frame",
        "ref": "frame_abc",
        "session_id": "sess_1",
        "project_root": "/tmp/project",
        "produced_by_job": None,
        "created_at": datetime(2026, 7, 18, tzinfo=UTC),
        "row_count": 2,
        "byte_size": 128,
        "lineage": Lineage(),
    }
    values.update(overrides)
    return BaseFrameMeta(**values)


def _digest(ref: str = "frame_abc") -> ArtifactDigest:
    return ArtifactDigest(
        artifact_ref=ref,
        operator=OperatorSemantics(
            operator="observe",
            operator_version="v1",
            artifact_family="metric_frame",
            semantic_shape="scalar",
        ),
        subject=make_test_subject(metric_id="sales.revenue", analysis_axis="scalar"),
        scope=make_test_analysis_scope("sales.revenue"),
        omissions=OmissionSummary(
            retained_items=0,
            omitted_items=0,
            bounded=True,
        ),
        fallback=RawFallback(
            artifact_ref=ref,
            findings_available=True,
            rows_available=True,
        ),
        fingerprint="sha256:test",
    )


def _metric_frame() -> MetricFrame:
    return MetricFrame(
        _df=pd.DataFrame({"value": [1.0]}),
        meta=MetricFrameMeta(
            **make_test_metric_meta_contract("sales.revenue"),
            ref="metric_1",
            session_id="sess_1",
            project_root="/tmp/project",
            produced_by_job=None,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            row_count=1,
            byte_size=8,
            lineage=Lineage(),
            metric_id="sales.revenue",
            axes={},
            measure={"field": "value"},
            window=None,
            where={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )


def test_meta_defaults_are_truthful_and_old_names_are_absent():
    meta = _meta()
    frame = BaseFrame(_df=pd.DataFrame({"value": [1.0, 2.0]}), meta=meta)

    assert meta.evidence_status == "unavailable"
    assert meta.evidence_digest is None
    assert meta.analysis_scope is None
    assert meta.issues == ()
    assert frame.evidence_status == "unavailable"
    assert frame.evidence_digest is None
    for removed in ("confidence_scope", "evidence_summary", "blocking_issues"):
        assert not hasattr(meta, removed)
        assert not hasattr(frame, removed)


def test_frame_is_immutable_and_to_pandas_returns_a_copy():
    frame = BaseFrame(_df=pd.DataFrame({"value": [1.0, 2.0]}), meta=_meta())
    exported = frame.to_pandas()
    exported.loc[0, "value"] = 99.0
    assert frame.to_pandas().iloc[0, 0] == 1.0
    selected = frame["value"]
    selected.iloc[0] = 77.0
    assert frame["value"].iloc[0] == 1.0
    with pytest.raises(FrameMutationError):
        frame["other"] = 1


def test_frame_column_read_copies_only_the_selected_result(monkeypatch):
    frame = BaseFrame(
        _df=pd.DataFrame({"selected": [1.0], "unselected": [2.0]}),
        meta=_meta(row_count=1),
    )

    def reject_full_copy():
        raise AssertionError("column reads must not copy the full dataframe")

    monkeypatch.setattr(frame, "_dataframe_copy", reject_full_copy)

    selected = frame["selected"]
    selected.iloc[0] = 99.0
    assert frame["selected"].iloc[0] == 1.0


def test_contract_is_the_only_structured_issue_path():
    issue = EvidenceAvailabilityIssue(
        issue_id="iss_1",
        kind="evidence_digest_unavailable",
        severity="blocking",
        source_refs=("frame_abc",),
        failed_stage="digest",
        findings_available=True,
        fallback=RawFallback(
            artifact_ref="frame_abc",
            findings_available=True,
            rows_available=True,
            recommended_when=("partial_evidence",),
        ),
        stable_error_category="DigestBuildError",
    )
    frame = BaseFrame(
        _df=pd.DataFrame({"value": [1.0]}),
        meta=_meta(evidence_status="partial", issues=(issue,)),
    )

    assert frame.contract().issues == (issue,)
    assert not hasattr(frame, "issues")
    rendered = frame.render()
    assert "evidence_digest_unavailable" in rendered
    assert "stage=digest" in rendered


def test_affordance_preserves_compare_parameter_roles_without_call_planner():
    affordance = next(
        item for item in _metric_frame().contract().affordances if item.capability_id == "compare"
    )
    assert [item.parameter for item in affordance.input_requirements] == [
        "a",
        "alignment",
        "b",
        "sampling",
    ]
    assert {
        item.parameter: item.bindable_from_current_artifact
        for item in affordance.input_requirements
    } == {"a": True, "alignment": False, "b": True, "sampling": False}
    assert not hasattr(affordance, "required_inputs")
    assert not hasattr(affordance, "param_template")


def test_affordance_and_contract_models_are_closed_and_immutable():
    requirement = ArtifactInputRequirement(
        parameter="source",
        accepted_families=("MetricFrame",),
        bindable_from_current_artifact=True,
    )
    affordance = ArtifactAffordance(
        capability_id="assess_quality",
        public_entrypoint="session.assess_quality(...) ",
        help_target="assess_quality",
        input_requirements=(requirement,),
        expected_output_family="QualityReport",
    )
    contract = ArtifactContract(
        kind="metric_frame",
        ref="frame_abc",
        is_canonical=True,
        artifact_schema=_metric_frame().contract().artifact_schema,
        affordances=(affordance,),
    )
    with pytest.raises(ValidationError):
        ArtifactInputRequirement(
            parameter="source",
            accepted_families=("MetricFrame",),
            bindable_from_current_artifact=True,
            unexpected=True,  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        contract.issues = ()  # type: ignore[misc]


def test_digest_is_session_local_for_content_identity_and_renders_before_preview():
    without_digest = _meta(evidence_digest=None)
    with_digest = _meta(evidence_status="complete", evidence_digest=_digest())
    assert stable_meta_payload(with_digest) == stable_meta_payload(without_digest)

    frame = BaseFrame(
        _df=pd.DataFrame({"value": range(20)}),
        meta=_meta(
            evidence_status="complete",
            evidence_digest=_digest(),
            row_count=20,
        ),
    )
    rendered = frame.render(max_output_bytes=None)
    assert "evidence: no evidence findings emitted" in rendered
    assert rendered.index("evidence:") < rendered.index("preview:")


def test_show_points_to_full_rows_when_digest_items_are_omitted():
    subject = make_test_subject(metric_id="sales.revenue", analysis_axis="change")
    scope = make_test_analysis_scope("sales.revenue")
    item = ChangeFact(
        item_id="item_1",
        artifact_ref="frame_abc",
        subject=subject,
        scope=scope,
        derivation=DerivationRule(
            rule_id="extract.delta",
            rule_version="v2",
            operator="compare",
            source_fields=("delta",),
            source_finding_refs=(),
        ),
        delta=1.0,
        direction="increase",
    )
    digest = ArtifactDigest(
        artifact_ref="frame_abc",
        operator=OperatorSemantics(
            operator="compare",
            operator_version="v1",
            artifact_family="delta_frame",
            semantic_shape="segmented",
        ),
        subject=subject,
        scope=scope,
        items=(item,),
        boundaries=(
            InferenceBoundary(
                kind="full_distribution_not_in_digest",
                reason="digest_bound_exceeded",
                required_evidence=("full_distribution",),
            ),
        ),
        omissions=OmissionSummary(
            retained_items=1,
            omitted_items=3,
            omitted_kinds=("change",),
            bounded=True,
        ),
        fallback=RawFallback(
            artifact_ref="frame_abc",
            findings_available=True,
            rows_available=True,
        ),
        fingerprint="sha256:test",
    )
    frame = BaseFrame(
        _df=pd.DataFrame({"value": range(8)}),
        meta=_meta(evidence_status="complete", evidence_digest=digest, row_count=8),
    )

    rendered = frame.render(max_output_bytes=None)

    assert "evidence: items=1 omitted=3; call .to_pandas() for all rows" in rendered
    assert "full_distribution_not_in_digest" in rendered


def test_repr_and_show_are_bounded_agent_reads(capsys):
    frame = BaseFrame(_df=pd.DataFrame({"value": range(200)}), meta=_meta(row_count=200))
    assert frame.id == frame.ref
    assert "call .show() to inspect" in repr(frame)
    frame.show(max_output_bytes=300)
    assert len(capsys.readouterr().out.encode()) <= 301
