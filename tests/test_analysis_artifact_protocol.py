"""Phase 1 protocol consistency across all public artifact families."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from marivo.analysis.frames.association import AssociationResult, AssociationResultMeta
from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.base import ArtifactContract, ArtifactSchema, ArtifactState
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.forecast import ForecastFrame, ForecastFrameMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.frames.quality import QualityReport, QualityReportMeta
from marivo.analysis.lineage import Lineage


def _base_meta(kind: str, ref: str, row_count: int = 1) -> dict[str, Any]:
    return {
        "kind": kind,
        "ref": ref,
        "session_id": "sess_protocol",
        "project_root": "/tmp/project",
        "produced_by_job": "job_protocol",
        "created_at": datetime(2026, 6, 26, 8, 0, 0, tzinfo=UTC),
        "row_count": row_count,
        "byte_size": 0,
        "lineage": Lineage(),
        "content_hash": "sha256:" + "a" * 64,
    }


def _artifact_cases():
    yield MetricFrame(
        _df=pd.DataFrame({"bucket_start": ["2026-06-18"], "value": [1.0]}),
        meta=MetricFrameMeta(
            **_base_meta("metric_frame", "frame_metric"),
            metric_id="sales.revenue",
            axes={},
            measure={"name": "revenue"},
            window=None,
            where={},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    yield DeltaFrame(
        _df=pd.DataFrame({"delta": [1.0]}),
        meta=DeltaFrameMeta(
            **_base_meta("delta_frame", "frame_delta"),
            metric_id="sales.revenue",
            source_current_ref="frame_current",
            source_baseline_ref="frame_baseline",
            alignment={"kind": "window_bucket"},
            semantic_kind="time_series",
            semantic_model="sales",
        ),
    )
    yield AttributionFrame(
        _df=pd.DataFrame({"region": ["US"], "contribution": [1.0]}),
        meta=AttributionFrameMeta(
            **_base_meta("attribution_frame", "frame_attr"),
            metric_ids=["sales.revenue"],
            source_refs=["frame_delta"],
            scope_delta_ref="frame_delta",
            attribution_kind="decomposition",
            driver_field="region",
            value_column="delta",
            contribution_column="contribution",
            method="sum",
            params={"by": "region"},
            semantic_kind="segmented",
            semantic_model="sales",
        ),
    )
    yield CandidateSet(
        _df=pd.DataFrame({"item_id": ["cand_1"], "score": [3.0]}),
        meta=CandidateSetMeta(
            **_base_meta("candidate_set", "frame_candidates"),
            shape="point_anomaly",
            objective="point_anomalies",
            strategy="zscore",
            source_ref="frame_metric",
            source_kind="metric_frame",
            metric_ids=["sales.revenue"],
            semantic_kind="time_series",
            semantic_model="sales",
            source_refs=["frame_metric"],
            params={"threshold": 3.0},
        ),
    )
    yield AssociationResult(
        _df=pd.DataFrame({"metric_a": ["a"], "metric_b": ["b"], "correlation": [0.5]}),
        meta=AssociationResultMeta(
            **_base_meta("association_result", "frame_assoc"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.a", "sales.b"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            method="pearson",
            alignment={"kind": "window_bucket"},
            lag_policy={},
            aligned_row_count=5,
            dropped_row_count=0,
            correlation=0.5,
        ),
    )
    yield HypothesisTestResult(
        _df=pd.DataFrame({"segment": ["all"], "rejected": [False]}),
        meta=HypothesisTestResultMeta(
            **_base_meta("hypothesis_test_result", "frame_test"),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.revenue"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            hypothesis="mean_changed",
            method="paired_t",
            alignment={"kind": "window_bucket"},
            sampling={"unit": "day"},
            alpha=0.05,
            result_shape="single",
            segment_dimensions=[],
            rejected_count=0,
            not_enough_data_count=0,
        ),
    )
    yield ForecastFrame(
        _df=pd.DataFrame({"bucket_start": ["2026-06-27"], "forecast": [2.0]}),
        meta=ForecastFrameMeta(
            **_base_meta("forecast_frame", "frame_forecast"),
            source_refs=["frame_history"],
            metric_id="sales.revenue",
            semantic_model="sales",
            semantic_kind="time_series",
            measure={"name": "revenue"},
            axes={},
            history_window={"start": "2026-06-01", "end": "2026-06-26"},
            forecast_window={"start": "2026-06-26", "end": "2026-06-27"},
            horizon=1,
            horizon_unit="day",
            model="naive",
            seasonality_period=None,
            interval_level=0.95,
            interval_method="normal_residual",
            train_row_count_per_segment={"all": 10},
            segment_dimensions=[],
        ),
    )
    yield QualityReport(
        _df=pd.DataFrame({"check_id": ["row_count"], "status": ["ok"], "message": ["ok"]}),
        meta=QualityReportMeta(
            **_base_meta("quality_report", "frame_quality"),
            source_refs=["frame_metric"],
            report_shape="metric",
            target_kind="metric_frame",
            target_metric_id="sales.revenue",
            target_semantic_model="sales",
            target_semantic_kind="time_series",
            checks_run=["row_count"],
            overall_status="ok",
            blocking_issue_count=0,
            warning_count=0,
        ),
    )


def test_public_artifact_families_share_phase1_protocol() -> None:
    forbidden = {
        "headline",
        "conclusion",
        "recommendation",
        "recommended_followups",
        "next_actions",
        "decision_descriptor",
    }

    for artifact in _artifact_cases():
        tag = f"{type(artifact).__name__}(ref={artifact.ref!r})"
        assert artifact.ref, f"{tag}: ref is falsy"
        assert artifact.kind == artifact.meta.kind, f"{tag}: kind mismatch"
        assert isinstance(artifact.contract(), ArtifactContract), f"{tag}: contract() type"
        assert isinstance(artifact.state, ArtifactState), f"{tag}: state type"
        assert artifact.state.content_hash == "sha256:" + "a" * 64, f"{tag}: content_hash"
        assert artifact.to_pandas() is not artifact._df, f"{tag}: to_pandas not isolated"

        contract = artifact.contract()
        assert isinstance(contract.artifact_schema, ArtifactSchema), (
            f"{tag}: contract().artifact_schema type"
        )
        assert contract.artifact_schema.columns, f"{tag}: contract().artifact_schema columns empty"

        payload = {
            "contract": contract.model_dump(mode="json"),
            "state": artifact.state.model_dump(mode="json"),
        }
        for projection_name, projection in payload.items():
            assert forbidden.isdisjoint(projection.keys()), (
                f"{tag}: {projection_name} leaked forbidden keys"
            )
            assert "recommend" not in str(projection).lower(), (
                f"{tag}: {projection_name} contains 'recommend'"
            )
