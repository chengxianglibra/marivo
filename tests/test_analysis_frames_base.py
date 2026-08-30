"""Base frame protocol after the typed-digest cutover."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from marivo._compat import UTC
from marivo.analysis.errors import FrameMutationError
from marivo.analysis.evidence.types import (
    ArtifactDigest,
    ChangeFact,
    DerivationRule,
    DigestReadContract,
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
from marivo.introspection.live.model import LiveHelpTarget
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


def test_to_pandas_coerces_decimal_columns_to_float64() -> None:
    frame = BaseFrame(
        _df=pd.DataFrame(
            {
                "value": [Decimal("1.50"), Decimal("2.25")],
                "region": ["US", "CA"],
                "count": [3, 4],
                "ratio": [0.5, 0.75],
                "when": pd.to_datetime(["2026-07-01", "2026-07-02"]),
            }
        ),
        meta=_meta(row_count=2),
    )
    exported = frame.to_pandas()

    # Decimal columns are coerced to float64 for terminal pandas arithmetic.
    assert exported["value"].dtype == "float64"
    assert exported["value"].tolist() == [1.5, 2.25]

    # Non-decimal columns keep their native dtype and values.
    assert exported["region"].tolist() == ["US", "CA"]
    assert exported["count"].dtype == "int64"
    assert exported["ratio"].dtype == "float64"
    assert pd.api.types.is_datetime64_any_dtype(exported["when"])

    # The export remains a defensive copy isolated from the internal frame.
    exported.loc[0, "value"] = 99.0
    assert frame.to_pandas()["value"].iloc[0] == 1.5


def test_to_pandas_coerces_nullable_decimal_column() -> None:
    frame = BaseFrame(
        _df=pd.DataFrame({"value": [Decimal("1.50"), None, Decimal("3.00")]}),
        meta=_meta(row_count=3),
    )
    exported = frame.to_pandas()
    assert exported["value"].dtype == "float64"
    assert exported["value"].iloc[0] == 1.5
    assert pd.isna(exported["value"].iloc[1])
    assert exported["value"].iloc[2] == 3.0


def test_contract_schema_declares_export_dtype_for_decimal_columns() -> None:
    frame = BaseFrame(
        _df=pd.DataFrame(
            {
                "value": [Decimal("1.50"), Decimal("2.25")],
                "region": ["US", "CA"],
                "count": [3, 4],
            }
        ),
        meta=_meta(row_count=2),
    )
    schema = {column.name: column.dtype for column in frame.contract().artifact_schema.columns}

    # The contract must describe the exported dtype, matching to_pandas().
    assert schema["value"] == "float64"
    assert schema["region"] == "object"
    assert schema["count"] == "int64"


def test_frame_row_count_matches_materialized_shape() -> None:
    frame = BaseFrame(_df=pd.DataFrame({"value": [1.0, 2.0]}), meta=_meta())

    assert frame.row_count == 2
    assert frame.row_count == frame.shape[0]


def test_frame_row_count_fails_closed_on_persisted_metadata_drift() -> None:
    with pytest.raises(ValueError, match="frame row count mismatch"):
        BaseFrame(
            _df=pd.DataFrame({"value": [1.0, 2.0]}),
            meta=_meta(row_count=3),
        )


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
        meta=_meta(row_count=1, evidence_status="partial", issues=(issue,)),
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
        "alignment",
        "baseline",
        "current",
    ]
    assert {
        item.parameter: item.bindable_from_current_artifact
        for item in affordance.input_requirements
    } == {"alignment": False, "baseline": True, "current": True}
    assert not hasattr(affordance, "required_inputs")
    assert not hasattr(affordance, "param_template")


def test_metric_transform_affordances_expose_parameter_help_routes() -> None:
    affordances = {item.capability_id: item for item in _metric_frame().contract().affordances}

    rollup = {item.parameter: item for item in affordances["transform.rollup"].input_requirements}
    assert rollup["grain"].accepted_families == ()
    assert rollup["grain"].bindable_from_current_artifact is False
    assert rollup["grain"].help_targets == (
        LiveHelpTarget(surface="analysis", canonical_id="grain"),
        LiveHelpTarget(surface="semantic", canonical_id="calendar_grain"),
    )

    rank = {item.parameter: item for item in affordances["transform.rank"].input_requirements}
    assert rank["method"].help_targets == (
        LiveHelpTarget(surface="analysis", canonical_id="RankMethod"),
    )

    normalize = {
        item.parameter: item for item in affordances["transform.normalize"].input_requirements
    }
    assert normalize["mode"].help_targets == (
        LiveHelpTarget(surface="analysis", canonical_id="NormalizeKind"),
    )
    assert normalize["baseline"].help_targets == (
        LiveHelpTarget(surface="analysis", canonical_id="NormalizeBaseline"),
    )


def test_affordance_and_contract_models_are_closed_and_immutable():
    requirement = ArtifactInputRequirement(
        parameter="source",
        accepted_families=("MetricFrame",),
        bindable_from_current_artifact=True,
    )
    affordance = ArtifactAffordance(
        capability_id="BaseFrame.show",
        public_entrypoint="frame.show()",
        help_target=LiveHelpTarget(
            surface="analysis",
            canonical_id="BaseFrame.show",
        ),
        input_requirements=(requirement,),
        expected_output_family="terminal_text",
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
        ArtifactInputRequirement(
            parameter="mode",
            accepted_families=(),
            bindable_from_current_artifact=False,
        )
    with pytest.raises(ValidationError):
        ArtifactInputRequirement(
            parameter="step",
            accepted_families=("EventFrame",),
            bindable_from_current_artifact=False,
            derivable_from_current_artifact=True,
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


def test_artifact_digest_contract_returns_structural_read_contract() -> None:
    contract = _digest().contract()

    assert isinstance(contract, DigestReadContract)
    assert contract.exact_reads == (
        "session.evidence.digest('frame_abc')",
        "session.evidence.findings(artifact_ref='frame_abc')",
        "session.get_frame('frame_abc')",
    )
    assert "DigestReadContract" in repr(contract)
    assert "call .show()" in repr(contract)


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

    assert (
        "evidence: items=1 omitted=3; recover=session.evidence.findings(artifact_ref='frame_abc')"
    ) in rendered
    assert "subject=sales.revenue" in rendered
    assert "full_distribution_not_in_digest" in rendered


def test_repr_and_show_are_bounded_agent_reads(capsys):
    frame = BaseFrame(_df=pd.DataFrame({"value": range(200)}), meta=_meta(row_count=200))
    # Artifact identity is exposed via ``ref`` only; there is no ``id`` alias.
    assert not hasattr(frame, "id")
    assert "call .show() to inspect" in repr(frame)
    frame.show(max_output_bytes=300)
    assert len(capsys.readouterr().out.encode()) <= 301


def test_frame_default_preview_caps_at_50_rows_with_exact_recovery() -> None:
    frame = BaseFrame(_df=pd.DataFrame({"value": range(53)}), meta=_meta(row_count=53))

    rendered = frame.render()

    assert "\n49\n" in rendered
    assert "\n50\n" not in rendered
    assert "preview (displayed=50 total=53 omitted=3)" in rendered
    assert "session.get_frame('frame_abc').to_pandas()" in rendered
    assert "\n52\n" in frame.render(max_output_bytes=None)


def test_compute_quality_summary_coverage_canonicalizes_aware_scope(
    tmp_path,
    monkeypatch,
):
    """Issue #70: commit-time quality_summary coverage must not report 0.0 when
    the scope window is tz-aware and the frame time column is naive wall-clock."""
    import marivo.analysis.session as session_attach
    from marivo.analysis.frames._meta_defaults import compute_quality_summary
    from tests.shared_fixtures import make_metric_frame

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": pd.Timestamp("2026-06-30T00:00:00") + pd.Timedelta(hours=h), "value": 1.0}
        for h in range(12)
    ]
    frame = make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes={"time": {"field": "time", "grain": "hour"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={
            "start": "2026-06-30T00:00:00+08:00",
            "end": "2026-07-01T00:00:00+08:00",
            "grain": "hour",
            "time_dimension": "time",
        },
        session=session,
    )

    qs = compute_quality_summary(frame)
    assert qs.coverage == pytest.approx(0.5)


def test_compute_quality_summary_coverage_uses_frame_report_tz(
    tmp_path,
    monkeypatch,
):
    """Issue #70 P2: commit-time summary must canonicalize on the frame's report
    timezone (the tz observe used to bucket), not the expected window's tz.

    Half-window geometry pins the basis: window is aware +08:00 but the frame
    carries report_tz=UTC; the observed naive buckets 08:00..11:00 fall in
    [06-30T00:00, 06-30T12:00) only when canonicalized in +08:00, and fall
    entirely outside when canonicalized in UTC — so the two bases disagree and
    the test is sensitive to which one summary picked.
    """
    import marivo.analysis.session as session_attach
    from marivo.analysis.frames._meta_defaults import compute_quality_summary
    from tests.shared_fixtures import make_metric_frame

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": pd.Timestamp("2026-06-30T08:00:00") + pd.Timedelta(hours=h), "value": 1.0}
        for h in range(4)
    ]
    frame = make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes={"time": {"field": "time", "grain": "hour"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={
            "start": "2026-06-30T00:00:00+08:00",
            "end": "2026-06-30T12:00:00+08:00",
            "grain": "hour",
            "time_dimension": "time",
        },
        report_tz="UTC",
        session=session,
    )

    qs = compute_quality_summary(frame)
    # In report_tz UTC the aware window is 06-29T16:00..06-30T04:00 (UTC
    # wall-clock) and the naive 08:00..11:00 buckets fall outside => 0.0.
    # If summary silently canonicalized on the expected side (+08:00) the
    # observed buckets would cover 4/12 => 0.3333. It must use the frame's
    # report_tz (the observe bucketing basis).
    assert qs.coverage == pytest.approx(0.0)
    # report_tz must survive persistence/reload so summary stays consistent
    # with the check on later reads of the same artifact.
    loaded = session.get_frame(frame.ref)
    assert loaded.meta.report_tz == "UTC"


def test_compute_quality_summary_matches_check_when_report_tz_differs_from_scope(
    tmp_path,
    monkeypatch,
):
    """Issue #70 P2: when report_tz differs from the scope window tz, the
    commit-time summary must agree with the time_coverage check (which uses the
    session report_tz), not silently diverge.

    Probe from review: aware +08:00 full-day window, 24 naive observed buckets,
    frame report_tz=UTC. observe buckets naive timestamps by report_tz wall-clock
    so only 16 of the 24 buckets fall inside the UTC-canonicalized window.
    """
    import marivo.analysis.session as session_attach
    from marivo.analysis.frames._meta_defaults import compute_quality_summary
    from tests.shared_fixtures import make_metric_frame

    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = session_attach.get_or_create(name="demo")
    rows = [
        {"time": pd.Timestamp("2026-06-30T00:00:00") + pd.Timedelta(hours=h), "value": 1.0}
        for h in range(24)
    ]
    frame = make_metric_frame(
        pd.DataFrame(rows),
        metric_id="sales.revenue",
        axes={"time": {"field": "time", "grain": "hour"}},
        measure={"field": "value", "aggregation": "sum"},
        semantic_kind="time_series",
        semantic_model="sales",
        window={
            "start": "2026-06-30T00:00:00+08:00",
            "end": "2026-07-01T00:00:00+08:00",
            "grain": "hour",
            "time_dimension": "time",
        },
        report_tz="UTC",
        session=session,
    )

    qs = compute_quality_summary(frame)
    # UTC-canonicalized window is 06-29T16:00..06-30T16:00; naive observed
    # buckets 06-30T00:00..23:00 cover 16 of the 24 buckets => 2/3.
    # Falling back to the expected side (+08:00) would report 1.0.
    assert qs.coverage == pytest.approx(2.0 / 3.0)
