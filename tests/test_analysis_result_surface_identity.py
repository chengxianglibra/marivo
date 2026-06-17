from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from marivo.analysis.frames.attribution import AttributionFrame, AttributionFrameMeta
from marivo.analysis.frames.candidate import CandidateSet, CandidateSetMeta
from marivo.analysis.frames.component import ComponentFrame, ComponentFrameMeta
from marivo.analysis.frames.exploration import ExplorationResult, ExplorationResultMeta
from marivo.analysis.frames.hypothesis import HypothesisTestResult, HypothesisTestResultMeta
from marivo.analysis.lineage import Lineage


def _created_at() -> datetime:
    return datetime(2026, 6, 17, 10, 0, 0, tzinfo=UTC)


def _base_meta(*, kind: str, ref: str, row_count: int = 1) -> dict[str, object]:
    return {
        "kind": kind,
        "ref": ref,
        "session_id": "sess_identity",
        "project_root": "/tmp/proj",
        "produced_by_job": "job_identity",
        "created_at": _created_at(),
        "row_count": row_count,
        "byte_size": 0,
        "lineage": Lineage(),
    }


def test_attribution_frame_identity_includes_kind_and_method() -> None:
    frame = AttributionFrame(
        _df=pd.DataFrame({"region": ["north"], "contribution": [10.0]}),
        meta=AttributionFrameMeta(
            **_base_meta(kind="attribution_frame", ref="frame_attr", row_count=1),
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

    assert frame._repr_identity() == (
        "AttributionFrame ref=frame_attr attribution_kind=decomposition method=sum rows=1"
    )
    assert repr(frame).startswith("<AttributionFrame ref=frame_attr attribution_kind=decomposition")


def test_component_frame_identity_includes_parent_and_metric() -> None:
    frame = ComponentFrame(
        _df=pd.DataFrame({"failed": [1.0], "total": [4.0], "failure_rate": [0.25]}),
        meta=ComponentFrameMeta(
            **_base_meta(kind="component_frame", ref="frame_component", row_count=1),
            parent_ref="frame_parent",
            parent_kind="metric_frame",
            metric_id="sales.failure_rate",
            composition_kind="ratio",
            components={"numerator": "sales.failed", "denominator": "sales.total"},
            axes={},
            semantic_kind="scalar",
            semantic_model="sales",
        ),
    )

    assert frame._repr_identity() == (
        "ComponentFrame ref=frame_component parent=frame_parent metric=sales.failure_rate rows=1"
    )
    assert repr(frame).startswith("<ComponentFrame ref=frame_component parent=frame_parent")


def test_candidate_set_identity_includes_objective_and_strategy() -> None:
    frame = CandidateSet(
        _df=pd.DataFrame({"candidate_id": ["cand_1"], "rank": [1]}),
        meta=CandidateSetMeta(
            **_base_meta(kind="candidate_set", ref="frame_candidates", row_count=1),
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

    assert frame._repr_identity() == (
        "CandidateSet ref=frame_candidates objective=point_anomalies strategy=zscore rows=1"
    )
    assert repr(frame).startswith("<CandidateSet ref=frame_candidates objective=point_anomalies")


def test_hypothesis_test_result_identity_includes_hypothesis_method_and_rejections() -> None:
    frame = HypothesisTestResult(
        _df=pd.DataFrame({"segment": ["all"], "rejected": [True]}),
        meta=HypothesisTestResultMeta(
            **_base_meta(kind="hypothesis_test_result", ref="frame_hypothesis", row_count=1),
            source_refs=["frame_a", "frame_b"],
            metric_ids=["sales.revenue"],
            semantic_kinds=["time_series", "time_series"],
            semantic_models=["sales", "sales"],
            hypothesis="mean_changed",
            method="paired_t",
            alignment={"kind": "window_bucket"},
            sampling={"minimum_pairs": 2},
            alpha=0.05,
            result_shape="single",
            segment_dimensions=[],
            rejected_count=1,
            not_enough_data_count=0,
        ),
    )

    assert frame._repr_identity() == (
        "HypothesisTestResult ref=frame_hypothesis hypothesis=mean_changed "
        "method=paired_t rejected=1 rows=1"
    )
    assert repr(frame).startswith(
        "<HypothesisTestResult ref=frame_hypothesis hypothesis=mean_changed"
    )


def test_exploration_result_identity_includes_source_kind() -> None:
    frame = ExplorationResult(
        _df=pd.DataFrame({"value": [1]}),
        meta=ExplorationResultMeta(
            **_base_meta(kind="exploration_result", ref="frame_explore", row_count=1),
            source_kind="pandas",
        ),
    )

    assert frame._repr_identity() == "ExplorationResult ref=frame_explore source=pandas rows=1"
    assert repr(frame).startswith("<ExplorationResult ref=frame_explore source=pandas")
