"""Phase 1 typed shape model: predictors, accessors, and summary/repr."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from marivo.analysis.errors import ComponentDecompositionError, SemanticKindMismatchError
from marivo.analysis.frames.delta import DeltaFrame, DeltaFrameMeta
from marivo.analysis.frames.metric import MetricFrame, MetricFrameMeta
from marivo.analysis.intents._shape import (
    attribution_output_shape,
    compare_output_shape,
    observe_output_shape,
)
from marivo.analysis.lineage import Lineage
from tests.shared_fixtures import make_test_delta_contract, make_test_metric_meta_contract


def _now() -> datetime:
    return datetime(2026, 5, 24, 10, 0, 0, tzinfo=UTC)


def _metric_meta(semantic_kind: str) -> MetricFrameMeta:
    return MetricFrameMeta(
        **make_test_metric_meta_contract("sales.revenue"),
        ref="frame_m",
        session_id="s",
        project_root="/p",
        produced_by_job=None,
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.revenue",
        axes={},
        measure={"name": "revenue"},
        window=None,
        where={},
        semantic_kind=semantic_kind,
        semantic_model="sales",
    )


def _delta_meta(
    *,
    semantic_kind: str = "segmented",
    component_ref: str | None = None,
    composition: dict | None = None,
) -> DeltaFrameMeta:
    return DeltaFrameMeta(
        **make_test_delta_contract("sales.failure_rate"),
        ref="frame_d",
        session_id="s",
        project_root="/p",
        produced_by_job=None,
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
        metric_id="sales.failure_rate",
        source_current_ref="frame_cur",
        source_baseline_ref="frame_base",
        alignment={"kind": "segment_join"},
        semantic_kind=semantic_kind,
        semantic_model="sales",
        component_ref=component_ref,
        composition=composition,
    )


@pytest.mark.parametrize(
    "has_grain, has_dimensions, expected",
    [
        (False, False, "scalar"),
        (True, False, "time_series"),
        (False, True, "segmented"),
        (True, True, "panel"),
    ],
)
def test_observe_output_shape_matrix(has_grain, has_dimensions, expected):
    assert observe_output_shape(has_grain=has_grain, has_dimensions=has_dimensions) == expected


@pytest.mark.parametrize("kind", ["scalar", "time_series", "segmented", "panel"])
def test_compare_output_shape_is_passthrough(kind):
    assert compare_output_shape(_metric_meta(kind)) == kind


def test_attribution_output_shape_sum_when_no_component():
    assert attribution_output_shape(_delta_meta(component_ref=None)) == "sum"


def test_attribution_output_shape_ratio_mix():
    meta = _delta_meta(component_ref="frame_comp", composition={"kind": "ratio"})
    assert attribution_output_shape(meta) == "ratio_mix"


def test_attribution_output_shape_weighted_mix():
    meta = _delta_meta(component_ref="frame_comp", composition={"kind": "weighted_mean"})
    assert attribution_output_shape(meta) == "weighted_mix"


def test_attribution_output_shape_unknown_kind_raises():
    meta = _delta_meta(component_ref="frame_comp", composition={"kind": "mystery"})
    with pytest.raises(ComponentDecompositionError):
        attribution_output_shape(meta)


@pytest.mark.parametrize(
    "composition",
    [None, {}],
    ids=["composition_none", "composition_empty"],
)
def test_attribution_output_shape_raises_when_component_ref_set_but_composition_missing(
    composition,
):
    meta = _delta_meta(component_ref="frame_comp", composition=composition)
    with pytest.raises(ComponentDecompositionError):
        attribution_output_shape(meta)


def test_metric_frame_semantic_shape_reads_meta():
    mf = MetricFrame(_df=pd.DataFrame({"v": [1.0]}), meta=_metric_meta("time_series"))
    assert mf.semantic_shape == "time_series"


def test_metric_frame_as_accessor_returns_self_on_match():
    mf = MetricFrame(_df=pd.DataFrame({"v": [1.0]}), meta=_metric_meta("time_series"))
    assert mf.as_time_series() is mf


def test_metric_frame_as_accessor_raises_on_mismatch():
    mf = MetricFrame(_df=pd.DataFrame({"v": [1.0]}), meta=_metric_meta("scalar"))
    with pytest.raises(SemanticKindMismatchError) as excinfo:
        mf.as_panel()
    rendered = str(excinfo.value)
    assert "semantic_shape" in rendered
    assert "panel" in rendered
    assert "scalar" in rendered


def test_delta_frame_semantic_shape_and_accessor():
    df = DeltaFrame(_df=pd.DataFrame({"delta": [1.0]}), meta=_delta_meta(semantic_kind="panel"))
    assert df.semantic_shape == "panel"
    assert df.as_panel() is df
    with pytest.raises(SemanticKindMismatchError):
        df.as_segmented()


def test_summary_exposes_semantic_shape_for_metric_frame():
    mf = MetricFrame(_df=pd.DataFrame({"v": [1.0]}), meta=_metric_meta("segmented"))
    assert mf.contract().artifact_schema.semantic_shape == "segmented"


def test_summary_semantic_shape_is_none_for_base_frame():
    from marivo.analysis.frames.base import BaseFrame, BaseFrameMeta

    meta = BaseFrameMeta(
        kind="metric_frame",
        ref="frame_b",
        session_id="s",
        project_root="/p",
        produced_by_job=None,
        created_at=_now(),
        row_count=1,
        byte_size=0,
        lineage=Lineage(),
    )
    frame = BaseFrame(_df=pd.DataFrame({"v": [1.0]}), meta=meta)
    assert frame.contract().artifact_schema.semantic_shape is None


def test_repr_identity_includes_semantic_shape_for_metric_frame():
    mf = MetricFrame(_df=pd.DataFrame({"v": [1.0]}), meta=_metric_meta("panel"))
    r = repr(mf)
    assert "shape=panel" in r


def test_delta_predicted_attribution_shape_sum_when_no_component():
    frame = DeltaFrame(_df=pd.DataFrame({"delta": [1.0]}), meta=_delta_meta(component_ref=None))
    assert frame.predicted_attribution_shape() == "sum"


def test_delta_predicted_attribution_shape_ratio_mix():
    meta = _delta_meta(component_ref="frame_comp", composition={"kind": "ratio"})
    frame = DeltaFrame(_df=pd.DataFrame({"delta": [1.0]}), meta=meta)
    assert frame.predicted_attribution_shape() == "ratio_mix"


def test_delta_predicted_attribution_shape_weighted_mix():
    meta = _delta_meta(component_ref="frame_comp", composition={"kind": "weighted_mean"})
    frame = DeltaFrame(_df=pd.DataFrame({"delta": [1.0]}), meta=meta)
    assert frame.predicted_attribution_shape() == "weighted_mix"
