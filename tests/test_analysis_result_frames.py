import json
from datetime import UTC, datetime

import pandas as pd
import pytest

import marivo.analysis as mv
import marivo.analysis.session as session_attach
from marivo.analysis.errors import FrameMetaInvalidError
from marivo.analysis.evidence.types import (
    AnalysisScope,
    ArtifactDigest,
    OmissionSummary,
    OperatorSemantics,
    RawFallback,
    Subject,
)
from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.lineage import Lineage, LineageStep
from marivo.analysis.session._runtime import persist_frame


def _now():
    return datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)


def _base_meta(session, *, kind, ref):
    return {
        "kind": kind,
        "ref": ref,
        "session_id": session.id,
        "project_root": str(session.project_root),
        "produced_by_job": "job_test",
        "created_at": _now(),
        "row_count": 1,
        "byte_size": 0,
        "lineage": Lineage(
            steps=[
                LineageStep(
                    intent="test",
                    job_ref="job_test",
                    inputs=[],
                    params_digest="sha256:test",
                )
            ]
        ),
    }


def _digest(ref: str, *, operator: str, family: str) -> ArtifactDigest:
    return ArtifactDigest(
        artifact_ref=ref,
        operator=OperatorSemantics(
            operator=operator,
            operator_version="v1",
            artifact_family=family,
        ),
        subject=Subject(metric="sales.revenue", analysis_axis="scalar"),
        scope=AnalysisScope(metric_ids=("sales.revenue",)),
        omissions=OmissionSummary(retained_items=0, omitted_items=0, bounded=True),
        fallback=RawFallback(
            artifact_ref=ref,
            findings_available=True,
            rows_available=True,
        ),
        fingerprint=f"sha256:{ref}",
    )


def test_candidate_set_rejects_removed_persisted_affordances(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = mv.session.get_or_create(name="demo")
    frame = CandidateSet(
        _df=pd.DataFrame(
            {
                "candidate_id": ["cand_1"],
                "source_ref": ["frame_source"],
                "score": [3.5],
                "direction": ["high"],
                "threshold": [3.0],
                "keys_json": ['{"bucket":"2026-05-01"}'],
                "affordances_json": ["[]"],
            }
        ),
        meta=CandidateSetMeta(
            **_base_meta(session, kind="candidate_set", ref="frame_candidates"),
            shape="point_anomaly",
            source_ref="frame_source",
            source_kind="metric_frame",
            objective="point_anomalies",
            strategy="zscore",
            metric_ids=["sales.revenue"],
            semantic_kind="time_series",
            semantic_model="sales",
            source_refs=["frame_source"],
            params={"threshold": 3.0},
        ),
    )
    frame.meta = persist_frame(session, frame)
    meta_path = session._layout.frames_dir / frame.ref / "meta.json"
    legacy_meta = json.loads(meta_path.read_text())
    legacy_meta["affordances"] = [
        {
            "capability_id": "assess_quality",
            "public_entrypoint": "session.assess_quality(...) ",
            "help_target": "assess_quality",
            "expected_output_family": "QualityReport",
        }
    ]
    meta_path.write_text(json.dumps(legacy_meta))

    with pytest.raises(FrameMetaInvalidError, match="corrupt current-schema metadata payload"):
        session.get_frame("frame_candidates")


def test_association_result_round_trips_through_load_frame(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = mv.session.get_or_create(name="demo")
    frame = AssociationResult(
        _df=pd.DataFrame({"correlation": [0.75], "aligned_row_count": [10]}),
        meta=AssociationResultMeta(
            **_base_meta(session, kind="association_result", ref="frame_assoc"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.revenue", "sales.orders"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            method="pearson",
            alignment={"kind": "window_bucket"},
            lag_policy={"mode": "single", "offset": 0},
            aligned_row_count=10,
            dropped_row_count=0,
            correlation=0.75,
            evidence_digest=_digest(
                "frame_assoc", operator="correlate", family="association_result"
            ),
            evidence_status="complete",
        ),
    )
    frame.meta = persist_frame(session, frame)

    loaded = session.get_frame("frame_assoc")

    assert isinstance(loaded, AssociationResult)
    assert loaded.meta.kind == "association_result"
    assert loaded.meta.source_refs == ["frame_a", "frame_b"]
    assert loaded.meta.correlation == 0.75
    assert loaded.to_pandas().iloc[0]["correlation"] == 0.75

    rendered = loaded.render()
    assert "method=pearson" in rendered
    assert "r=" in rendered
    assert "aligned=" in rendered
    assert "dropped=" in rendered
    for metric_id in loaded.meta.metric_ids:
        assert metric_id in rendered
    assert "summary()" not in rendered

    association_text = loaded.render(max_output_bytes=None)
    assert "method=pearson" in association_text
    assert "evidence=complete" in association_text
    assert association_text.index("evidence:") < association_text.index("preview:")


def test_quality_report_renders_evidence_with_family_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_attach._reset_process_state()
    session = mv.session.get_or_create(name="demo")
    quality = QualityReport(
        _df=pd.DataFrame({"check": ["missing_values"], "status": ["ok"]}),
        meta=QualityReportMeta(
            **_base_meta(session, kind="quality_report", ref="frame_quality"),
            source_refs=["frame_metric"],
            report_shape="metric",
            target_kind="metric_frame",
            target_metric_id="sales.revenue",
            target_semantic_model="sales",
            target_semantic_kind="time_series",
            checks_run=["missing_values"],
            overall_status="warning",
            blocking_issue_count=0,
            warning_count=1,
            evidence_digest=_digest(
                "frame_quality", operator="assess_quality", family="quality_report"
            ),
            evidence_status="complete",
        ),
    )

    quality_text = quality.render(max_output_bytes=None)
    assert "status=warning" in quality_text
    assert "evidence=complete" in quality_text
    assert "no evidence findings emitted" in quality_text
    assert quality_text.index("evidence:") < quality_text.index("preview:")
